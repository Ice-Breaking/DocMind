"""文档级 ACL：知识库按用户授权访问（默认公开 + 按文档限制）。

模型：
- 文档默认公开（docs_meta 无记录或非 restricted）
- 标记 restricted 后，仅被显式 grant 的用户可见
- 检索链路按当前用户过滤来源（见 hybrid.search 的 allowed_sources）

安全要点（面试可讲）：
- 未授权文档被检索过滤后，工具返回与"真没有"完全相同的无命中话术——
  不泄露受限文档的存在性
- 语义缓存联动：引用了受限文档的答案不入缓存；命中缓存时若当前用户
  无权访问答案引用的受限文档，视为未命中（防跨用户泄露）
"""
import os
import re
import sqlite3
import threading

from docmind import config
from docmind.rag.chunker import SUPPORTED_EXTS

DB_PATH = os.path.join(config.PROJECT_ROOT, "data", "chat.db")
_local = threading.local()

_SCHEMA = """
CREATE TABLE IF NOT EXISTS docs_meta(
    doc_name TEXT PRIMARY KEY,
    restricted INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS doc_grants(
    username TEXT NOT NULL,
    doc_name TEXT NOT NULL,
    UNIQUE(username, doc_name)
);
"""

# 从答案里抽取 [来源: 文件名] / [来源: 文件名 · 第N页] 中的文件名
_SOURCE_RE = re.compile(
    r"\[来源: ([^\]·\n]+?\.(?:md|pdf|docx|xlsx|txt|png|jpg|jpeg|webp))")


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


# ---------------- 当前用户（线程级上下文，Gradio 每请求一线程） ----------------
def set_current_user(username: str) -> None:
    _local.current_user = username or ""


def get_current_user() -> str:
    return getattr(_local, "current_user", "")


# ---------------- 文档清单与限制标记 ----------------
def all_docs() -> list[str]:
    """知识库目录内的受支持文档文件名"""
    root = config.KNOWLEDGE_DIR
    if not os.path.isdir(root):
        return []
    return sorted(
        n for n in os.listdir(root)
        if os.path.splitext(n)[1].lower() in SUPPORTED_EXTS
    )


def is_restricted(doc_name: str) -> bool:
    row = _conn().execute(
        "SELECT restricted FROM docs_meta WHERE doc_name = ?", (doc_name,)).fetchone()
    return bool(row and row["restricted"])


def set_restricted(doc_name: str, restricted: bool) -> None:
    c = _conn()
    c.execute(
        """INSERT INTO docs_meta(doc_name, restricted) VALUES(?, ?)
           ON CONFLICT(doc_name) DO UPDATE SET restricted = excluded.restricted""",
        (doc_name, 1 if restricted else 0),
    )
    c.commit()


def grant(username: str, doc_name: str) -> None:
    c = _conn()
    c.execute("INSERT OR IGNORE INTO doc_grants(username, doc_name) VALUES(?, ?)",
              (username, doc_name))
    c.commit()


def revoke(username: str, doc_name: str) -> None:
    c = _conn()
    c.execute("DELETE FROM doc_grants WHERE username = ? AND doc_name = ?",
              (username, doc_name))
    c.commit()


def grants_for(doc_name: str) -> list[str]:
    rows = _conn().execute(
        "SELECT username FROM doc_grants WHERE doc_name = ? ORDER BY username",
        (doc_name,)).fetchall()
    return [r["username"] for r in rows]


def list_acl() -> list[dict]:
    """全部文档的 ACL 状态（供 CLI/管理展示）"""
    return [{"doc": d, "restricted": is_restricted(d), "grants": grants_for(d)}
            for d in all_docs()]


# ---------------- 授权判定 ----------------
def allowed_docs(username: str) -> set[str]:
    """用户可见文档集合：公开文档 + 显式授权的限制文档"""
    c = _conn()
    granted = {r["doc_name"] for r in c.execute(
        "SELECT doc_name FROM doc_grants WHERE username = ?", (username or "",))}
    return {d for d in all_docs() if not is_restricted(d) or d in granted}


def extract_sources(text: str) -> list[str]:
    """从答案文本抽取引用的文件名"""
    return _SOURCE_RE.findall(text or "")


def answer_allowed(answer_text: str, username: str) -> bool:
    """答案引用的受限文档当前用户是否全部有权（语义缓存防跨用户泄露用）。

    按「是否 restricted」精确判定而非要求来源 ∈ 默认库白名单：
    非默认知识库（data/kb_docs/<kb_id>/）的文档不在 all_docs() 清单里，
    但它们不受 ACL 管辖（默认公开）——按白名单判定会把多库答案一律
    判为无权，语义缓存对多库用户永久失效。"""
    c = _conn()
    granted = {r["doc_name"] for r in c.execute(
        "SELECT doc_name FROM doc_grants WHERE username = ?", (username or "",))}
    return all(not is_restricted(src) or src in granted
               for src in extract_sources(answer_text))
