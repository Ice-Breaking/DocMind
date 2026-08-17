"""增量索引单测（mock embedding，离线）：文件增/改/删 → 切片差量；全局参数变化全量回退"""
import hashlib

import pytest

from docmind import config
from docmind.rag import cache
from docmind.rag.hybrid import HybridRetriever
from docmind.rag.vector_store import VectorStore

EMBED_CALLS: list[list[str]] = []


def fake_embed(texts):
    """确定性伪向量 + 调用记录：同文本 → 同向量，用于断言 embedding 调用范围"""
    EMBED_CALLS.append(list(texts))
    vecs = []
    for t in texts:
        h = hashlib.sha256(t.encode("utf-8")).digest()
        vecs.append([b / 255.0 for b in h[:8]])
    return vecs


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr("docmind.rag.vector_store.embed", fake_embed)
    monkeypatch.setattr(cache, "CACHE_DIR", str(tmp_path / "index"))
    EMBED_CALLS.clear()
    kb = tmp_path / "kb"
    kb.mkdir()
    (kb / "a.md").write_text("# 文档A\n\n这是文档 A 的内容，讲述部署流程。", encoding="utf-8")
    (kb / "b.md").write_text("# 文档B\n\n这是文档 B 的内容，讲述定价策略。", encoding="utf-8")
    return kb


def test_no_change_skips_embedding(env):
    """文件无变化 → 增量直接返回，零 embedding 调用"""
    store = VectorStore()
    store.build(str(env))
    calls_after_build = len(EMBED_CALLS)
    stats = store.rebuild_incremental(str(env))
    assert stats == {"added": 0, "removed": 0, "modified": 0,
                     "unchanged": 2, "chunks": len(store.chunks)}
    assert len(EMBED_CALLS) == calls_after_build   # 未变文件不重复向量化


def test_modified_file_reembedded_only(env):
    """改一个文件 → 只重切该文件，未变文件的切片原样保留"""
    store = VectorStore()
    store.build(str(env))
    old_a = {c["text"] for c in store.chunks if c["source"] == "a.md"}
    (env / "b.md").write_text("# 文档B\n\n文档 B 的全新内容，改为讲述风险管理，内容明显变长了。",
                              encoding="utf-8")
    stats = store.rebuild_incremental(str(env))
    assert (stats["added"], stats["removed"], stats["modified"],
            stats["unchanged"]) == (0, 0, 1, 1)
    assert {c["text"] for c in store.chunks if c["source"] == "a.md"} == old_a
    assert any("风险管理" in c["text"] for c in store.chunks if c["source"] == "b.md")
    assert not any("定价策略" in c["text"] for c in store.chunks)
    assert store._matrix is not None and len(store._matrix) == len(store.chunks)


def test_add_and_remove(env):
    """新增 + 删除文件 → 统计准确，切片来源同步"""
    store = VectorStore()
    store.build(str(env))
    (env / "c.md").write_text("# 文档C\n\n新增的文档 C。", encoding="utf-8")
    (env / "a.md").unlink()
    stats = store.rebuild_incremental(str(env))
    assert (stats["added"], stats["removed"], stats["unchanged"]) == (1, 1, 1)
    assert {c["source"] for c in store.chunks} == {"b.md", "c.md"}


def test_global_param_change_forces_full_rebuild(env, monkeypatch):
    """切片参数变化 → 增量复用失效，回退全量重建"""
    store = VectorStore()
    store.build(str(env))
    monkeypatch.setattr(config, "CHUNK_SIZE", 100)
    stats = store.rebuild_incremental(str(env))
    assert stats.get("full_rebuild") is True
    assert stats["chunks"] == len(store.chunks) > 0


def test_retriever_follows_incremental_rebuild(env):
    """HybridRetriever 经 store.version 感知切片变化，懒重建 BM25 后检索到新内容"""
    store = VectorStore()
    store.build(str(env))
    hr = HybridRetriever(store)
    hr.build()
    # 重建前：新内容不在库中必然检不到（rerank=False 时其余候选是无关切片）
    assert not any("季度利润目标" in h.text
                   for h in hr.search("季度利润目标", top_k=2, rerank=False))
    (env / "c.md").write_text("# 文档C\n\n公司季度利润目标是五个亿。", encoding="utf-8")
    store.rebuild_incremental(str(env))
    hits = hr.search("季度利润目标", top_k=2, rerank=False)
    assert hits and "季度利润目标" in hits[0].text
