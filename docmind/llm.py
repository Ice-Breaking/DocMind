"""LLM 客户端封装：OpenAI 兼容模式对接百炼（DashScope）"""
from openai import OpenAI

from docmind import config

_client: OpenAI | None = None


def get_client() -> OpenAI:
    """获取单例 OpenAI 客户端（懒加载，方便测试时 mock）"""
    global _client
    if _client is None:
        if not config.DASHSCOPE_API_KEY:
            raise RuntimeError("未配置 DASHSCOPE_API_KEY，请在 .env 中填写")
        _client = OpenAI(
            api_key=config.DASHSCOPE_API_KEY,
            base_url=config.DASHSCOPE_BASE_URL,
        )
    return _client


def chat(messages: list[dict], tools: list[dict] | None = None):
    """发起一次对话，返回 ChatCompletion 的 message 对象。

    tools 传入 OpenAI function calling 格式的工具列表；
    若模型决定调工具，返回的 message.tool_calls 非空。
    """
    kwargs = {}
    if tools:
        kwargs["tools"] = tools
        kwargs["tool_choice"] = "auto"
    resp = get_client().chat.completions.create(
        model=config.CHAT_MODEL,
        messages=messages,
        **kwargs,
    )
    return resp.choices[0].message


def embed(texts: list[str]) -> list[list[float]]:
    """文本向量化（RAG 用）"""
    resp = get_client().embeddings.create(
        model=config.EMBEDDING_MODEL,
        input=texts,
    )
    return [d.embedding for d in resp.data]
