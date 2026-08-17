"""动态追问生成：按回答内容生成针对性追问（替代固定三问）。

设计（面试可讲）：
- 主链路回答完成后，前端按需请求 /api/suggest（问题 + 答案节选）
- 一次低成本 LLM 调用（max_tokens 限长），要求严格输出 JSON 数组
- 解析容错：剥离 ```json 代码围栏、截取首个 JSON 数组、逐项清洗
- 生成/解析失败回退固定三问，UX 永不缺位
- 结果按答案哈希入库缓存（store.suggestions），同答案不重复生成
"""
import json
import logging
import re

from docmind.llm import chat

logger = logging.getLogger(__name__)

FALLBACK_SUGGESTIONS = [
    "能详细解释一下吗？",
    "有哪些实际应用场景？",
    "与其他技术相比有什么优势？",
]

_PROMPT = """请基于下面的问答，生成 3 个用户最可能继续追问的高价值问题。要求：
1. 紧扣回答内容、具体可答，避免"能详细解释一下吗"这类万能空泛问题
2. 严格输出 JSON 数组，不要任何其他文字：["问题1", "问题2", "问题3"]

问题：{q}

回答（节选）：
{a}
"""

_JSON_ARRAY_RE = re.compile(r"\[[\s\S]*?\]")


def parse_suggestions(text: str) -> list[str]:
    """从 LLM 输出解析追问列表：容错代码围栏/多余文字，取首个 JSON 数组"""
    if not text:
        return []
    m = _JSON_ARRAY_RE.search(text)
    if not m:
        return []
    try:
        arr = json.loads(m.group(0))
    except json.JSONDecodeError:
        return []
    if not isinstance(arr, list):
        return []
    items = [str(x).strip().strip('"\'') for x in arr]
    return [x for x in items if x][:3]


def generate_suggestions(question: str, answer: str) -> list[str]:
    """LLM 生成追问；失败回退固定三问（UX 永不缺位）"""
    try:
        msg = chat(
            [{"role": "user",
              "content": _PROMPT.format(q=(question or "")[:200],
                                         a=(answer or "")[:800])}],
            max_tokens=150,
        )
        items = parse_suggestions(msg.content or "")
        if items:
            return items
    except Exception as e:  # noqa: BLE001 - 副任务失败不阻塞
        logger.warning(f"动态追问生成失败，回退固定建议: {e}")
    return list(FALLBACK_SUGGESTIONS)
