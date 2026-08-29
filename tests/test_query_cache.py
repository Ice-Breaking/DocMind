"""查询级热缓存（query_cache）单测：LRU 行为、embedding/rerank 命中语义、
与 HybridRetriever._rerank 的集成、开关关闭时直通。全部离线（mock 网络）。"""
import threading

import pytest

from docmind.rag import query_cache
from docmind.rag.hybrid import HybridRetriever
from docmind.rag.vector_store import SearchHit


# ---------------- LruCache 基础行为 ----------------

def test_lru_evicts_oldest():
    c = query_cache.LruCache(maxsize=2)
    c.put("a", 1)
    c.put("b", 2)
    c.get("a")               # a 晋升，b 变最旧
    c.put("c", 3)            # 淘汰 b
    assert c.get("a") == (True, 1)
    assert c.get("b") == (False, None)
    assert c.get("c") == (True, 3)


def test_lru_thread_safety():
    c = query_cache.LruCache(maxsize=64)
    def hammer(i):
        for j in range(200):
            c.put(f"k{(i * 7 + j) % 64}", j)
            c.get(f"k{j % 64}")
    threads = [threading.Thread(target=hammer, args=(i,)) for i in range(8)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert len(c) <= 64      # 容量上限不被并发击穿


# ---------------- embedding 查询缓存 ----------------

def test_embed_query_cached_hits(monkeypatch):
    calls = []
    def fake_embed(texts):
        calls.append(list(texts))
        return [[0.1, 0.2]]
    assert query_cache.embed_query_cached(fake_embed, "什么是 RAG？") == [0.1, 0.2]
    assert query_cache.embed_query_cached(fake_embed, "什么是 RAG？") == [0.1, 0.2]
    assert len(calls) == 1   # 同题第二次命中缓存，不再调 API


def test_embed_query_cached_key_includes_model(monkeypatch):
    calls = []
    def fake_embed(texts):
        calls.append(list(texts))
        return [[0.1]]
    monkeypatch.setattr("docmind.config.EMBEDDING_MODEL", "text-embedding-v3")
    query_cache.embed_query_cached(fake_embed, "同一问题")
    monkeypatch.setattr("docmind.config.EMBEDDING_MODEL", "text-embedding-v4")
    query_cache.embed_query_cached(fake_embed, "同一问题")
    assert len(calls) == 2   # 模型切换 → 换键 → 不复用旧向量


def test_embed_query_cached_disabled(monkeypatch):
    monkeypatch.setattr(query_cache, "QUERY_EMBED_CACHE_ENABLED", False)
    calls = []
    def fake_embed(texts):
        calls.append(list(texts))
        return [[0.1]]
    query_cache.embed_query_cached(fake_embed, "q")
    query_cache.embed_query_cached(fake_embed, "q")
    assert len(calls) == 2   # 开关关闭：每次直通（同代码 A/B 压测用）


# ---------------- rerank 结果缓存 ----------------

def _hit(text, score):
    return SearchHit(text=text, source="a.md", score=score, page=1)


def test_rerank_cached_hits_and_isolation():
    calls = []
    def fake_rerank(query, candidates, top_n):
        calls.append(query)
        return [_hit(c.text, 0.9 - i * 0.1) for i, c in enumerate(candidates)]
    cands = [SearchHit(text=f"chunk{i}", source="a.md", score=0.5, page=1)
             for i in range(3)]
    first = query_cache.rerank_cached(fake_rerank, "问题", cands, 4)
    second = query_cache.rerank_cached(fake_rerank, "问题", cands, 4)
    assert len(calls) == 1
    assert [h.text for h in second] == [h.text for h in first]
    assert [h.score for h in second] == [h.score for h in first]
    # 命中返回的是拷贝：原地修改不得污染缓存条目
    second[0].score = 123.0
    third = query_cache.rerank_cached(fake_rerank, "问题", cands, 4)
    assert third[0].score != 123.0


def test_rerank_cached_candidates_change_invalidates():
    calls = []
    def fake_rerank(query, candidates, top_n):
        calls.append(len(candidates))
        return [_hit(c.text, 0.9) for c in candidates]
    cands = [SearchHit(text="chunk", source="a.md", score=0.5, page=1)]
    query_cache.rerank_cached(fake_rerank, "问题", cands, 4)
    query_cache.rerank_cached(fake_rerank, "问题", cands + [
        SearchHit(text="chunk-new", source="b.md", score=0.5, page=1)], 4)
    assert calls == [1, 2]   # 候选集变化 → 键变化 → 知识库更新天然失效


def test_rerank_cached_disabled_and_empty(monkeypatch):
    monkeypatch.setattr(query_cache, "RERANK_CACHE_ENABLED", False)
    calls = []
    def fake_rerank(query, candidates, top_n):
        calls.append(query)
        return []
    query_cache.rerank_cached(fake_rerank, "q", [_hit("t", 0.5)], 4)
    query_cache.rerank_cached(fake_rerank, "q", [_hit("t", 0.5)], 4)
    assert len(calls) == 2
    # 空候选直通（上层 search 已保证不空，此处防御）
    query_cache.rerank_cached(fake_rerank, "q", [], 4)
    assert len(calls) == 3


# ---------------- 与 HybridRetriever._rerank 集成 ----------------

def test_hybrid_rerank_cache_skips_http(monkeypatch):
    http_calls = []
    class FakeResp:
        def raise_for_status(self):
            pass
        def json(self):
            return {"output": {"results": [
                {"index": 0, "relevance_score": 0.9},
                {"index": 1, "relevance_score": 0.4}]}}

    def fake_post(url, **kwargs):
        http_calls.append(url)
        return FakeResp()

    monkeypatch.setattr("docmind.rag.hybrid._SESSION.post", fake_post)
    ret = HybridRetriever(store=None)
    cands = [SearchHit(text=f"chunk{i}", source="a.md", score=0.5, page=1)
             for i in range(2)]
    first = ret._rerank("什么是 MCP？", cands, 2)
    second = ret._rerank("什么是 MCP？", cands, 2)
    assert len(http_calls) == 1
    assert [h.score for h in second] == [h.score for h in first]


def test_hybrid_rerank_failure_not_cached(monkeypatch):
    """失败结果不进缓存：第一次抛异常，第二次仍走到 HTTP（可自愈重试）"""
    state = {"n": 0}
    class BadResp:
        def raise_for_status(self):
            raise RuntimeError("上游 500")
        def json(self):
            return {}

    def fake_post(url, **kwargs):
        state["n"] += 1
        if state["n"] == 1:
            return BadResp()
        class OkResp:
            def raise_for_status(self):
                pass
            def json(self):
                return {"output": {"results": [
                    {"index": 0, "relevance_score": 0.8}]}}
        return OkResp()

    monkeypatch.setattr("docmind.rag.hybrid._SESSION.post", fake_post)
    ret = HybridRetriever(store=None)
    cands = [SearchHit(text="chunk", source="a.md", score=0.5, page=1)]
    with pytest.raises(RuntimeError):
        ret._rerank("失败后重试", cands, 1)
    ranked = ret._rerank("失败后重试", cands, 1)
    assert ranked[0].score == pytest.approx(0.8)
