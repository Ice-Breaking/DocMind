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
"""


def _conn() -> sqlite3.Connection:
    conn = getattr(_local, "conn", None)
    if conn is None:
        os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
        conn = sqlite3.connect(DB_PATH)
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
    """返回最相似且 ≥ 阈值的 (缓存问题, 缓存答案, 条目id)；无命中返回 None"""
    qv = np.asarray(vec, dtype=np.float32)
    best_sim, best = config.CACHE_THRESHOLD, None
    for row in _conn().execute("SELECT id, question, answer, vec FROM semantic_cache"):
        sim = _cosine(qv, np.asarray(json.loads(row["vec"]), dtype=np.float32))
        if sim >= best_sim:
            best_sim, best = sim, (row["question"], row["answer"], row["id"])
    if best:
        _conn().execute("UPDATE semantic_cache SET hits = hits + 1 WHERE id = ?", (best[2],))
        _conn().commit()
    return best


def save(question: str, answer: str, vec: list[float] | np.ndarray) -> None:
    """写入缓存（同问题去重：先删旧条目）"""
    c = _conn()
    c.execute("DELETE FROM semantic_cache WHERE question = ?", (question,))
    c.execute(
        "INSERT INTO semantic_cache(question, answer, vec, created_at) VALUES(?,?,?,?)",
        (question, answer, json.dumps([float(x) for x in vec]), time.time()),
    )
    c.commit()


def stats() -> dict:
    row = _conn().execute(
        "SELECT COUNT(*) AS n, COALESCE(SUM(hits), 0) AS h FROM semantic_cache"
    ).fetchone()
    return {"entries": row["n"], "total_hits": row["h"]}
