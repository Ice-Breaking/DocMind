"""运营面板：统计、badcase 处理、审计事件、告警。

连接经包门面晚绑定获取（store._conn()），便于测试整体替换 DB。"""
import time
from docmind import store

def stats_overview() -> dict:
    """看板概览：用户/会话/消息/反馈统计"""
    c = store._conn()
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
    c = store._conn()
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
    c = store._conn()
    c.execute(
        """INSERT INTO feedback_status(feedback_id, status, note, updated_at)
           VALUES(?, ?, ?, ?)
           ON CONFLICT(feedback_id) DO UPDATE
           SET status = excluded.status, note = excluded.note,
               updated_at = excluded.updated_at""",
        (feedback_id, status, note, time.time()))
    c.commit()
    return True


# ---- 个人统计 ----
def stats_for_user(user: str) -> dict:
    """个人看板：累计消息数 / 今日调用 / 待处理 badcase"""
    c = store._conn()
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


# ================= 审计日志 =================

def record_audit(actor: str, action: str, target: str = "", detail: str = "") -> None:
    """记录治理事件；失败静默，绝不影响业务主链路"""
    try:
        c = store._conn()
        c.execute(
            "INSERT INTO audit_events(actor, action, target, detail, created_at) VALUES(?,?,?,?,?)",
            (actor or "", action, target or "", str(detail)[:300], time.time()))
        c.commit()
    except Exception:  # noqa: BLE001
        pass


def list_audit(actor: str = "", action: str = "", days: int = 0,
               limit: int = 500) -> list[dict]:
    c = store._conn()
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
    c = store._conn()
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
    c = store._conn()
    if status:
        rows = c.execute("SELECT * FROM alerts WHERE status=? ORDER BY id DESC LIMIT ?",
                         (status, limit)).fetchall()
    else:
        rows = c.execute("SELECT * FROM alerts ORDER BY id DESC LIMIT ?",
                         (limit,)).fetchall()
    return [dict(r) for r in rows]


def set_alert_status(alert_id: int, status: str) -> bool:
    c = store._conn()
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
