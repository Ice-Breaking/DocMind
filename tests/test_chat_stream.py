"""SSE 事件生成器单测：事件协议 / 多轮重建 / 缓存联动 / 异常兜底"""
from types import SimpleNamespace

from docmind import chat_stream, config, semantic_cache, store


class FakeAgent:
    """最小 Agent 替身：可编程步骤序列"""

    def __init__(self, steps=(), error=None):
        self.steps = steps
        self.error = error
        self.history = []
        self.last_tools = set()
        self.asked = []

    def reset(self):
        self.history = []

    def ask(self, q):
        self.asked.append(q)
        if self.error:
            raise self.error
        yield from self.steps


def kinds(events):
    return [e["kind"] for e in events]


def test_event_protocol(monkeypatch):
    """步骤/token/final 事件逐条透传"""
    monkeypatch.setattr(config, "SEMANTIC_CACHE", False)
    steps = [
        SimpleNamespace(kind="thinking", text="嗯，需要检索"),
        SimpleNamespace(kind="tool_call", text="调用 knowledge_search"),
        SimpleNamespace(kind="tool_result", text="返回 2 条"),
        SimpleNamespace(kind="token", text="你好"),
        SimpleNamespace(kind="token", text="世界"),
        SimpleNamespace(kind="final", text="你好世界"),
    ]
    events = list(chat_stream.stream_events(FakeAgent(steps), "测试"))
    assert kinds(events) == ["thinking", "step", "step", "token", "token", "final"]
    assert events[1]["step_kind"] == "tool_call"
    assert events[-1]["answer"] == "你好世界"


def test_history_rebuild_from_db(temp_db):
    """多轮上下文：每请求从 DB raw 对确定性重建（system + 历史对）"""
    monkey_cfg = config
    orig = monkey_cfg.SEMANTIC_CACHE
    monkey_cfg.SEMANTIC_CACHE = False
    try:
        store.append_message("s1", "user", "第一轮问题", raw="第一轮问题", user="u")
        store.append_message("s1", "assistant", "渲染版", raw="第一轮干净回答", user="u")
        agent = FakeAgent([SimpleNamespace(kind="final", text="第二轮回答")])
        events = list(chat_stream.stream_events(agent, "第二轮问题", session_id="s1"))
        assert events[-1]["answer"] == "第二轮回答"
        # history 重建：system + 2 条历史（raw 纯净版）+ —— 新问题由 ask 内部追加
        assert agent.history[0]["role"] == "system"
        assert agent.history[1] == {"role": "user", "content": "第一轮问题"}
        assert agent.history[2] == {"role": "assistant", "content": "第一轮干净回答"}
    finally:
        monkey_cfg.SEMANTIC_CACHE = orig


def test_no_history_without_session(temp_db):
    monkeypatch_cfg = config.SEMANTIC_CACHE
    config.SEMANTIC_CACHE = False
    try:
        agent = FakeAgent([SimpleNamespace(kind="final", text="答")])
        list(chat_stream.stream_events(agent, "新问题"))
        assert agent.history == []   # 无 session_id 不重建
    finally:
        config.SEMANTIC_CACHE = monkeypatch_cfg


def test_cache_hit_skips_agent(temp_db, monkeypatch):
    """缓存命中：cache + final 事件，Agent 不被调用"""
    monkeypatch.setattr(config, "SEMANTIC_CACHE", True)
    monkeypatch.setattr(chat_stream, "embed", lambda texts: [[1.0, 0.0, 0.0]])
    semantic_cache.save("什么是 MCP？", "缓存的答案", [1.0, 0.0, 0.0])
    agent = FakeAgent([SimpleNamespace(kind="final", text="不该出现")])
    events = list(chat_stream.stream_events(agent, "什么是 MCP？"))
    assert kinds(events) == ["cache", "final"]
    assert events[0]["answer"] == "缓存的答案"
    assert agent.asked == []          # Agent 链路被跳过


def test_cache_acl_block(temp_db, temp_kb, monkeypatch):
    """缓存答案引用当前用户无权的受限文档 → 视为未命中走主链路"""
    monkeypatch.setattr(config, "SEMANTIC_CACHE", True)
    monkeypatch.setattr(chat_stream, "embed", lambda texts: [[1.0, 0.0, 0.0]])
    (temp_kb / "sec.md").write_text("x", encoding="utf-8")
    from docmind import acl
    acl.set_restricted("sec.md", True)
    semantic_cache.save("机密问题", "答案 [来源: sec.md]", [1.0, 0.0, 0.0])
    agent = FakeAgent([SimpleNamespace(kind="final", text="主链路回答")])
    events = list(chat_stream.stream_events(agent, "机密问题"))
    assert "cache" not in kinds(events)
    assert events[-1]["answer"] == "主链路回答"
    assert agent.asked == ["机密问题"]


def test_error_fallback(temp_db, monkeypatch):
    """Agent 抛异常 → error + ⚠️ final，不挂空流；错误答案不入缓存"""
    monkeypatch.setattr(config, "SEMANTIC_CACHE", True)
    monkeypatch.setattr(chat_stream, "embed", lambda texts: [[0.0, 1.0, 0.0]])
    agent = FakeAgent(error=RuntimeError("LLM 挂了"))
    events = list(chat_stream.stream_events(agent, "任意问题"))
    assert "error" in kinds(events)
    final = events[-1]
    assert final["kind"] == "final" and final["answer"].startswith("⚠️")
    assert semantic_cache.stats()["entries"] == 0


def test_cache_write_after_answer(temp_db, monkeypatch):
    """正常回答完成后写入语义缓存"""
    monkeypatch.setattr(config, "SEMANTIC_CACHE", True)
    monkeypatch.setattr(chat_stream, "embed", lambda texts: [[0.5, 0.5, 0.0]])
    agent = FakeAgent([SimpleNamespace(kind="final", text="正常答案")])
    list(chat_stream.stream_events(agent, "会被缓存的问题"))
    assert semantic_cache.stats()["entries"] == 1
