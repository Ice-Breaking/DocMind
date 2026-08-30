"""大小模型智能路由：简单请求走本地小模型，复杂任务走云端主模型。

设计动机：
    QPS 瓶颈在上游 LLM API（每请求 1 次 embedding + 1 次 rerank + 1 次对话），
    而线上真实流量里有相当比例是寒暄/确认/超短追问——这类请求用 7B 本地
    模型即可胜任，既省云 API 成本又降低首字延迟。路由层在不改变任何调用方
    签名的前提下，于 llm.chat / chat_stream 内部完成分流。

路由规则（按优先级，先命中先生效）：
    1. 调用方显式指定 model=      → 不路由（多模态等场景调用方最清楚）
    2. 多模态消息（含 image_url） → 云端（视觉模型在百炼）
    3. 携带工具定义（Agent 推理步）→ 云端主模型（工具调用稳定性优先）
    4. 思维链开启                 → 云端（enable_thinking 仅百炼支持）
    5. 寒暄/超短无知识意图请求     → 本地小模型
    6. FAQ 灰度分流（可选，默认关）→ 按 md5(question)%100 < PCT 确定性
       分流到本地；同题永远同后端，比例可平滑放大（成本测算见
       scripts/cost_report.py --faq-offload-pct）
    7. 其余                       → 云端主模型

降级链：
    本地调用失败/超时 → 自动回退云端重试一次。本地是「增强」而非依赖，
    与项目「增强类故障永不阻断主链路」原则一致（同 rerank→RRF、
    Langfuse→JSONL 的降级哲学）。

可观测：
    每次决策记 docmind_llm_route_total{backend, reason}，配合既有
    LLM_CALLS/LATENCY/TOKENS 即可在 Grafana 上算出本地命中率与成本节省。
"""
import hashlib
import logging
import re

from docmind import config

logger = logging.getLogger(__name__)

# 寒暄/元请求白名单：分词后逐词匹配（按标点/空白切分），绝不误伤知识型提问。
# 组合寒暄（如「好的，谢谢」）天然覆盖；先做长度闸门再切词，长问题零成本短路。
_TRIVIAL_WORDS = {
    "你好", "您好", "hi", "hello", "hey", "嗨",
    "谢谢", "感谢", "多谢", "thx", "thanks",
    "再见", "拜拜", "晚安", "早安",
    "好的", "好嘞", "收到", "ok", "okay",
    "在吗", "在么", "测试", "test",
    "你是谁", "你叫什么", "你叫什么名字", "你会什么",
}

_SPLIT_RE = re.compile(r"[!！。，,~～?？\s.]+")

# 决策原因常量（进 Prometheus label，取值集合必须有限）
REASON_EXPLICIT = "explicit"      # 调用方显式指定模型
REASON_MULTIMODAL = "multimodal"  # 含图片消息
REASON_TOOLS = "tools"            # Agent 工具调用步
REASON_THINKING = "thinking"      # 思维链
REASON_TRIVIAL = "trivial"        # 寒暄/超短
REASON_FAQ_OFFLOAD = "faq_offload"  # FAQ 灰度分流（知识问答→本地）
REASON_DEFAULT = "default"        # 默认走云端


def extract_user_text(messages: list[dict] | None) -> str:
    """取最后一条 user 消息的纯文本（多模态 content 只拼文本段）。"""
    if not messages:
        return ""
    for m in reversed(messages):
        if m.get("role") != "user":
            continue
        c = m.get("content")
        if isinstance(c, str):
            return c.strip()
        if isinstance(c, list):
            return " ".join(
                x.get("text", "") for x in c
                if isinstance(x, dict) and x.get("type") == "text").strip()
    return ""


def has_image(messages: list[dict] | None) -> bool:
    """消息中是否含多模态图片段。"""
    for m in messages or []:
        c = m.get("content")
        if isinstance(c, list) and any(
                isinstance(x, dict) and x.get("type") == "image_url" for x in c):
            return True
    return False


def is_trivial_query(text: str) -> bool:
    """寒暄/超短判定：先过字符数闸门（长问题零成本短路），
    再按标点切词、要求全部命中白名单。

    「什么是 RAG？」「帮我查一下端口占用」这类短但知识意图明确的提问
    含白名单外实词 → 正确落回云端主模型。"""
    t = (text or "").strip()
    if not t or len(t) > config.ROUTER_TRIVIAL_MAX_CHARS:
        return False
    tokens = [tok for tok in _SPLIT_RE.split(t.lower()) if tok]
    return bool(tokens) and all(tok in _TRIVIAL_WORDS for tok in tokens)


def faq_bucket(text: str) -> int:
    """FAQ 灰度分桶：md5(question)%100，跨进程/重启确定性一致。

    不能用内建 hash()——Python 对 str 哈希加盐（PYTHONHASHSEED），
    每次进程启动结果不同，会破坏「同题同后端」的灰度语义。"""
    digest = hashlib.md5((text or "").strip().encode("utf-8")).hexdigest()
    return int(digest, 16) % 100


def local_cfg() -> tuple[str, str, str]:
    """本地后端连接三元组 (model, base_url, api_key)。"""
    return (config.LOCAL_CHAT_MODEL, config.LOCAL_LLM_BASE_URL,
            config.LOCAL_LLM_API_KEY)


class RouteDecision:
    """一次路由决策结果：目标后端 + 命中原因（供指标与 trace 使用）。"""

    __slots__ = ("model", "base_url", "api_key", "backend", "reason")

    def __init__(self, model: str, base_url: str, api_key: str,
                 backend: str, reason: str):
        self.model = model
        self.base_url = base_url
        self.api_key = api_key
        self.backend = backend    # "local" | "cloud"
        self.reason = reason

    def target(self) -> tuple[str, str, str]:
        """(model, base_url, api_key)，llm 层据此取客户端。"""
        return (self.model, self.base_url, self.api_key)


def resolve(messages: list[dict] | None, cloud_cfg: tuple[str, str, str],
            has_tools: bool = False, thinking: bool = False) -> RouteDecision:
    """路由决策入口。cloud_cfg 为云端在线配置 (model, base_url, api_key)
    （由 llm._active_cfg 提供，注入以避免循环 import）。

    返回 RouteDecision；任何情况下都不会抛异常——路由器自身故障按
    「走云端默认路」处理（决策层永不阻断主链路）。"""
    cloud_model, cloud_base, cloud_key = cloud_cfg
    try:
        # 规则 0：总开关关闭或本地能力未启用 → 全部云端
        if not (config.MODEL_ROUTER and config.LOCAL_LLM_ENABLED):
            return RouteDecision(cloud_model, cloud_base, cloud_key,
                                 "cloud", REASON_DEFAULT)

        # 规则 1-3：显式模型由调用方负责；多模态/工具/思维链一律云端
        if has_image(messages):
            return RouteDecision(cloud_model, cloud_base, cloud_key,
                                 "cloud", REASON_MULTIMODAL)
        if has_tools:
            return RouteDecision(cloud_model, cloud_base, cloud_key,
                                 "cloud", REASON_TOOLS)
        if thinking:
            return RouteDecision(cloud_model, cloud_base, cloud_key,
                                 "cloud", REASON_THINKING)

        # 规则 4：寒暄/超短无知识意图 → 本地小模型
        if is_trivial_query(extract_user_text(messages)):
            lm, lb, lk = local_cfg()
            return RouteDecision(lm, lb, lk, "local", REASON_TRIVIAL)

        # 规则 5：FAQ 灰度分流（可选）：按 md5 分桶把 x% 知识问答导流本地。
        # 同题永远同后端；本地失败仍有云端降级链兜底，风险可控。
        pct = max(0, min(100, int(config.ROUTER_FAQ_OFFLOAD_PCT)))
        if pct > 0:
            text = extract_user_text(messages)
            if text and faq_bucket(text) < pct:
                lm, lb, lk = local_cfg()
                return RouteDecision(lm, lb, lk, "local", REASON_FAQ_OFFLOAD)

        # 规则 6：默认云端主模型
        return RouteDecision(cloud_model, cloud_base, cloud_key,
                             "cloud", REASON_DEFAULT)
    except Exception:  # noqa: BLE001 - 决策层故障兜底走云端
        logger.warning("model_router.resolve 异常，回退云端默认路",
                       exc_info=True)
        return RouteDecision(cloud_model, cloud_base, cloud_key,
                             "cloud", REASON_DEFAULT)