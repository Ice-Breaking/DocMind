"""面试量化数据：检索质量评测（三路线 × 双数据集）+ 运营指标采集。

产出 Markdown 片段（stdout），供 docs/面试准备.md 引用。
评测口径：Recall@4（Top-4 内命中期望文档）、MRR（平均倒数排名）。
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from docmind import core, semantic_cache, store
from docmind.rag.eval_set import EVAL_SET, HARD_SET


def evaluate(search_fn, dataset):
    hits, mrr_sum, misses = 0, 0.0, []
    for q, expected in dataset:
        try:
            results = search_fn(q)
        except Exception:
            results = []
        rank = None
        for i, r in enumerate(results, 1):
            if getattr(r, "source", "") == expected:
                rank = i
                break
        if rank is not None and rank <= 4:
            hits += 1
        if rank is not None:
            mrr_sum += 1.0 / rank
        else:
            misses.append(q)
    n = len(dataset)
    return hits / n, mrr_sum / n, misses


def main():
    print("构建索引…")
    core.build_shared()
    vstore = core._shared_state["store"]
    retriever = core._shared_state["retriever"]
    print(f"切片数: {len(vstore.chunks)}\n")

    rows = []
    for name, fn in [
        ("纯向量", lambda q: vstore.search(q, top_k=4)),
        ("混合 RRF", lambda q: retriever.search(q, top_k=4, rerank=False)),
        ("混合 + Rerank", lambda q: retriever.search(q, top_k=4, rerank=True)),
    ]:
        t0 = time.time()
        r1, m1, miss1 = evaluate(fn, EVAL_SET)
        r2, m2, miss2 = evaluate(fn, HARD_SET)
        dur = time.time() - t0
        rows.append((name, r1, m1, r2, m2, dur, miss1, miss2))
        print(f"[{name}] 基础集 Recall@4={r1:.1%} MRR={m1:.3f} | "
              f"困难集 Recall@4={r2:.1%} MRR={m2:.3f} ({dur:.0f}s)")

    print("\n---- Markdown 表格 ----")
    print("| 检索路线 | 基础集 Recall@4 | 基础集 MRR | 困难集 Recall@4 | 困难集 MRR |")
    print("|---|---|---|---|---|")
    for name, r1, m1, r2, m2, *_ in rows:
        print(f"| {name} | {r1:.1%} | {m1:.3f} | {r2:.1%} | {m2:.3f} |")

    print("\n---- 困难集未命中（混合+Rerank）----")
    for q in rows[-1][7]:
        print(" -", q)

    # ---- 运营指标 ----
    print("\n---- 运营指标 ----")
    try:
        cs = semantic_cache.stats()
        print("semantic_cache:", cs)
    except Exception as e:
        print("cache stats error:", e)
    ov = store.stats_overview()
    print("overview:", {k: ov[k] for k in
                         ("users", "sessions", "messages", "feedback_up",
                          "feedback_down", "badcase_pending") if k in ov})
    # 拒答次数（全量 trace）
    import json
    import os
    from docmind import config
    refusals = 0
    gens = 0
    if os.path.exists(config.TRACE_LOG_PATH):
        with open(config.TRACE_LOG_PATH, encoding="utf-8") as f:
            for line in f:
                try:
                    d = json.loads(line)
                except ValueError:
                    continue
                if d.get("name") == "evidence-refusal":
                    refusals += 1
                if d.get("kind") == "generation":
                    gens += 1
    print(f"evidence-refusal events: {refusals}, generation total: {gens}")


if __name__ == "__main__":
    main()
