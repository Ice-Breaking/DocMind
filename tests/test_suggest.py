"""动态追问单测：JSON 解析容错 / 回退 / store 缓存"""
from types import SimpleNamespace

from docmind import store, suggest


def test_parse_plain_json():
    assert suggest.parse_suggestions('["a", "b", "c"]') == ["a", "b", "c"]


def test_parse_with_code_fence():
    """LLM 套代码围栏也能解析"""
    assert suggest.parse_suggestions('```json\n["x", "y"]\n```') == ["x", "y"]


def test_parse_with_surrounding_text():
    assert suggest.parse_suggestions('好的：["q1", "q2", "q3", "q4"] 以上') == ["q1", "q2", "q3"]


def test_parse_truncates_to_three():
    assert suggest.parse_suggestions('["1","2","3","4","5"]') == ["1", "2", "3"]


def test_parse_invalid():
    assert suggest.parse_suggestions("不是 JSON") == []
    assert suggest.parse_suggestions("") == []
    assert suggest.parse_suggestions(None) == []
    assert suggest.parse_suggestions('{"a": 1}') == []      # 对象不是数组


def test_parse_strips_empty_items():
    assert suggest.parse_suggestions('["a", "", "  ", "b"]') == ["a", "b"]


def test_generate_fallback_on_llm_failure(monkeypatch):
    """LLM 失败 → 回退固定三问（UX 永不缺位）"""
    def _fail(msgs, max_tokens=None):
        raise RuntimeError("LLM 不可用")
    monkeypatch.setattr(suggest, "chat", _fail)
    items = suggest.generate_suggestions("问题", "回答")
    assert items == suggest.FALLBACK_SUGGESTIONS


def test_generate_fallback_on_bad_output(monkeypatch):
    """LLM 输出无法解析 → 回退固定三问"""
    monkeypatch.setattr(suggest, "chat",
                        lambda msgs, max_tokens=None: SimpleNamespace(content="我不会输出 JSON"))
    items = suggest.generate_suggestions("问题", "回答")
    assert items == suggest.FALLBACK_SUGGESTIONS


def test_generate_uses_llm_output(monkeypatch):
    monkeypatch.setattr(suggest, "chat",
                        lambda msgs, max_tokens=None: SimpleNamespace(content='["追问1","追问2","追问3"]'))
    items = suggest.generate_suggestions("问题", "回答")
    assert items == ["追问1", "追问2", "追问3"]


def test_store_suggestions_cache(temp_db):
    """按答案哈希缓存：命中返回，未命中 None，覆盖更新"""
    assert store.get_suggestions("h1") is None
    store.save_suggestions("h1", ["a", "b"])
    assert store.get_suggestions("h1") == ["a", "b"]
    store.save_suggestions("h1", ["c"])          # 覆盖
    assert store.get_suggestions("h1") == ["c"]
