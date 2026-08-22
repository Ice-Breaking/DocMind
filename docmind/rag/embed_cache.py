"""切片级 embedding 缓存：text hash → 向量，SQLite 持久化。

manifest 是文件级指纹——文件改动后整个文件的所有切片都会重新 embed，
即使只改了一段。本缓存在切片粒度去重：文本未变的切片直接命中，
只有真正新增/变化的切片才调 embedding API。

容量控制：简单 LRU——写入时若超上限，按 last_seen 淘汰最旧条目。
"""
import hashlib
import os
import sqlite3
import threading

import numpy as np

from docmind import config

DB_PATH = os.path.join(config.PROJECT_ROOT, "data", "index", "embed_cache.db")
_local = threading.local()
MAX_ENTRIES = 200_000   # ~200k × (1KB 向量 + 开销) ≈ 数百 MB 上限，按需调整

_SCHEMA = """
CREATE TABLE IF NOT EXISTS embed_cache(
    text_hash TEXT PRIMARY KEY,
    vec BLOB NOT NULL,
    last_seen REAL
);
"""


def _conn() -> sqlite3.Connection:
    conn = getattr(_local, "conn", None)
    if conn is None:
        os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
        conn = sqlite3.connect(DB_PATH)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        conn.executescript(_SCHEMA)
        conn.commit()
        _local.conn = conn
    return conn


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:20]


def invalidate(texts: list[str]) -> None:
    """删除这批文本的缓存条目。 Chroma 写入失败（如维度校验拒绝）时回滚——
    否则坏向量滞留缓存，后续重建命中后反复失败且无 API 调用可自愈"""
    if not texts:
        return
    try:
        c = _conn()
        hashes = [_hash(t) for t in texts]
        c.execute(
            f"DELETE FROM embed_cache WHERE text_hash IN ({','.join('?' * len(hashes))})",
            hashes)
        c.commit()
    except sqlite3.Error:
        pass


def embed_cached(embed_fn, texts: list[str]) -> list[list[float]]:
    """带缓存的批量嵌入：命中的直接返回，未命中的批量调 embed_fn 后写回。
    embed_fn: docmind.llm.embed（注入以便测试 monkeypatch）"""
    if not texts:
        return []
    c = _conn()
    hashes = [_hash(t) for t in texts]
    placeholders = ",".join("?" * len(hashes))
    cached: dict[str, bytes] = {}
    try:
        for row in c.execute(
                f"SELECT text_hash, vec FROM embed_cache WHERE text_hash IN ({placeholders})",
                hashes):
            cached[row[0]] = row[1]
    except sqlite3.Error:
        cached = {}

    result: list[list[float] | None] = [None] * len(texts)
    miss_idx = [i for i, h in enumerate(hashes) if h not in cached]
    for i, h in enumerate(hashes):
        if h in cached:
            result[i] = np.frombuffer(cached[h], dtype=np.float32).tolist()

    if miss_idx:
        miss_texts = [texts[i] for i in miss_idx]
        vectors = embed_fn(miss_texts)
        # 批内维度必须一致——不一致（如测试 mock / 上游异常返回）时
        # 放弃写入缓存，只透传结果，防坏向量污染
        dims = {len(v) for v in vectors if v is not None}
        cacheable = len(dims) == 1 and next(iter(dims), 0) > 0
        if cacheable:
            import time
            now = time.time()
            rows = [(hashes[i], sqlite3.Binary(
                np.asarray(v, dtype=np.float32).tobytes()), now)
                for i, v in zip(miss_idx, vectors)]
            try:
                c.executemany(
                    "INSERT OR REPLACE INTO embed_cache(text_hash, vec, last_seen) VALUES(?,?,?)",
                    rows)
                # 容量控制：超限淘汰最旧
                n = c.execute("SELECT COUNT(*) FROM embed_cache").fetchone()[0]
                if n > MAX_ENTRIES:
                    c.execute(
                        "DELETE FROM embed_cache WHERE text_hash IN ("
                        "  SELECT text_hash FROM embed_cache ORDER BY last_seen LIMIT ?)",
                        (n - MAX_ENTRIES,))
                c.commit()
            except sqlite3.Error:
                pass   # 缓存写失败不影响主流程
        for i, v in zip(miss_idx, vectors):
            result[i] = [float(x) for x in v]
    return result
