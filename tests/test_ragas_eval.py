"""ragas_eval 单测：判官输出解析、四指标计算公式、单指标故障隔离。

全部 mock 判官（llm.chat）与 embedding（llm.embed），离线运行。
"""
from docmind import ragas_eval as rg


class _Msg:
    def __init__(self, content: str):
        self.content = content


# 判官提示词的特征子串：fake chat 按它分发不同应答
_MARK_FAITHFUL = "原子断言"
_MARK_RELEVANCY = "反向生成"
_MARK_PRECISION = "是否相关"
_MARK_RECALL = "归因成立"


# ── JSON 解析容错 ─────────────────────────────────────────────────────────

def test_extract_json_with_fence_and_noise():
    text = '好的，以下是结果：\n```json\n{"relevant": true}\n```\n以上仅供参考'
    assert rg._extract_json(text) == {"relevant": True}


def test_extract_json_nested_braces_in_strings():
    text = '{"claim": "符号 } 不破坏解析", "supported": false}'
    out = rg._extract_json(text)
    assert out["supported"] is False


def test_extract_json_garbage():
    assert rg._extract_json("完全不是 JSON 的输出") is None
    assert rg._extract_json("") is None


# ── 忠实度 ────────────────────────────────────────────────────────────────

def test_faithfulness_partial_support(monkeypatch):
    def fake_chat(messages, **kw):
        return _Msg('{"claims": ['
                    '{"claim": "RAG 是检索增强生成", "supported": true},'
                    '{"claim": "RAG 诞生于 1999 年", "supported": false},'
                    '{"claim": "包含检索与生成两阶段", "supported": true}]}')
    monkeypatch.setattr(rg, "chat", fake_chat)
    r = rg.faithfulness("什么是RAG", "回答……", ["上下文"])
    assert r["score"] == round(2 / 3, 4)
    assert r["claims_total"] == 3
    assert r["claims_supported"] == 2


def test_faithfulness_unparseable_returns_none(monkeypatch):
    monkeypatch.setattr(rg, "chat", lambda *a, **k: _Msg("胡言乱语"))
    r = rg.faithfulness("q", "a", ["c"])
    assert r["score"] is None and "note" in r


# ── 答案相关性 ────────────────────────────────────────────────────────────

def test_answer_relevancy_cosine_mean(monkeypatch):
    def fake_chat(messages, **kw):
        assert _MARK_RELEVANCY in messages[0]["content"]
        return _Msg('{"questions": ["q1", "q2", "q3"]}')
    s = 0.7071067811865476
    monkeypatch.setattr(rg, "chat", fake_chat)
    monkeypatch.setattr(rg, "embed",
                        lambda texts: [[1.0, 0.0],   # 原问题
                                       [1.0, 0.0],   # cos=1
                                       [0.0, 1.0],   # cos=0
                                       [s, s]])      # cos≈0.7071
    r = rg.answer_relevancy("原问题", "答案")
    assert r["score"] == round((1.0 + 0.0 + s) / 3, 4)
    assert len(r["generated_questions"]) == 3


def test_answer_relevancy_bad_judge_output(monkeypatch):
    monkeypatch.setattr(rg, "chat", lambda *a, **k: _Msg('{"foo": 1}'))
    r = rg.answer_relevancy("q", "a")
    assert r["score"] is None


# ── 上下文精确率 ──────────────────────────────────────────────────────────

def test_context_precision_average_precision(monkeypatch):
    """rels=[相关, 无关, 相关] → AP=(P@1×1 + P@3×1)/2 = (1 + 2/3)/2 ≈ 0.8333"""
    calls = {"n": 0}

    def fake_chat(messages, **kw):
        assert _MARK_PRECISION in messages[0]["content"]
        calls["n"] += 1
        verdict = calls["n"] != 2          # 第 2 条无关
        return _Msg(f'{{"relevant": {str(verdict).lower()}}}')

    monkeypatch.setattr(rg, "chat", fake_chat)
    r = rg.context_precision("q", ["c1", "c2", "c3"])
    assert r["score"] == round((1.0 + 2 / 3) / 2, 4)
    assert r["relevances"] == [True, False, True]


def test_context_precision_none_relevant(monkeypatch):
    monkeypatch.setattr(rg, "chat", lambda *a, **k: _Msg('{"relevant": false}'))
    r = rg.context_precision("q", ["c1"])
    assert r["score"] == 0.0


# ── 上下文召回率 ──────────────────────────────────────────────────────────

def test_context_recall_sentence_attribution(monkeypatch):
    def fake_chat(messages, **kw):
        assert _MARK_RECALL in messages[0]["content"]
        return _Msg('{"attributable": true}')

    monkeypatch.setattr(rg, "chat", fake_chat)
    r = rg.context_recall("第一句话内容完整。第二句话也很清楚。",
                          ["上下文资料"])
    assert r["sentences"] == 2
    assert r["attributable"] == 2
    assert r["score"] == 1.0


def test_split_sentences_drops_tiny_fragments():
    parts = rg._split_sentences("这是完整的一句话描述。嗯。另一句完整表达。")
    assert parts == ["这是完整的一句话描述", "另一句完整表达"]


# ── 聚合与故障隔离 ────────────────────────────────────────────────────────

def test_evaluate_isolates_metric_failures(monkeypatch):
    """忠实度判官抛异常只影响自身；其余指标照常出分。"""

    def fake_chat(messages, **kw):
        p = messages[0]["content"]
        if _MARK_FAITHFUL in p:
            raise RuntimeError("judge down")
        if _MARK_PRECISION in p:
            return _Msg('{"relevant": true}')
        if _MARK_RELEVANCY in p:
            return _Msg('{"questions": ["a", "b", "c"]}')
        if _MARK_RECALL in p:
            return _Msg('{"attributable": false}')
        raise AssertionError("未知判官提示词")

    monkeypatch.setattr(rg, "chat", fake_chat)
    monkeypatch.setattr(rg, "embed", lambda texts: [[1.0]] * len(texts))
    res = rg.evaluate_ragas("q", "答案内容足够长。", ["c1"],
                            expected_answer="参考答案句子。")
    m = res["metrics"]
    assert m["faithfulness"]["score"] is None
    assert m["context_precision"]["score"] == 1.0
    assert m["context_recall"]["score"] == 0.0
    assert isinstance(m["answer_relevancy"]["score"], float)
    assert res["summary"]["scored_metrics"] == 3
    assert res["meta"]["contexts"] == 1


def test_evaluate_without_expected_answer_skips_recall(monkeypatch):
    monkeypatch.setattr(
        rg, "chat", lambda *a, **k: _Msg('{"relevant": true}'))
    res = rg.evaluate_ragas("q", "a", ["c1"])
    assert "context_recall" not in res["metrics"]


def test_evaluate_empty_contexts(monkeypatch):
    monkeypatch.setattr(rg, "chat", lambda *a, **k: (_ for _ in ()).throw(
        AssertionError("不应触发判官")))
    res = rg.evaluate_ragas("q", "a", [])
    assert res["meta"]["contexts"] == 0
    assert res["metrics"]["context_precision"]["score"] is None