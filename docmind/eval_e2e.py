"""端到端评测：真实链路（ReAct + 混合检索 + 工具）跑用例，量化回答质量。

与 rag/eval_set.py 的检索评测互补：那里只评"检索命不命中"，这里评
"完整问答链路给出的最终回答好不好"。

评分维度（每维 0/1，加权汇总）：
- source_hit 0.5：引用是否命中预期来源文件（知识库类用例）
- keywords   0.3：回答是否覆盖关键要点（定义了 keywords 的用例；未定义则权重并入 source）
- format     0.2：是否按规范给出来源标注（[来源:...] 或【模型通识】）
- OOD 用例（知识库外问题）：评"诚实性"——是否如实承认知识库没有/标注通识，不伪造来源

用法：
    python -m docmind.eval_e2e              # 冒烟集（8 题，约 8 分钟）
    python -m docmind.eval_e2e --all        # 全量 47+ 题（约 40 分钟）
    python -m docmind.eval_e2e --limit 15   # 前 N 题
    python -m docmind.eval_e2e --judge      # 追加 LLM 评审（1-5 分，另耗 token）

报告输出：控制台汇总表 + data/eval/e2e_report_<时间戳>.json
"""
import argparse
import json
import os
import re
import time
from datetime import datetime

from docmind.core import build_agent
from docmind.rag.eval_set import EVAL_SET, HARD_SET

# 冒烟集：跨类别抽样（概念/专有名词/流程/口语化/英文/知识库文档/PDF）
SMOKE_IDX = [0, 7, 13, 20, 24, 35, 36]

# 为部分用例人工标注关键要点（关键词命中即算覆盖）
KEYWORDS = {
    "什么是 RAG？": ["检索增强", "知识库"],
    "Function Calling 是什么？": ["函数", "工具"],
    "Agent 如何防止死循环？": ["步数", "重复"],
    "什么是 MCP？": ["协议", "工具"],
    "list 和 tuple 有什么区别？": ["可变", "不可变"],
    "什么是装饰器？": ["函数", "@"],
}

# OOD 用例：知识库里没有，考察"不编造"
OOD_CASES = [
    {"q": "红烧肉怎么做才好吃？", "note": "完全域外（烹饪）"},
    # 世界杯类体育赛果用例会被百炼内容安全拦截（走异常分支），换无审查风险的知识截止类问题
    {"q": "可控核聚变什么时候能商业化发电？", "note": "知识截止后/预测类"},
]

HONEST_MARKERS = ["知识库无相关内容", "模型通识", "未找到", "没有找到", "无法从知识库"]


def build_cases(all_mode: bool, limit: int | None) -> list[dict]:
    cases = []
    pool = list(EVAL_SET) + list(HARD_SET)
    idxs = range(len(pool)) if all_mode else SMOKE_IDX
    for i in idxs:
        if limit and len(cases) >= limit:
            break
        q, src = pool[i]
        cases.append({"q": q, "source": src, "keywords": KEYWORDS.get(q)})
    for c in OOD_CASES:
        cases.append({"q": c["q"], "source": None, "ood": True, "note": c["note"]})
    return cases


def score_case(case: dict, answer: str) -> dict:
    r = {"q": case["q"]}
    if case.get("ood"):
        # 细粒度诚实性：不伪造知识库来源（0.6，底线）+ 透明度标注（0.4，规范）
        fake_kb = bool(re.search(r"\[来源: [^\]]+\.(?:md|pdf|docx|xlsx|txt)", answer))
        has_marker = any(m in answer for m in HONEST_MARKERS)
        r["no_fake_kb"] = not fake_kb
        r["transparency_marker"] = has_marker
        r["honest"] = bool(r["no_fake_kb"] and has_marker)
        r["score"] = round(0.6 * r["no_fake_kb"] + 0.4 * has_marker, 3)
        return r
    source_hit = case["source"] in answer
    has_citation = "[来源:" in answer or "【模型通识】" in answer
    kws = case.get("keywords")
    if kws:
        kw_score = sum(1 for k in kws if k in answer) / len(kws)
        total = 0.5 * source_hit + 0.3 * kw_score + 0.2 * has_citation
    else:
        kw_score = None
        total = 0.7 * source_hit + 0.3 * has_citation
    r.update({"source_hit": bool(source_hit), "has_citation": bool(has_citation),
              "kw_score": kw_score, "score": round(total, 3)})
    return r


def llm_judge(question: str, answer: str) -> dict:
    """LLM 评审：1-5 分 + 一句话理由（--judge 时启用）"""
    from docmind.llm import get_client
    from docmind import config
    prompt = (f"你是问答质量评审。请给以下回答打 1-5 分（5 最好），只输出 JSON："
              f'{{"score": 数字, "reason": "一句话理由"}}\n'
              f"问题：{question}\n回答：{answer[:1500]}")
    resp = get_client().chat.completions.create(
        model=config.CHAT_MODEL, messages=[{"role": "user", "content": prompt}],
        temperature=0.0)
    text = resp.choices[0].message.content or ""
    m = re.search(r"\{[\s\S]*?\}", text)
    try:
        return json.loads(m.group(0)) if m else {"score": None, "reason": text[:60]}
    except json.JSONDecodeError:
        return {"score": None, "reason": text[:60]}


def run_one(agent, case: dict, judge: bool) -> dict:
    agent.reset()
    final = ""
    t0 = time.time()
    try:
        for step in agent.ask(case["q"]):
            if step.kind == "final":
                final = step.text
    except Exception as e:  # noqa: BLE001
        final = f"[执行异常] {e}"
    r = score_case(case, final)
    r["elapsed"] = round(time.time() - t0, 1)
    if judge:
        try:
            j = llm_judge(case["q"], final)
            r["judge_score"], r["judge_reason"] = j.get("score"), j.get("reason", "")
        except Exception as e:  # noqa: BLE001
            r["judge_score"], r["judge_reason"] = None, f"评审失败: {e}"
    return r


def main() -> int:
    ap = argparse.ArgumentParser(description="DocMind 端到端评测")
    ap.add_argument("--all", action="store_true", help="跑全量评测集（默认冒烟集）")
    ap.add_argument("--limit", type=int, default=None, help="只跑前 N 题")
    ap.add_argument("--judge", action="store_true", help="追加 LLM 评审（1-5 分）")
    args = ap.parse_args()

    cases = build_cases(args.all, args.limit)
    print(f"[eval] 用例数: {len(cases)}（{'全量' if args.all else '冒烟'}，"
          f"judge={'开' if args.judge else '关'}）")
    agent, _, _ = build_agent()

    results = []
    for i, case in enumerate(cases, 1):
        print(f"[{i}/{len(cases)}] {case['q'][:30]} ...", flush=True)
        r = run_one(agent, case, args.judge)
        results.append(r)
        tag = f"诚实{'✓' if r['honest'] else '✗'}" if "honest" in r \
            else f"来源{'✓' if r['source_hit'] else '✗'}"
        print(f"    → {r['score']:.2f}  {tag}  {r['elapsed']}s")

    # ---- 汇总 ----
    n = len(results)
    avg = sum(r["score"] for r in results) / n if n else 0
    kb_results = [r for r in results if "source_hit" in r]
    ood_results = [r for r in results if "honest" in r]
    summary = {
        "时间": datetime.now().isoformat(timespec="seconds"),
        "用例数": n,
        "平均得分": round(avg, 3),
        "来源命中率": (f"{sum(r['source_hit'] for r in kb_results)}/{len(kb_results)}"
                    if kb_results else "N/A"),
        "OOD不伪造来源": (f"{sum(r['no_fake_kb'] for r in ood_results)}/{len(ood_results)}"
                      if ood_results else "N/A"),
        "OOD透明度标注": (f"{sum(r['transparency_marker'] for r in ood_results)}/{len(ood_results)}"
                      if ood_results else "N/A"),
    }
    if args.judge:
        js = [r["judge_score"] for r in results if r.get("judge_score")]
        summary["LLM评审均分"] = round(sum(js) / len(js), 2) if js else None

    print("\n========== 评测汇总 ==========")
    for k, v in summary.items():
        print(f"  {k}: {v}")
    print("\n明细：")
    for r in results:
        line = f"  {r['score']:.2f} | {r['q'][:26]}"
        if "honest" in r:
            line += (f" | 不伪造:{'✓' if r['no_fake_kb'] else '✗'}"
                     f" 标注:{'✓' if r['transparency_marker'] else '✗'}")
        else:
            line += f" | 来源:{'✓' if r['source_hit'] else '✗'}"
        if r.get("judge_score"):
            line += f" | 评审:{r['judge_score']}"
        print(line)

    # ---- 报告落盘 ----
    out_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                           "data", "eval")
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, f"e2e_report_{datetime.now():%Y%m%d_%H%M%S}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"summary": summary, "results": results}, f,
                  ensure_ascii=False, indent=2)
    print(f"\n报告已保存: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
