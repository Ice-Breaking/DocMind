"""Agent 推理缓存：相同问题跳过 LLM 推理，直接重放工具调用。

机制：
- key = (question_hash, kb_ids, system_prompt_hash)
- value = (tool_calls_sequence, final_answer)
- 缓存命中时直接返回答案，跳过整个 ReAct 循环
- 只缓存纯知识检索类问题（无 web_search/时间等动态工具）

安全边界：
- 阈值：问题完全一致（hash 匹配）
- 动态工具（天气/时间/联网）的回答不缓存
- 错误回答不缓存
- TTL：24 小时（知识库变化后失效）
"""
import hashlib
import json
import os
import sqlite3
import threading
import time
from typing import Optional

from docmind import config

DB_PATH = os.path.join(config.PROJECT_ROOT, "data", "agent_reasoning_cache.db")
_local = threading.local()

_SCHEMA = """
CREATE TABLE IF NOT EXISTS agent_reasoning_cache(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    question_hash TEXT NOT NULL,
    kb_ids TEXT DEFAULT '[]',
    system_prompt_hash TEXT NOT NULL,
    question TEXT NOT NULL,
    answer TEXT NOT NULL,
    tool_sequence TEXT DEFAULT '[]',
    created_at REAL NOT NULL,
    hits INTEGER DEFAULT 0,
    UNIQUE(question_hash, kb_ids, system_prompt_hash)
);
CREATE INDEX IF NOT EXISTS idx_reasoning_hash ON agent_reasoning_cache(question_hash);
CREATE INDEX IF NOT EXISTS idx_reasoning_created ON agent_reasoning_cache(created_at DESC);
"""

# 不应缓存的动态工具
_DYNAMIC_TOOLS = {"web_search", "get_current_time", "get_weather"}

# 缓存 TTL：24 小时
_CACHE_TTL = 86400


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


def _hash_text(text: str) -> str:
    """计算文本哈希"""
    return hashlib.sha256(text.encode()).hexdigest()[:16]


def lookup(question: str, kb_ids: list[str], system_prompt: str) -> Optional[str]:
    """查询推理缓存，返回答案；未命中或过期返回 None

    kb_ids: 知识库 ID 列表（影响检索结果）
    system_prompt: 系统提示词（影响推理行为）
    """
    q_hash = _hash_text(question)
    kb_key = json.dumps(sorted(kb_ids), ensure_ascii=False)
    sp_hash = _hash_text(system_prompt)

    c = _conn()
    row = c.execute(
        """SELECT answer, tool_sequence, created_at FROM agent_reasoning_cache
           WHERE question_hash = ? AND kb_ids = ? AND system_prompt_hash = ?""",
        (q_hash, kb_key, sp_hash)
    ).fetchone()

    if row is None:
        return None

    # 检查是否过期
    if time.time() - row["created_at"] > _CACHE_TTL:
        c.execute(
            """DELETE FROM agent_reasoning_cache
               WHERE question_hash = ? AND kb_ids = ? AND system_prompt_hash = ?""",
            (q_hash, kb_key, sp_hash)
        )
        c.commit()
        return None

    # 检查工具序列：含动态工具的不使用缓存
    tool_seq = json.loads(row["tool_sequence"])
    if any(tool in _DYNAMIC_TOOLS for tool in tool_seq):
        return None

    # 命中：更新计数
    c.execute(
        """UPDATE agent_reasoning_cache SET hits = hits + 1
           WHERE question_hash = ? AND kb_ids = ? AND system_prompt_hash = ?""",
        (q_hash, kb_key, sp_hash)
    )
    c.commit()

    return row["answer"]


def save(question: str, kb_ids: list[str], system_prompt: str,
         answer: str, tool_sequence: list[str]) -> None:
    """保存推理结果到缓存

    tool_sequence: 本轮调用的工具名称列表（用于判断是否可缓存）
    """
    # 不缓存动态工具的结果
    if any(tool in _DYNAMIC_TOOLS for tool in tool_sequence):
        return

    # 不缓存错误回答
    if answer.startswith("⚠️"):
        return

    q_hash = _hash_text(question)
    kb_key = json.dumps(sorted(kb_ids), ensure_ascii=False)
    sp_hash = _hash_text(system_prompt)

    c = _conn()
    c.execute(
        """INSERT OR REPLACE INTO agent_reasoning_cache
           (question_hash, kb_ids, system_prompt_hash, question, answer, tool_sequence, created_at, hits)
           VALUES(?, ?, ?, ?, ?, ?, ?, 0)""",
        (q_hash, kb_key, sp_hash, question, answer,
         json.dumps(tool_sequence, ensure_ascii=False), time.time())
    )
    c.commit()


def cleanup_expired() -> int:
    """清理过期缓存，返回删除数量"""
    c = _conn()
    cutoff = time.time() - _CACHE_TTL
    c.execute("DELETE FROM agent_reasoning_cache WHERE created_at < ?", (cutoff,))
    deleted = c.total_changes
    c.commit()
    return deleted


def stats() -> dict:
    """缓存统计"""
    c = _conn()
    row = c.execute(
        "SELECT COUNT(*) AS n, COALESCE(SUM(hits), 0) AS h FROM agent_reasoning_cache"
    ).fetchone()
    return {"entries": row["n"], "total_hits": row["h"]}


def clear() -> int:
    """清空全部缓存条目，返回删除数。

    知识库重建/文档增删后调用——推理缓存不感知 KB 内容变化，
    不清理会继续返回基于旧文档的回答（虽有 TTL 但最长滞后 24h）"""
    c = _conn()
    deleted = c.execute("DELETE FROM agent_reasoning_cache").rowcount
    c.commit()
    return deleted
