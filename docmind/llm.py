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


def chat(messages: list[dict], tools: list[dict] | None = None):
    """发起一次对话，返回 ChatCompletion 的 message 对象。

    tools 传入 OpenAI function calling 格式的工具列表；
    若模型决定调工具，返回的 message.tool_calls 非空。
    """
    kwargs = {}
    if tools:
        kwargs["tools"] = tools
        kwargs["tool_choice"] = "auto"
    return _with_retry(lambda: get_client().chat.completions.create(
        model=config.CHAT_MODEL,
        messages=messages,
        **kwargs,
    )).choices[0].message


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
