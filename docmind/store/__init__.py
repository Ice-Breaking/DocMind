"""会话持久化门面（docmind.store）：SQLite 单文件存储（data/chat.db，零依赖标准库）。

由原单文件 store.py 拆分而来：
- 本模块仅保留连接层（DB_PATH/_local/_SCHEMA/_conn，WAL+busy_timeout+thread-local）；
- 业务域拆至子模块（chat/users/assistants/admin/eval/apikeys/ingest/models），
  此处全量再导出——外部 `from docmind import store; store.xxx(...)` 用法不变；
- 子模块经 `store._conn()` 晚绑定取连接，测试 monkeypatch 本模块的
  DB_PATH/_local 即可整体替换存储位置（与拆分前语义一致）。
"""
import logging
import os
import sqlite3
import threading

from docmind import config

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
    msg_count INTEGER DEFAULT 0,
    last_msg TEXT DEFAULT '',
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
    ip TEXT DEFAULT '',
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
CREATE TABLE IF NOT EXISTS auth_tokens(
    token_hash TEXT PRIMARY KEY,
    username TEXT NOT NULL,
    expires_at REAL
);
CREATE INDEX IF NOT EXISTS idx_authtokens_user ON auth_tokens(username);
CREATE INDEX IF NOT EXISTS idx_authtokens_expiry ON auth_tokens(expires_at);
"""


_schema_lock = threading.Lock()
_schema_ready_for: str | None = None   # 已完成 schema 初始化的 DB 路径


def _init_schema(conn: sqlite3.Connection) -> None:
    """建表 + 向前兼容迁移（幂等，进程内每 DB 路径只执行一次）。

    原实现每次新建连接都重放全量 DDL（≈175 行 executescript + 多次
    PRAGMA table_info）——SSE 每请求新起线程 → 每条聊天消息都白付
    一次建表开销，executescript 还隐式 COMMIT 加剧锁竞争。"""
    conn.executescript(_SCHEMA)
    # (session_id, seq) 唯一索引：并发 append 时 MAX(seq)+1 可能撞号，
    # 由唯一约束兜底（append_message 捕获后重算重试）。旧库存量重复
    # 数据会导致建索引失败——容忍跳过，不做破坏性迁移
    try:
        conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS "
                     "idx_messages_session_seq ON messages(session_id, seq)")
    except sqlite3.DatabaseError:
        logging.getLogger(__name__).warning(
            "messages(session_id, seq) 存量数据有重复，唯一索引未建立")
    # 动态列补充（向前兼容旧数据库）
    cols = [r["name"] for r in conn.execute("PRAGMA table_info(messages)")]
    if "raw" not in cols:
        conn.execute("ALTER TABLE messages ADD COLUMN raw TEXT DEFAULT ''")
    s_cols = [r["name"] for r in conn.execute("PRAGMA table_info(sessions)")]
    if "user" not in s_cols:
        conn.execute("ALTER TABLE sessions ADD COLUMN user TEXT DEFAULT ''")
    if "assistant_id" not in s_cols:
        conn.execute("ALTER TABLE sessions ADD COLUMN assistant_id TEXT DEFAULT ''")
    # msg_count/last_msg 冗余列：list_sessions 原先对每会话做相关子查询
    # （COUNT + 取末条消息），消息量增长后侧边栏线性变慢；改为写入时
    # 维护、查询直读。存量库 ALTER 后回填一次
    if "msg_count" not in s_cols or "last_msg" not in s_cols:
        if "msg_count" not in s_cols:
            conn.execute("ALTER TABLE sessions ADD COLUMN msg_count INTEGER DEFAULT 0")
        if "last_msg" not in s_cols:
            conn.execute("ALTER TABLE sessions ADD COLUMN last_msg TEXT DEFAULT ''")
        conn.execute("""
            UPDATE sessions SET
                msg_count = (SELECT COUNT(*) FROM messages
                              WHERE messages.session_id = sessions.id),
                last_msg = COALESCE((SELECT substr(REPLACE(
                        COALESCE(m2.raw, m2.content), char(10), ' '), 1, 60)
                    FROM messages m2 WHERE m2.session_id = sessions.id
                    ORDER BY m2.seq DESC LIMIT 1), '')
        """)
    u_cols = [r["name"] for r in conn.execute("PRAGMA table_info(users)")]
    if "is_admin" not in u_cols:
        conn.execute("ALTER TABLE users ADD COLUMN is_admin INTEGER DEFAULT 0")
    if "must_change_pwd" not in u_cols:
        conn.execute("ALTER TABLE users ADD COLUMN must_change_pwd INTEGER DEFAULT 0")
    if "avatar" not in u_cols:
        conn.execute("ALTER TABLE users ADD COLUMN avatar TEXT DEFAULT ''")
    if "pending_avatar" not in u_cols:
        conn.execute("ALTER TABLE users ADD COLUMN pending_avatar TEXT DEFAULT ''")
    if "pending_avatar_at" not in u_cols:
        conn.execute("ALTER TABLE users ADD COLUMN pending_avatar_at REAL DEFAULT 0")
    # 审计表补来源 IP 列（二轮回归 P2）：安全事件需按 IP 溯源；
    # 存量库走 ALTER 向前兼容，新建库由上方 schema 直接携带
    a_cols = [r["name"] for r in conn.execute("PRAGMA table_info(audit_events)")]
    if "ip" not in a_cols:
        conn.execute("ALTER TABLE audit_events ADD COLUMN ip TEXT DEFAULT ''")
    conn.commit()


def _conn() -> sqlite3.Connection:
    conn = getattr(_local, "conn", None)
    if conn is None:
        os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
        conn = sqlite3.connect(DB_PATH)
        conn.execute("PRAGMA journal_mode=WAL")
        # WAL 下 NORMAL 安全（崩溃不丢已 checkpoint 数据）且大幅减少 fsync；
        # FULL 的额外持久性保证对聊天场景收益有限
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA busy_timeout=5000")
        conn.row_factory = sqlite3.Row
        global _schema_ready_for
        with _schema_lock:
            # 仅首个连接执行 schema 初始化（按路径判定：测试 monkeypatch
            # DB_PATH 切库后自动对新库重新初始化）
            if _schema_ready_for != DB_PATH:
                _init_schema(conn)
                _schema_ready_for = DB_PATH
        _local.conn = conn
    return conn


# ---- 业务域再导出（门面）----
from docmind.store.chat import (
    append_exchange, append_message, delete_session, get_feedback, get_messages_full, get_session_messages, get_suggestions, list_all_sessions, list_sessions, load_pairs_with_images, load_raw_pairs, load_session, save_feedback, save_suggestions, session_owner,
)
from docmind.store.users import (
    _hash_password, change_password, clear_pending_avatar, count_admins, create_user, delete_user, delete_user_cascade, ensure_external_user, ensure_seed_admin, export_user_data, get_must_change_pwd, get_pending_avatar, get_user_avatar, is_admin, list_pending_avatars, list_user_queries, list_users, list_users_rich, reset_password, set_admin, set_password, set_pending_avatar, set_user_avatar, verify_password, verify_user,
)
from docmind.store.assistants import (
    create_assistant, create_kb, delete_assistant, delete_kb, ensure_default_kb_and_assistant, get_assistant, get_kb, kb_used_by_assistants, list_assistants, list_kbs, rename_kb, update_assistant,
)
from docmind.store.admin import (
    create_alert, list_alerts, list_audit, list_badcases, record_audit, set_alert_status, set_badcase_status, stats_for_user, stats_overview,
)
from docmind.store.eval import (
    create_eval_dataset, create_eval_run, delete_eval_dataset, get_eval_dataset, get_eval_run, list_eval_datasets, list_eval_runs, update_eval_dataset, update_eval_run,
)
from docmind.store.apikeys import (
    _hash_key, create_api_key, list_api_keys, revoke_api_key, touch_api_key, validate_api_key,
)
from docmind.store.ingest import (
    complete_pending_tasks, create_ingest_task, list_ingest_tasks, update_ingest_task,
)
from docmind.store.models import (
    create_model, delete_model, get_active_model, get_model, list_models, set_active_model, update_model,
)

__all__ = ['_hash_key', '_hash_password', 'append_exchange', 'append_message', 'change_password', 'clear_pending_avatar', 'complete_pending_tasks', 'count_admins', 'create_alert', 'create_api_key', 'create_assistant', 'create_eval_dataset', 'create_eval_run', 'create_ingest_task', 'create_kb', 'create_model', 'create_user', 'delete_assistant', 'delete_eval_dataset', 'delete_kb', 'delete_model', 'delete_session', 'delete_user', 'delete_user_cascade', 'ensure_default_kb_and_assistant', 'ensure_external_user', 'ensure_seed_admin', 'export_user_data', 'get_active_model', 'get_assistant', 'get_eval_dataset', 'get_eval_run', 'get_feedback', 'get_kb', 'get_messages_full', 'get_model', 'get_must_change_pwd', 'get_pending_avatar', 'get_session_messages', 'get_suggestions', 'get_user_avatar', 'is_admin', 'kb_used_by_assistants', 'list_alerts', 'list_all_sessions', 'list_api_keys', 'list_assistants', 'list_audit', 'list_badcases', 'list_eval_datasets', 'list_eval_runs', 'list_ingest_tasks', 'list_kbs', 'list_models', 'list_pending_avatars', 'list_sessions', 'list_user_queries', 'list_users', 'list_users_rich', 'load_pairs_with_images', 'load_raw_pairs', 'load_session', 'record_audit', 'rename_kb', 'reset_password', 'revoke_api_key', 'save_feedback', 'save_suggestions', 'session_owner', 'set_active_model', 'set_admin', 'set_alert_status', 'set_badcase_status', 'set_password', 'set_pending_avatar', 'set_user_avatar', 'stats_for_user', 'stats_overview', 'touch_api_key', 'update_assistant', 'update_eval_dataset', 'update_eval_run', 'update_ingest_task', 'update_model', 'validate_api_key', 'verify_password', 'verify_user']
