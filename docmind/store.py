"""会话持久化、反馈闭环与用户认证：SQLite 单文件存储（data/chat.db，零依赖标准库）

表设计：
- users：账号（pbkdf2 密码哈希），认证门禁与数据隔离的基础
- sessions：会话（id/标题/时间戳/所属用户），标题取首条用户消息
- messages：消息（session_id + seq 定序，role/content 原样存取），刷新页面自动恢复
- feedback：👍/👎 评价（session_id + seq 唯一，重复点击覆盖），badcase 收集用

线程模型：sqlite 连接放 thread-local（FastAPI 线程池多线程访问安全）。
"""
import hashlib
import json
import logging
import os
import secrets
import sqlite3
import threading
import time
import uuid

from docmind import config

logger = logging.getLogger(__name__)

DB_PATH = os.path.join(config.PROJECT_ROOT, "data", "chat.db")
_local = threading.local()

_SCHEMA = """
CREATE TABLE IF NOT EXISTS users(
    username TEXT PRIMARY KEY,
    pw_hash TEXT NOT NULL,
    is_admin INTEGER DEFAULT 0,
    created_at REAL
);
CREATE TABLE IF NOT EXISTS sessions(
    id TEXT PRIMARY KEY,
    title TEXT DEFAULT '',
    user TEXT DEFAULT '',
    created_at REAL,
    updated_at REAL
);
CREATE INDEX IF NOT EXISTS idx_sessions_user_updated ON sessions(user, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_sessions_updated ON sessions(updated_at DESC);
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
CREATE INDEX IF NOT EXISTS idx_messages_created ON messages(created_at DESC);
CREATE TABLE IF NOT EXISTS feedback(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    seq INTEGER NOT NULL,
    rating TEXT NOT NULL,
    created_at REAL,
    UNIQUE(session_id, seq)
);
CREATE INDEX IF NOT EXISTS idx_feedback_created ON feedback(created_at DESC);
CREATE TABLE IF NOT EXISTS feedback_status(
    feedback_id INTEGER PRIMARY KEY,
    status TEXT DEFAULT 'pending',
    note TEXT DEFAULT '',
    updated_at REAL
);
CREATE INDEX IF NOT EXISTS idx_feedback_status ON feedback_status(status, updated_at DESC);
CREATE TABLE IF NOT EXISTS suggestions(
    answer_hash TEXT PRIMARY KEY,
    items TEXT NOT NULL,
    created_at REAL
);
CREATE TABLE IF NOT EXISTS knowledge_bases(
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    description TEXT DEFAULT '',
    doc_dir TEXT DEFAULT '',
    created_at REAL
);
CREATE TABLE IF NOT EXISTS assistants(
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    avatar TEXT DEFAULT '',
    system_prompt TEXT DEFAULT '',
    kb_ids TEXT DEFAULT '[]',
    model_config TEXT DEFAULT '{}',
    owner TEXT DEFAULT '',
    created_at REAL,
    updated_at REAL
);
CREATE INDEX IF NOT EXISTS idx_assistants_owner ON assistants(owner, updated_at DESC);
CREATE TABLE IF NOT EXISTS eval_datasets(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    kb_id TEXT DEFAULT 'default',
    items TEXT DEFAULT '[]',
    created_at REAL
);
CREATE TABLE IF NOT EXISTS api_keys(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    prefix TEXT NOT NULL,
    key_hash TEXT NOT NULL UNIQUE,
    scope_kb_ids TEXT DEFAULT '[]',
    created_by TEXT DEFAULT '',
    created_at REAL,
    expires_at REAL,
    revoked_at REAL,
    last_used_at REAL
);
CREATE INDEX IF NOT EXISTS idx_apikeys_prefix ON api_keys(prefix);
CREATE INDEX IF NOT EXISTS idx_apikeys_created ON api_keys(created_at DESC);
CREATE TABLE IF NOT EXISTS ingest_tasks(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kb_id TEXT NOT NULL,
    filename TEXT NOT NULL,
    mode TEXT DEFAULT 'upload',
    status TEXT DEFAULT 'pending',
    message TEXT DEFAULT '',
    created_by TEXT DEFAULT '',
    created_at REAL,
    updated_at REAL
);
CREATE INDEX IF NOT EXISTS idx_ingest_kb_status ON ingest_tasks(kb_id, status, created_at DESC);
CREATE TABLE IF NOT EXISTS audit_events(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    actor TEXT DEFAULT '',
    action TEXT NOT NULL,
    target TEXT DEFAULT '',
    detail TEXT DEFAULT '',
    created_at REAL
);
CREATE INDEX IF NOT EXISTS idx_audit_created ON audit_events(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_audit_actor ON audit_events(actor, created_at DESC);
CREATE TABLE IF NOT EXISTS alerts(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    type TEXT NOT NULL,
    severity TEXT DEFAULT 'warning',
    message TEXT NOT NULL,
    dedupe_key TEXT DEFAULT '',
    status TEXT DEFAULT 'open',
    created_at REAL,
    acked_at REAL,
    resolved_at REAL
);
CREATE INDEX IF NOT EXISTS idx_alerts_status ON alerts(status, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_alerts_dedupe ON alerts(dedupe_key, status);
CREATE TABLE IF NOT EXISTS models(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    kind TEXT NOT NULL,
    base_url TEXT DEFAULT '',
    api_key TEXT DEFAULT '',
    model_name TEXT NOT NULL,
    is_active INTEGER DEFAULT 0,
    created_by TEXT DEFAULT '',
    created_at REAL
);
CREATE INDEX IF NOT EXISTS idx_models_kind_active ON models(kind, is_active);
CREATE TABLE IF NOT EXISTS eval_runs(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    dataset_id INTEGER NOT NULL,
    mode TEXT DEFAULT 'rerank',
    top_k INTEGER DEFAULT 4,
    status TEXT DEFAULT 'pending',
    recall REAL DEFAULT 0,
    mrr REAL DEFAULT 0,
    total INTEGER DEFAULT 0,
    hits INTEGER DEFAULT 0,
    details TEXT DEFAULT '[]',
    duration_ms INTEGER DEFAULT 0,
    created_by TEXT DEFAULT '',
    created_at REAL
);
CREATE INDEX IF NOT EXISTS idx_eval_dataset ON eval_runs(dataset_id, created_at DESC);
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
        # 动态列补充（向前兼容旧数据库）
        cols = [r["name"] for r in conn.execute("PRAGMA table_info(messages)")]
        if "raw" not in cols:
            conn.execute("ALTER TABLE messages ADD COLUMN raw TEXT DEFAULT ''")
        s_cols = [r["name"] for r in conn.execute("PRAGMA table_info(sessions)")]
        if "user" not in s_cols:
            conn.execute("ALTER TABLE sessions ADD COLUMN user TEXT DEFAULT ''")
        if "assistant_id" not in s_cols:
            conn.execute("ALTER TABLE sessions ADD COLUMN assistant_id TEXT DEFAULT ''")
        u_cols = [r["name"] for r in conn.execute("PRAGMA table_info(users)")]
        if "is_admin" not in u_cols:
            conn.execute("ALTER TABLE users ADD COLUMN is_admin INTEGER DEFAULT 0")
        if "must_change_pwd" not in u_cols:
            conn.execute("ALTER TABLE users ADD COLUMN must_change_pwd INTEGER DEFAULT 0")
        if "avatar" not in u_cols:
            conn.execute("ALTER TABLE users ADD COLUMN avatar TEXT DEFAULT ''")
        if "pending_avatar" not in u_cols:
            conn.execute("ALTER TABLE users ADD COLUMN pending_avatar TEXT DEFAULT ''")
            conn.execute("ALTER TABLE users ADD COLUMN pending_avatar_at REAL DEFAULT 0")
        conn.commit()
        _local.conn = conn
    return conn


def append_message(session_id: str, role: str, content: str, raw: str | None = None,
                   user: str | None = None, assistant_id: str = "") -> int:
    """追加一条消息，返回其在会话内的序号（从 0 起）。

    content 为展示内容（含思维链/引用标记等渲染格式）；
    raw 为干净文本（assistant 的纯净回答），用于切换会话时恢复 LLM 多轮上下文。
    assistant_id：新建会话时归属的助手（空串=默认助手），已存在会话不受影响。
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
            "INSERT INTO sessions(id, title, user, assistant_id, created_at, updated_at) VALUES(?,?,?,?,?,?)",
            (session_id, content[:30] if role == "user" else "", user or "", assistant_id, now, now),
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


def list_sessions(user: str | None = None, limit: int = 50,
                  assistant_id: str | None = None) -> list[dict]:
    """会话列表（按最近活跃倒序）：只看本人会话 + 尚未归属的历史会话（打开即认领）

    assistant_id 为 None 时行为与旧版完全一致；指定时额外按助手过滤。
    返回项含 assistant_id（空值归一为 "default"）。
    """
    c = _conn()
    sql = """SELECT s.id, s.title, s.updated_at, s.assistant_id, COUNT(m.id) AS msg_count
           FROM sessions s LEFT JOIN messages m ON m.session_id = s.id
           WHERE (s.user = '' OR s.user = ?)"""
    params: list = [user or ""]
    if assistant_id is not None:
        sql += " AND s.assistant_id = ?"
        params.append(assistant_id)
    sql += " GROUP BY s.id ORDER BY s.updated_at DESC LIMIT ?"
    params.append(limit)
    rows = c.execute(sql, params).fetchall()
    return [{"id": r["id"], "title": r["title"], "msg_count": r["msg_count"],
             "updated_at": r["updated_at"],
             "assistant_id": (r["assistant_id"] or "default") if "assistant_id" in r.keys() else "default"}
            for r in rows]


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


# ---------------------------------------------------------------- GDPR 合规
def export_user_data(username: str) -> dict:
    """导出用户全部关联数据（GDPR 数据可携带权）：账号/会话/消息/反馈"""
    c = _conn()
    user_row = c.execute(
        "SELECT username, is_admin, created_at FROM users WHERE username = ?",
        (username,)).fetchone()
    if not user_row:
        return {"error": "User not found"}

    sessions = c.execute(
        """SELECT s.id, s.title, s.created_at, s.updated_at,
                  COUNT(m.id) AS msg_count
           FROM sessions s LEFT JOIN messages m ON m.session_id = s.id
           WHERE s.user = ? GROUP BY s.id ORDER BY s.updated_at DESC""",
        (username,)).fetchall()

    all_messages: dict[str, list[dict]] = {}
    for sess in sessions:
        msgs = c.execute(
            "SELECT seq, role, content, raw, created_at FROM messages "
            "WHERE session_id = ? ORDER BY seq", (sess["id"],)).fetchall()
        all_messages[sess["id"]] = [dict(m) for m in msgs]

    feedback = c.execute(
        """SELECT f.id, f.session_id, f.seq, f.rating, f.created_at
           FROM feedback f JOIN sessions s ON s.id = f.session_id
           WHERE s.user = ? ORDER BY f.created_at""", (username,)).fetchall()

    return {
        "user": dict(user_row),
        "sessions": [dict(s) for s in sessions],
        "messages": all_messages,
        "feedback": [dict(f) for f in feedback],
        "assistants": [a for a in list_assistants() if a.get("owner") == username],
        "exported_at": time.time(),
    }


def delete_user_cascade(username: str) -> dict:
    """级联删除用户及其全部关联数据（GDPR 被遗忘权），返回删除统计。

    删除顺序：feedback_status -> feedback -> messages -> sessions
              -> doc_grants（文档授权）-> users
    保护：不允许删除最后一个管理员，避免系统失去管理入口。
    """
    c = _conn()
    user = c.execute(
        "SELECT username, is_admin FROM users WHERE username = ?",
        (username,)).fetchone()
    if not user:
        return {"error": "User not found"}
    if user["is_admin"]:
        admin_count = c.execute(
            "SELECT COUNT(*) AS cnt FROM users WHERE is_admin = 1").fetchone()["cnt"]
        if admin_count <= 1:
            return {"error": "Cannot delete the last admin"}

    session_ids = [r["id"] for r in c.execute(
        "SELECT id FROM sessions WHERE user = ?", (username,)).fetchall()]
    stats = {"sessions": len(session_ids), "messages": 0, "feedback": 0}
    if session_ids:
        ph = ",".join("?" * len(session_ids))
        c.execute(
            f"DELETE FROM feedback_status WHERE feedback_id IN "
            f"(SELECT id FROM feedback WHERE session_id IN ({ph}))", session_ids)
        cur = c.execute(
            f"DELETE FROM feedback WHERE session_id IN ({ph})", session_ids)
        stats["feedback"] = cur.rowcount
        cur = c.execute(
            f"DELETE FROM messages WHERE session_id IN ({ph})", session_ids)
        stats["messages"] = cur.rowcount
        c.execute(f"DELETE FROM sessions WHERE id IN ({ph})", session_ids)

    # 文档级 ACL 授权记录（acl.py 的 doc_grants 与本表同库）
    has_grants = c.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'doc_grants'"
    ).fetchone()
    if has_grants:
        c.execute("DELETE FROM doc_grants WHERE username = ?", (username,))

    # 该用户名下的自定义助手（默认助手 owner='' 不受影响）
    c.execute("DELETE FROM assistants WHERE owner = ?", (username,))

    c.execute("DELETE FROM users WHERE username = ?", (username,))
    c.commit()
    return {"ok": True, **stats}


def ensure_seed_admin() -> None:
    """无任何账号时播种 admin（密码取 ADMIN_PASSWORD 环境变量，默认 admin123）"""
    if list_users():
        return
    pw = os.getenv("ADMIN_PASSWORD", "admin123")
    create_user("admin", pw)
    set_admin("admin", True)
    _conn().execute("UPDATE users SET must_change_pwd = 1 WHERE username = 'admin'")
    _conn().commit()
    logger.info("已创建初始账号 admin（请尽快用 manage_users reset 修改密码）")



def get_must_change_pwd(username: str) -> bool:
    """Check if user must change password on next login."""
    row = _conn().execute(
        "SELECT must_change_pwd FROM users WHERE username = ?", (username,)
    ).fetchone()
    return bool(row and row["must_change_pwd"])


def change_password(username: str, old_password: str, new_password: str) -> tuple[bool, str]:
    """Change password and clear the must_change_pwd flag. Returns (success, message)."""
    if len(new_password) < 8:
        return False, "新密码至少 8 个字符"
    c = _conn()
    row = c.execute("SELECT pw_hash FROM users WHERE username = ?", (username,)).fetchone()
    if not row or not verify_password(old_password, row["pw_hash"]):
        return False, "原密码错误"
    cur = c.execute(
        "UPDATE users SET pw_hash = ?, must_change_pwd = 0 WHERE username = ? AND pw_hash = ?",
        (_hash_password(new_password), username, row["pw_hash"])
    )
    c.commit()
    if cur.rowcount == 0:
        return False, "密码已被其他请求修改，请重试"
    return True, "密码已修改"


# ---------------------------------------------------------------- 管理后台
def set_admin(username: str, is_admin: bool) -> bool:
    c = _conn()
    cur = c.execute("UPDATE users SET is_admin = ? WHERE username = ?",
                    (1 if is_admin else 0, username))
    c.commit()
    return cur.rowcount > 0


def is_admin(username: str) -> bool:
    row = _conn().execute(
        "SELECT is_admin FROM users WHERE username = ?", (username,)).fetchone()
    return bool(row and row["is_admin"])


def stats_overview() -> dict:
    """看板概览：用户/会话/消息/反馈统计"""
    c = _conn()
    q = lambda sql: c.execute(sql).fetchone()[0]  # noqa: E731
    fb = {r["rating"]: r["n"] for r in c.execute(
        "SELECT rating, COUNT(*) AS n FROM feedback GROUP BY rating")}
    pending = c.execute(
        """SELECT COUNT(*) FROM feedback f
           LEFT JOIN feedback_status fs ON fs.feedback_id = f.id
           WHERE f.rating = 'down' AND COALESCE(fs.status, 'pending') = 'pending'"""
    ).fetchone()[0]
    return {
        "users": q("SELECT COUNT(*) FROM users"),
        "sessions": q("SELECT COUNT(*) FROM sessions"),
        "messages": q("SELECT COUNT(*) FROM messages"),
        "feedback_up": fb.get("up", 0),
        "feedback_down": fb.get("down", 0),
        "badcase_pending": pending,
    }


def list_badcases(limit: int = 100) -> list[dict]:
    """👎 反馈明细（badcase 流转列表）：问题 + 回答节选 + 处理状态"""
    c = _conn()
    rows = c.execute(
        """SELECT f.id, f.session_id, f.seq, f.created_at,
                  s.user, s.title,
                  fs.status, fs.note
           FROM feedback f
           JOIN sessions s ON s.id = f.session_id
           LEFT JOIN feedback_status fs ON fs.feedback_id = f.id
           WHERE f.rating = 'down'
           ORDER BY f.created_at DESC LIMIT ?""", (limit,)).fetchall()
    out = []
    for r in rows:
        ans = c.execute(
            "SELECT content FROM messages WHERE session_id = ? AND seq = ?",
            (r["session_id"], r["seq"])).fetchone()
        ques = c.execute(
            "SELECT content FROM messages WHERE session_id = ? AND seq = ?",
            (r["session_id"], r["seq"] - 1)).fetchone()
        out.append({
            "id": r["id"], "user": r["user"] or "(匿名)", "session": r["session_id"],
            "session_title": r["title"], "status": r["status"] or "pending",
            "note": r["note"] or "",
            "question": (ques["content"] if ques else "")[:100],
            "answer_excerpt": (ans["content"] if ans else "")[:200],
            "created": r["created_at"],
        })
    return out


def set_badcase_status(feedback_id: int, status: str, note: str = "") -> bool:
    c = _conn()
    c.execute(
        """INSERT INTO feedback_status(feedback_id, status, note, updated_at)
           VALUES(?, ?, ?, ?)
           ON CONFLICT(feedback_id) DO UPDATE
           SET status = excluded.status, note = excluded.note,
               updated_at = excluded.updated_at""",
        (feedback_id, status, note, time.time()))
    c.commit()
    return True


def list_all_sessions(limit: int = 100) -> list[dict]:
    """审计：全部用户的会话列表"""
    rows = _conn().execute(
        """SELECT s.id, s.user, s.title, s.updated_at, COUNT(m.id) AS msg_count
           FROM sessions s LEFT JOIN messages m ON m.session_id = s.id
           GROUP BY s.id ORDER BY s.updated_at DESC LIMIT ?""", (limit,)).fetchall()
    return [{"id": r["id"], "user": r["user"] or "(匿名)", "title": r["title"],
             "msg_count": r["msg_count"], "updated_at": r["updated_at"]}
            for r in rows]


def get_messages_full(session_id: str) -> list[dict]:
    """返回会话全部消息的所有字段（不截断 content），供前端完整展示"""
    rows = _conn().execute(
        "SELECT id, seq, role, content, raw, created_at FROM messages WHERE session_id = ? ORDER BY seq",
        (session_id,),
    ).fetchall()
    return [{"id": r["id"], "seq": r["seq"], "role": r["role"],
             "content": r["content"], "raw": r["raw"], "created_at": r["created_at"]}
            for r in rows]


def get_session_messages(session_id: str, excerpt: int = 300) -> list[dict]:
    rows = _conn().execute(
        "SELECT role, content FROM messages WHERE session_id = ? ORDER BY seq",
        (session_id,)).fetchall()
    return [{"role": r["role"], "content": (r["content"] or "")[:excerpt]}
            for r in rows]



def get_suggestions(answer_hash: str) -> list[str] | None:
    """按答案哈希取缓存的动态追问；未命中返回 None"""
    import json as _json
    row = _conn().execute(
        "SELECT items FROM suggestions WHERE answer_hash = ?", (answer_hash,)).fetchone()
    if not row:
        return None
    try:
        return _json.loads(row["items"])
    except _json.JSONDecodeError:
        return None


def save_suggestions(answer_hash: str, items: list[str]) -> None:
    import json as _json
    c = _conn()
    c.execute(
        """INSERT INTO suggestions(answer_hash, items, created_at) VALUES(?,?,?)
           ON CONFLICT(answer_hash) DO UPDATE SET items = excluded.items""",
        (answer_hash, _json.dumps(items, ensure_ascii=False), time.time()))
    c.commit()


# ---------------------------------------------------------------- 多助手 / 知识库
def ensure_default_kb_and_assistant() -> None:
    """幂等播种默认知识库与默认助手"""
    c = _conn()
    if not c.execute("SELECT 1 FROM knowledge_bases WHERE id='default'").fetchone():
        c.execute(
            "INSERT INTO knowledge_bases(id,name,description,doc_dir,created_at) VALUES(?,?,?,?,?)",
            ("default", "默认知识库", "系统内置知识库", config.KNOWLEDGE_DIR, time.time()))
    if not c.execute("SELECT 1 FROM assistants WHERE id='default'").fetchone():
        c.execute(
            "INSERT INTO assistants(id,name,avatar,system_prompt,kb_ids,model_config,owner,created_at,updated_at) "
            "VALUES(?,?,?,?,?,?,?,?,?)",
            ("default", "默认助手", "", "", '["default"]', "{}", "", time.time(), time.time()))
    c.commit()


# ---- Assistants CRUD ----
def list_assistants(owner: str = "") -> list[dict]:
    c = _conn()
    rows = c.execute("SELECT * FROM assistants ORDER BY created_at").fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["kb_ids"] = json.loads(d.get("kb_ids") or "[]")
        out.append(d)
    return out


def get_assistant(aid: str) -> dict | None:
    c = _conn()
    r = c.execute("SELECT * FROM assistants WHERE id=?", (aid,)).fetchone()
    if not r:
        return None
    d = dict(r)
    d["kb_ids"] = json.loads(d.get("kb_ids") or "[]")
    return d


def create_assistant(name: str, owner: str = "", avatar: str = "",
                     system_prompt: str = "", kb_ids: list | None = None,
                     model_config: dict | None = None) -> dict:
    c = _conn()
    aid = str(uuid.uuid4())
    now = time.time()
    c.execute(
        "INSERT INTO assistants(id,name,avatar,system_prompt,kb_ids,model_config,owner,created_at,updated_at) "
        "VALUES(?,?,?,?,?,?,?,?,?)",
        (aid, name, avatar, system_prompt, json.dumps(kb_ids or ["default"]),
         json.dumps(model_config or {}), owner, now, now))
    c.commit()
    return get_assistant(aid)


def update_assistant(aid: str, **fields) -> dict | None:
    if aid == "default" and "name" in fields and not fields.get("name"):
        return None
    c = _conn()
    allowed = {"name", "avatar", "system_prompt", "kb_ids", "model_config"}
    sets, vals = [], []
    for k, v in fields.items():
        if k not in allowed:
            continue
        if k in ("kb_ids", "model_config"):
            v = json.dumps(v)
        sets.append(f"{k}=?")
        vals.append(v)
    if not sets:
        return get_assistant(aid)
    sets.append("updated_at=?")
    vals.append(time.time())
    vals.append(aid)
    c.execute(f"UPDATE assistants SET {','.join(sets)} WHERE id=?", vals)
    c.commit()
    return get_assistant(aid)


def delete_assistant(aid: str) -> bool:
    if aid == "default":
        return False
    c = _conn()
    cur = c.execute("DELETE FROM assistants WHERE id=?", (aid,))
    c.commit()
    return cur.rowcount > 0


# ---- Knowledge bases ----
def list_kbs() -> list[dict]:
    c = _conn()
    return [dict(r) for r in c.execute(
        "SELECT * FROM knowledge_bases ORDER BY created_at").fetchall()]


def get_kb(kb_id: str) -> dict | None:
    c = _conn()
    r = c.execute("SELECT * FROM knowledge_bases WHERE id=?", (kb_id,)).fetchone()
    return dict(r) if r else None


def create_kb(name: str, description: str = "") -> dict:
    c = _conn()
    kb_id = str(uuid.uuid4())
    c.execute(
        "INSERT INTO knowledge_bases(id,name,description,doc_dir,created_at) VALUES(?,?,?,?,?)",
        (kb_id, name, description, f"data/kb_docs/{kb_id}", time.time()))
    c.commit()
    return get_kb(kb_id)


def delete_kb(kb_id: str) -> bool:
    if kb_id == "default":
        return False
    c = _conn()
    cur = c.execute("DELETE FROM knowledge_bases WHERE id=?", (kb_id,))
    c.commit()
    return cur.rowcount > 0


def kb_used_by_assistants(kb_id: str) -> bool:
    """检查是否有助手绑定了该知识库"""
    c = _conn()
    for r in c.execute("SELECT kb_ids FROM assistants").fetchall():
        try:
            if kb_id in json.loads(r["kb_ids"] or "[]"):
                return True
        except Exception:
            continue
    return False


# ---- 个人统计 ----
def stats_for_user(user: str) -> dict:
    """个人看板：累计消息数 / 今日调用 / 待处理 badcase"""
    c = _conn()
    total = c.execute(
        "SELECT COUNT(*) AS n FROM messages m JOIN sessions s ON m.session_id = s.id WHERE s.user = ?",
        (user,)).fetchone()["n"]
    import datetime as _dt
    today_start = _dt.datetime.now().replace(
        hour=0, minute=0, second=0, microsecond=0).timestamp()
    today = c.execute(
        "SELECT COUNT(*) AS n FROM messages m JOIN sessions s ON m.session_id = s.id "
        "WHERE s.user = ? AND m.created_at >= ?", (user, today_start)).fetchone()["n"]
    badcase = c.execute(
        """SELECT COUNT(*) AS n FROM feedback f
           JOIN sessions s ON f.session_id = s.id
           LEFT JOIN feedback_status fs ON fs.feedback_id = f.id
           WHERE s.user = ? AND f.rating = 'down'
             AND COALESCE(fs.status, 'pending') = 'pending'""", (user,)).fetchone()["n"]
    return {"total_messages": total, "today_calls": today, "badcase_pending": badcase}



# ================= 评测集 / 评测运行 =================

def list_eval_datasets() -> list[dict]:
    c = _conn()
    rows = [dict(r) for r in c.execute(
        "SELECT * FROM eval_datasets ORDER BY id").fetchall()]
    for r in rows:
        r["items"] = json.loads(r.get("items") or "[]")
    return rows


def get_eval_dataset(ds_id: int) -> dict | None:
    c = _conn()
    r = c.execute("SELECT * FROM eval_datasets WHERE id=?", (ds_id,)).fetchone()
    if not r:
        return None
    d = dict(r)
    d["items"] = json.loads(d.get("items") or "[]")
    return d


def create_eval_dataset(name: str, kb_id: str, items: list) -> dict:
    c = _conn()
    cur = c.execute(
        "INSERT INTO eval_datasets(name, kb_id, items, created_at) VALUES(?,?,?,?)",
        (name, kb_id or "default", json.dumps(items, ensure_ascii=False), time.time()))
    c.commit()
    return get_eval_dataset(cur.lastrowid)


def update_eval_dataset(ds_id: int, name: str | None = None,
                        kb_id: str | None = None, items: list | None = None) -> dict | None:
    c = _conn()
    if not get_eval_dataset(ds_id):
        return None
    if name is not None:
        c.execute("UPDATE eval_datasets SET name=? WHERE id=?", (name, ds_id))
    if kb_id is not None:
        c.execute("UPDATE eval_datasets SET kb_id=? WHERE id=?", (kb_id, ds_id))
    if items is not None:
        c.execute("UPDATE eval_datasets SET items=? WHERE id=?",
                  (json.dumps(items, ensure_ascii=False), ds_id))
    c.commit()
    return get_eval_dataset(ds_id)


def delete_eval_dataset(ds_id: int) -> bool:
    c = _conn()
    cur = c.execute("DELETE FROM eval_datasets WHERE id=?", (ds_id,))
    c.execute("DELETE FROM eval_runs WHERE dataset_id=?", (ds_id,))
    c.commit()
    return cur.rowcount > 0


def create_eval_run(dataset_id: int, mode: str, top_k: int, created_by: str) -> int:
    c = _conn()
    cur = c.execute(
        """INSERT INTO eval_runs(dataset_id, mode, top_k, status, created_by, created_at)
           VALUES(?,?,?,?,?,?)""",
        (dataset_id, mode, top_k, "pending", created_by, time.time()))
    c.commit()
    return cur.lastrowid


def update_eval_run(run_id: int, **fields) -> None:
    c = _conn()
    allowed = {"status", "recall", "mrr", "total", "hits", "details", "duration_ms"}
    cols, vals = [], []
    for k, v in fields.items():
        if k not in allowed:
            continue
        if k == "details" and not isinstance(v, str):
            v = json.dumps(v, ensure_ascii=False)
        cols.append(f"{k}=?")
        vals.append(v)
    if not cols:
        return
    vals.append(run_id)
    c.execute(f"UPDATE eval_runs SET {', '.join(cols)} WHERE id=?", vals)
    c.commit()


def list_eval_runs(dataset_id: int | None = None, limit: int = 50) -> list[dict]:
    c = _conn()
    if dataset_id:
        rows = c.execute(
            "SELECT * FROM eval_runs WHERE dataset_id=? ORDER BY id DESC LIMIT ?",
            (dataset_id, limit)).fetchall()
    else:
        rows = c.execute(
            "SELECT * FROM eval_runs ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        # 列表视图不带明细（可能很大），只给命中/未命中数
        details = json.loads(d.get("details") or "[]")
        d["miss_count"] = sum(1 for x in details if not x.get("hit_rank"))
        d.pop("details", None)
        out.append(d)
    return out


def get_eval_run(run_id: int) -> dict | None:
    c = _conn()
    r = c.execute("SELECT * FROM eval_runs WHERE id=?", (run_id,)).fetchone()
    if not r:
        return None
    d = dict(r)
    d["details"] = json.loads(d.get("details") or "[]")
    return d


# ================= API Key =================

def _hash_key(key: str) -> str:
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


def create_api_key(name: str, scope_kb_ids: list, created_by: str,
                   expires_at: float | None = None) -> dict:
    """创建 API Key：明文仅本次返回，库里只存前缀 + SHA256 哈希"""
    plain = "dm_" + secrets.token_urlsafe(24)
    c = _conn()
    cur = c.execute(
        """INSERT INTO api_keys(name, prefix, key_hash, scope_kb_ids, created_by,
                                 created_at, expires_at)
           VALUES(?,?,?,?,?,?,?)""",
        (name, plain[:11], _hash_key(plain),
         json.dumps(scope_kb_ids or [], ensure_ascii=False),
         created_by, time.time(), expires_at))
    c.commit()
    row = dict(c.execute("SELECT * FROM api_keys WHERE id=?",
                         (cur.lastrowid,)).fetchone())
    row["scope_kb_ids"] = json.loads(row["scope_kb_ids"] or "[]")
    row["key"] = plain          # 仅此一次出现在响应里
    row.pop("key_hash", None)   # 哈希永不出库
    return row


def list_api_keys() -> list[dict]:
    c = _conn()
    rows = c.execute("SELECT * FROM api_keys ORDER BY id DESC").fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["scope_kb_ids"] = json.loads(d["scope_kb_ids"] or "[]")
        d.pop("key_hash", None)
        now = time.time()
        d["active"] = (d["revoked_at"] is None
                       and (d["expires_at"] is None or d["expires_at"] > now))
        out.append(d)
    return out


def revoke_api_key(key_id: int) -> bool:
    c = _conn()
    cur = c.execute("UPDATE api_keys SET revoked_at=? WHERE id=? AND revoked_at IS NULL",
                    (time.time(), key_id))
    c.commit()
    return cur.rowcount > 0


def validate_api_key(plain: str) -> dict | None:
    """开放接口鉴权：哈希匹配 + 未吊销 + 未过期；返回含 scope 的行"""
    c = _conn()
    r = c.execute("SELECT * FROM api_keys WHERE key_hash=?",
                  (_hash_key(plain),)).fetchone()
    if not r:
        return None
    d = dict(r)
    now = time.time()
    if d["revoked_at"] is not None:
        return None
    if d["expires_at"] is not None and d["expires_at"] <= now:
        return None
    d["scope_kb_ids"] = json.loads(d["scope_kb_ids"] or "[]")
    return d


def touch_api_key(key_id: int) -> None:
    c = _conn()
    c.execute("UPDATE api_keys SET last_used_at=? WHERE id=?", (time.time(), key_id))
    c.commit()


# ================= 入库任务 =================

def create_ingest_task(kb_id: str, filename: str, mode: str, status: str,
                       message: str, created_by: str) -> int:
    c = _conn()
    cur = c.execute(
        """INSERT INTO ingest_tasks(kb_id, filename, mode, status, message,
                                     created_by, created_at, updated_at)
           VALUES(?,?,?,?,?,?,?,?)""",
        (kb_id, filename, mode, status, message, created_by, time.time(), time.time()))
    c.commit()
    return cur.lastrowid


def update_ingest_task(task_id: int, status: str, message: str = "") -> None:
    c = _conn()
    c.execute("UPDATE ingest_tasks SET status=?, message=?, updated_at=? WHERE id=?",
              (status, message, time.time(), task_id))
    c.commit()


def list_ingest_tasks(kb_id: str, limit: int = 50) -> list[dict]:
    c = _conn()
    return [dict(r) for r in c.execute(
        "SELECT * FROM ingest_tasks WHERE kb_id=? ORDER BY id DESC LIMIT ?",
        (kb_id, limit)).fetchall()]


def complete_pending_tasks(kb_id: str) -> None:
    """索引重建成功后：把该库待生效的上传/删除任务标记为完成"""
    c = _conn()
    c.execute(
        """UPDATE ingest_tasks SET status='done', message='索引已生效', updated_at=?
           WHERE kb_id=? AND status='pending' AND mode IN ('upload','delete')""",
        (time.time(), kb_id))
    c.commit()


# ================= 模型配置 =================

def list_models(kind: str | None = None) -> list[dict]:
    c = _conn()
    if kind:
        rows = c.execute("SELECT * FROM models WHERE kind=? ORDER BY id", (kind,)).fetchall()
    else:
        rows = c.execute("SELECT * FROM models ORDER BY kind, id").fetchall()
    out = []
    for r in rows:
        d = dict(r)
        # API Key 脱敏：只保留尾 4 位
        k = d.get("api_key") or ""
        d["api_key_masked"] = ("****" + k[-4:]) if k else ""
        d.pop("api_key", None)
        out.append(d)
    return out


def get_model(model_id: int) -> dict | None:
    c = _conn()
    r = c.execute("SELECT * FROM models WHERE id=?", (model_id,)).fetchone()
    return dict(r) if r else None


def create_model(name: str, kind: str, base_url: str, api_key: str,
                 model_name: str, created_by: str) -> dict:
    c = _conn()
    cur = c.execute(
        """INSERT INTO models(name, kind, base_url, api_key, model_name, is_active,
                               created_by, created_at)
           VALUES(?,?,?,?,?,?,?,?)""",
        (name, kind, base_url, api_key, model_name, 0, created_by, time.time()))
    c.commit()
    return get_model(cur.lastrowid)


def update_model(model_id: int, **fields) -> dict | None:
    c = _conn()
    allowed = {"name", "base_url", "api_key", "model_name"}
    cols, vals = [], []
    for k, v in fields.items():
        if k in allowed and v is not None:
            cols.append(f"{k}=?")
            vals.append(v)
    if cols:
        vals.append(model_id)
        c.execute(f"UPDATE models SET {', '.join(cols)} WHERE id=?", vals)
        c.commit()
    return get_model(model_id)


def delete_model(model_id: int) -> bool:
    c = _conn()
    cur = c.execute("DELETE FROM models WHERE id=?", (model_id,))
    c.commit()
    return cur.rowcount > 0


def set_active_model(model_id: int) -> bool:
    """同 kind 内唯一生效：先清零再置位"""
    c = _conn()
    r = c.execute("SELECT kind FROM models WHERE id=?", (model_id,)).fetchone()
    if not r:
        return False
    c.execute("UPDATE models SET is_active=0 WHERE kind=?", (r["kind"],))
    c.execute("UPDATE models SET is_active=1 WHERE id=?", (model_id,))
    c.commit()
    return True


def get_active_model(kind: str) -> dict | None:
    c = _conn()
    r = c.execute("SELECT * FROM models WHERE kind=? AND is_active=1",
                  (kind,)).fetchone()
    return dict(r) if r else None


# ================= 审计日志 =================

def record_audit(actor: str, action: str, target: str = "", detail: str = "") -> None:
    """记录治理事件；失败静默，绝不影响业务主链路"""
    try:
        c = _conn()
        c.execute(
            "INSERT INTO audit_events(actor, action, target, detail, created_at) VALUES(?,?,?,?,?)",
            (actor or "", action, target or "", str(detail)[:300], time.time()))
        c.commit()
    except Exception:  # noqa: BLE001
        pass


def list_audit(actor: str = "", action: str = "", days: int = 0,
               limit: int = 500) -> list[dict]:
    c = _conn()
    sql = "SELECT * FROM audit_events WHERE 1=1"
    args: list = []
    if actor:
        sql += " AND actor=?"
        args.append(actor)
    if action:
        sql += " AND action=?"
        args.append(action)
    if days > 0:
        sql += " AND created_at>=?"
        args.append(time.time() - days * 86400)
    sql += " ORDER BY id DESC LIMIT ?"
    args.append(max(1, min(limit, 5000)))
    return [dict(r) for r in c.execute(sql, args).fetchall()]


# ================= 告警 =================

def create_alert(type_: str, severity: str, message: str,
                 dedupe_key: str = "") -> int | None:
    """创建告警；同一 dedupe_key 存在 open 告警时跳过（返回 None）避免刷屏"""
    c = _conn()
    if dedupe_key:
        r = c.execute("SELECT id FROM alerts WHERE dedupe_key=? AND status='open'",
                      (dedupe_key,)).fetchone()
        if r:
            return None
    cur = c.execute(
        """INSERT INTO alerts(type, severity, message, dedupe_key, status, created_at)
           VALUES(?,?,?,?, 'open', ?)""",
        (type_, severity, message, dedupe_key, time.time()))
    c.commit()
    return cur.lastrowid


def list_alerts(status: str = "", limit: int = 100) -> list[dict]:
    c = _conn()
    if status:
        rows = c.execute("SELECT * FROM alerts WHERE status=? ORDER BY id DESC LIMIT ?",
                         (status, limit)).fetchall()
    else:
        rows = c.execute("SELECT * FROM alerts ORDER BY id DESC LIMIT ?",
                         (limit,)).fetchall()
    return [dict(r) for r in rows]


def set_alert_status(alert_id: int, status: str) -> bool:
    c = _conn()
    if status == "acknowledged":
        cur = c.execute("UPDATE alerts SET status=?, acked_at=? WHERE id=? AND status='open'",
                        (status, time.time(), alert_id))
    elif status == "resolved":
        cur = c.execute("UPDATE alerts SET status=?, resolved_at=? WHERE id=? AND status!='resolved'",
                        (status, time.time(), alert_id))
    else:
        return False
    c.commit()
    return cur.rowcount > 0


# ================= 外部账号（LDAP 自动开通） =================

def ensure_external_user(username: str) -> None:
    """LDAP 首登自动开通本地账号：随机密码（登录走 LDAP，本地密码永不使用）"""
    c = _conn()
    r = c.execute("SELECT username FROM users WHERE username=?", (username,)).fetchone()
    if r:
        return
    create_user(username, secrets.token_urlsafe(24))


# ================= 用户管理（管理端） =================

def reset_password(username: str, new_password: str) -> tuple[bool, str]:
    """管理员重置密码：无需旧密码，置 must_change_pwd 强制首登修改"""
    if len(new_password) < 8:
        return False, "新密码至少 8 个字符"
    c = _conn()
    cur = c.execute("UPDATE users SET pw_hash=?, must_change_pwd=1 WHERE username=?",
                    (_hash_password(new_password), username))
    c.commit()
    if cur.rowcount == 0:
        return False, "用户不存在"
    return True, "密码已重置，用户下次登录须修改密码"


def count_admins() -> int:
    return _conn().execute("SELECT COUNT(*) FROM users WHERE is_admin=1").fetchone()[0]


def list_users_rich() -> list[dict]:
    """用户富列表：附带会话数与消息数统计"""
    c = _conn()
    rows = c.execute("""
        SELECT u.username, u.is_admin, u.must_change_pwd, u.created_at,
               (SELECT COUNT(*) FROM sessions s WHERE s.user = u.username) AS sessions,
               (SELECT COUNT(*) FROM messages m
                  JOIN sessions s2 ON s2.id = m.session_id
                 WHERE s2.user = u.username) AS messages
        FROM users u ORDER BY u.created_at
    """).fetchall()
    return [dict(r) for r in rows]


def list_user_queries(user: str = "", q: str = "", days: int = 0,
                      limit: int = 500) -> list[dict]:
    """管理员视角：全部用户的提问记录（messages role=user 关联会话）"""
    c = _conn()
    sql = """SELECT s.user AS user, m.session_id AS session_id,
                    s.title AS session_title, m.content AS question,
                    m.created_at AS created_at
             FROM messages m JOIN sessions s ON s.id = m.session_id
             WHERE m.role = 'user'"""
    args: list = []
    if user:
        sql += " AND s.user = ?"
        args.append(user)
    if q:
        sql += " AND m.content LIKE ?"
        args.append(f"%{q}%")
    if days > 0:
        sql += " AND m.created_at >= ?"
        args.append(time.time() - days * 86400)
    sql += " ORDER BY m.created_at DESC LIMIT ?"
    args.append(max(1, min(limit, 2000)))
    return [dict(r) for r in c.execute(sql, args).fetchall()]


# ================= 用户头像 =================

def get_user_avatar(username: str) -> str:
    row = _conn().execute(
        "SELECT avatar FROM users WHERE username = ?", (username,)).fetchone()
    return (row["avatar"] or "") if row else ""


def set_user_avatar(username: str, avatar: str) -> bool:
    cur = _conn().execute(
        "UPDATE users SET avatar = ? WHERE username = ?", (avatar, username))
    _conn().commit()
    return cur.rowcount > 0


# ================= 头像上传审核 =================

def set_pending_avatar(username: str, fname: str) -> bool:
    cur = _conn().execute(
        "UPDATE users SET pending_avatar=?, pending_avatar_at=? WHERE username=?",
        (fname, time.time(), username))
    _conn().commit()
    return cur.rowcount > 0


def clear_pending_avatar(username: str) -> None:
    _conn().execute(
        "UPDATE users SET pending_avatar='', pending_avatar_at=0 WHERE username=?",
        (username,))
    _conn().commit()


def get_pending_avatar(username: str) -> tuple:
    row = _conn().execute(
        "SELECT pending_avatar, pending_avatar_at FROM users WHERE username=?",
        (username,)).fetchone()
    return (row["pending_avatar"] or "", row["pending_avatar_at"] or 0) if row else ("", 0)


def list_pending_avatars() -> list:
    rows = _conn().execute(
        """SELECT username, avatar, pending_avatar, pending_avatar_at
           FROM users WHERE pending_avatar != '' ORDER BY pending_avatar_at""").fetchall()
    return [dict(r) for r in rows]
