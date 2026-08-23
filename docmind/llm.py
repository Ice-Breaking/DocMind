"""LLM 客户端封装：OpenAI 兼容模式对接百炼（DashScope）"""
import time

from openai import (
    APIConnectionError,
    APITimeoutError,
    BadRequestError,
    InternalServerError,
    OpenAI,
    RateLimitError,
)

from docmind import config
from docmind import trace
from docmind.metrics import ERRORS, LLM_CALLS, LLM_LATENCY, LLM_TOKENS


def _record_llm_metrics(status: str, start: float, resp=None) -> None:
    """记录 LLM 调用指标；任何异常静默吞掉，绝不影响主链路"""
    try:
        LLM_CALLS.labels(model=config.CHAT_MODEL, status=status).inc()
        LLM_LATENCY.labels(model=config.CHAT_MODEL).observe(time.time() - start)
        if resp is not None and getattr(resp, "usage", None):
            LLM_TOKENS.labels(direction="input").inc(resp.usage.prompt_tokens or 0)
            LLM_TOKENS.labels(direction="output").inc(resp.usage.completion_tokens or 0)
    except Exception:  # noqa: BLE001
        pass

_client: OpenAI | None = None

# 可重试的瞬时错误（限流/超时/服务端抖动）
_RETRYABLE = (RateLimitError, APITimeoutError, APIConnectionError, InternalServerError)
_MAX_RETRIES = 3


def _active_cfg(kind: str) -> tuple[str, str, str]:
    """在线模型配置优先：返回 (model_name, base_url, api_key)；
    未配置生效模型时回退 .env。kind: llm / embedding"""
    try:
        from docmind import store as _store
        m = _store.get_active_model(kind)
        if m:
            return (m["model_name"],
                    m.get("base_url") or config.DASHSCOPE_BASE_URL,
                    m.get("api_key") or config.DASHSCOPE_API_KEY)
    except Exception:  # noqa: BLE001 - 库未就绪/异常时回退 env 配置
        pass
    model = config.EMBEDDING_MODEL if kind == "embedding" else config.CHAT_MODEL
    return model, config.DASHSCOPE_BASE_URL, config.DASHSCOPE_API_KEY


_clients: dict[tuple, OpenAI] = {}


def get_client() -> OpenAI:
    """获取 OpenAI 客户端：按在线配置的 (base_url, key) 缓存多实例，
    模型管理页切换供应商后立即生效；无在线配置时用默认单例"""
    global _client
    model_name, base_url, api_key = _active_cfg("llm")
    cache_key = (base_url, api_key)
    cli = _clients.get(cache_key)
    if cli is None:
        if not api_key:
            raise RuntimeError("未配置 DASHSCOPE_API_KEY，请在 .env 中填写")
        cli = OpenAI(
            api_key=api_key,
            base_url=base_url,
            timeout=60.0,  # 避免请求挂起导致界面卡在“思考中”
        )
        _clients[cache_key] = cli
    return cli


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
    """追踪日志轻量化：只保留最近 3 条，每条截断 200 字。
    多模态 content（list）取文本段并标注图片数，避免切片 list 报错"""
    out = []
    for m in messages[-3:]:
        content = m.get("content") or ""
        if isinstance(content, list):
            n_img = sum(1 for c in content if isinstance(c, dict) and c.get("type") == "image_url")
            texts = " ".join(c.get("text", "") for c in content
                             if isinstance(c, dict) and c.get("type") == "text")
            content = f"[+{n_img}图] {texts}"
        out.append({"role": m.get("role"), "content": str(content)[:200]})
    return out


def _vl_extra_body(messages: list[dict]) -> dict:
    """多模态消息自动开启 VL 高分辨率模式。

    大图（如 3072×4096 手机原图）默认被缩到低分辨率处理，封面小字/
    曲目表全部糊掉，模型只能编造（实测：默认模式曲目表全错，高分辨率
    模式逐字正确）。检测到 image_url 即开启，调用方无感。"""
    for m in messages:
        c = m.get("content")
        if isinstance(c, list) and any(
                isinstance(x, dict) and x.get("type") == "image_url" for x in c):
            return {"vl_high_resolution_images": True}
    return {}


def chat(messages: list[dict], tools: list[dict] | None = None,
         max_tokens: int | None = None, temperature: float | None = None,
         model: str | None = None):
    """发起一次对话，返回 ChatCompletion 的 message 对象。

    tools 传入 OpenAI function calling 格式的工具列表；
    若模型决定调工具，返回的 message.tool_calls 非空。
    max_tokens：限制输出长度，None 时使用 config.MAX_OUTPUT_TOKENS 防止截断。
    temperature：生成温度，None 时使用默认值 0.1。
    model：显式覆盖模型（如多模态消息须用 VISION_MODEL），None 用在线配置。"""
    kwargs = {}
    if tools:
        kwargs["tools"] = tools
        kwargs["tool_choice"] = "auto"
    # 默认使用配置的最大 token 数，防止回复被截断
    kwargs["max_tokens"] = max_tokens if max_tokens is not None else config.MAX_OUTPUT_TOKENS
    _model = model or _active_cfg("llm")[0]
    with trace.span("llm-chat", kind="generation", model=_model,
                    input=_brief_messages(messages)) as ctx:
        _start = time.time()
        try:
            resp = _with_retry(lambda: get_client().chat.completions.create(
                model=_model,
                messages=messages,
                temperature=temperature if temperature is not None else 0.1,
                extra_body=_vl_extra_body(messages) or None,
                **kwargs,
            ))
        except Exception:
            _record_llm_metrics("error", _start)
            try:
                ERRORS.labels(stage="llm").inc()
            except Exception:  # noqa: BLE001
                pass
            raise
        _record_llm_metrics("success", _start, resp)
        msg = resp.choices[0].message
        ctx["output"] = (msg.content or f"[调用工具: {[tc.function.name for tc in msg.tool_calls]}]")[:300] if msg.tool_calls or msg.content else ""
        if getattr(resp, "usage", None):
            ctx["usage"] = {"input": resp.usage.prompt_tokens, "output": resp.usage.completion_tokens}
        return msg


def chat_stream(messages: list[dict], tools: list[dict] | None = None,
                enable_thinking: bool = False, max_tokens: int | None = None,
                model: str | None = None):
    """流式对话：yield ChatCompletionChunk，调用方自行累积内容与 tool_calls。

    带 usage 统计（stream_options）；创建阶段的瞬时错误同样退避重试。
    enable_thinking=True 时请求百炼思维链（delta.reasoning_content 逐段返回），
    仅部分模型支持：不支持的模型报参数错误，自动去掉该参数重试（降级不影响主链路）。
    max_tokens：限制输出长度，None 时使用 config.MAX_OUTPUT_TOKENS 防止截断。
    model：显式覆盖模型（多模态消息用 VISION_MODEL），None 用在线配置。
    """
    kwargs = {"stream": True, "stream_options": {"include_usage": True}}
    if tools:
        kwargs["tools"] = tools
        kwargs["tool_choice"] = "auto"
    # 默认使用配置的最大 token 数
    kwargs["max_tokens"] = max_tokens if max_tokens is not None else config.MAX_OUTPUT_TOKENS

    _model = model or _active_cfg("llm")[0]

    def _create(thinking: bool):
        extra = {"enable_thinking": True} if thinking else {}
        extra.update(_vl_extra_body(messages))   # 多模态消息自动高分辨率
        return get_client().chat.completions.create(
            model=_model,
            messages=messages,
            # 百炼建议：开启思维链时温度不宜过低（0.1 易陷入重复推理）
            temperature=0.6 if thinking else 0.1,
            extra_body=extra or None,
            **kwargs,
        )

    def _stream_with_metrics(gen):
        """流式指标包装：首 chunk 记成功/耗时，usage 尾包记 token，异常记 error"""
        _start = time.time()
        _counted = False
        try:
            for chunk in gen:
                if not _counted:
                    _record_llm_metrics("success", _start)
                    _counted = True
                if getattr(chunk, "usage", None):
                    try:
                        LLM_TOKENS.labels(direction="input").inc(chunk.usage.prompt_tokens or 0)
                        LLM_TOKENS.labels(direction="output").inc(chunk.usage.completion_tokens or 0)
                    except Exception:  # noqa: BLE001
                        pass
                yield chunk
        except Exception:
            _record_llm_metrics("error", _start)
            try:
                ERRORS.labels(stage="llm").inc()
            except Exception:  # noqa: BLE001
                pass
            raise

    try:
        yield from _stream_with_metrics(_with_retry(lambda: _create(enable_thinking)))
    except BadRequestError:
        if not enable_thinking:
            raise
        yield from _stream_with_metrics(_with_retry(lambda: _create(False)))


def embed(texts: list[str]) -> list[list[float]]:
    """文本向量化（RAG 用）。百炼单次最多 10 条，自动分批提交。
    注意：切换在线 Embedding 模型只影响新增切片，存量索引维度不变——
    换模型后必须全量重建知识库索引。"""
    _model, _base, _key = _active_cfg("embedding")
    cli = _clients.get((_base, _key))
    if cli is None:
        cli = OpenAI(api_key=_key, base_url=_base, timeout=60.0)
        _clients[(_base, _key)] = cli
    batch_size = 10
    results: list[list[float]] = []
    for i in range(0, len(texts), batch_size):
        resp = _with_retry(lambda chunk=texts[i:i + batch_size]: cli.embeddings.create(
            model=_model,
            input=chunk,
        ))
        results.extend(d.embedding for d in resp.data)
    return results
