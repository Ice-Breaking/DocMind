"""共享 fixtures：临时 DB（隔离真实 chat.db）+ 临时知识库目录"""
import threading

import pytest

from docmind import acl, config, semantic_cache, store


@pytest.fixture
def temp_db(tmp_path, monkeypatch):
    """store/acl/semantic_cache 全部指向临时 DB，互不污染真实数据"""
    monkeypatch.setattr(store, "DB_PATH", str(tmp_path / "chat.db"))
    monkeypatch.setattr(store, "_local", threading.local())
    monkeypatch.setattr(acl, "DB_PATH", str(tmp_path / "chat.db"))
    monkeypatch.setattr(acl, "_local", threading.local())
    monkeypatch.setattr(semantic_cache, "DB_PATH", str(tmp_path / "cache.db"))
    monkeypatch.setattr(semantic_cache, "_local", threading.local())
    return tmp_path


@pytest.fixture
def temp_kb(tmp_path, monkeypatch):
    """临时知识库目录（chunker/acl 读 config.KNOWLEDGE_DIR）"""
    kb = tmp_path / "kb"
    kb.mkdir()
    monkeypatch.setattr(config, "KNOWLEDGE_DIR", str(kb))
    return kb
