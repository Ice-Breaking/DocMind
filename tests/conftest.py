"""共享 fixtures：临时 DB（隔离真实 chat.db）+ 临时知识库目录"""
import threading

import pytest

from docmind import acl, agent_reasoning_cache, config, semantic_cache, store


@pytest.fixture(autouse=True)
def _isolate_reasoning_cache(tmp_path, monkeypatch):
    """推理缓存隔离：该模块直连 data/ 真实库，不隔离时测试问题会写入
    生产缓存，导致 pytest 第二次运行命中缓存短路（事件序列断言全挂）"""
    monkeypatch.setattr(agent_reasoning_cache, "DB_PATH",
                        str(tmp_path / "reasoning.db"))
    monkeypatch.setattr(agent_reasoning_cache, "_local", threading.local())


@pytest.fixture(autouse=True)
def _isolate_tokenize_cache(tmp_path, monkeypatch):
    """分词缓存隔离：BM25 构建会写 data/index/tokenize_cache.db，
    测试统一指向临时库，不污染真实缓存目录"""
    from docmind.rag import tokenize_cache
    monkeypatch.setattr(tokenize_cache, "DB_PATH", str(tmp_path / "tokenize.db"))
    monkeypatch.setattr(tokenize_cache, "_local", threading.local())


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
