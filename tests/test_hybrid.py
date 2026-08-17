"""混合检索单测（mock embedding，离线）：双路召回 / ACL 过滤 / 页码透传"""
import hashlib

import numpy as np
import pytest

from docmind.rag.hybrid import HybridRetriever
from docmind.rag.vector_store import VectorStore


def fake_embed(texts):
    """确定性伪向量：同文本 → 同向量（余弦 1.0），不同文本近似正交"""
    vecs = []
    for t in texts:
        h = hashlib.sha256(t.encode("utf-8")).digest()
        vecs.append([b / 255.0 for b in h[:64]])
    return vecs


@pytest.fixture
def retriever(monkeypatch):
    monkeypatch.setattr("docmind.rag.vector_store.embed", fake_embed)
    chunks = [
        {"source": "公开.md", "text": "DocMind 默认端口是 7860"},
        {"source": "机密.md", "text": "内部项目代号是 DM-SECRET"},
        {"source": "公开.pdf", "text": "启动方式是 python -m docmind.app", "page": 2},
    ]
    store = VectorStore(chunks=chunks)
    store._matrix = np.asarray(fake_embed([c["text"] for c in chunks]), dtype=np.float32)
    hr = HybridRetriever(store)
    hr.build()
    return hr


def test_bm25_recall(retriever):
    """BM25 关键词路命中"""
    hits = retriever.search("默认端口", top_k=4, rerank=False)
    assert any("7860" in h.text for h in hits)


def test_vector_recall_exact(retriever):
    """与切片文本完全一致的查询 → 双路 rank 1 → RRF 融合分最高"""
    hits = retriever.search("DocMind 默认端口是 7860", top_k=4, rerank=False)
    assert hits[0].text == "DocMind 默认端口是 7860"
    # RRF 分数语义：双路命中叠加，头部必然显著高于其余候选
    assert hits[0].score > hits[1].score


def test_page_metadata_passthrough(retriever):
    """PDF 切片的页码元数据透传到检索结果"""
    hits = retriever.search("启动方式是 python -m docmind.app", top_k=4, rerank=False)
    pdf_hits = [h for h in hits if h.source == "公开.pdf"]
    assert pdf_hits and pdf_hits[0].page == 2


def test_acl_filter_excludes_unallowed(retriever):
    """allowed_sources 过滤：无权文档不进结果"""
    hits = retriever.search("内部项目代号是 DM-SECRET", top_k=4, rerank=False,
                            allowed_sources={"公开.md", "公开.pdf"})
    assert all(h.source != "机密.md" for h in hits)


def test_acl_filter_allows_granted(retriever):
    """授权文档正常返回"""
    hits = retriever.search("内部项目代号是 DM-SECRET", top_k=4, rerank=False,
                            allowed_sources={"公开.md", "公开.pdf", "机密.md"})
    assert any(h.source == "机密.md" for h in hits)


def test_no_filter_returns_all(retriever):
    """allowed_sources=None 不过滤（CLI/兼容路径）"""
    hits = retriever.search("内部项目代号是 DM-SECRET", top_k=4, rerank=False)
    assert hits[0].source == "机密.md"
