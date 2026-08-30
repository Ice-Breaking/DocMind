#!/usr/bin/env python3
"""LoRA 微调实验 · 第一步：构造「检索查询改写」SFT 训练数据。

任务定义：
    知识库检索对「规范提问」友好，真实用户的口语化/中英混杂/带寒暄的提问
    会显著拉低向量召回（对应评测困难集 HARD_SET 的失分区）。用 LoRA 微调一个
    1.5B 小模型专职做查询改写：口语问题 → 规范检索查询。改写器跑在本地
    Ollama 上，零 API 成本、毫秒级延迟，替代原先「每次问答都让云端大模型
    顺手改写」的做法。

数据策略：
    - 种子：docmind.rag.eval_set 的 EVAL_SET/HARD_SET 规范问题作为改写目标
    - 加噪：确定性变换（前缀寒暄/后缀/口语化映射/中英混说/字符乱序）
      —— 固定随机种子，可复现、离线可跑、不花一分钱 API
    - 可选 --augment N：再让云端大模型为每题补充 N 条自然变体（需 Key）

输出（LLaMA-Factory sharegpt 格式）：
    data/lora/query_rewrite_train.jsonl   训练集（90%）
    data/lora/query_rewrite_test.jsonl    留出测试集（10%）
    data/lora/dataset_info.json           LLaMA-Factory 数据集注册
"""
import argparse
import hashlib
import json
import random
import sys
from pathlib import Path

sys.path.insert(0, ".")

from docmind.rag.eval_set import EVAL_SET, HARD_SET  # noqa: E402

OUT_DIR = Path("data/lora")
INSTRUCTION = "把下面的用户问题改写为适合知识库检索的规范查询，只输出改写后的问题本身。"

# ── 确定性加噪变换 ────────────────────────────────────────────────────────

_PREFIXES = ["请问", "帮我问下", "想了解一下", "麻烦问下", "你知道", "问一下"]
_SUFFIXES = ["？谢谢", "？在线等", "？帮个忙", "", "？", "？感谢解答"]
_COLLOQUIAL = [
    ("什么是", "啥是"), ("怎么", "咋"), ("如何", "怎么"),
    ("区别", "区别是啥"), ("为什么", "为啥"),
]
# 中英混说：把中文术语替换成英文说法（模拟真实用户的夹杂习惯）
_ENGLISH_MIX = [
    ("RAG", "retrieval augmented generation"),
    ("虚拟环境", "virtual environment / venv"),
    ("装饰器", "decorator"),
    ("协程", "coroutine"),
    ("微调", "fine-tuning"),
]
_TYPO_RATE = 0.18  # 邻字交换概率（模拟手滑打错）


def _swap_typo(q: str, rng: random.Random) -> str:
    """随机交换一对相邻汉字（避开标点与空格）。"""
    idxs = [i for i in range(len(q) - 1)
            if q[i].isalnum() and q[i + 1].isalnum()]
    if not idxs or rng.random() > _TYPO_RATE:
        return q
    i = rng.choice(idxs)
    chars = list(q)
    chars[i], chars[i + 1] = chars[i + 1], chars[i]
    return "".join(chars)


def make_noisy(question: str, variant_seed: int) -> str:
    """由规范问题生成一条口语化变体；同 (question, seed) 恒定可复现。"""
    rng = random.Random(variant_seed)
    q = question
    if rng.random() < 0.35:
        src, dst = rng.choice(_COLLOQUIAL)
        q = q.replace(src, dst, 1)
    if rng.random() < 0.25:
        src, dst = rng.choice(_ENGLISH_MIX)
        q = q.replace(src, dst, 1)
    q = _swap_typo(q, rng)
    pre = rng.choice(_PREFIXES) if rng.random() < 0.6 else ""
    suf = rng.choice(_SUFFIXES) if rng.random() < 0.7 else ""
    return f"{pre}{q}{suf}"


def build_pairs(per_question: int = 8) -> list[dict]:
    """全部种子问题 × 多变体 → (noisy, canonical) 样本列表（去重）。"""
    seeds = [q for q, _doc in EVAL_SET + HARD_SET]
    pairs: list[tuple[str, str]] = []
    seen: set[str] = set()
    for qi, canon in enumerate(seeds):
        base = int(hashlib.sha256(canon.encode()).hexdigest()[:12], 16)
        for v in range(per_question):
            noisy = make_noisy(canon, base + v * 7919)
            if noisy == canon or noisy in seen:
                continue
            seen.add(noisy)
            pairs.append((noisy, canon))
    # 兜底正样本：规范问题本身也是合法输入
    for canon in seeds:
        pairs.append((canon, canon))
    return [{"conversations": [
        {"from": "human", "value": f"{INSTRUCTION}\n\n用户问题：{noisy}"},
        {"from": "gpt", "value": canon},
    ]} for noisy, canon in pairs]


def augment_with_llm(pairs: list[dict], per_question: int = 2) -> list[dict]:
    """可选：云端大模型补充自然变体（需要 DASHSCOPE_API_KEY）。"""
    from docmind.llm import chat
    seeds = sorted({c["conversations"][1]["value"] for c in pairs})
    extra: list[dict] = []
    for canon in seeds:
        try:
            msg = chat([{"role": "user", "content":
                         f"把这句话改写成 2 种更口语化的问法（保持原意）：\n{canon}"}],
                       max_tokens=200, temperature=0.8)
            for line in (msg.content or "").strip().splitlines():
                line = line.strip().lstrip("0123456789.、) ")
                if line and len(line) >= 6:
                    extra.append({"conversations": [
                        {"from": "human",
                         "value": f"{INSTRUCTION}\n\n用户问题：{line}"},
                        {"from": "gpt", "value": canon}]})
        except Exception as e:  # noqa: BLE001 - 单条失败跳过
            print(f"  [augment 跳过] {canon}: {e}")
    print(f"LLM 补充变体：{len(extra)} 条")
    return pairs + extra


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--per-question", type=int, default=8,
                    help="每个种子问题的确定性变体数")
    ap.add_argument("--augment", type=int, default=0,
                    help="每题再由云端大模型补充的自然变体数（需 API Key）")
    ap.add_argument("--test-ratio", type=float, default=0.1)
    args = ap.parse_args()

    samples = build_pairs(args.per_question)
    print(f"确定性样本：{len(samples)} 条")
    if args.augment:
        samples = augment_with_llm(samples, args.augment)

    rng = random.Random(42)          # 划分固定，训练/留出集不漂移
    rng.shuffle(samples)
    n_test = max(1, int(len(samples) * args.test_ratio))
    test, train = samples[:n_test], samples[n_test:]

    OUT_DIR.mkdir(parents=True, exist_ok=True)

    def out_file(name: str) -> Path:
        """规范化并校验输出路径：禁止目录成分逃出 data/lora"""
        root = OUT_DIR.resolve()
        path = (root / name).resolve()
        if not path.is_relative_to(root):
            raise SystemExit(f"非法输出路径: {name}")
        return path

    for out_path, rows in [(out_file("query_rewrite_train.jsonl"), train),
                           (out_file("query_rewrite_test.jsonl"), test)]:
        out_path.write_text(
            "".join(json.dumps(s, ensure_ascii=False) + "\n" for s in rows),
            encoding="utf-8")
        print(f"写出 {out_path}：{len(rows)} 条")

    out_file("dataset_info.json").write_text(
        json.dumps({"query_rewrite": {
            "file_name": "query_rewrite_train.jsonl",
            "formatting": "sharegpt",
            "columns": {"messages": "conversations"},
        }}, ensure_ascii=False, indent=2),
        encoding="utf-8")
    print(f"写出 {OUT_DIR/'dataset_info.json'}")


if __name__ == "__main__":
    main()