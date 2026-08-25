"""会话/消息/反馈/追问建议：聊天持久化核心。

连接经包门面晚绑定获取（store._conn()），便于测试整体替换 DB。"""
import os
import sqlite3
import time
from docmind import store


def _gen_title(content: str) -> str:
    """首条 user 消息 → 会话标题：剥离图片 markdown（/files/uploads 短链与
    data URL 两种形式均覆盖，右括号可选以兼容历史 30 字截断残串），折叠
    空白后取前 30 字；纯图消息标题即 [图片]。列表/审计页会直接渲染标题
    文本，源码串露出既难看又占宽。（2026-08-24 修复：原建会话分支直接
    content[:30] 漏清洗；补标题分支 re.sub 少传 string 参数，走到必 TypeError）"""
    import re as _re
    clean = _re.sub(r"!\[[^\]]*\]\([^)]*\)?", "[图片]", content or "")
    clean = _re.sub(r"\s+", " ", clean).strip()
    return clean[:30] or "[图片]"


def _append_message_conn(c, session_id: str, role: str, content: str,
                         raw: str | None, user: str | None,
                         assistant_id: str, now: float) -> int:
    """在给定连接上追加一条消息（不 commit，由调用方控制事务边界）。

    调用方持写锁（BEGIN IMMEDIATE）时 MAX(seq)+1 与 INSERT 之间
    不可能有其他写者插入，序号计算天然原子。"""
    seq = c.execute(
        "SELECT COALESCE(MAX(seq), -1) + 1 FROM messages WHERE session_id = ?",
        (session_id,),
    ).fetchone()[0]
    try:
        c.execute(
            "INSERT INTO messages(session_id, seq, role, content, raw, created_at) VALUES(?,?,?,?,?,?)",
            (session_id, seq, role, content, raw if raw is not None else content, now),
        )
    except sqlite3.IntegrityError:
        # 并发窗口另一请求已插入同 seq（idx_messages_session_seq 唯一约束
        # 兜底触发，未持写锁的调用方路径）：重算序号再插一次；再撞则让异常上抛
        seq = c.execute(
            "SELECT COALESCE(MAX(seq), -1) + 1 FROM messages WHERE session_id = ?",
            (session_id,),
        ).fetchone()[0]
        c.execute(
            "INSERT INTO messages(session_id, seq, role, content, raw, created_at) VALUES(?,?,?,?,?,?)",
            (session_id, seq, role, content, raw if raw is not None else content, now),
        )
    row = c.execute("SELECT title, user FROM sessions WHERE id = ?", (session_id,)).fetchone()
    # 冗余列维护：msg_count/last_msg 写入时更新（list_sessions 直读，
    # 免去每会话相关子查询随消息量线性恶化）
    last_msg = (raw if raw is not None else content or "").replace("\n", " ")[:60]
    if row is None:
        c.execute(
            "INSERT INTO sessions(id, title, user, msg_count, last_msg, assistant_id, created_at, updated_at) VALUES(?,?,?,?,?,?,?,?)",
            (session_id, _gen_title(content) if role == "user" else "",
             user or "", 1, last_msg, assistant_id, now, now),
        )
    else:
        if not row["title"] and role == "user":
            # 补标题：会话先由 assistant 消息创建时 title 为空，首条 user
            # 消息到达时在此清洗落库
            c.execute("UPDATE sessions SET title = ? WHERE id = ?",
                      (_gen_title(content), session_id))
        if not row["user"] and user:
            c.execute("UPDATE sessions SET user = ? WHERE id = ?", (user, session_id))
        c.execute("UPDATE sessions SET msg_count = msg_count + 1, last_msg = ?, "
                  "updated_at = ? WHERE id = ?", (last_msg, now, session_id))
    return seq


def append_message(session_id: str, role: str, content: str, raw: str | None = None,
                   user: str | None = None, assistant_id: str = "") -> int:
    """追加一条消息，返回其在会话内的序号（从 0 起）。

    content 为展示内容（含思维链/引用标记等渲染格式）；
    raw 为干净文本（assistant 的纯净回答），用于切换会话时恢复 LLM 多轮上下文。
    assistant_id：新建会话时归属的助手（空串=默认助手），已存在会话不受影响。
    """
    c = store._conn()
    # BEGIN IMMEDIATE： upfront 取写锁，MAX(seq)+1 与 INSERT 原子化
    # （默认 deferred 事务下 SELECT 不加锁，两写者可同时算出同一 seq）
    if not c.in_transaction:
        c.execute("BEGIN IMMEDIATE")
    try:
        seq = _append_message_conn(c, session_id, role, content, raw,
                                   user, assistant_id, time.time())
        c.commit()
        return seq
    except Exception:
        if c.in_transaction:
            c.rollback()
        raise


def append_exchange(session_id: str, user_content: str, user_raw: str,
                    assistant_content: str, user: str | None = None,
                    assistant_id: str = "") -> None:
    """一轮问答（user + assistant 两条消息）单事务落库。

    原先两条消息各自独立 commit：进程在两次 commit 之间崩溃会留下
    只有半轮的会话（多轮上下文重建时 user 悬空）；单事务保证原子。"""
    c = store._conn()
    if not c.in_transaction:
        c.execute("BEGIN IMMEDIATE")
    try:
        now = time.time()
        _append_message_conn(c, session_id, "user", user_content, user_raw,
                             user, assistant_id, now)
        _append_message_conn(c, session_id, "assistant", assistant_content,
                             assistant_content, user, assistant_id, now)
        c.commit()
    except Exception:
        if c.in_transaction:
            c.rollback()
        raise


def load_session(session_id: str) -> list[dict]:
    """按序返回会话消息 [{role, content}]（Gradio messages 格式，可直接回填 Chatbot）"""
    c = store._conn()
    rows = c.execute(
        "SELECT role, content FROM messages WHERE session_id = ? ORDER BY seq",
        (session_id,),
    ).fetchall()
    return [{"role": r["role"], "content": r["content"]} for r in rows]


def save_feedback(session_id: str, seq: int, rating: str) -> None:
    """保存/覆盖某条消息的评价（up/down）"""
    c = store._conn()
    c.execute(
        """INSERT INTO feedback(session_id, seq, rating, created_at) VALUES(?,?,?,?)
           ON CONFLICT(session_id, seq) DO UPDATE
           SET rating = excluded.rating, created_at = excluded.created_at""",
        (session_id, seq, rating, time.time()),
    )
    c.commit()


def get_feedback(session_id: str) -> dict:
    """返回 {消息序号(str): rating}，供前端恢复选中态"""
    c = store._conn()
    rows = c.execute(
        "SELECT seq, rating FROM feedback WHERE session_id = ?", (session_id,)
    ).fetchall()
    return {str(r["seq"]): r["rating"] for r in rows}


def load_raw_pairs(session_id: str) -> list[tuple[str, str]]:
    """按序返回 [(role, raw)]，供恢复 LLM 多轮上下文（过滤空 raw）"""
    c = store._conn()
    rows = c.execute(
        "SELECT role, raw FROM messages WHERE session_id = ? ORDER BY seq",
        (session_id,),
    ).fetchall()
    return [(r["role"], r["raw"]) for r in rows if r["raw"]]


def load_pairs_with_images(session_id: str) -> list[tuple[str, str, str | None]]:
    """按序返回 [(role, raw, image_path)]，多轮上下文重建用。

    image_path：该轮 user 消息携带的附件路径（/files/uploads/…，从
    content 的 markdown 提取）；无图为 None。追问图片细节（"右边那张
    是什么"）时模型需要重新看到图——历史重建须回填多模态消息。"""
    import re as _re
    _IMG_RE = _re.compile(r'!\[图片\]\((/files/uploads/[^)]+)\)')
    c = store._conn()
    rows = c.execute(
        "SELECT role, raw, content FROM messages WHERE session_id = ? ORDER BY seq",
        (session_id,),
    ).fetchall()
    out = []
    for r in rows:
        raw = r["raw"] or ""
        m = _IMG_RE.search(r["content"] or "")
        out.append((r["role"], raw, m.group(1) if m else None))
    return out


def list_sessions(user: str | None = None, limit: int = 50,
                  assistant_id: str | None = None,
                  offset: int = 0) -> list[dict]:
    """会话列表（按最近活跃倒序）：只看本人会话 + 尚未归属的历史会话（打开即认领）

    assistant_id 为 None 时行为与旧版完全一致；指定时额外按助手过滤。
    返回项含 assistant_id（空值归一为 "default"）。
    msg_count/last_msg 直读 sessions 冗余列（写入时维护）——
    原先每会话相关子查询随消息量线性恶化。"""
    c = store._conn()
    sql = """SELECT s.id, s.title, s.updated_at, s.assistant_id,
             COALESCE(s.msg_count, 0) AS msg_count, COALESCE(s.last_msg, '') AS last_msg
           FROM sessions s
           WHERE (s.user = '' OR s.user = ?)"""
    params: list = [user or ""]
    if assistant_id is not None:
        sql += " AND s.assistant_id = ?"
        params.append(assistant_id)
    sql += " ORDER BY s.updated_at DESC LIMIT ? OFFSET ?"
    params.extend([limit, max(0, offset)])
    rows = c.execute(sql, params).fetchall()
    return [{"id": r["id"], "title": r["title"], "msg_count": r["msg_count"],
             "updated_at": r["updated_at"], "last_msg": r["last_msg"] or "",
             "assistant_id": (r["assistant_id"] or "default") if "assistant_id" in r.keys() else "default"}
            for r in rows]


def session_owner(session_id: str) -> str | None:
    """返回会话所属用户；会话不存在返回 None"""
    row = store._conn().execute(
        "SELECT user FROM sessions WHERE id = ?", (session_id,)).fetchone()
    return row["user"] if row else None


def delete_session(session_id: str) -> None:
    """删除会话及其消息与反馈"""
    c = store._conn()
    c.execute("DELETE FROM messages WHERE session_id = ?", (session_id,))
    c.execute("DELETE FROM feedback WHERE session_id = ?", (session_id,))
    c.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
    c.commit()


def list_all_sessions(limit: int = 100, offset: int = 0) -> list[dict]:
    """审计：全部用户的会话列表（first_image：首条 user 消息携带的
    图片 URL，审计页标题列渲染缩略图供直接查看）"""
    import re as _re_img
    _img_re = _re_img.compile(r'!\[[^\]]*\]\((/files/uploads/[^)]+)\)')
    rows = store._conn().execute(
        """SELECT s.id, s.user, s.title, s.updated_at, COUNT(m.id) AS msg_count,
               (SELECT m2.content FROM messages m2
                 WHERE m2.session_id = s.id AND m2.role = 'user'
                 ORDER BY m2.seq LIMIT 1) AS first_user_content
           FROM sessions s LEFT JOIN messages m ON m.session_id = s.id
           GROUP BY s.id ORDER BY s.updated_at DESC LIMIT ? OFFSET ?""",
           (limit, max(0, offset))).fetchall()
    out = []
    # uploads 目录与 docs_api._UPLOADS_DIR 同布局（容器/本地 CWD 均为项目根）
    _uploads_dir = os.path.join("data", "uploads")
    for r in rows:
        m = _img_re.search(r["first_user_content"] or "")
        img = m.group(1) if m else ""
        if img:
            # 文件已被清理/丢失时返回空串：审计页按 first_image 条件渲染
            # 缩略图，空串不渲染 <Image>，避免破图图标（实测 e2e 会话
            # 附件被清理后审计列表长期挂破图）
            if not os.path.isfile(os.path.join(_uploads_dir,
                                               os.path.basename(img))):
                img = ""
        out.append({"id": r["id"], "user": r["user"] or "(匿名)", "title": r["title"],
                    "msg_count": r["msg_count"], "updated_at": r["updated_at"],
                    "first_image": img})
    return out


def get_messages_full(session_id: str) -> list[dict]:
    """返回会话全部消息的所有字段（不截断 content），供前端完整展示"""
    rows = store._conn().execute(
        "SELECT id, seq, role, content, raw, created_at FROM messages WHERE session_id = ? ORDER BY seq",
        (session_id,),
    ).fetchall()
    return [{"id": r["id"], "seq": r["seq"], "role": r["role"],
             "content": r["content"], "raw": r["raw"], "created_at": r["created_at"]}
            for r in rows]


def get_session_messages(session_id: str, excerpt: int = 300) -> list[dict]:
    rows = store._conn().execute(
        "SELECT role, content FROM messages WHERE session_id = ? ORDER BY seq",
        (session_id,)).fetchall()
    return [{"role": r["role"], "content": (r["content"] or "")[:excerpt]}
            for r in rows]


def get_suggestions(answer_hash: str) -> list[str] | None:
    """按答案哈希取缓存的动态追问；未命中返回 None"""
    import json as _json
    row = store._conn().execute(
        "SELECT items FROM suggestions WHERE answer_hash = ?", (answer_hash,)).fetchone()
    if not row:
        return None
    try:
        return _json.loads(row["items"])
    except _json.JSONDecodeError:
        return None


def save_suggestions(answer_hash: str, items: list[str]) -> None:
    import json as _json
    c = store._conn()
    c.execute(
        """INSERT INTO suggestions(answer_hash, items, created_at) VALUES(?,?,?)
           ON CONFLICT(answer_hash) DO UPDATE SET items = excluded.items""",
        (answer_hash, _json.dumps(items, ensure_ascii=False), time.time()))
    c.commit()
