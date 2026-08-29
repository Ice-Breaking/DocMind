"""混合检索器：BM25 关键词路 + 向量语义路 → RRF 融合 → 可选 gte-rerank 精排。

设计要点（面试可讲）：
- 双路互补：向量擅长语义近似，BM25 擅长专有名词/缩写/精确关键词
- RRF（Reciprocal Rank Fusion）按排名而非分数融合，无需归一化两路量纲
- Rerank 独立成级：初筛候选控制在 10 条以内再精排，兼顾效果与延迟/成本
- Rerank 失败自动降级为 RRF 结果，保证可用性
- 查询级热缓存（query_cache）：同题重复请求跳过 embedding/rerank 两次
  网络往返，键含模型名与候选集指纹，内容/模型变化自动失效
"""
import logging
import os
import threading
import time

import jieba
import requests
from rank_bm25 import BM25Okapi

from docmind import config, trace
from docmind.rag.query_cache import rerank_cached
from docmind.rag.tokenize_cache import tokenize_cached
from docmind.rag.vector_store import SearchHit, VectorStore

logger = logging.getLogger(__name__)

RRF_K = int(os.getenv("DOCMIND_RRF_K", "60"))          # RRF 平滑常数，经验值 60
CANDIDATE_K = int(os.getenv("DOCMIND_CANDIDATE_K", "10"))   # 初筛候选数量（送入 Rerank 的上限）
RERANK_URL = "https://dashscope.aliyuncs.com/api/v1/services/rerank/text-rerank/text-rerank"
# rerank 超时收敛：10 条候选的精排远不需要 30s，端点故障时每请求
# 吃满超时会把 P99 拖到灾难级；可经环境变量覆盖
RERANK_TIMEOUT = float(os.getenv("RERANK_TIMEOUT", "8"))

# Rerank HTTP 连接池：Session 复用 TCP/TLS 连接，每次精排省去重新握手
# 的 ~100-300ms；urllib3 连接池线程安全，多请求并发可共享
_SESSION = requests.Session()

# BM25 后台重建阈值：rank_bm25 是纯 Python 实现，构建 O(总词数)，
# 大语料秒级到十秒级——超过阈值的语料改为后台重建，期间旧索引继续
# 服务（快照机制保证不撕裂）；小语料保持同步懒重建（测试依赖该语义：
# 重建后首次检索立即可见新内容）
_ASYNC_REBUILD_MIN_CHUNKS = int(os.getenv("DOCMIND_BM25_ASYNC_MIN_CHUNKS", "2000"))

# ---------------- Rerank 熔断器 ----------------
# 连续失败 N 次进入冷却期，冷却期内直接跳过 rerank（降级 RRF），
# 端点故障时避免每个请求都等待超时；半开：冷却期过后放行一次探测
_RERANK_BREAKER_MAX_FAILS = 3
_RERANK_COOLDOWN_SECONDS = 60.0
_breaker_lock = threading.Lock()
_breaker_fails = 0
_breaker_open_until = 0.0


def _rerank_breaker_open() -> bool:
    with _breaker_lock:
        return time.monotonic() < _breaker_open_until


def _rerank_breaker_record(success: bool) -> None:
    global _breaker_fails, _breaker_open_until
    with _breaker_lock:
        if success:
            _breaker_fails = 0
            return
        _breaker_fails += 1
        if _breaker_fails >= _RERANK_BREAKER_MAX_FAILS:
            _breaker_open_until = time.monotonic() + _RERANK_COOLDOWN_SECONDS
            _breaker_fails = 0
            logger.warning(f"Rerank 连续失败 {_RERANK_BREAKER_MAX_FAILS} 次，"
                           f"熔断 {_RERANK_COOLDOWN_SECONDS:.0f}s（期间降级 RRF）")


def tokenize(text: str) -> list[str]:
    """jieba 分词 + 小写化，供 BM25 使用"""
    return [t.lower() for t in jieba.cut(text) if t.strip()]


def filter_reranked(ranked: list[SearchHit]) -> list[SearchHit]:
    """Rerank 结果过滤：绝对下限 + 相对头部比例，代替固定阈值。

    固定阈值会因语料/模型不同而失准：真实相关的 relevance_score
    可能在 0.1～0.9 全区间分布，而无关内容一般 < 0.08。
    因此：头部候选过低→整体无关；其余候选跟头部比较而非跟固定线比较。
    """
    if not ranked:
        return []
    top = ranked[0].score
    if top < config.RERANK_MIN_TOP_SCORE:
        return []
    floor = max(config.RERANK_ABS_FLOOR, top * config.RERANK_RELATIVE_RATIO)
    return [h for h in ranked if h.score >= floor]


class HybridRetriever:
    def __init__(self, store: VectorStore):
        self.store = store
        self.bm25: BM25Okapi | None = None
        self._text2idx: dict[str, int] = {}
        # BM25 构建时的 store 版本号与 chunks 快照（身份校验）；
        # 与 store.snapshot() 不一致时懒重建（增量索引后所有持有本
        # store 的检索器自动保持一致）
        self._built_version = -1
        self._built_chunks: list[dict] = []
        # 构建锁：防止并发检索同时触发 build 产生
        # 「_text2idx 新 / bm25 旧」的撕裂状态
        self._build_lock = threading.Lock()
        # 后台重建进行中标记（防重复投递）
        self._rebuild_pending = False

    def build(self) -> None:
        """在向量库已 build 的基础上，同步构建 BM25 索引（空语料时 BM25 置空）。

        分词走切片级缓存（tokenize_cache）：增量重建后 version 变化触发
        全量重建时，文本未变的切片直接命中缓存跳过 jieba，只有真正新增/
        变化的切片才参与分词——与 embed_cache 同思路的 CPU 级去重。"""
        version, chunks = self.store.snapshot()
        self._build_from(chunks, version)

    def _build_from(self, chunks: list[dict], version: int) -> None:
        """从给定快照构建 BM25，构建全部在局部变量完成后一次性发布
        （快照式发布：任何时刻观察到的 (bm25, _text2idx, chunks) 均自洽）"""
        text2idx = {c["text"]: i for i, c in enumerate(chunks)}
        token_lists = tokenize_cached([c["text"] for c in chunks],
                                      tokenizer=tokenize) if chunks else []
        bm25 = BM25Okapi(token_lists) if chunks else None
        self._text2idx = text2idx
        self.bm25 = bm25
        self._built_chunks = chunks
        self._built_version = version

    def _ensure_index(self) -> list[dict]:
        """懒重建并返回当前快照的 chunks：store 切片变化（增量索引/
        全量重建）后 version/chunks 身份变化，此处自动重建 BM25。
        返回值即检索全程使用的 chunks 快照——禁止再读 self.store.chunks
        （可能与索引错位）。

        大语料走后台重建：首个请求不承担全量构建的秒级尾延迟，
        期间旧索引继续服务（最终一致）；小语料同步重建（重建后
        首次检索立即可见新内容，测试依赖该语义）。"""
        version, chunks = self.store.snapshot()
        if self._built_version != version or self._built_chunks is not chunks:
            if len(chunks) >= _ASYNC_REBUILD_MIN_CHUNKS:
                self._kick_background_rebuild()
                return self._built_chunks
            with self._build_lock:
                # 双检：拿到锁后可能已被其他线程构建完毕
                version, chunks = self.store.snapshot()
                if self._built_version != version or self._built_chunks is not chunks:
                    self._build_from(chunks, version)
        return self._built_chunks

    def _kick_background_rebuild(self) -> None:
        """投递后台 BM25 重建（幂等：进行中不重复投递）"""
        with self._build_lock:
            if self._rebuild_pending:
                return
            self._rebuild_pending = True

        def _run():
            try:
                version, chunks = self.store.snapshot()
                if self._built_version != version or self._built_chunks is not chunks:
                    self._build_from(chunks, version)
                    logger.info(f"BM25 后台重建完成（{len(chunks)} 个切片）")
            except Exception:  # noqa: BLE001 - 后台重建失败下次检索重试
                logger.exception("BM25 后台重建失败")
            finally:
                with self._build_lock:
                    self._rebuild_pending = False

        threading.Thread(target=_run, daemon=True,
                         name="bm25-rebuild").start()

    # ---------------- 内部：双路召回 ----------------
    def _bm25_rank(self, query: str, top_k: int,
                   allowed_sources: set[str] | None = None,
                   chunks: list[dict] | None = None) -> list[tuple[int, float]]:
        if self.bm25 is None:
            return []
        chunks = chunks if chunks is not None else self._built_chunks
        scores = self.bm25.get_scores(tokenize(query))
        # ACL 下推：无权来源的文档不参与排序与截断（否则受限文档挤占
        # top_k 名额，受限文档越多召回越差）
        if allowed_sources is not None:
            order = sorted(
                (i for i in range(len(scores))
                 if scores[i] > 0 and chunks[i].get("source", "") in allowed_sources),
                key=lambda i: scores[i], reverse=True)[:top_k]
        else:
            order = sorted(range(len(scores)), key=lambda i: scores[i],
                           reverse=True)[:top_k]
        return [(i, float(scores[i])) for i in order if scores[i] > 0]

    def _rerank(self, query: str, candidates: list[SearchHit], top_n: int) -> list[SearchHit]:
        """带结果缓存的精排入口：同 (model, query, 候选集, top_n) 命中直接返回。
        缓存只存成功结果，失败/熔断语义不变（见 query_cache.rerank_cached）"""
        return rerank_cached(self._rerank_api, query, candidates, top_n)

    def _rerank_api(self, query: str, candidates: list[SearchHit], top_n: int) -> list[SearchHit]:
        """百炼 gte-rerank 精排；失败时抛异常由上层降级。
        用 requests 而非 urllib：自带 certifi 根证书，规避 macOS 上
        urllib 常见的 CERTIFICATE_VERIFY_FAILED 问题"""
        if _rerank_breaker_open():
            raise RuntimeError("rerank 熔断中（端点近期连续失败），跳过精排")
        payload = {
            "model": config.RERANK_MODEL,
            "input": {
                "query": query,
                "documents": [h.text for h in candidates],
            },
            "parameters": {"top_n": top_n, "return_documents": False},
        }
        try:
            resp = _SESSION.post(
                RERANK_URL,
                json=payload,
                headers={"Authorization": f"Bearer {config.DASHSCOPE_API_KEY}"},
                timeout=RERANK_TIMEOUT,
            )
            resp.raise_for_status()
            results = resp.json()["output"]["results"]
        except Exception:
            _rerank_breaker_record(False)
            raise
        _rerank_breaker_record(True)
        ranked = []
        for r in sorted(results, key=lambda x: x["relevance_score"], reverse=True):
            src = candidates[r["index"]]
            ranked.append(SearchHit(text=src.text, source=src.source, score=r["relevance_score"],
                                    page=src.page))
        return ranked

    # ---------------- 对外：检索入口 ----------------
    def _dense_recall(self, query: str, candidate_k: int,
                      query_vec: list[float] | None, kb_tag: str,
                      allowed_sources: set[str] | None = None) -> list[SearchHit]:
        """向量路召回（独立成方法：与 BM25 路并行执行，各自记录 trace span）。
        allowed_sources 下推到 store 层（Chroma where / numpy 截断前过滤）"""
        with trace.span("retrieval:dense", kind="retrieval",
                        kb=kb_tag, input=query[:80]) as _dc:
            hits = self.store.search(query, top_k=candidate_k, query_vec=query_vec,
                                     allowed_sources=allowed_sources)
            _dc["output"] = len(hits)
        return hits

    def _sparse_recall(self, query: str, candidate_k: int, kb_tag: str,
                       allowed_sources: set[str] | None = None,
                       chunks: list[dict] | None = None) -> list[tuple[int, float]]:
        """BM25 路召回（纯 CPU，与向量路并行执行）"""
        with trace.span("retrieval:sparse", kind="retrieval",
                        kb=kb_tag, input=query[:80]) as _sc:
            hits = self._bm25_rank(query, candidate_k,
                                   allowed_sources=allowed_sources, chunks=chunks)
            _sc["output"] = len(hits)
        return hits

    def search(
        self,
        query: str,
        top_k: int | None = None,
        rerank: bool = True,
        candidate_k: int = CANDIDATE_K,
        allowed_sources: set[str] | None = None,
        query_vec: list[float] | None = None,
    ) -> list[SearchHit]:
        """allowed_sources：文档级 ACL 过滤——只保留授权来源的候选（rerank 前过滤，
        避免无权文档挤占 top_k 名额）；None 表示不过滤。
        query_vec：已对同一 query 算过的向量，透传免重复 embed"""
        # 懒重建 + 取快照：检索全程使用同一份 (索引, chunks)，
        # 增量重建并发进行也不会错位
        chunks = self._ensure_index()
        k = top_k or config.TOP_K
        kb_tag = getattr(self.store, "collection_name",
                         getattr(self.store, "_collection_name", ""))

        # 双路并行召回：dense 路（含 query embedding 网络往返 100-500ms）
        # 与 sparse 路（纯 CPU）完全独立，串行是纯等待叠加；
        # 总耗时取两者较大者，rerank 仍依赖融合结果保持后置。
        # ACL 白名单下推到两路内部（截断前过滤）
        import concurrent.futures as _cf
        with _cf.ThreadPoolExecutor(max_workers=2) as _ex:
            f_dense = _ex.submit(self._dense_recall, query, candidate_k,
                                 query_vec, kb_tag, allowed_sources)
            f_sparse = _ex.submit(self._sparse_recall, query, candidate_k,
                                  kb_tag, allowed_sources, chunks)
            vec_hits = f_dense.result()
            bm25_hits = f_sparse.result()

        candidates = self._rrf_merge(vec_hits, bm25_hits, chunks, candidate_k,
                                     allowed_sources)

        if rerank and candidates:
            try:
                with trace.span("retrieval:rerank", kind="retrieval",
                                kb=kb_tag, input=query[:80]) as _rc:
                    ranked = self._rerank(query, candidates, top_n=k)
                    _rc["output"] = len(ranked)
                # relevance_score 有真实语义（0~1），用“绝对下限+相对头部”过滤；
                # RRF 分数只是排名融合值，无阈值语义，不过滤
                return filter_reranked(ranked)
            except Exception as e:  # noqa: BLE001 - Rerank 失败降级为 RRF
                logger.warning(f"Rerank 调用失败，降级为 RRF 结果: {e}")
        return candidates[:k]

    def _rrf_merge(
        self,
        vec_hits: list[SearchHit],
        bm25_hits: list[tuple[int, float]],
        chunks: list[dict],
        candidate_k: int,
        allowed_sources: set[str] | None = None,
    ) -> list[SearchHit]:
        """RRF 融合 + ACL 过滤（search 与 search_debug 共用一份实现）"""
        rrf: dict[int, float] = {}
        for rank, h in enumerate(vec_hits, 1):
            idx = self._text2idx.get(h.text)
            if idx is not None:
                rrf[idx] = rrf.get(idx, 0.0) + 1.0 / (RRF_K + rank)
        for rank, (idx, _score) in enumerate(bm25_hits, 1):
            rrf[idx] = rrf.get(idx, 0.0) + 1.0 / (RRF_K + rank)

        merged = sorted(rrf.items(), key=lambda kv: kv[1], reverse=True)[:candidate_k]
        candidates = [
            SearchHit(
                text=chunks[i]["text"],
                source=chunks[i]["source"],
                score=s,
                page=chunks[i].get("page"),
            )
            for i, s in merged
        ]
        if allowed_sources is not None:
            candidates = [c for c in candidates if c.source in allowed_sources]
        return candidates

    # ---------------- 对外：检索调优调试 ----------------
    def search_debug(self, query: str, top_k: int | None = None,
                     rerank: bool = True, candidate_k: int = CANDIDATE_K,
                     allowed_sources: set[str] | None = None) -> dict:
        """检索调优实验室专用：与 search 同逻辑，但额外返回各阶段耗时、
        召回数量与最终路线，便于定位「召回不准」发生在哪一级。
        不走 trace.span（调试请求不应污染线上链路日志）。"""
        chunks = self._ensure_index()
        k = top_k or config.TOP_K
        stages: list[dict] = []

        t0 = time.perf_counter()
        vec_hits = self.store.search(query, top_k=candidate_k)
        stages.append({"stage": "dense 向量召回", "duration_ms": round((time.perf_counter() - t0) * 1000, 1),
                       "count": len(vec_hits)})

        t0 = time.perf_counter()
        bm25_hits = self._bm25_rank(query, candidate_k)
        stages.append({"stage": "sparse BM25 召回", "duration_ms": round((time.perf_counter() - t0) * 1000, 1),
                       "count": len(bm25_hits)})

        t0 = time.perf_counter()
        candidates = self._rrf_merge(vec_hits, bm25_hits, chunks, candidate_k,
                                     allowed_sources)
        stages.append({"stage": "RRF 融合去重", "duration_ms": round((time.perf_counter() - t0) * 1000, 1),
                       "count": len(candidates)})

        route = "dense + sparse → RRF"
        final = candidates[:k]
        if rerank and candidates:
            t0 = time.perf_counter()
            try:
                ranked = self._rerank(query, candidates, top_n=k)
                filtered = filter_reranked(ranked)
                stages.append({"stage": "rerank 精排+阈值过滤",
                               "duration_ms": round((time.perf_counter() - t0) * 1000, 1),
                               "count": len(filtered)})
                route = "dense + sparse → RRF → rerank"
                final = filtered
            except Exception as e:  # noqa: BLE001 - 与主链路一致的降级策略
                stages.append({"stage": "rerank（失败降级 RRF）",
                               "duration_ms": round((time.perf_counter() - t0) * 1000, 1),
                               "count": len(final), "error": str(e)[:120]})

        return {
            "route": route,
            "stages": stages,
            "hits": [
                {"rank": i, "text": h.text, "source": h.source,
                 "page": h.page, "score": round(float(h.score), 4)}
                for i, h in enumerate(final, 1)
            ],
        }
