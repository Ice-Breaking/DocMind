"""检索质量评测：对比 纯向量 vs 混合检索(BM25+向量+RRF) vs 混合+Rerank。

用法：python scripts/eval_retrieval.py [--with-rerank]
指标：Recall@4（Top-4 内命中正确文档的比例）、平均排名 MRR
"""
import argparse
import sys

from docmind.rag.eval_set import EVAL_SET, HARD_SET
from docmind.rag.vector_store import VectorStore


def evaluate(name: str, search_fn, dataset):
    hits, mrr_sum = 0, 0.0
    misses = []
    for q, expected in dataset:
        results = search_fn(q)
        rank = None
        for i, r in enumerate(results, 1):
            if r.source == expected:
                rank = i
                break
        if rank is not None and rank <= 4:
            hits += 1
        if rank is not None:
            mrr_sum += 1.0 / rank
        else:
            misses.append(q)
    n = len(dataset)
    print(f"[{name}] Recall@4 = {hits}/{n} = {hits / n:.1%}   MRR = {mrr_sum / n:.3f}")
    if misses:
        print(f"  未命中: {misses}")
    return hits / n


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--with-rerank", action="store_true", help="同时评测 Rerank 版本")
    args = parser.parse_args()

    print("构建索引（向量化需要调用百炼 API，请稍候）...")
    store = VectorStore()
    store.build()
    print(f"切片数: {len(store.chunks)}\n")

    evaluate("纯向量-基础集", lambda q: store.search(q, top_k=10), EVAL_SET)
    evaluate("纯向量-困难集", lambda q: store.search(q, top_k=10), HARD_SET)

    from docmind.rag.hybrid import HybridRetriever
    hybrid = HybridRetriever(store)
    hybrid.build()
    evaluate("混合RRF-基础集", lambda q: hybrid.search(q, top_k=10, rerank=False), EVAL_SET)
    evaluate("混合RRF-困难集", lambda q: hybrid.search(q, top_k=10, rerank=False), HARD_SET)

    if args.with_rerank:
        evaluate("混合+Rerank-基础集", lambda q: hybrid.search(q, top_k=10, rerank=True), EVAL_SET)
        evaluate("混合+Rerank-困难集", lambda q: hybrid.search(q, top_k=10, rerank=True), HARD_SET)


if __name__ == "__main__":
    main()
