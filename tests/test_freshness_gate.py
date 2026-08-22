"""时效闸门与缓存治理单测：铁律「搜索必须最新」的代码级保证"""
from types import SimpleNamespace

from docmind import chat_stream, semantic_cache, web_search_cache
from docmind.chat_stream import _is_freshness_critical


class FakeAgent:
    def __init__(self, answer="回答"):
        self.answer = answer
        self.history = []
        self.last_tools = set()
        self.asked = []

    def ask(self, q, **kw):
        self.asked.append(q)
        yield SimpleNamespace(kind="final", text=self.answer)


# ---------------- 时效闸门判定 ----------------
def test_gate_catches_freshness_words():
    """时效词命中 → 闸门拦截"""
    for q in ("最新的大模型有哪些", "今天北京天气", "现在几点了",
              "最近有什么新闻", "今年 GDP 多少"):
        assert _is_freshness_critical(q), q


def test_gate_passes_ordinary_questions():
    """普通知识问题不拦截（保持缓存加速）"""
    for q in ("什么是 RAG", "Python 的 GIL 是什么", "如何部署系统",
              "产品的架构是怎样的"):
        assert not _is_freshness_critical(q), q


# ---------------- 闸门与读缓存联动 ----------------
def test_freshness_question_bypasses_cache_read(temp_db, monkeypatch):
    """时效问题即使缓存里有相似条目也不读、不写——强制走主链路"""
    monkeypatch.setattr(chat_stream, "embed", lambda texts: [[1.0, 0.0, 0.0]])
    semantic_cache.save("最新的 AI 新闻", "旧缓存答案", [1.0, 0.0, 0.0])
    agent = FakeAgent("新鲜回答")
    events = list(chat_stream.stream_events(agent, "最新的 AI 新闻"))
    # 未命中 cache/reasoning 事件，走主链路
    kinds = [e["kind"] for e in events]
    assert "cache" not in kinds and "reasoning" not in kinds
    assert agent.asked == ["最新的 AI 新闻"]
    # 时效回答也不写入语义缓存
    assert semantic_cache.stats()["entries"] == 1  # 仅 save 手动写入的那条


def test_ordinary_question_still_cached(temp_db, monkeypatch):
    """普通问题缓存链路不受闸门影响"""
    monkeypatch.setattr(chat_stream, "embed", lambda texts: [[1.0, 0.0, 0.0]])
    agent = FakeAgent("知识回答")
    events = list(chat_stream.stream_events(agent, "什么是 RAG"))
    assert events[-1]["answer"] == "知识回答"
    assert semantic_cache.stats()["entries"] == 1
    # 第二次同问题 → 语义缓存命中秒回
    agent2 = FakeAgent("不应被调用")
    events2 = list(chat_stream.stream_events(agent2, "什么是 RAG"))
    kinds2 = [e["kind"] for e in events2]
    assert "cache" in kinds2
    assert agent2.asked == []


# ---------------- 缓存清理 ----------------
def test_semantic_cache_clear(temp_db):
    semantic_cache.save("q1", "a1", [1.0, 0.0])
    semantic_cache.save("q2", "a2", [0.0, 1.0])
    assert semantic_cache.stats()["entries"] == 2
    assert semantic_cache.clear() == 2
    assert semantic_cache.stats()["entries"] == 0


def test_reasoning_cache_clear(temp_db):
    from docmind import agent_reasoning_cache
    agent_reasoning_cache.save("q1", [], "sp", "答案", [])
    assert agent_reasoning_cache.stats()["entries"] == 1
    assert agent_reasoning_cache.clear() == 1
    assert agent_reasoning_cache.stats()["entries"] == 0


# ---------------- web_search TTL 分级 ----------------
def test_web_search_ttl_grading(temp_db, monkeypatch):
    monkeypatch.setattr(web_search_cache, "DB_PATH", str(temp_db / "ws.db"))
    monkeypatch.setattr(web_search_cache, "_local", __import__("threading").local())
    web_search_cache.put("最新的 AI 新闻", [{"title": "t"}])
    web_search_cache.put("什么是量子计算", [{"title": "t"}])
    c = web_search_cache._conn()
    ttls = dict(c.execute("SELECT query, ttl FROM web_search_cache"))
    assert ttls["最新的 AI 新闻"] == 600       # 时效类短 TTL
    assert ttls["什么是量子计算"] > 600        # 普通类默认 TTL


# ---------------- 术语解读预过滤 ----------------
def test_skip_interpret_conservative():
    from docmind.agent.react_agent import _skip_interpret
    assert _skip_interpret("hi")               # 纯 ASCII 短输入
    assert _skip_interpret("2+2")              # 符号/数字
    assert not _skip_interpret("拐老板是什么意思")   # 本地术语表命中
    assert not _skip_interpret("Tell me about the deployment guide")  # 长英文可能含术语
    assert not _skip_interpret("这是什么《黑话》")   # 含书名号
    assert not _skip_interpret("多轮对话上下文如何重建")  # 中文长句
