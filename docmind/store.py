"""会话持久化、反馈闭环与用户认证：SQLite 单文件存储（data/chat.db，零依赖标准库）

表设计：
- users：账号（pbkdf2 密码哈希），认证门禁与数据隔离的基础
- sessions：会话（id/标题/时间戳/所属用户），标题取首条用户消息
- messages：消息（session_id + seq 定序，role/content 原样存取），刷新页面自动恢复
- feedback：👍/👎 评价（session_id + seq 唯一，重复点击覆盖），badcase 收集用

线程模型：sqlite 连接放 thread-local（FastAPI 线程池多线程访问安全）。
"""
import hashlib
import os
import secrets
import sqlite3
import threading
import time

from docmind import config

DB_PATH = os.path.join(config.PROJECT_ROOT, "data", "chat.db")
_local = threading.local()

_SCHEMA = """
CREATE TABLE IF NOT EXISTS users(
    username TEXT PRIMARY KEY,
    pw_hash TEXT NOT NULL,
    created_at REAL
);
CREATE TABLE IF NOT EXISTS sessions(
    id TEXT PRIMARY KEY,
    title TEXT DEFAULT '',
    user TEXT DEFAULT '',
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
        s_cols = [r["name"] for r in conn.execute("PRAGMA table_info(sessions)")]
        if "user" not in s_cols:
            conn.execute("ALTER TABLE sessions ADD COLUMN user TEXT DEFAULT ''")
        conn.commit()
        _local.conn = conn
    return conn


def append_message(session_id: str, role: str, content: str, raw: str | None = None,
                   user: str | None = None) -> int:
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
    row = c.execute("SELECT title, user FROM sessions WHERE id = ?", (session_id,)).fetchone()
    if row is None:
        c.execute(
            "INSERT INTO sessions(id, title, user, created_at, updated_at) VALUES(?,?,?,?,?)",
            (session_id, content[:30] if role == "user" else "", user or "", now, now),
        )
    else:
        if not row["title"] and role == "user":
            c.execute("UPDATE sessions SET title = ? WHERE id = ?", (content[:30], session_id))
        if not row["user"] and user:
            c.execute("UPDATE sessions SET user = ? WHERE id = ?", (user, session_id))
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


def list_sessions(user: str | None = None, limit: int = 50) -> list[dict]:
    """会话列表（按最近活跃倒序）：只看本人会话 + 尚未归属的历史会话（打开即认领）"""
    c = _conn()
    rows = c.execute(
        """SELECT s.id, s.title, s.updated_at, COUNT(m.id) AS msg_count
           FROM sessions s LEFT JOIN messages m ON m.session_id = s.id
           WHERE s.user = '' OR s.user = ?
           GROUP BY s.id ORDER BY s.updated_at DESC LIMIT ?""",
        (user or "", limit),
    ).fetchall()
    return [{"id": r["id"], "title": r["title"], "msg_count": r["msg_count"],
             "updated_at": r["updated_at"]} for r in rows]


def session_owner(session_id: str) -> str | None:
    """返回会话所属用户；会话不存在返回 None"""
    row = _conn().execute(
        "SELECT user FROM sessions WHERE id = ?", (session_id,)).fetchone()
    return row["user"] if row else None


def delete_session(session_id: str) -> None:
    """删除会话及其消息与反馈"""
    c = _conn()
    c.execute("DELETE FROM messages WHERE session_id = ?", (session_id,))
    c.execute("DELETE FROM feedback WHERE session_id = ?", (session_id,))
    c.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
    c.commit()


# ---------------------------------------------------------------- 用户认证
_PBKDF2_ITER = 200_000


def _hash_password(password: str, salt: bytes | None = None) -> str:
    """pbkdf2-sha256 哈希，存储格式 salt_hex$hash_hex"""
    salt = salt or secrets.token_bytes(16)
    h = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, _PBKDF2_ITER)
    return salt.hex() + "$" + h.hex()


def verify_password(password: str, stored: str) -> bool:
    try:
        salt_hex, hash_hex = stored.split("$", 1)
        expect = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"),
                                     bytes.fromhex(salt_hex), _PBKDF2_ITER)
        return secrets.compare_digest(expect.hex(), hash_hex)
    except (ValueError, AttributeError):
        return False


def create_user(username: str, password: str) -> bool:
    """新建用户；已存在返回 False"""
    c = _conn()
    try:
        c.execute("INSERT INTO users(username, pw_hash, created_at) VALUES(?,?,?)",
                  (username, _hash_password(password), time.time()))
        c.commit()
        return True
    except sqlite3.IntegrityError:
        return False


def set_password(username: str, password: str) -> bool:
    c = _conn()
    cur = c.execute("UPDATE users SET pw_hash = ? WHERE username = ?",
                    (_hash_password(password), username))
    c.commit()
    return cur.rowcount > 0


def verify_user(username: str, password: str) -> bool:
    row = _conn().execute(
        "SELECT pw_hash FROM users WHERE username = ?", (username,)).fetchone()
    return bool(row) and verify_password(password, row["pw_hash"])


def list_users() -> list[dict]:
    rows = _conn().execute(
        "SELECT username, created_at FROM users ORDER BY created_at").fetchall()
    return [{"username": r["username"], "created_at": r["created_at"]} for r in rows]


def delete_user(username: str) -> bool:
    c = _conn()
    cur = c.execute("DELETE FROM users WHERE username = ?", (username,))
    c.commit()
    return cur.rowcount > 0


def ensure_seed_admin() -> None:
    """无任何账号时播种 admin（密码取 ADMIN_PASSWORD 环境变量，默认 admin123）"""
    if list_users():
        return
    pw = os.getenv("ADMIN_PASSWORD", "admin123")
    create_user("admin", pw)
    print("[DocMind] 已创建初始账号 admin（请尽快用 manage_users reset 修改密码）")
