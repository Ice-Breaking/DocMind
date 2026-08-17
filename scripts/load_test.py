"""压测：开放检索端点 POST /open/v1/retrieve 并发梯度测试。

口径：并发梯度 1/4/8，每级固定请求数；指标 QPS、P50/P95/P99、错误率。
每次请求真实走 向量召回 + BM25 + RRF + Rerank 全链路（与生产一致）。
临时 API Key 用完即删，不留脏数据。
"""
import os
import statistics
import sys
import time
from concurrent.futures import ThreadPoolExecutor

import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from docmind import store  # noqa: E402

BASE = os.getenv("DOCMIND_BASE", "http://127.0.0.1:7860")
QUESTIONS = [
    "什么是 RAG？",
    "Temperature 参数有什么作用？",
    "list 和 tuple 有什么区别？",
    "什么是装饰器？",
    "MCP 支持哪些传输方式？",
    "端口 7860 被占用了怎么办？",
    "如何提升 RAG 的检索质量？",
    "虚拟环境怎么激活？",
    "什么是幻觉问题？",
    "Agent 如何防止死循环？",
    "什么是 LoRA？",
    "新增文档后问答没引用新内容怎么办？",
]
REQUESTS_PER_LEVEL = 12
LEVELS = [1, 4, 8]


def percentile(arr, p):
    if not arr:
        return 0.0
    arr = sorted(arr)
    return arr[min(len(arr) - 1, int(len(arr) * p))]


def one_call(idx, key):
    q = QUESTIONS[idx % len(QUESTIONS)]
    t0 = time.perf_counter()
    try:
        r = requests.post(
            f"{BASE}/open/v1/retrieve",
            json={"question": q, "top_k": 4},
            headers={"Authorization": f"Bearer {key}"},
            timeout=60,
        )
        latency = (time.perf_counter() - t0) * 1000
        ok = r.status_code == 200 and r.json().get("count", -1) >= 0
        return ok, latency, r.status_code
    except Exception:
        return False, (time.perf_counter() - t0) * 1000, 0


def main():
    key_row = store.create_api_key("压测临时密钥", [], "load-test")
    key = key_row["key"]
    key_id = key_row["id"]
    print(f"临时密钥已创建 (id={key_id})，开始压测 {BASE}\n")

    results = []
    try:
        for c in LEVELS:
            latencies, errors = [], 0
            t0 = time.perf_counter()
            with ThreadPoolExecutor(max_workers=c) as ex:
                for ok, lat, code in ex.map(lambda i: one_call(i, key),
                                            range(REQUESTS_PER_LEVEL)):
                    latencies.append(lat)
                    if not ok:
                        errors += 1
            elapsed = time.perf_counter() - t0
            qps = REQUESTS_PER_LEVEL / elapsed
            row = {
                "concurrency": c,
                "qps": round(qps, 2),
                "p50": round(percentile(latencies, 0.50)),
                "p95": round(percentile(latencies, 0.95)),
                "p99": round(percentile(latencies, 0.99)),
                "avg": round(statistics.mean(latencies)),
                "errors": errors,
                "error_rate": f"{errors / REQUESTS_PER_LEVEL:.1%}",
            }
            results.append(row)
            print(f"[并发 {c}] QPS={row['qps']}  P50={row['p50']}ms  "
                  f"P95={row['p95']}ms  P99={row['p99']}ms  "
                  f"avg={row['avg']}ms  错误率={row['error_rate']}")
    finally:
        store.revoke_api_key(key_id)
        conn = store._conn()
        conn.execute("DELETE FROM api_keys WHERE id=?", (key_id,))
        conn.commit()
        print("\n临时密钥已清理")

    print("\n---- Markdown 表格 ----")
    print("| 并发数 | QPS | P50 (ms) | P95 (ms) | P99 (ms) | 平均 (ms) | 错误率 |")
    print("|---|---|---|---|---|---|---|")
    for r in results:
        print(f"| {r['concurrency']} | {r['qps']} | {r['p50']} | {r['p95']} "
              f"| {r['p99']} | {r['avg']} | {r['error_rate']} |")


if __name__ == "__main__":
    main()
