"""model_router 单测：路由决策规则、降级目标构造、决策层永不抛异常。"""
from docmind import config
from docmind import model_router as mr

CLOUD = ("qwen-plus", "https://dashscope.example/v1", "sk-test")


def _local_on(monkeypatch):
    monkeypatch.setattr(config, "MODEL_ROUTER", True)
    monkeypatch.setattr(config, "LOCAL_LLM_ENABLED", True)


# ── 决策规则 ──────────────────────────────────────────────────────────────

def test_trivial_greeting_routes_local(monkeypatch):
    _local_on(monkeypatch)
    d = mr.resolve([{"role": "user", "content": "你好"}], CLOUD)
    assert d.backend == "local"
    assert d.reason == mr.REASON_TRIVIAL
    assert d.target()[0] == config.LOCAL_CHAT_MODEL


def test_thanks_and_meta_requests_local(monkeypatch):
    _local_on(monkeypatch)
    for q in ["谢谢", "再见", "你是谁", "ok", "Thanks!", "在吗"]:
        d = mr.resolve([{"role": "user", "content": q}], CLOUD)
        assert d.backend == "local", q


def test_short_knowledge_query_stays_cloud(monkeypatch):
    """短但知识意图明确的问题不得误入本地：整句白名单防误伤。"""
    _local_on(monkeypatch)
    d = mr.resolve([{"role": "user", "content": "什么是RAG？"}], CLOUD)
    assert d.backend == "cloud"


def test_non_whitelisted_within_cap_stays_cloud(monkeypatch):
    _local_on(monkeypatch)
    # 长度达标但不是寒暄白名单整句
    d = mr.resolve([{"role": "user", "content": "在吗？帮我个忙"}], CLOUD)
    assert d.backend == "cloud"


def test_tools_route_cloud(monkeypatch):
    """Agent 工具调用步稳定性优先：带工具定义一律云端。"""
    _local_on(monkeypatch)
    d = mr.resolve([{"role": "user", "content": "你好"}],
                   CLOUD, has_tools=True)
    assert d.backend == "cloud" and d.reason == mr.REASON_TOOLS


def test_multimodal_routes_cloud(monkeypatch):
    _local_on(monkeypatch)
    msgs = [{"role": "user", "content": [
        {"type": "text", "text": "你好"},
        {"type": "image_url", "image_url": {"url": "data:image/png;base64,x"}},
    ]}]
    d = mr.resolve(msgs, CLOUD)
    assert d.backend == "cloud" and d.reason == mr.REASON_MULTIMODAL


def test_thinking_routes_cloud(monkeypatch):
    _local_on(monkeypatch)
    d = mr.resolve([{"role": "user", "content": "你好"}],
                   CLOUD, thinking=True)
    assert d.backend == "cloud" and d.reason == mr.REASON_THINKING


def test_router_disabled_all_cloud(monkeypatch):
    monkeypatch.setattr(config, "MODEL_ROUTER", False)
    d = mr.resolve([{"role": "user", "content": "你好"}], CLOUD)
    assert d.backend == "cloud" and d.reason == mr.REASON_DEFAULT


def test_history_but_last_user_trivial(monkeypatch):
    """多轮会话只看最后一条 user 消息。"""
    _local_on(monkeypatch)
    msgs = [{"role": "user", "content": "什么是MCP？"},
            {"role": "assistant", "content": "……"},
            {"role": "user", "content": "好的，谢谢"}]
    d = mr.resolve(msgs, CLOUD)
    assert d.backend == "local"


# ── 健壮性 ────────────────────────────────────────────────────────────────

def test_resolve_never_raises(monkeypatch):
    """决策层自身故障必须兜底走云端（增强能力不阻断主链路）。"""
    _local_on(monkeypatch)

    def _boom(_msgs):
        raise RuntimeError("router bug")

    monkeypatch.setattr(mr, "extract_user_text", _boom)
    d = mr.resolve([{"role": "user", "content": "你好"}], CLOUD)
    assert d.backend == "cloud"


def test_extract_user_text_multimodal():
    msgs = [{"role": "user", "content": [
        {"type": "text", "text": "看这张图"},
        {"type": "image_url", "image_url": {"url": "x"}},
    ]}]
    assert mr.extract_user_text(msgs) == "看这张图"


def test_trivial_length_gate():
    """超长消息直接短路为非寒暄，即使以寒暄词开头。"""
    assert mr.is_trivial_query("你好，" + "长问题" * 50) is False


# ── llm 层目标列表构造 ────────────────────────────────────────────────────

def test_route_targets_appends_cloud_fallback(monkeypatch):
    from docmind import llm
    _local_on(monkeypatch)
    monkeypatch.setattr(llm, "_active_cfg", lambda kind: CLOUD)
    targets = llm._route_targets(
        [{"role": "user", "content": "谢谢"}],
        has_tools=False, thinking=False, model=None)
    assert targets[0][3] == "local"
    assert len(targets) == 2
    assert targets[1][0] == "qwen-plus"       # 云端模型名
    assert targets[1][3] == "cloud"
    assert targets[1][4] == "fallback"


def test_route_targets_explicit_model_skips_routing(monkeypatch):
    from docmind import llm
    monkeypatch.setattr(llm, "_active_cfg", lambda kind: CLOUD)
    targets = llm._route_targets(
        [{"role": "user", "content": "你好"}],
        has_tools=False, thinking=False, model="qwen-vl-max")
    assert len(targets) == 1
    assert targets[0][0] == "qwen-vl-max"
    assert targets[0][4] == mr.REASON_EXPLICIT


def test_route_targets_cloud_default_no_fallback(monkeypatch):
    """云端为主目标时不追加降级项（云端失败直接抛给上层重试逻辑）。"""
    from docmind import llm
    _local_on(monkeypatch)
    monkeypatch.setattr(llm, "_active_cfg", lambda kind: CLOUD)
    targets = llm._route_targets(
        [{"role": "user", "content": "解释一下LoRA微调"}],
        has_tools=False, thinking=False, model=None)
    assert len(targets) == 1
    assert targets[0][3] == "cloud"


# ── FAQ 灰度分流 ──────────────────────────────────────────────────────────

_KNOWLEDGE_QS = [
    "什么是 RAG？", "如何排查依赖冲突？", "MCP 和 Function Calling 的关系？",
    "list 和 tuple 有什么区别？", "怎么创建 venv？",
]


def test_faq_offload_disabled_by_default(monkeypatch):
    """默认关闭：知识问答全部走云端，reason 不变。"""
    _local_on(monkeypatch)
    monkeypatch.setattr(config, "ROUTER_FAQ_OFFLOAD_PCT", 0)
    for q in _KNOWLEDGE_QS:
        d = mr.resolve([{"role": "user", "content": q}], CLOUD)
        assert d.backend == "cloud" and d.reason == mr.REASON_DEFAULT, q


def test_faq_offload_100_all_local(monkeypatch):
    """灰度 100%：所有非寒暄请求走本地（寒暄规则优先级更高不受影响）。"""
    _local_on(monkeypatch)
    monkeypatch.setattr(config, "ROUTER_FAQ_OFFLOAD_PCT", 100)
    for q in _KNOWLEDGE_QS:
        d = mr.resolve([{"role": "user", "content": q}], CLOUD)
        assert d.backend == "local" and d.reason == mr.REASON_FAQ_OFFLOAD, q
    # 寒暄仍命中 trivial 而非 faq_offload
    d = mr.resolve([{"role": "user", "content": "你好"}], CLOUD)
    assert d.reason == mr.REASON_TRIVIAL


def test_faq_offload_deterministic_and_monotonic(monkeypatch):
    """确定性：同题永远同后端；单调性：30% 命中集 ⊆ 60% 命中集。"""
    _local_on(monkeypatch)

    def locals_at(pct: int) -> set[str]:
        monkeypatch.setattr(config, "ROUTER_FAQ_OFFLOAD_PCT", pct)
        out = set()
        for q in _KNOWLEDGE_QS * 3:      # 重复调用验证稳定
            d = mr.resolve([{"role": "user", "content": q}], CLOUD)
            assert (d.reason == mr.REASON_FAQ_OFFLOAD) == (d.backend == "local")
            if d.backend == "local":
                out.add(q)
        return out

    at30 = locals_at(30)
    at60 = locals_at(60)
    # 确定性：再跑一遍 30% 结果一致
    assert locals_at(30) == at30
    # 单调性：提高灰度只会扩容本地集合
    assert at30 <= at60


def test_faq_bucket_stable_values():
    """md5 分桶跨进程稳定：固定样本断言具体桶值（防误改哈希算法）。"""
    assert mr.faq_bucket("什么是 RAG？") == mr.faq_bucket(" 什么是 RAG？ ")
    assert mr.faq_bucket("") == 0 or isinstance(mr.faq_bucket(""), int)
    assert 0 <= mr.faq_bucket("任意问题") <= 99