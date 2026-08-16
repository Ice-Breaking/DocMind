"""LLM 客户端封装：OpenAI 兼容模式对接百炼（DashScope）"""
import time

from openai import (
    APIConnectionError,
    APITimeoutError,
    InternalServerError,
    OpenAI,
    RateLimitError,
)

from docmind import config
from docmind import trace

_client: OpenAI | None = None

# 可重试的瞬时错误（限流/超时/服务端抖动）
_RETRYABLE = (RateLimitError, APITimeoutError, APIConnectionError, InternalServerError)
_MAX_RETRIES = 3


def get_client() -> OpenAI:
    """获取单例 OpenAI 客户端（懒加载，方便测试时 mock）"""
    global _client
    if _client is None:
        if not config.DASHSCOPE_API_KEY:
            raise RuntimeError("未配置 DASHSCOPE_API_KEY，请在 .env 中填写")
        _client = OpenAI(
            api_key=config.DASHSCOPE_API_KEY,
            base_url=config.DASHSCOPE_BASE_URL,
            timeout=60.0,  # 避免请求挂起导致界面卡在“思考中”
        )
    return _client


def _with_retry(fn):
    """瞬时错误退避重试，避免偶发限流/超时导致回答中断"""
    for attempt in range(_MAX_RETRIES):
        try:
            return fn()
        except _RETRYABLE:
            if attempt == _MAX_RETRIES - 1:
                raise
            time.sleep(2 * (attempt + 1))


def _brief_messages(messages: list[dict]) -> list[dict]:
    """追踪日志轻量化：只保留最近 3 条，每条截断 200 字"""
    out = []
    for m in messages[-3:]:
        content = m.get("content") or ""
        out.append({"role": m.get("role"), "content": content[:200]})
    return out


def chat(messages: list[dict], tools: list[dict] | None = None):
    """发起一次对话，返回 ChatCompletion 的 message 对象。

    tools 传入 OpenAI function calling 格式的工具列表；
    若模型决定调工具，返回的 message.tool_calls 非空。
    """
    kwargs = {}
    if tools:
        kwargs["tools"] = tools
        kwargs["tool_choice"] = "auto"
    with trace.span("llm-chat", kind="generation", model=config.CHAT_MODEL,
                    input=_brief_messages(messages)) as ctx:
        resp = _with_retry(lambda: get_client().chat.completions.create(
            model=config.CHAT_MODEL,
            messages=messages,
            temperature=0.1,  # 知识问答场景用低温度：提升工具调用/指令遵循的稳定性
            **kwargs,
        ))
        msg = resp.choices[0].message
        ctx["output"] = (msg.content or f"[调用工具: {[tc.function.name for tc in msg.tool_calls]}]")[:300] if msg.tool_calls or msg.content else ""
        if getattr(resp, "usage", None):
            ctx["usage"] = {"input": resp.usage.prompt_tokens, "output": resp.usage.completion_tokens}
        return msg


def chat_stream(messages: list[dict], tools: list[dict] | None = None):
    """流式对话：yield ChatCompletionChunk，调用方自行累积内容与 tool_calls。

    带 usage 统计（stream_options）；创建阶段的瞬时错误同样退避重试。
    """
    kwargs = {"stream": True, "stream_options": {"include_usage": True}}
    if tools:
        kwargs["tools"] = tools
        kwargs["tool_choice"] = "auto"

    def _create():
        return get_client().chat.completions.create(
            model=config.CHAT_MODEL,
            messages=messages,
            temperature=0.1,
            **kwargs,
        )

    yield from _with_retry(_create)


def embed(texts: list[str]) -> list[list[float]]:
    """文本向量化（RAG 用）。百炼单次最多 10 条，自动分批提交"""
    batch_size = 10
    results: list[list[float]] = []
    for i in range(0, len(texts), batch_size):
        resp = _with_retry(lambda chunk=texts[i:i + batch_size]: get_client().embeddings.create(
            model=config.EMBEDDING_MODEL,
            input=chunk,
        ))
        results.extend(d.embedding for d in resp.data)
    return results
