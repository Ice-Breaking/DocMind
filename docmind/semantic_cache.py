"""语义缓存：高频问题秒回（省 token、降延迟）。

机制：问题 → embedding → 与缓存条目余弦相似度比对，≥ CACHE_THRESHOLD 视为同问，
直接返回缓存答案，跳过整个 Agent 链路（检索/工具/生成全省）。

安全边界（面试可讲）：
- 阈值保守（默认 0.92）：宁可不命中，不可错配（错配 = 张冠李戴的答案）
- 时效类回答（天气/时间/web_search 参与）不写入缓存，防过期数据
- 错误兜底类回答（⚠️ 开头）不写入
- 缓存失效不阻塞主链路（全 try/except）
"""
import json
import os
import sqlite3
import threading
import time

import numpy as np

from docmind import config
from docmind.pii import contains_pii

DB_PATH = os.path.join(config.PROJECT_ROOT, "data", "cache.db")
_local = threading.local()

_SCHEMA = """
CREATE TABLE IF NOT EXISTS semantic_cache(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    question TEXT NOT NULL,
    answer TEXT NOT NULL,
    vec TEXT NOT NULL,
    created_at REAL,
    hits INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_cache_created ON semantic_cache(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_cache_hits ON semantic_cache(hits DESC);
"""


def _conn() -> sqlite3.Connection:
    conn = getattr(_local, "conn", None)
    if conn is None:
        os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
        conn = sqlite3.connect(DB_PATH)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        conn.row_factory = sqlite3.Row
        conn.executescript(_SCHEMA)
        conn.commit()
        _local.conn = conn
    return conn


def _cosine(a: np.ndarray, b: np.ndarray) -> float:
    na, nb = np.linalg.norm(a), np.linalg.norm(b)
    if na == 0 or nb == 0:
        return 0.0
    return float(np.dot(a, b) / (na * nb))


def lookup(vec: list[float] | np.ndarray) -> tuple[str, str, int] | None:
    """返回最相似且 ≥ 阈值的 (缓存问题, 缓存答案, 条目id)；无命中返回 None

    性能优化：只查询最近 500 条热缓存，避免全表扫描"""
    qv = np.asarray(vec, dtype=np.float32)
    best_sim, best = config.CACHE_THRESHOLD, None
    # 优化：只查最近 500 条（按创建时间 + 命中次数综合排序）
    query = """
        SELECT id, question, answer, vec FROM semantic_cache
        ORDER BY hits DESC, created_at DESC
        LIMIT 500
    """
    for row in _conn().execute(query):
        sim = _cosine(qv, np.asarray(json.loads(row["vec"]), dtype=np.float32))
        if sim >= best_sim:
            best_sim, best = sim, (row["question"], row["answer"], row["id"])
    if best:
        _conn().execute("UPDATE semantic_cache SET hits = hits + 1 WHERE id = ?", (best[2],))
        _conn().commit()
    return best


def save(question: str, answer: str, vec: list[float] | np.ndarray) -> None:
    """写入缓存（同问题去重：先删旧条目）"""
    if contains_pii(question):
        return  # Don't cache questions containing PII
    c = _conn()
    c.execute("DELETE FROM semantic_cache WHERE question = ?", (question,))
    c.execute(
        "INSERT INTO semantic_cache(question, answer, vec, created_at) VALUES(?,?,?,?)",
        (question, answer, json.dumps([float(x) for x in vec]), time.time()),
    )
    c.commit()


def delete_entry(entry_id: int) -> None:
    """Delete a single cache entry by its id."""
    c = _conn()
    c.execute("DELETE FROM semantic_cache WHERE id = ?", (entry_id,))
    c.commit()


def stats() -> dict:
    row = _conn().execute(
        "SELECT COUNT(*) AS n, COALESCE(SUM(hits), 0) AS h FROM semantic_cache"
    ).fetchone()
    return {"entries": row["n"], "total_hits": row["h"]}


def cleanup_stale_entries(days: int = 7) -> int:
    """清理陈旧缓存：删除创建超过 N 天且从未命中的条目

    返回删除的条目数"""
    cutoff = time.time() - (days * 86400)
    c = _conn()
    c.execute("DELETE FROM semantic_cache WHERE hits = 0 AND created_at < ?", (cutoff,))
    deleted = c.total_changes
    c.commit()
    return deleted
