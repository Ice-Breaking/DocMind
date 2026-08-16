"""混合检索器：BM25 关键词路 + 向量语义路 → RRF 融合 → 可选 gte-rerank 精排。

设计要点（面试可讲）：
- 双路互补：向量擅长语义近似，BM25 擅长专有名词/缩写/精确关键词
- RRF（Reciprocal Rank Fusion）按排名而非分数融合，无需归一化两路量纲
- Rerank 独立成级：初筛候选控制在 10 条以内再精排，兼顾效果与延迟/成本
- Rerank 失败自动降级为 RRF 结果，保证可用性
"""
import jieba
import requests
from rank_bm25 import BM25Okapi

from docmind import config
from docmind.rag.vector_store import SearchHit, VectorStore

RRF_K = 60          # RRF 平滑常数，经验值 60
CANDIDATE_K = 10    # 初筛候选数量（送入 Rerank 的上限）
RERANK_URL = "https://dashscope.aliyuncs.com/api/v1/services/rerank/text-rerank/text-rerank"


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

    def build(self) -> None:
        """在向量库已 build 的基础上，同步构建 BM25 索引"""
        chunks = self.store.chunks
        self._text2idx = {c["text"]: i for i, c in enumerate(chunks)}
        self.bm25 = BM25Okapi([tokenize(c["text"]) for c in chunks])

    # ---------------- 内部：双路召回 ----------------
    def _bm25_rank(self, query: str, top_k: int) -> list[tuple[int, float]]:
        assert self.bm25 is not None, "HybridRetriever 未 build"
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
        resp = requests.post(
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
    ) -> list[SearchHit]:
        k = top_k or config.TOP_K

        vec_hits = self.store.search(query, top_k=candidate_k)
        bm25_hits = self._bm25_rank(query, candidate_k)

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

        if rerank and candidates:
            try:
                ranked = self._rerank(query, candidates, top_n=k)
                # relevance_score 有真实语义（0~1），用“绝对下限+相对头部”过滤；
                # RRF 分数只是排名融合值，无阈值语义，不过滤
                return filter_reranked(ranked)
            except Exception as e:  # noqa: BLE001 - Rerank 失败降级为 RRF
                print(f"[警告] Rerank 调用失败，降级为 RRF 结果: {e}")
        return candidates[:k]
