"""联网搜索结果缓存层：减少重复查询、加速响应。

缓存策略：
- SQLite 持久化存储，服务重启不丢失
- TTL 分级：新闻类 10 分钟，知识类 30 分钟（默认）
- 自动清理过期条目
- key = query 的 hash，避免长查询占内存
"""
import hashlib
import json
import os
import sqlite3
import threading
import time
from typing import Optional

from docmind import config

DB_PATH = os.path.join(config.PROJECT_ROOT, "data", "web_search_cache.db")
_local = threading.local()

_SCHEMA = """
CREATE TABLE IF NOT EXISTS web_search_cache(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    query_hash TEXT UNIQUE NOT NULL,
    query TEXT NOT NULL,
    results TEXT NOT NULL,
    created_at REAL NOT NULL,
    ttl INTEGER DEFAULT 1800,
    hits INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_websearch_hash ON web_search_cache(query_hash);
CREATE INDEX IF NOT EXISTS idx_websearch_created ON web_search_cache(created_at DESC);
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


def _cache_key(query: str) -> str:
    """查询的缓存键：SHA256 前16位"""
    return hashlib.sha256(query.encode()).hexdigest()[:16]


def get(query: str) -> Optional[list[dict]]:
    """从缓存获取搜索结果；过期/不存在返回 None"""
    key = _cache_key(query)
    c = _conn()
    row = c.execute(
        "SELECT results, created_at, ttl FROM web_search_cache WHERE query_hash = ?",
        (key,)
    ).fetchone()

    if row is None:
        return None

    # 检查是否过期
    if time.time() - row["created_at"] > row["ttl"]:
        c.execute("DELETE FROM web_search_cache WHERE query_hash = ?", (key,))
        c.commit()
        return None

    # 命中：更新计数
    c.execute("UPDATE web_search_cache SET hits = hits + 1 WHERE query_hash = ?", (key,))
    c.commit()

    return json.loads(row["results"])


# 时效敏感词：命中使用短 TTL——"最新/新闻"类查询结果过时最快，
# 30 分钟默认 TTL 对这类问题等于返回旧闻
_FRESH_TTL_WORDS = ("最新", "新闻", "热点", "刚刚", "今天", "现在", "目前",
                    "当前", "实时", "昨日", "昨天", "跌破", "暴涨")
_FRESH_TTL_SECONDS = 600   # 时效类 10 分钟


def _ttl_for(query: str, default_ttl: int) -> int:
    return _FRESH_TTL_SECONDS if any(w in query for w in _FRESH_TTL_WORDS) else default_ttl


def put(query: str, results: list[dict], ttl: int = None) -> None:
    """写入缓存；超出容量时淘汰最老的

    ttl: 缓存生存时间（秒），None 按查询内容自动分级：
    时效敏感词命中的查询用短 TTL（10 分钟），其余用默认 TTL"""
    if ttl is None:
        ttl = _ttl_for(query, config.WEB_SEARCH_CACHE_TTL)

    key = _cache_key(query)
    c = _conn()

    # 使用 INSERT OR REPLACE 更新或插入
    c.execute(
        """INSERT OR REPLACE INTO web_search_cache(query_hash, query, results, created_at, ttl, hits)
           VALUES(?, ?, ?, ?, ?, 0)""",
        (key, query, json.dumps(results, ensure_ascii=False), time.time(), ttl)
    )
    c.commit()


def cleanup_expired() -> int:
    """清理过期条目，返回删除数量"""
    c = _conn()
    # 删除所有过期条目
    c.execute(
        "DELETE FROM web_search_cache WHERE created_at + ttl < ?",
        (time.time(),)
    )
    deleted = c.total_changes
    c.commit()
    return deleted


def stats() -> dict:
    """缓存统计"""
    c = _conn()
    row = c.execute(
        "SELECT COUNT(*) AS n, COALESCE(SUM(hits), 0) AS h FROM web_search_cache"
    ).fetchone()
    return {"entries": row["n"], "total_hits": row["h"]}
