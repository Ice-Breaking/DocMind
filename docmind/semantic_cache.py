"""语义缓存：高频问题秒回（省 token、降延迟）。

机制：问题 → embedding → 与缓存条目余弦相似度比对，≥ CACHE_THRESHOLD 视为同问，
直接返回缓存答案，跳过整个 Agent 链路（检索/工具/生成全省）。

安全边界（面试可讲）：
- 阈值保守（默认 0.92）：宁可不命中，不可错配（错配 = 张冠李戴的答案）
- KB 作用域：条目按绑定的知识库集合隔离——助手 A（KB-X）的答案不会被
  路由到 KB-Y 的请求命中（跨库答案串扰 = 张冠李戴的另一种形态）
- embedding 模型指纹：换模型后存量向量与新查询向量量纲失真，
  检测到指纹变化自动清空缓存（与全量重建索引同一语义）
- 时效类回答（天气/时间/web_search 参与）不写入缓存，防过期数据
- 错误兜底类回答（⚠️ 开头）不写入
- 问题与答案双侧 PII 检查（答案由 LLM 基于 KB 生成，可能复述 KB 内 PII）
- 缓存失效不阻塞主链路（全 try/except）
"""
import json
import logging
import os
import sqlite3
import threading
import time

import numpy as np

from docmind import config
from docmind.pii import contains_pii

logger = logging.getLogger(__name__)

DB_PATH = os.path.join(config.PROJECT_ROOT, "data", "cache.db")
_local = threading.local()

_SCHEMA = """
CREATE TABLE IF NOT EXISTS semantic_cache(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    question TEXT NOT NULL,
    answer TEXT NOT NULL,
    vec TEXT NOT NULL,
    created_at REAL,
    hits INTEGER DEFAULT 0,
    kb_scope TEXT DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_cache_created ON semantic_cache(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_cache_hits ON semantic_cache(hits DESC);
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
        # 旧库存量补 kb_scope 列（向前兼容），空作用域视为默认库。
        # 注意 kb_scope 的索引必须在 ALTER 之后创建——放 _SCHEMA 里会在
        # 旧库（无该列）上直接报 no such column，连接初始化整体失败
        cols = [r["name"] for r in conn.execute("PRAGMA table_info(semantic_cache)")]
        if "kb_scope" not in cols:
            conn.execute("ALTER TABLE semantic_cache ADD COLUMN kb_scope TEXT DEFAULT ''")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_cache_scope ON semantic_cache(kb_scope)")
        conn.execute("UPDATE semantic_cache SET kb_scope = 'default' WHERE kb_scope = ''")
        conn.commit()
        _local.conn = conn
    return conn


def _embedding_fingerprint() -> str:
    """当前 embedding 模型标识：模型切换后存量向量不可比，缓存须清空"""
    try:
        from docmind.llm import _active_cfg
        return _active_cfg("embedding")[0]
    except Exception:  # noqa: BLE001
        return ""


_model_state: dict = {"fp": None}


def _ensure_model_scope() -> None:
    """embedding 模型变化时清空缓存（幂等，进程内只清一次/模型）。

    原先缓存只在知识库重建时清——经「模型管理」页切换 embedding 模型
    后，存量条目向量与新查询向量量纲失真，相似度不可信仍会命中。"""
    fp = _embedding_fingerprint()
    if _model_state["fp"] is None:
        _model_state["fp"] = fp
        return
    if fp != _model_state["fp"]:
        try:
            n = clear()
            logger.info(f"embedding 模型切换（{_model_state['fp']} → {fp}），"
                        f"已清空语义缓存 {n} 条")
        except Exception:  # noqa: BLE001
            logger.warning("embedding 模型切换后清缓存失败", exc_info=True)
        _model_state["fp"] = fp


def kb_scope_key(kb_ids: list | None) -> str:
    """知识库集合 → 作用域键（排序后拼接，集合等价即同键）；空 = 默认库"""
    ids = sorted({str(k) for k in (kb_ids or []) if k and k != "default"})
    return "|".join(ids) or "default"


def _decode_vec(raw) -> np.ndarray:
    """解码向量：新条目为 float32 BLOB（O(1) 反序列化），
    旧条目为 JSON 文本（升级前写入的，向后兼容读取）"""
    if isinstance(raw, (bytes, bytearray)):
        return np.frombuffer(raw, dtype=np.float32)
    return np.asarray(json.loads(raw), dtype=np.float32)


def lookup(vec: list[float] | np.ndarray,
           kb_ids: list | None = None) -> tuple[str, str, int] | None:
    """返回最相似且 ≥ 阈值的 (缓存问题, 缓存答案, 条目id)；无命中返回 None

    - 只在当前 KB 作用域内命中（跨库答案串扰防护）
    - 最近 500 条滑动窗口（按创建时间）：原先按 hits DESC 排序会让
      零命中新条目在表 >500 行后永远进不了候选窗口（冷启动饥饿）
    - 相似度用矩阵乘一次算完（500×d @ d），替代 Python 逐条余弦"""
    _ensure_model_scope()
    qv = np.asarray(vec, dtype=np.float32)
    qn = float(np.linalg.norm(qv))
    if qn == 0:
        return None
    qv = qv / qn
    scope = kb_scope_key(kb_ids)
    # kb_scope='' 的行为升级前写入的存量条目（无作用域时代），
    # 视作默认库作用域参与命中（连接时的回填 UPDATE 只覆盖既有行）
    if scope == "default":
        rows = _conn().execute(
            """
            SELECT id, question, answer, vec FROM semantic_cache
            WHERE kb_scope = ? OR kb_scope = ''
            ORDER BY created_at DESC
            LIMIT 500
            """,
            (scope,),
        ).fetchall()
    else:
        rows = _conn().execute(
            """
            SELECT id, question, answer, vec FROM semantic_cache
            WHERE kb_scope = ?
            ORDER BY created_at DESC
            LIMIT 500
            """,
            (scope,),
        ).fetchall()
    if not rows:
        return None
    mat = np.vstack([_decode_vec(r["vec"]) for r in rows])
    norms = np.linalg.norm(mat, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    sims = (mat / norms) @ qv
    best_i = int(np.argmax(sims))
    if float(sims[best_i]) < config.CACHE_THRESHOLD:
        return None
    best = rows[best_i]
    c = _conn()
    c.execute("UPDATE semantic_cache SET hits = hits + 1 WHERE id = ?", (best["id"],))
    c.commit()
    return best["question"], best["answer"], best["id"]


def save(question: str, answer: str, vec: list[float] | np.ndarray,
         kb_ids: list | None = None) -> None:
    """写入缓存（同问题+同作用域去重：先删旧条目；向量以 float32 BLOB 存储）。
    问题与答案双侧 PII 检查：答案由 LLM 基于 KB 生成，可能复述 KB 内 PII"""
    _ensure_model_scope()
    if contains_pii(question) or contains_pii(answer):
        return
    c = _conn()
    scope = kb_scope_key(kb_ids)
    c.execute("DELETE FROM semantic_cache WHERE question = ? AND kb_scope = ?",
              (question, scope))
    c.execute(
        "INSERT INTO semantic_cache(question, answer, vec, created_at, kb_scope) VALUES(?,?,?,?,?)",
        (question, answer,
         sqlite3.Binary(np.asarray(vec, dtype=np.float32).tobytes()),
         time.time(), scope),
    )
    c.commit()


def delete_entry(entry_id: int) -> None:
    """Delete a single cache entry by its id."""
    c = _conn()
    c.execute("DELETE FROM semantic_cache WHERE id = ?", (entry_id,))
    c.commit()


def stats() -> dict:
    row = _conn().execute(
        "SELECT COUNT(*) AS n, COALESCE(SUM(hits), 0) AS h FROM semantic_cache"
    ).fetchone()
    return {"entries": row["n"], "total_hits": row["h"]}


def cleanup_stale_entries(days: int = 7) -> int:
    """清理陈旧缓存：删除创建超过 N 天且从未命中的条目

    返回删除的条目数"""
    cutoff = time.time() - (days * 86400)
    c = _conn()
    c.execute("DELETE FROM semantic_cache WHERE hits = 0 AND created_at < ?", (cutoff,))
    deleted = c.total_changes
    c.commit()
    return deleted


def clear() -> int:
    """清空全部缓存条目，返回删除数。

    知识库重建/文档增删后调用——否则旧缓存答案继续命中，
    引用已删除或已修改的文档内容（与证据拒答/引用溯源的承诺矛盾）"""
    c = _conn()
    deleted = c.execute("DELETE FROM semantic_cache").rowcount
    c.commit()
    return deleted
