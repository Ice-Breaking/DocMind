#!/usr/bin/env python3
"""LoRA 微调实验 · 第三步：改写器 A/B 对比评测（微调前 vs 微调后）。

评测口径（面试可讲——数字必须可复现，杜绝编造）：
    用同一批「口语化变体」分别喂给基线模型与微调模型做查询改写，
    再把改写结果送入 DocMind 检索链路，看能否命中评测集标注的源文档。
    报告两组 Recall@k 与平均改写延迟 —— 微调收益直接体现在检索指标上。

前置条件：
    1. 知识库已建索引（服务启动过一次即可）
    2. 两个改写端点可用（默认都是本地 Ollama；基线用原版小模型）
       ollama pull qwen2.5:1.5b          # 基线
       ollama create qwen2.5-rewrite-lora -f scripts/lora/Modelfile.rewrite
       #                                    （微调版，见 merge_and_serve.sh）
用法：
    .venv/bin/python scripts/lora/eval_rewrite.py \
        --baseline-model qwen2.5:1.5b --tuned-model qwen2.5-rewrite-lora
"""
import argparse
import json
import sys
import time

sys.path.insert(0, ".")

from openai import OpenAI  # noqa: E402

from docmind.rag.eval_set import EVAL_SET, HARD_SET  # noqa: E402
from scripts.lora.gen_rewrite_data import INSTRUCTION, make_noisy  # noqa: E402

REWRITE_SYSTEM = INSTRUCTION


def rewrite(client: OpenAI, model: str, question: str) -> str:
    """调一个改写端点；失败返回原问题（等价于「不比基线差」的保守口径）。"""
    try:
        resp = client.chat.completions.create(
            model=model,
            messages=[{"role": "system", "content": REWRITE_SYSTEM},
                      {"role": "user", "content": question}],
            temperature=0.1, max_tokens=128)
        out = (resp.choices[0].message.content or "").strip()
        # 小模型偶尔带引号/前缀，做最小清洗
        return out.strip("\"'` ") or question
    except Exception:  # noqa: BLE001 - 改写失败退回原问题
        return question


def run_arm(client: OpenAI, model: str, cases: list[dict],
            retriever, top_k: int) -> dict:
    hits = 0
    total_ms = 0.0
    misses: list[dict] = []
    for c in cases:
        t0 = time.time()
        rewritten = rewrite(client, model, c["noisy"])
        total_ms += (time.time() - t0) * 1000
        try:
            results = retriever.search(rewritten, top_k=top_k, rerank=False)
        except Exception:  # noqa: BLE001 - 检索异常记未命中
            results = []
        ok = any(getattr(r, "source", "") == c["expected"]
                 for r in results)
        hits += int(ok)
        if not ok:
            misses.append({"noisy": c["noisy"], "rewritten": rewritten,
                           "expected": c["expected"],
                           "group": c.get("group", "?")})
    n = len(cases)
    return {"model": model, "recall": round(hits / n, 4), "hits": hits,
            "n": n, "avg_rewrite_ms": round(total_ms / n, 1),
            "misses": misses}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--baseline-base", default="http://localhost:11434/v1")
    ap.add_argument("--baseline-model", default="qwen2.5:1.5b")
    ap.add_argument("--tuned-base", default="http://localhost:11434/v1")
    ap.add_argument("--tuned-model", default="qwen2.5-rewrite-lora")
    ap.add_argument("--top-k", type=int, default=4)
    ap.add_argument("--variants-per-q", type=int, default=3)
    ap.add_argument("--rerank", action="store_true",
                    help="检索时启用云端 rerank（更接近真实链路，但调 API）")
    ap.add_argument("--report", default="", help="结果 JSON 落盘路径")
    args = ap.parse_args()

    # 构造固定测试样本（与训练同分布不同种子段，v 从 100 起）
    # group 标记样本来自常规集(EVAL_SET)还是困难集(HARD_SET)，
    # 困难集=口语化重灾区，是查询改写微调的目标场景
    cases: list[dict] = []
    for group, pairs in [("eval", EVAL_SET), ("hard", HARD_SET)]:
        for q, doc in pairs:
            base = int(__import__("hashlib").sha256(
                q.encode()).hexdigest()[:12], 16)
            seen = set()
            for v in range(100, 100 + args.variants_per_q):
                noisy = make_noisy(q, base + v * 7919)
                if noisy != q and noisy not in seen:
                    seen.add(noisy)
                    cases.append({"noisy": noisy, "canonical": q,
                                  "expected": doc, "group": group})
    print(f"测试样本：{len(cases)} 条（{args.variants_per_q} 变体 × "
          f"{len(EVAL_SET) + len(HARD_SET)} 题）")

    from docmind.core import build_shared, get_shared
    if get_shared("retriever") is None:
        build_shared()
    retriever = get_shared("retriever")

    baseline_cli = OpenAI(api_key="ollama", base_url=args.baseline_base,
                          timeout=60, max_retries=0)
    tuned_cli = OpenAI(api_key="ollama", base_url=args.tuned_base,
                       timeout=60, max_retries=0)

    print("\n== 基线（未微调）==")
    base_res = run_arm(baseline_cli, args.baseline_model, cases,
                       retriever, args.top_k)
    print("== 微调后 ==")
    tuned_res = run_arm(tuned_cli, args.tuned_model, cases,
                        retriever, args.top_k)

    print(f"\n{'':<28}{'Recall@' + str(args.top_k):>10}{'平均改写ms':>12}")
    print(f"{'基线 ' + base_res['model']:<30}{base_res['recall']:>8}"
          f"{base_res['avg_rewrite_ms']:>12}")
    print(f"{'微调 ' + tuned_res['model']:<30}{tuned_res['recall']:>8}"
          f"{tuned_res['avg_rewrite_ms']:>12}")
    delta = round(tuned_res["recall"] - base_res["recall"], 4)
    print(f"\nRecall 提升：{delta:+.4f}")

    # 分组口径：常规集 vs 困难集（微调的目标场景，收益应集中在 hard 组）
    groups: dict[str, dict] = {}
    for g in ("eval", "hard"):
        cs = [c for c in cases if c["group"] == g]
        row = {"n": len(cs)}
        for name, res in [("baseline", base_res), ("tuned", tuned_res)]:
            missed = sum(1 for m in res["misses"]
                         if m.get("group") == g)
            row[name] = round((len(cs) - missed) / len(cs), 4) if cs else 0.0
        row["delta"] = round(row["tuned"] - row["baseline"], 4)
        groups[g] = row
        print(f"[{g}] n={row['n']}  基线 {row['baseline']} → "
              f"微调 {row['tuned']}（{row['delta']:+.4f}）")

    for name, res in [("baseline", base_res), ("tuned", tuned_res)]:
        print(f"\n[{name}] 未命中样例（最多 5 条）：")
        for m in res["misses"][:5]:
            print(f"  输入: {m['noisy']}\n  改写: {m['rewritten']}"
                  f"\n  期望: {m['expected']}")

    if args.report:
        with open(args.report, "w", encoding="utf-8") as f:
            json.dump({"cases": len(cases), "top_k": args.top_k,
                       "baseline": {k: v for k, v in base_res.items()
                                    if k != "misses"},
                       "tuned": {k: v for k, v in tuned_res.items()
                                 if k != "misses"},
                       "groups": groups,
                       "delta_recall": delta}, f,
                      ensure_ascii=False, indent=2)
        print(f"\n结果已写入 {args.report}")


if __name__ == "__main__":
    main()