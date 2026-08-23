"""分词缓存单测：切片级去重 / 未命中增量直算 / 缓存故障降级"""
import threading

import pytest


@pytest.fixture
def tok_cache(tmp_path, monkeypatch):
    from docmind.rag import tokenize_cache
    monkeypatch.setattr(tokenize_cache, "DB_PATH", str(tmp_path / "tok.db"))
    monkeypatch.setattr(tokenize_cache, "_local", threading.local())
    return tokenize_cache


def test_dedup_and_incremental_miss(tok_cache):
    """命中缓存跳过分词；只有未命中的新文本触发直算"""
    calls = {"n": 0}

    def fake_tok(text):
        calls["n"] += 1
        return [text.lower()]

    texts = ["端口是 7860", "启动方式 python"]
    r1 = tok_cache.tokenize_cached(texts, tokenizer=fake_tok)
    assert calls["n"] == 2
    assert r1 == [["端口是 7860"], ["启动方式 python"]]

    # 第二次：全部命中缓存，零分词调用
    r2 = tok_cache.tokenize_cached(texts, tokenizer=fake_tok)
    assert calls["n"] == 2 and r2 == r1

    # 第三次：旧文本命中 + 新文本直算（只传未命中的给 tokenizer）
    r3 = tok_cache.tokenize_cached(texts + ["新文本"], tokenizer=fake_tok)
    assert calls["n"] == 3
    assert r3[:2] == r1 and r3[2] == ["新文本"]


def test_empty_input(tok_cache):
    assert tok_cache.tokenize_cached([], tokenizer=lambda t: []) == []


def test_same_text_single_entry(tok_cache):
    """相同文本去重：只算一次、结果一致"""
    calls = {"n": 0}

    def fake_tok(text):
        calls["n"] += 1
        return list(text)

    out = tok_cache.tokenize_cached(["重复文本"] * 4, tokenizer=fake_tok)
    # 同批内同文本也只算一次（hash 去重后仅一个 miss）
    assert calls["n"] == 1
    assert out == [list("重复文本")] * 4


def test_db_failure_fallback(tmp_path, monkeypatch):
    """缓存层故障（连接失败）自动降级为全量直算，不抛异常不阻塞"""
    import sqlite3

    from docmind.rag import tokenize_cache
    monkeypatch.setattr(tokenize_cache, "DB_PATH", str(tmp_path / "ok.db"))
    monkeypatch.setattr(tokenize_cache, "_local", threading.local())

    def boom():
        raise sqlite3.OperationalError("disk I/O error")

    monkeypatch.setattr(tokenize_cache, "_conn", boom)
    out = tokenize_cache.tokenize_cached(["你好世界"], tokenizer=lambda t: ["你", "好"])
    assert out == [["你", "好"]]


def test_hybrid_build_uses_cache(monkeypatch):
    """集成冒烟：HybridRetriever.build 走缓存路径后检索行为不变"""
    import hashlib

    import numpy as np

    from docmind.rag.hybrid import HybridRetriever
    from docmind.rag import vector_store as _vs
    from docmind.rag.vector_store import VectorStore

    def fake_embed(texts):
        vecs = []
        for t in texts:
            h = hashlib.sha256(t.encode("utf-8")).digest()
            vecs.append([b / 255.0 for b in h[:64]])
        return vecs

    # 查询向量也走伪嵌入，保证维度与内存矩阵一致（同 test_hybrid 约定）
    monkeypatch.setattr(_vs, "embed", fake_embed)

    chunks = [{"source": "a.md", "text": "DocMind 默认端口是 7860"}]
    store = VectorStore(chunks=chunks)
    store._matrix = np.asarray(fake_embed([c["text"] for c in chunks]),
                               dtype=np.float32)
    hr = HybridRetriever(store)
    hr.build()
    hits = hr.search("默认端口", top_k=2, rerank=False)
    assert any("7860" in h.text for h in hits)