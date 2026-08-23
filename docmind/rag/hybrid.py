"""混合检索器：BM25 关键词路 + 向量语义路 → RRF 融合 → 可选 gte-rerank 精排。

设计要点（面试可讲）：
- 双路互补：向量擅长语义近似，BM25 擅长专有名词/缩写/精确关键词
- RRF（Reciprocal Rank Fusion）按排名而非分数融合，无需归一化两路量纲
- Rerank 独立成级：初筛候选控制在 10 条以内再精排，兼顾效果与延迟/成本
- Rerank 失败自动降级为 RRF 结果，保证可用性
"""
import logging
import time

import jieba
import requests
from rank_bm25 import BM25Okapi

from docmind import config, trace
from docmind.rag.tokenize_cache import tokenize_cached
from docmind.rag.vector_store import SearchHit, VectorStore

logger = logging.getLogger(__name__)

RRF_K = 60          # RRF 平滑常数，经验值 60
CANDIDATE_K = 10    # 初筛候选数量（送入 Rerank 的上限）
RERANK_URL = "https://dashscope.aliyuncs.com/api/v1/services/rerank/text-rerank/text-rerank"

# Rerank HTTP 连接池：Session 复用 TCP/TLS 连接，每次精排省去重新握手
# 的 ~100-300ms；urllib3 连接池线程安全，多请求并发可共享
_SESSION = requests.Session()


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
        # BM25 构建时的 store 版本号；与 store.version 不一致时懒重建
        # （增量索引后所有持有本 store 的检索器自动保持一致）
        self._built_version = -1

    def build(self) -> None:
        """在向量库已 build 的基础上，同步构建 BM25 索引（空语料时 BM25 置空）。

        分词走切片级缓存（tokenize_cache）：增量重建后 version 变化触发
        全量重建时，文本未变的切片直接命中缓存跳过 jieba，只有真正新增/
        变化的切片才参与分词——与 embed_cache 同思路的 CPU 级去重。"""
        chunks = self.store.chunks
        self._text2idx = {c["text"]: i for i, c in enumerate(chunks)}
        token_lists = tokenize_cached([c["text"] for c in chunks],
                                      tokenizer=tokenize) if chunks else []
        self.bm25 = BM25Okapi(token_lists) if chunks else None
        self._built_version = self.store.version

    # ---------------- 内部：双路召回 ----------------
    def _bm25_rank(self, query: str, top_k: int) -> list[tuple[int, float]]:
        if self.bm25 is None:
            return []
        scores = self.bm25.get_scores(tokenize(query))
        order = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)[:top_k]
        return [(i, float(scores[i])) for i in order if scores[i] > 0]

    def _rerank(self, query: str, candidates: list[SearchHit], top_n: int) -> list[SearchHit]:
        """百炼 gte-rerank 精排；失败时抛异常由上层降级。
        用 requests 而非 urllib：自带 certifi 根证书，规避 macOS 上
        urllib 常见的 CERTIFICATE_VERIFY_FAILED 问题"""
        payload = {
            "model": config.RERANK_MODEL,
            "input": {
                "query": query,
                "documents": [h.text for h in candidates],
            },
            "parameters": {"top_n": top_n, "return_documents": False},
        }
        resp = _SESSION.post(
            RERANK_URL,
            json=payload,
            headers={"Authorization": f"Bearer {config.DASHSCOPE_API_KEY}"},
            timeout=30,
        )
        resp.raise_for_status()
        results = resp.json()["output"]["results"]
        ranked = []
        for r in sorted(results, key=lambda x: x["relevance_score"], reverse=True):
            src = candidates[r["index"]]
            ranked.append(SearchHit(text=src.text, source=src.source, score=r["relevance_score"],
                                    page=src.page))
        return ranked

    # ---------------- 对外：检索入口 ----------------
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
        # 懒重建：store 切片变化（增量索引/全量重建）后 version +1，
        # 此处自动重建 BM25，无需调用方手动同步
        if self._built_version != self.store.version:
            self.build()
        k = top_k or config.TOP_K
        kb_tag = getattr(self.store, "_collection_name", "")

        # 链路阶段埋点：dense/sparse/rerank 各自独立 span，
        # 供检索日志按阶段下钻耗时（链路分析）
        with trace.span("retrieval:dense", kind="retrieval",
                        kb=kb_tag, input=query[:80]) as _dc:
            vec_hits = self.store.search(query, top_k=candidate_k, query_vec=query_vec)
            _dc["output"] = len(vec_hits)
        with trace.span("retrieval:sparse", kind="retrieval",
                        kb=kb_tag, input=query[:80]) as _sc:
            bm25_hits = self._bm25_rank(query, candidate_k)
            _sc["output"] = len(bm25_hits)

        # RRF 融合：每路按排名贡献 1/(RRF_K + rank)，双路命中叠加
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
                text=self.store.chunks[i]["text"],
                source=self.store.chunks[i]["source"],
                score=s,
                page=self.store.chunks[i].get("page"),
            )
            for i, s in merged
        ]
        if allowed_sources is not None:
            candidates = [c for c in candidates if c.source in allowed_sources]

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

    # ---------------- 对外：检索调优调试 ----------------
    def search_debug(self, query: str, top_k: int | None = None,
                     rerank: bool = True, candidate_k: int = CANDIDATE_K,
                     allowed_sources: set[str] | None = None) -> dict:
        """检索调优实验室专用：与 search 同逻辑，但额外返回各阶段耗时、
        召回数量与最终路线，便于定位「召回不准」发生在哪一级。
        不走 trace.span（调试请求不应污染线上链路日志）。"""
        if self._built_version != self.store.version:
            self.build()
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
        rrf: dict[int, float] = {}
        for rank, h in enumerate(vec_hits, 1):
            idx = self._text2idx.get(h.text)
            if idx is not None:
                rrf[idx] = rrf.get(idx, 0.0) + 1.0 / (RRF_K + rank)
        for rank, (idx, _score) in enumerate(bm25_hits, 1):
            rrf[idx] = rrf.get(idx, 0.0) + 1.0 / (RRF_K + rank)
        merged = sorted(rrf.items(), key=lambda kv: kv[1], reverse=True)[:candidate_k]
        candidates = [
            SearchHit(text=self.store.chunks[i]["text"],
                      source=self.store.chunks[i]["source"],
                      score=s, page=self.store.chunks[i].get("page"))
            for i, s in merged
        ]
        if allowed_sources is not None:
            candidates = [c for c in candidates if c.source in allowed_sources]
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
