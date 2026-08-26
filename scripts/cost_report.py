"""大小模型路由成本测算：每千次查询的云端 vs 智能路由对比。

方法（面试可讲）：
1. 路由分布不是拍脑袋 —— 用 model_router.resolve() 在「寒暄组 + 知识组」
   样本集上逐条实测决策（纯决策层调用，零 API 开销、完全可复现）；
2. 成本 = 云端按 token 计费 + 本地按「功率 x 时延」折算电费，
   所有单价均为命令行参数（下方列出的默认值需按实际账单校准）；
3. 输出全云端 vs 智能路由的每千次查询成本对比与节省比例。

用法：
    python scripts/cost_report.py                     # 默认参数出报告
    python scripts/cost_report.py --report out.json   # 追加写 JSON

默认定价参考（阿里云百炼公开牌价，2025；请以最新账单为准）：
    qwen-plus: 输入 0.8 元/百万 token，输出 2 元/百万 token
本地电费模型：Mac 笔记本跑 7B-Q4 整机约 25W，市电 0.55 元/kWh。
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# ── 样本集 ────────────────────────────────────────────────────────────────

# 寒暄/无知识意图组：真实客服场景高频开场白（含组合寒暄）
TRIVIAL_SAMPLES = [
    "你好", "您好", "在吗", "嗨", "hello", "Hi",
    "谢谢", "好的，谢谢", "辛苦了", "再见", "晚安",
    "你好呀", "感谢感谢", "收到", "嗯嗯",
]


def load_knowledge_samples() -> list[str]:
    """知识问答组：直接复用检索评测集的问题（保证与真实业务同分布）。"""
    from docmind.rag.eval_set import EVAL_SET, HARD_SET
    return [q for q, _ in EVAL_SET] + [q for q, _ in HARD_SET]


# ── 路由分布实测 ──────────────────────────────────────────────────────────

def measure_route_distribution() -> dict:
    """对全部样本跑 resolve()，返回 {backend: n} 与明细。"""
    from docmind import config, model_router

    # 强制开启路由，避免宿主 .env 影响可复现性
    config.MODEL_ROUTER = True
    config.LOCAL_LLM_ENABLED = True

    cloud_cfg = ("qwen-plus",
                 "https://dashscope.aliyuncs.com/compatible-mode/v1", "")
    counts = {"local": 0, "cloud": 0}
    reasons: dict[str, int] = {}
    detail = []
    for text in TRIVIAL_SAMPLES + load_knowledge_samples():
        d = model_router.resolve([{"role": "user", "content": text}],
                                 cloud_cfg)
        counts[d.backend] += 1
        reasons[d.reason] = reasons.get(d.reason, 0) + 1
        detail.append({"q": text, "backend": d.backend, "reason": d.reason})
    total = sum(counts.values())
    return {"counts": counts, "total": total, "detail": detail,
            "reasons": reasons,
            "trivial_n": len(TRIVIAL_SAMPLES),
            "knowledge_n": total - len(TRIVIAL_SAMPLES)}


# ── 成本模型 ─────────────────────────────────────────────────────────────

def cost_per_call(in_tokens: int, out_tokens: int, p: argparse.Namespace,
                  backend: str, seconds: float | None = None) -> float:
    """单次调用成本（元）。backend: 'local' | 'cloud'。

    seconds 仅本地路径生效：覆盖 p.local_seconds（知识问答生成长，
    寒暄生成短，两者耗时不同）。"""
    if backend == "cloud":
        return (in_tokens / 1000 * p.cloud_in_price
                + out_tokens / 1000 * p.cloud_out_price)
    kwh = p.local_watts * (seconds or p.local_seconds) / 3_600_000
    return kwh * p.electricity_price


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--cloud-in-price", type=float, default=0.0008,
                    help="云端输入单价 元/千token（默认 qwen-plus 牌价）")
    ap.add_argument("--cloud-out-price", type=float, default=0.002,
                    help="云端输出单价 元/千token（默认 qwen-plus 牌价）")
    ap.add_argument("--kb-in-tokens", type=int, default=2000,
                    help="知识问答单次输入 token（RAG 上下文，默认 2000）")
    ap.add_argument("--kb-out-tokens", type=int, default=400,
                    help="知识问答单次输出 token（默认 400）")
    ap.add_argument("--trivial-in-tokens", type=int, default=20,
                    help="寒暄单次输入 token（默认 20）")
    ap.add_argument("--trivial-out-tokens", type=int, default=30,
                    help="寒暄单次输出 token（默认 30）")
    ap.add_argument("--local-watts", type=float, default=25,
                    help="本地推理整机功率 W（默认 25W）")
    ap.add_argument("--local-seconds", type=float, default=1.5,
                    help="本地单次生成秒数（默认 1.5s，可实测替换）")
    ap.add_argument("--local-seconds-kb", type=float, default=8.0,
                    help="KB generation seconds on local (default 8s)")
    ap.add_argument("--faq-offload-pct", type=float, default=0.0,
                    help="scenario: additionally offload x%% KB calls to local (0-100)")

    ap.add_argument("--electricity-price", type=float, default=0.55,
                    help="电价 元/kWh（默认 0.55）")
    ap.add_argument("--report", type=str, default="",
                    help="额外输出 JSON 报告路径")
    args = ap.parse_args()

    dist = measure_route_distribution()
    counts = dist["counts"]
    reasons = dist["reasons"]
    n_local = counts["local"]
    total = dist["total"]
    triv_n, kb_n = dist["trivial_n"], dist["knowledge_n"]

    def ccost(backend: str, in_t: int, out_t: int,
              seconds: float | None = None) -> float:
        return cost_per_call(in_t, out_t, args, backend, seconds=seconds)

    kb_cloud = ccost("cloud", args.kb_in_tokens, args.kb_out_tokens)
    tr_cloud = ccost("cloud", args.trivial_in_tokens, args.trivial_out_tokens)
    tr_local = ccost("local", args.trivial_in_tokens, args.trivial_out_tokens,
                     seconds=args.local_seconds)
    kb_local = ccost("local", args.kb_in_tokens, args.kb_out_tokens,
                     seconds=args.local_seconds_kb)

    base = triv_n * tr_cloud + kb_n * kb_cloud            # 全云端基线

    # 按 reason 细分：trivial→寒暄价、faq_offload→本地知识价，其余云端
    n_triv_local = reasons.get("trivial", 0)
    n_faq_local = reasons.get("faq_offload", 0)
    routed = (n_triv_local * tr_local                       # 寒暄落本地
              + (triv_n - n_triv_local) * tr_cloud          # 未命中仍小请求
              + n_faq_local * kb_local                      # FAQ 灰度走本地
              + (kb_n - n_faq_local) * kb_cloud)            # 其余知识问答云端
    save_pct = (base - routed) / base * 100 if base else 0.0
    per_1k_base = base / total * 1000
    per_1k_routed = routed / total * 1000

    # 情景分析：若把 x% 的知识问答也分流到本地（纯数学外推，
    # 与 ROUTER_FAQ_OFFLOAD_PCT 环境变量控制的实测分流互不影响）
    offload = min(max(args.faq_offload_pct, 0.0), 100.0) / 100
    extra_faq = max(0, kb_n * offload - n_faq_local)      # 情景值不含已生效部分
    routed_faq = routed + extra_faq * (kb_local - kb_cloud)
    save_faq_pct = ((base - routed_faq) / base * 100) if base else 0.0
    per_1k_faq = routed_faq / total * 1000

    print("=" * 62)
    print("DocMind 大小模型路由 · 成本测算报告")
    print("=" * 62)
    print(f"\n[1] 路由分布（resolve() 实测，样本 N={total}）")
    print(f"    寒暄组 {triv_n} 条 → 命中本地 {n_triv_local} 条"
          f"（{n_triv_local / triv_n * 100:.0f}%）")
    if n_faq_local:
        print(f"    知识组 {kb_n} 条 → FAQ 灰度分流本地 {n_faq_local} 条"
              f"（ROUTER_FAQ_OFFLOAD_PCT 生效中），其余走云端")
    else:
        print(f"    知识组 {kb_n} 条 → 走云端（预期行为）")
    print(f"    总分流率 {counts['local'] / total * 100:.1f}%")

    print("\n[2] 单次调用成本（按参数假设计价）")
    print(f"    云端知识问答: {kb_cloud * 100:.4f} 分/次 "
          f"(in {args.kb_in_tokens} tok × {args.cloud_in_price} + "
          f"out {args.kb_out_tokens} tok × {args.cloud_out_price} 元/千tok)")
    print(f"    云端寒暄    : {tr_cloud * 100:.4f} 分/次")
    print(f"    本地寒暄    : {tr_local * 10000:.4f} 厘/次 "
          f"({args.local_watts}W × {args.local_seconds}s × "
          f"{args.electricity_price} 元/kWh)")

    print(f"\n[3] 本样本集 {total} 次查询对比")
    print(f"    全云端基线 : {base * 100:.2f} 分")
    print(f"    智能路由后 : {routed * 100:.2f} 分")
    print(f"    节省       : {(base - routed) * 100:.2f} 分 ({save_pct:.1f}%)")

    print("\n[4] 折算每千次查询（同构成外推）")
    print(f"    全云端: {per_1k_base:.3f} 元 → 路由后: {per_1k_routed:.3f} 元 "
          f"→ 省 {per_1k_base - per_1k_routed:.3f} 元/千次 ({save_pct:.1f}%)")
    if offload > 0:
        print(f"    情景分析：再分流 {offload * 100:.0f}% 知识问答到本地 → "
              f"{per_1k_faq:.3f} 元/千次（省 {save_faq_pct:.1f}%）")
    print("    注：寒暄分流的核心收益在首响延迟与企业数据不出域，"
          "成本收益需靠 FAQ 类分流放大")
    print("\n注: 路由分布为 resolve() 实测；token 量/单价/功耗为参数假设，")
    print("    请以生产账单校准后复跑（--help 查看全部参数）。")

    if args.report:
        Path(args.report).parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "assumptions": vars(args),
            "route_distribution": {
                "total": total, "local": n_local,
                "cloud": counts["cloud"],
                "reasons": reasons,
                "offload_pct": round(n_local / total * 100, 1),
                "detail": dist["detail"],
            },
            "cost_per_call_yuan": {"kb_cloud": kb_cloud,
                                   "kb_local": kb_local,
                                   "trivial_cloud": tr_cloud,
                                   "trivial_local": tr_local},
            "per_sample_set": {"all_cloud": base, "routed": routed,
                               "routed_with_faq_offload": routed_faq,
                               "faq_offload_pct": offload * 100,
                               "save_pct": round(save_pct, 1),
                               "save_pct_with_faq_offload":
                                   round(save_faq_pct, 1)},
            "per_1000_queries_yuan": {
                "all_cloud": round(per_1k_base, 4),
                "routed": round(per_1k_routed, 4),
                "routed_with_faq_offload": round(per_1k_faq, 4),
                "save": round(per_1k_base - per_1k_routed, 4),
                "save_pct": round(save_pct, 1)},
        }
        Path(args.report).write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8")
        print(f"\nJSON 报告已写入 {args.report}")


if __name__ == "__main__":
    main()