"""LLM 客户端封装：OpenAI 兼容模式对接百炼（DashScope）"""
import logging
import os
import random
import time
from concurrent.futures import ThreadPoolExecutor

from openai import (
    APIConnectionError,
    APITimeoutError,
    BadRequestError,
    InternalServerError,
    OpenAI,
    RateLimitError,
)

from docmind import config
from docmind import model_router
from docmind import trace
from docmind.metrics import ERRORS, LLM_CALLS, LLM_LATENCY, LLM_ROUTES, LLM_TOKENS

logger = logging.getLogger(__name__)


def _record_llm_metrics(status: str, start: float, resp=None,
                        model: str | None = None) -> None:
    """记录 LLM 调用指标；任何异常静默吞掉，绝不影响主链路。
    model：实际使用的模型名（路由分流后可能与 config.CHAT_MODEL 不同）"""
    try:
        _m = model or config.CHAT_MODEL
        LLM_CALLS.labels(model=_m, status=status).inc()
        LLM_LATENCY.labels(model=_m).observe(time.time() - start)
        if resp is not None and getattr(resp, "usage", None):
            LLM_TOKENS.labels(direction="input").inc(resp.usage.prompt_tokens or 0)
            LLM_TOKENS.labels(direction="output").inc(resp.usage.completion_tokens or 0)
    except Exception:  # noqa: BLE001
        pass

_client: OpenAI | None = None  # 兼容旧引用（部分测试/脚本 import 该符号）

# 可重试的瞬时错误（限流/超时/服务端抖动）
_RETRYABLE = (RateLimitError, APITimeoutError, APIConnectionError, InternalServerError)
_MAX_RETRIES = 3


_active_cfg_cache: dict[str, tuple[float, tuple[str, str, str]]] = {}
_ACTIVE_CFG_TTL = 30.0   # 秒；模型管理页切换后最迟 30s 生效（切换时主动失效）


def invalidate_active_cfg() -> None:
    """主动失效模型配置缓存（模型管理页 set_active_model 后调用），
    切换立即生效而不必等 TTL 过期"""
    _active_cfg_cache.clear()


def _active_cfg(kind: str) -> tuple[str, str, str]:
    """在线模型配置优先：返回 (model_name, base_url, api_key)；
    未配置生效模型时回退 .env。kind: llm / embedding

    带 30s TTL 缓存：ReAct 每步循环都会触发本查询，原先每次都打
    一遍 SQLite（get_active_model），纯浪费。"""
    cached = _active_cfg_cache.get(kind)
    if cached and time.time() - cached[0] < _ACTIVE_CFG_TTL:
        return cached[1]
    try:
        from docmind import store as _store
        m = _store.get_active_model(kind)
        if m:
            cfg = (m["model_name"],
                   m.get("base_url") or config.DASHSCOPE_BASE_URL,
                   m.get("api_key") or config.DASHSCOPE_API_KEY)
            _active_cfg_cache[kind] = (time.time(), cfg)
            return cfg
    except Exception:  # noqa: BLE001 - 库未就绪/异常时回退 env 配置
        pass
    model = config.EMBEDDING_MODEL if kind == "embedding" else config.CHAT_MODEL
    cfg = (model, config.DASHSCOPE_BASE_URL, config.DASHSCOPE_API_KEY)
    _active_cfg_cache[kind] = (time.time(), cfg)
    return cfg


_clients: dict[tuple, OpenAI] = {}


def _get_or_create_client(base_url: str, api_key: str, timeout: float = 60.0) -> OpenAI:
    cli = _clients.get((base_url, api_key))
    if cli is None:
        if not api_key:
            raise RuntimeError("未配置 DASHSCOPE_API_KEY，请在 .env 中填写")
        # max_retries=0：SDK 自带 2 次重试与 _with_retry 叠加会把最坏情况
        # 放大到 3×2=6 次同请求重试，限流场景反而加重上游压力——
        # 退避策略统一收敛到 _with_retry 一处
        cli = OpenAI(api_key=api_key, base_url=base_url,
                     timeout=timeout, max_retries=0)
        _clients[(base_url, api_key)] = cli
    return cli


def get_client() -> OpenAI:
    """获取 OpenAI 客户端：按在线配置的 (base_url, key) 缓存多实例，
    模型管理页切换供应商后立即生效；无在线配置时用默认单例"""
    _model, base_url, api_key = _active_cfg("llm")
    return _get_or_create_client(base_url, api_key)


def _route_targets(messages: list[dict] | None, has_tools: bool,
                   thinking: bool, model: str | None) -> list[tuple]:
    """计算按优先级排列的调用目标列表，每项
    (model, base_url, api_key, backend, reason)。

    - 调用方显式指定 model 时不路由（多模态等场景调用方最清楚该用谁）
    - 主目标为本地小模型时在尾部追加云端降级项：本地不可用逐个回退，
      增强类故障永不阻断主链路"""
    cloud_cfg = _active_cfg("llm")
    if model:
        return [(model, cloud_cfg[1], cloud_cfg[2],
                 "cloud", model_router.REASON_EXPLICIT)]
    d = model_router.resolve(messages, cloud_cfg,
                             has_tools=has_tools, thinking=thinking)
    targets = [(*d.target(), d.backend, d.reason)]
    if d.backend == "local":
        targets.append((*cloud_cfg, "cloud", "fallback"))
    return targets


def _with_retry(fn):
    """瞬时错误指数退避 + 抖动重试，避免偶发限流/超时导致回答中断。

    固定间隔退避在多客户端同时被限流时会造成同步重试风暴
    （thundering herd），jitter 打散重试时刻。"""
    for attempt in range(_MAX_RETRIES):
        try:
            return fn()
        except _RETRYABLE:
            if attempt == _MAX_RETRIES - 1:
                raise
            time.sleep(2 * (attempt + 1) + random.uniform(0, 1))


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
    model：显式覆盖模型（如多模态消息须用 VISION_MODEL），None 用在线配置。

    大小模型路由：未显式指定模型时经 model_router 分流——寒暄/超短请求
    走本地小模型（省成本+低延迟），其余走云端主模型；本地失败自动降级
    云端重试一次（增强类故障永不阻断主链路）。
    """
    kwargs = {}
    if tools:
        kwargs["tools"] = tools
        kwargs["tool_choice"] = "auto"
    # 默认使用配置的最大 token 数，防止回复被截断
    kwargs["max_tokens"] = max_tokens if max_tokens is not None else config.MAX_OUTPUT_TOKENS
    _targets = _route_targets(messages, bool(tools), False, model)

    with trace.span("llm-chat", kind="generation", model=_targets[0][0],
                    input=_brief_messages(messages)) as ctx:
        _start = time.time()
        resp = None
        _used_model = None
        for tgt in _targets:
            _model, _base, _key, _backend, _reason = tgt
            try:
                LLM_ROUTES.labels(backend=_backend, reason=_reason).inc()
                cli = _get_or_create_client(
                    _base, _key,
                    timeout=(config.LOCAL_TIMEOUT_SECONDS
                             if _backend == "local" else 60.0))
                resp = _with_retry(
                    lambda m=_model, c=cli: c.chat.completions.create(
                        model=m,
                        messages=messages,
                        temperature=temperature if temperature is not None else 0.1,
                        extra_body=_vl_extra_body(messages) or None,
                        **kwargs,
                    ))
                _used_model = _model
                break
            except Exception as exc:
                if _backend == "local":
                    # 本地是「增强」不是依赖：失败立即降级云端再试
                    LLM_ROUTES.labels(backend="cloud", reason="fallback").inc()
                    logger.warning("本地模型 %s 调用失败，降级云端：%s",
                                   _model, exc)
                    continue
                _record_llm_metrics("error", _start, model=_model)
                try:
                    ERRORS.labels(stage="llm").inc()
                except Exception:  # noqa: BLE001
                    pass
                raise
        if resp is None:  # pragma: no cover - 目标列表恒非空，防御式兜底
            raise RuntimeError("LLM 调用目标列表为空")
        _record_llm_metrics("success", _start, resp, model=_used_model)
        msg = resp.choices[0].message
        ctx["output"] = (msg.content or f"[调用工具: {[tc.function.name for tc in msg.tool_calls]}]")[:300] if msg.tool_calls or msg.content else ""
        if getattr(resp, "usage", None):
            ctx["usage"] = {"input": resp.usage.prompt_tokens, "output": resp.usage.completion_tokens}
        return msg


def chat_stream(messages: list[dict], tools: list[dict] | None = None,
                enable_thinking: bool = False, max_tokens: int | None = None,
                model: str | None = None):
    """流式对话：yield ChatCompletionChunk，调用方自行累积内容与 tool_calls。

    带 usage 统计（stream_options）；建流阶段的瞬时错误同样退避重试。
    enable_thinking=True 时请求百炼思维链（delta.reasoning_content 逐段返回），
    仅部分模型支持：不支持的模型报参数错误，自动去掉该参数重试（降级不影响主链路）。
    max_tokens：限制输出长度，None 时使用 config.MAX_OUTPUT_TOKENS 防止截断。
    model：显式覆盖模型（多模态消息用 VISION_MODEL），None 用在线配置。

    大小模型路由：与 chat() 同规则；降级只发生在建流阶段（任何 chunk 尚未
    产出之前），一旦开始产出绝不切换后端——保证单次回答内容来自同一模型。
    """
    kwargs = {"stream": True, "stream_options": {"include_usage": True}}
    if tools:
        kwargs["tools"] = tools
        kwargs["tool_choice"] = "auto"
    # 默认使用配置的最大 token 数
    kwargs["max_tokens"] = max_tokens if max_tokens is not None else config.MAX_OUTPUT_TOKENS

    _targets = _route_targets(messages, bool(tools), enable_thinking, model)

    def _open_stream(target: tuple):
        """对单个目标建立流（含思维链不支持时去参重试）；返回 Stream 对象。"""
        m, base_url, key, backend = target[0], target[1], target[2], target[3]
        timeout = (config.LOCAL_TIMEOUT_SECONDS if backend == "local"
                   else 60.0)
        cli = _get_or_create_client(base_url, key, timeout=timeout)
        _model = m

        def _create(thinking: bool):
            extra = {"enable_thinking": True} if thinking else {}
            extra.update(_vl_extra_body(messages))   # 多模态消息自动高分辨率
            return cli.chat.completions.create(
                model=_model,
                messages=messages,
                # 百炼建议：开启思维链时温度不宜过低（0.1 易陷入重复推理）
                temperature=0.6 if thinking else 0.1,
                extra_body=extra or None,
                **kwargs,
            )

        try:
            return _with_retry(lambda th=enable_thinking: _create(th))
        except BadRequestError:
            if not enable_thinking:
                raise
            return _with_retry(lambda: _create(False))

    def _stream_with_metrics(gen, used_model: str):
        """流式指标包装：首 chunk 记成功/耗时，usage 尾包记 token，异常记 error"""
        _start = time.time()
        _counted = False
        try:
            for chunk in gen:
                if not _counted:
                    _record_llm_metrics("success", _start, model=used_model)
                    _counted = True
                if getattr(chunk, "usage", None):
                    try:
                        LLM_TOKENS.labels(direction="input").inc(chunk.usage.prompt_tokens or 0)
                        LLM_TOKENS.labels(direction="output").inc(chunk.usage.completion_tokens or 0)
                    except Exception:  # noqa: BLE001
                        pass
                yield chunk
        except Exception:
            _record_llm_metrics("error", _start, model=used_model)
            try:
                ERRORS.labels(stage="llm").inc()
            except Exception:  # noqa: BLE001
                pass
            raise

    last_err: Exception | None = None
    for tgt in _targets:
        try:
            LLM_ROUTES.labels(backend=tgt[3], reason=tgt[4]).inc()
            stream = _open_stream(tgt)
        except Exception as exc:
            last_err = exc
            if tgt[3] == "local":
                # 建流失败且尚未产出任何内容 → 安全降级云端
                LLM_ROUTES.labels(backend="cloud", reason="fallback").inc()
                logger.warning("本地模型 %s 建流失败，降级云端：%s", tgt[0], exc)
                continue
            raise
        yield from _stream_with_metrics(stream, used_model=tgt[0])
        return
    raise last_err  # pragma: no cover - 目标列表恒非空，防御式兜底


def embed(texts: list[str]) -> list[list[float]]:
    """文本向量化（RAG 用）。百炼单次最多 10 条，自动分批提交；
    批间受控并行（4 路）——千级切片首建原先要数百次串行往返，
    纯 RTT 叠加。注意：切换在线 Embedding 模型只影响新增切片，
    存量索引维度不变——换模型后必须全量重建知识库索引。"""
    _model, _base, _key = _active_cfg("embedding")
    cli = _get_or_create_client(_base, _key)
    batch_size = int(os.getenv("DOCMIND_EMBED_BATCH_SIZE", "10"))
    if len(texts) <= batch_size:
        resp = _with_retry(lambda: cli.embeddings.create(
            model=_model, input=texts))
        return [d.embedding for d in resp.data]

    batches = [texts[i:i + batch_size] for i in range(0, len(texts), batch_size)]

    def _embed_batch(chunk: list[str]) -> list[list[float]]:
        resp = _with_retry(lambda: cli.embeddings.create(model=_model, input=chunk))
        return [d.embedding for d in resp.data]

    # 保序：executor.map 按提交顺序返回结果
    with ThreadPoolExecutor(max_workers=4) as ex:
        results: list[list[list[float]]] = list(ex.map(_embed_batch, batches))
    return [vec for batch in results for vec in batch]
