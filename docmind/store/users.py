"""用户认证与账号管理：pbkdf2 口令、管理员、待审头像、数据导出/级联删除。

连接经包门面晚绑定获取（store._conn()），便于测试整体替换 DB。"""
import time
import secrets
import hashlib
import sqlite3
import os
from docmind import store
import logging

logger = logging.getLogger(__name__)

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
    c = store._conn()
    try:
        c.execute("INSERT INTO users(username, pw_hash, created_at) VALUES(?,?,?)",
                  (username, _hash_password(password), time.time()))
        c.commit()
        return True
    except sqlite3.IntegrityError:
        return False


def set_password(username: str, password: str) -> bool:
    c = store._conn()
    cur = c.execute("UPDATE users SET pw_hash = ? WHERE username = ?",
                    (_hash_password(password), username))
    c.commit()
    return cur.rowcount > 0


def verify_user(username: str, password: str) -> bool:
    row = store._conn().execute(
        "SELECT pw_hash FROM users WHERE username = ?", (username,)).fetchone()
    return bool(row) and verify_password(password, row["pw_hash"])


def list_users() -> list[dict]:
    rows = store._conn().execute(
        "SELECT username, created_at FROM users ORDER BY created_at").fetchall()
    return [{"username": r["username"], "created_at": r["created_at"]} for r in rows]


def delete_user(username: str) -> bool:
    c = store._conn()
    cur = c.execute("DELETE FROM users WHERE username = ?", (username,))
    c.commit()
    return cur.rowcount > 0


# ---------------------------------------------------------------- GDPR 合规
def export_user_data(username: str) -> dict:
    """导出用户全部关联数据（GDPR 数据可携带权）：账号/会话/消息/反馈"""
    c = store._conn()
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
        "assistants": [a for a in store.list_assistants() if a.get("owner") == username],
        "exported_at": time.time(),
    }


def delete_user_cascade(username: str) -> dict:
    """级联删除用户及其全部关联数据（GDPR 被遗忘权），返回删除统计。

    删除顺序：feedback_status -> feedback -> messages -> sessions
              -> doc_grants（文档授权）-> users
    保护：不允许删除最后一个管理员，避免系统失去管理入口。
    """
    c = store._conn()
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
    """无任何账号时播种 admin；密码必须经 ADMIN_PASSWORD 显式提供。

    安全约束：不再回退弱默认密码 admin123——空库且未配置时直接拒绝启动，
    防止新环境带着众所周知的默认凭据上线（红队实测可一键接管全站）。"""
    if list_users():
        return
    pw = os.getenv("ADMIN_PASSWORD", "")
    if not pw:
        raise RuntimeError(
            "检测到空用户库且未设置 ADMIN_PASSWORD：拒绝以默认密码创建管理员。"
            "请在 .env 中设置强密码（可用 openssl rand -base64 18 生成）后重启")
    create_user("admin", pw)
    set_admin("admin", True)
    store._conn().execute("UPDATE users SET must_change_pwd = 1 WHERE username = 'admin'")
    store._conn().commit()
    logger.info("已创建初始账号 admin（首登强制改密）")


def get_must_change_pwd(username: str) -> bool:
    """Check if user must change password on next login."""
    row = store._conn().execute(
        "SELECT must_change_pwd FROM users WHERE username = ?", (username,)
    ).fetchone()
    return bool(row and row["must_change_pwd"])


def change_password(username: str, old_password: str, new_password: str) -> tuple[bool, str]:
    """Change password and clear the must_change_pwd flag. Returns (success, message)."""
    if len(new_password) < 8:
        return False, "新密码至少 8 个字符"
    c = store._conn()
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
    c = store._conn()
    cur = c.execute("UPDATE users SET is_admin = ? WHERE username = ?",
                    (1 if is_admin else 0, username))
    c.commit()
    return cur.rowcount > 0


def is_admin(username: str) -> bool:
    row = store._conn().execute(
        "SELECT is_admin FROM users WHERE username = ?", (username,)).fetchone()
    return bool(row and row["is_admin"])


# ================= 外部账号（LDAP 自动开通） =================

def ensure_external_user(username: str) -> None:
    """LDAP 首登自动开通本地账号：随机密码（登录走 LDAP，本地密码永不使用）"""
    c = store._conn()
    r = c.execute("SELECT username FROM users WHERE username=?", (username,)).fetchone()
    if r:
        return
    create_user(username, secrets.token_urlsafe(24))


# ================= 用户管理（管理端） =================

def reset_password(username: str, new_password: str) -> tuple[bool, str]:
    """管理员重置密码：无需旧密码，置 must_change_pwd 强制首登修改"""
    if len(new_password) < 8:
        return False, "新密码至少 8 个字符"
    c = store._conn()
    cur = c.execute("UPDATE users SET pw_hash=?, must_change_pwd=1 WHERE username=?",
                    (_hash_password(new_password), username))
    c.commit()
    if cur.rowcount == 0:
        return False, "用户不存在"
    return True, "密码已重置，用户下次登录须修改密码"


def count_admins() -> int:
    return store._conn().execute("SELECT COUNT(*) FROM users WHERE is_admin=1").fetchone()[0]


def list_users_rich() -> list[dict]:
    """用户富列表：附带会话数与消息数统计"""
    c = store._conn()
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
    c = store._conn()
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
    row = store._conn().execute(
        "SELECT avatar FROM users WHERE username = ?", (username,)).fetchone()
    return (row["avatar"] or "") if row else ""


def set_user_avatar(username: str, avatar: str) -> bool:
    cur = store._conn().execute(
        "UPDATE users SET avatar = ? WHERE username = ?", (avatar, username))
    store._conn().commit()
    return cur.rowcount > 0


# ================= 头像上传审核 =================

def set_pending_avatar(username: str, fname: str) -> bool:
    cur = store._conn().execute(
        "UPDATE users SET pending_avatar=?, pending_avatar_at=? WHERE username=?",
        (fname, time.time(), username))
    store._conn().commit()
    return cur.rowcount > 0


def clear_pending_avatar(username: str) -> None:
    store._conn().execute(
        "UPDATE users SET pending_avatar='', pending_avatar_at=0 WHERE username=?",
        (username,))
    store._conn().commit()


def get_pending_avatar(username: str) -> tuple:
    row = store._conn().execute(
        "SELECT pending_avatar, pending_avatar_at FROM users WHERE username=?",
        (username,)).fetchone()
    return (row["pending_avatar"] or "", row["pending_avatar_at"] or 0) if row else ("", 0)


def list_pending_avatars() -> list:
    rows = store._conn().execute(
        """SELECT username, avatar, pending_avatar, pending_avatar_at
           FROM users WHERE pending_avatar != '' ORDER BY pending_avatar_at""").fetchall()
    return [dict(r) for r in rows]
