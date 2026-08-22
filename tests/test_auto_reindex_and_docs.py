"""自动重建/内容校验/版本备份/配额/URL 提取/滑动窗口/embed 缓存单测"""
import os
import threading

import pytest
from fastapi import HTTPException

from docmind import config
from docmind.docs_api import _validate_content, _extract_html_article


# ---------------- magic bytes / 文本编码校验 ----------------
def test_validate_pdf_magic():
    _validate_content("x.pdf", b"%PDF-1.7 ...")   # 通过
    with pytest.raises(HTTPException):
        _validate_content("fake.pdf", b"<html>not pdf</html>")


def test_validate_docx_zip_magic():
    _validate_content("x.docx", b"PK\x03\x04 rest")
    with pytest.raises(HTTPException):
        _validate_content("x.docx", b"plain text renamed")


def test_validate_text_utf8():
    _validate_content("x.csv", "列1,列2\na,b".encode("utf-8"))
    with pytest.raises(HTTPException):
        _validate_content("x.json", b"\xff\xfe\x00b")  # 非 UTF-8（GBK BOM 类）


# ---------------- URL 正文提取 ----------------
def test_extract_html_article():
    html = """<html><head><title>部署指南</title><style>x{}</style></head>
    <body><nav>首页 产品 关于</nav><h1>系统部署</h1>
    <p>第一步准备服务器环境，安装 Docker 与依赖镜像。</p>
    <script>alert(1)</script><p>第二步配置环境变量并启动服务。</p></body></html>"""
    title, text = _extract_html_article(html)
    assert title == "部署指南"
    assert "准备服务器环境" in text and "启动服务" in text
    assert "alert" not in text and "首页" not in text   # script/nav 已剔除


# ---------------- 自动重建防抖 ----------------
def test_schedule_reindex_debounce(monkeypatch):
    from docmind import auto_reindex
    fired = []
    monkeypatch.setattr(auto_reindex, "_run_reindex",
                        lambda kb: fired.append(kb))
    auto_reindex.schedule_reindex("kb1", delay=0.05)
    auto_reindex.schedule_reindex("kb1", delay=0.05)   # 重置计时,合并
    auto_reindex.schedule_reindex("kb2", delay=0.05)
    assert auto_reindex.pending_kbs() == ["kb1", "kb2"]
    import time
    time.sleep(0.15)
    assert sorted(fired) == ["kb1", "kb2"]             # 各执行一次(非三次)
    assert auto_reindex.pending_kbs() == []


# ---------------- 语义缓存 BLOB 往返 + 旧 JSON 兼容 ----------------
def test_semantic_cache_blob_roundtrip(temp_db):
    from docmind import semantic_cache
    v = [0.1, 0.2, 0.3]
    semantic_cache.save("q", "a", v)
    row = semantic_cache._conn().execute(
        "SELECT vec FROM semantic_cache").fetchone()
    assert isinstance(row["vec"], bytes)               # BLOB 存储
    hit = semantic_cache.lookup(v)
    assert hit is not None and hit[1] == "a"           # 命中


def test_semantic_cache_legacy_json_vec(temp_db):
    """升级前写入的 JSON 向量条目仍可命中"""
    import json as _json
    import time as _time
    from docmind import semantic_cache
    c = semantic_cache._conn()
    c.execute("INSERT INTO semantic_cache(question, answer, vec, created_at) "
              "VALUES(?,?,?,?)",
              ("老问题", "老答案", _json.dumps([1.0, 0.0, 0.0]), _time.time()))
    c.commit()
    hit = semantic_cache.lookup([1.0, 0.0, 0.0])
    assert hit is not None and hit[1] == "老答案"


# ---------------- 切片级 embedding 缓存 ----------------
def test_embed_cache_dedup(tmp_path, monkeypatch):
    from docmind.rag import embed_cache
    monkeypatch.setattr(embed_cache, "DB_PATH", str(tmp_path / "ec.db"))
    monkeypatch.setattr(embed_cache, "_local", threading.local())

    calls = {"n": 0}
    seen = []
    def fake_embed(texts):
        calls["n"] += 1
        seen.append(list(texts))
        return [[float(len(t))] * 4 for t in texts]

    r1 = embed_cache.embed_cached(fake_embed, ["aa", "bb", "cc"])
    assert calls["n"] == 1 and len(r1) == 3
    # 第二次:aa/bb 命中缓存,只有 dd 触发 API(且只传未命中的)
    r2 = embed_cache.embed_cached(fake_embed, ["aa", "bb", "dd"])
    assert calls["n"] == 2
    assert seen[1] == ["dd"]
    assert r2[0] == r1[0] and r2[1] == r1[1]


# ---------------- 预览倒排 ----------------
def test_chunks_by_source():
    from docmind.rag.vector_store import VectorStore
    s = VectorStore(chunks=[{"source": "a.md", "text": "1"},
                            {"source": "b.md", "text": "2"},
                            {"source": "a.md", "text": "3"}])
    got = s.chunks_by_source("a.md")
    assert [i for i, _ in got] == [0, 2]
    assert s.chunks_by_source("none.md") == []
    # version 变化后自动重建倒排
    s.chunks.append({"source": "a.md", "text": "4"})
    s.version += 1
    assert len(s.chunks_by_source("a.md")) == 3


# ---------------- 索引自愈:manifest 有但 Chroma 缺 ----------------
def test_rebuild_self_heals_orphan_files(tmp_path, monkeypatch):
    """manifest 记录"已索引"但索引实际缺失的文件,增量重建自动补录"""
    import threading as _th
    from unittest.mock import patch
    from docmind.rag import vector_store as vs
    from docmind.rag.vector_store import VectorStore
    from docmind.rag import cache as rcache

    kb = tmp_path / "kb"; kb.mkdir()
    (kb / "a.md").write_text("文档A内容,用于切片。", encoding="utf-8")
    (kb / "b.md").write_text("文档B内容,用于切片。", encoding="utf-8")

    idx = tmp_path / "idx"
    store = VectorStore(collection_name="t_heal", index_dir=str(idx))

    calls = []
    def fake_embed(texts):
        calls.append(list(texts))
        return [[0.1] * 8 for _ in texts]

    with patch.object(vs, "embed", fake_embed):
        n = store.build(str(kb), use_cache=False)
        assert n > 0
        store._persist_index_meta(str(kb))   # 建立与目录一致的 manifest+gfp
        # 模拟 b.md 切片从 Chroma 丢失(写入中断/外部损坏)
        store._get_collection().delete(where={"source": "b.md"})
        store.chunks = [c for c in store.chunks if c["source"] != "b.md"]

        # manifest 仍记录 b.md 已索引 → 重建应检测 orphan 并补录
        result = store.rebuild_incremental(str(kb))
        assert result.get("modified", 0) == 1
        assert {c["source"] for c in store.chunks} == {"a.md", "b.md"}


def test_build_cache_hit_requires_full_coverage(tmp_path):
    """Chroma 缺文件时 build() 不得命中缓存跳过重建"""
    from unittest.mock import patch
    from docmind.rag import vector_store as vs
    from docmind.rag.vector_store import VectorStore

    kb = tmp_path / "kb"; kb.mkdir()
    (kb / "a.md").write_text("文档A内容。", encoding="utf-8")
    (kb / "b.md").write_text("文档B内容。", encoding="utf-8")
    idx = tmp_path / "idx"

    def fake_embed(texts):
        return [[0.1] * 8 for _ in texts]

    store = VectorStore(collection_name="t_cov", index_dir=str(idx))
    with patch.object(vs, "embed", fake_embed):
        store.build(str(kb), use_cache=False)
        # 伪造:从内存镜像去掉 b.md 但保持 chroma_ready(模拟部分丢失)
        store.chunks = [c for c in store.chunks if c["source"] != "b.md"]
        n = store.build(str(kb))          # 应放弃缓存命中,重新全量
        sources = {c["source"] for c in store.chunks}
        assert sources == {"a.md", "b.md"} and n >= 2


# ---------------- 会话滑动窗口 ----------------
def test_history_sliding_window(temp_db):
    from docmind import chat_stream, store

    for i in range(30):
        store.append_message("s1", "user", f"问题{i}", raw=f"问题{i}", user="u")
        store.append_message("s1", "assistant", f"答{i}", raw=f"答{i}", user="u")

    class FakeAgent:
        def __init__(self):
            self.history = []
            self.last_tools = set()
        def ask(self, q, **kw):
            yield type("S", (), {"kind": "final", "text": "ok"})()

    agent = FakeAgent()
    config.SEMANTIC_CACHE = False
    try:
        list(chat_stream.stream_events(agent, "新问题", session_id="s1"))
    finally:
        config.SEMANTIC_CACHE = True
    msgs = agent.history
    assert len(msgs) <= config.MAX_HISTORY_TURNS + 2   # system + 注记 + 窗口
    assert any("未载入" in str(m.get("content", "")) for m in msgs[:2])
    assert "问题29" in str(msgs[-2]["content"])        # 最近轮次保留
