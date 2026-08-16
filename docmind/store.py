"""会话持久化与反馈闭环：SQLite 单文件存储（data/chat.db，零依赖标准库）

表设计：
- sessions：会话（id/标题/时间戳），标题取首条用户消息
- messages：消息（session_id + seq 定序，role/content 原样存取），刷新页面自动恢复
- feedback：👍/👎 评价（session_id + seq 唯一，重复点击覆盖），badcase 收集用

线程模型：sqlite 连接放 thread-local（FastAPI 线程池多线程访问安全）。
"""
import os
import sqlite3
import threading
import time

from docmind import config

DB_PATH = os.path.join(config.PROJECT_ROOT, "data", "chat.db")
_local = threading.local()

_SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions(
    id TEXT PRIMARY KEY,
    title TEXT DEFAULT '',
    created_at REAL,
    updated_at REAL
);
CREATE TABLE IF NOT EXISTS messages(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    seq INTEGER NOT NULL,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    raw TEXT DEFAULT '',
    created_at REAL
);
CREATE INDEX IF NOT EXISTS idx_messages_session ON messages(session_id, seq);
CREATE TABLE IF NOT EXISTS feedback(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    seq INTEGER NOT NULL,
    rating TEXT NOT NULL,
    created_at REAL,
    UNIQUE(session_id, seq)
);
"""


def _conn() -> sqlite3.Connection:
    conn = getattr(_local, "conn", None)
    if conn is None:
        os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        conn.executescript(_SCHEMA)
        cols = [r["name"] for r in conn.execute("PRAGMA table_info(messages)")]
        if "raw" not in cols:
            conn.execute("ALTER TABLE messages ADD COLUMN raw TEXT DEFAULT ''")
        conn.commit()
        _local.conn = conn
    return conn


def append_message(session_id: str, role: str, content: str, raw: str | None = None) -> int:
    """追加一条消息，返回其在会话内的序号（从 0 起）。

    content 为展示内容（含思维链/引用标记等渲染格式）；
    raw 为干净文本（assistant 的纯净回答），用于切换会话时恢复 LLM 多轮上下文。
    """
    c = _conn()
    now = time.time()
    seq = c.execute(
        "SELECT COALESCE(MAX(seq), -1) + 1 FROM messages WHERE session_id = ?",
        (session_id,),
    ).fetchone()[0]
    c.execute(
        "INSERT INTO messages(session_id, seq, role, content, raw, created_at) VALUES(?,?,?,?,?,?)",
        (session_id, seq, role, content, raw if raw is not None else content, now),
    )
    row = c.execute("SELECT title FROM sessions WHERE id = ?", (session_id,)).fetchone()
    if row is None:
        c.execute(
            "INSERT INTO sessions(id, title, created_at, updated_at) VALUES(?,?,?,?)",
            (session_id, content[:30] if role == "user" else "", now, now),
        )
    elif not row["title"] and role == "user":
        c.execute("UPDATE sessions SET title = ?, updated_at = ? WHERE id = ?",
                  (content[:30], now, session_id))
    else:
        c.execute("UPDATE sessions SET updated_at = ? WHERE id = ?", (now, session_id))
    c.commit()
    return seq


def load_session(session_id: str) -> list[dict]:
    """按序返回会话消息 [{role, content}]（Gradio messages 格式，可直接回填 Chatbot）"""
    c = _conn()
    rows = c.execute(
        "SELECT role, content FROM messages WHERE session_id = ? ORDER BY seq",
        (session_id,),
    ).fetchall()
    return [{"role": r["role"], "content": r["content"]} for r in rows]


def save_feedback(session_id: str, seq: int, rating: str) -> None:
    """保存/覆盖某条消息的评价（up/down）"""
    c = _conn()
    c.execute(
        """INSERT INTO feedback(session_id, seq, rating, created_at) VALUES(?,?,?,?)
           ON CONFLICT(session_id, seq) DO UPDATE
           SET rating = excluded.rating, created_at = excluded.created_at""",
        (session_id, seq, rating, time.time()),
    )
    c.commit()


def get_feedback(session_id: str) -> dict:
    """返回 {消息序号(str): rating}，供前端恢复选中态"""
    c = _conn()
    rows = c.execute(
        "SELECT seq, rating FROM feedback WHERE session_id = ?", (session_id,)
    ).fetchall()
    return {str(r["seq"]): r["rating"] for r in rows}


def load_raw_pairs(session_id: str) -> list[tuple[str, str]]:
    """按序返回 [(role, raw)]，供恢复 LLM 多轮上下文（过滤空 raw）"""
    c = _conn()
    rows = c.execute(
        "SELECT role, raw FROM messages WHERE session_id = ? ORDER BY seq",
        (session_id,),
    ).fetchall()
    return [(r["role"], r["raw"]) for r in rows if r["raw"]]


def list_sessions(limit: int = 50) -> list[dict]:
    """会话列表（按最近活跃倒序）：id/标题/消息数/更新时间，供侧边栏渲染"""
    c = _conn()
    rows = c.execute(
        """SELECT s.id, s.title, s.updated_at, COUNT(m.id) AS msg_count
           FROM sessions s LEFT JOIN messages m ON m.session_id = s.id
           GROUP BY s.id ORDER BY s.updated_at DESC LIMIT ?""",
        (limit,),
    ).fetchall()
    return [{"id": r["id"], "title": r["title"], "msg_count": r["msg_count"],
             "updated_at": r["updated_at"]} for r in rows]


def delete_session(session_id: str) -> None:
    """删除会话及其消息与反馈"""
    c = _conn()
    c.execute("DELETE FROM messages WHERE session_id = ?", (session_id,))
    c.execute("DELETE FROM feedback WHERE session_id = ?", (session_id,))
    c.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
    c.commit()
