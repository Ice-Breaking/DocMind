"""切片级 jieba 分词缓存：text hash → tokens(JSON)，SQLite 持久化。

BM25 路在索引构建时需要对全部切片分词；增量重建只变化少量文件，
但 HybridRetriever.build() 是全量重建——文本未变的切片直接复用缓存，
只有真正新增/变化的切片才跑 jieba（与 embed_cache 同思路的切片级去重，
大语料增量重建时显著降低 CPU 耗时）。

容错：任何缓存层故障（库损坏/磁盘满）自动降级为全量直算，不影响主流程。
容量控制：与 embed_cache 一致的简单 LRU——写入时若超上限，按 last_seen
淘汰最旧条目；分词结果由文本唯一确定，陈旧条目无正确性风险。
"""
import hashlib
import json
import os
import sqlite3
import threading

from docmind import config

DB_PATH = os.path.join(config.PROJECT_ROOT, "data", "index", "tokenize_cache.db")
_local = threading.local()
MAX_ENTRIES = 200_000   # 每条几百字节，20 万条上限覆盖大语料

_SCHEMA = """
CREATE TABLE IF NOT EXISTS tokenize_cache(
    text_hash TEXT PRIMARY KEY,
    tokens TEXT NOT NULL,
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


def tokenize_cached(texts: list[str], tokenizer=None) -> list[list[str]]:
    """带缓存的批量分词：命中的直接返回，未命中的批量调 tokenizer 后写回。

    tokenizer 注入式传入（默认取 docmind.rag.hybrid.tokenize，惰性导入
    避免循环依赖），便于测试替换。"""
    if not texts:
        return []
    if tokenizer is None:
        from docmind.rag.hybrid import tokenize as tokenizer  # noqa: F811
    import time

    hashes = [_hash(t) for t in texts]
    result: list[list[str] | None] = [None] * len(texts)
    try:
        c = _conn()
        placeholders = ",".join("?" * len(hashes))
        cached: dict[str, str] = {}
        c.execute("BEGIN")   # 显式事务：读+写在同一连接上顺序一致
        for row in c.execute(
                f"SELECT text_hash, tokens FROM tokenize_cache WHERE text_hash IN ({placeholders})",
                hashes):
            cached[row[0]] = row[1]

        miss_idx = []
        for i, h in enumerate(hashes):
            if h in cached:
                result[i] = json.loads(cached[h])
            else:
                miss_idx.append(i)

        if miss_idx:
            now = time.time()
            # 批内去重：相同文本（hash 相同）只分词一次，结果回填所有位置
            uniq: dict[str, list[str]] = {}
            for i in miss_idx:
                h = hashes[i]
                if h not in uniq:
                    uniq[h] = tokenizer(texts[i])
                result[i] = uniq[h]
            rows = [(h, json.dumps(toks, ensure_ascii=False), now)
                    for h, toks in uniq.items()]
            c.executemany(
                "INSERT OR REPLACE INTO tokenize_cache(text_hash, tokens, last_seen) VALUES(?,?,?)",
                rows)
            # 容量控制：超限淘汰最旧
            n = c.execute("SELECT COUNT(*) FROM tokenize_cache").fetchone()[0]
            if n > MAX_ENTRIES:
                c.execute(
                    "DELETE FROM tokenize_cache WHERE text_hash IN ("
                    "  SELECT text_hash FROM tokenize_cache ORDER BY last_seen LIMIT ?)",
                    (n - MAX_ENTRIES,))
            c.commit()
        else:
            c.rollback()
    except Exception:  # noqa: BLE001 - 缓存不可用时全量直算兜底
        for i, r in enumerate(result):
            if r is None:
                result[i] = tokenizer(texts[i])
    return result  # type: ignore[return-value]