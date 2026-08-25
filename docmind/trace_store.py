"""调用链追踪 SQLite 存储层：写入队列批量落库 + SQL 聚合查询 + JSONL 迁移。

为什么迁移（原 JSONL 的问题）：
- 管理端聚合（用量/成本/TopQuery/SLA/阶段统计）各自全量读文件并逐行
  json.loads——最大 50MB×6 个轮转归档，同一份数据被反复解析；
- 无索引：按时间/类型/状态过滤只能线性扫描；
- 轮转截断：RotatingFileHandler 滚动后旧数据静默丢失，SLA 趋势断档。

设计：
- 独立 data/trace.db（不与 chat.db 争写锁）；WAL + 单写线程批量
  executemany（256 条或 0.5s 触发），span() 只做无阻塞入队——追踪
  绝不拖慢主链路；
- 写失败降级 JSONL（原路径保留为 fallback），查询失败返回空聚合；
- 首次使用时把存量 JSONL（含 .1~.5 轮转归档）一次性导入，trace_meta
  标记防重；此后 JSONL 仅作降级通道，不再增长；
- 保留策略：retention(days) 删除过期事件，挂告警循环每日执行。

查询方（全部走本模块，禁止再直读 JSONL）：
admin（traces/usage/top-queries/overview）、alerts（成本/错误/SLA）、
retrieval_api（阶段统计）、eval_api（拒答统计）、scripts/view_traces。
"""
import json
import logging
import os
import queue
import sqlite3
import threading
import time
from datetime import datetime

from docmind import config

logger = logging.getLogger(__name__)

DB_PATH = os.path.join(config.PROJECT_ROOT, "data", "trace.db")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS trace_events(
    id TEXT PRIMARY KEY,
    ts TEXT,
    ts_epoch REAL,
    name TEXT DEFAULT '',
    kind TEXT DEFAULT '',
    status TEXT DEFAULT '',
    duration_ms INTEGER,
    model TEXT DEFAULT '',
    kb TEXT DEFAULT '',
    input TEXT,
    output TEXT,
    usage_input INTEGER,
    usage_output INTEGER,
    query_label TEXT DEFAULT '',
    extra TEXT
);
CREATE INDEX IF NOT EXISTS idx_trace_ts ON trace_events(ts_epoch);
CREATE INDEX IF NOT EXISTS idx_trace_kind_ts ON trace_events(kind, ts_epoch);
CREATE INDEX IF NOT EXISTS idx_trace_status_ts ON trace_events(status, ts_epoch);
CREATE INDEX IF NOT EXISTS idx_trace_name ON trace_events(name);
CREATE TABLE IF NOT EXISTS trace_meta(
    key TEXT PRIMARY KEY,
    value TEXT
);
"""

# 写入批量阈值 / 刷盘间隔
_BATCH_SIZE = 256
_FLUSH_INTERVAL = 0.5

_local = threading.local()
_q = queue.Queue(maxsize=100_000)
_writer_started = False
_start_lock = threading.Lock()
_dropped = 0   # 队列满被丢弃的条数（追踪可丢，主链路不能堵）


def _conn() -> sqlite3.Connection:
    conn = getattr(_local, "conn", None)
    if conn is None:
        os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
        conn = sqlite3.connect(DB_PATH)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA busy_timeout=5000")
        conn.row_factory = sqlite3.Row
        conn.executescript(_SCHEMA)
        conn.commit()
        _local.conn = conn
    return conn


# ---------------- 列抽取 ----------------
_COLS = ("id", "ts", "ts_epoch", "name", "kind", "status", "duration_ms",
         "model", "kb", "input", "output", "usage_input", "usage_output",
         "query_label", "extra")

_META_KEYS = ("id", "ts", "name", "kind", "status", "duration_ms",
              "model", "kb", "input", "output", "usage")


def _ts_epoch(ts: str) -> float:
    try:
        return datetime.strptime(str(ts)[:19], "%Y-%m-%d %H:%M:%S").timestamp()
    except ValueError:
        return time.time()


def _extract(d: dict) -> tuple:
    """span 记录 → 行元组。input/output 序列化保留，其余杂项进 extra"""
    ts = str(d.get("ts", ""))
    usage = d.get("usage") or {}
    extra = {k: v for k, v in d.items() if k not in set(_META_KEYS)}
    # query_label：generation 记录的最后一条用户消息（TopQuery 聚合键，
    # 写入时抽取，避免查询侧逐行解析 JSON）
    query_label = ""
    if d.get("kind") == "generation":
        for m in reversed(d.get("input") or []):
            if isinstance(m, dict) and m.get("role") == "user":
                query_label = str(m.get("content") or "").strip()[:80]
                break
    return (
        str(d.get("id") or f"{int(_ts_epoch(ts))}-{id(d):x}"),
        ts, _ts_epoch(ts),
        str(d.get("name", "")), str(d.get("kind", "")),
        str(d.get("status", "")),
        int(d["duration_ms"]) if isinstance(d.get("duration_ms"), (int, float)) else None,
        str(d.get("model", "")), str(d.get("kb", "")),
        json.dumps(d.get("input"), ensure_ascii=False, default=str)
        if d.get("input") is not None else None,
        str(d.get("output")) if d.get("output") is not None else None,
        int(usage.get("input", 0) or 0), int(usage.get("output", 0) or 0),
        query_label,
        json.dumps(extra, ensure_ascii=False, default=str) if extra else None,
    )


_INSERT_SQL = (
    f"INSERT OR REPLACE INTO trace_events({','.join(_COLS)}) "
    f"VALUES({','.join('?' for _ in _COLS)})"
)


# ---------------- 写入路径 ----------------
def record(d: dict) -> None:
    """span 记录入队（无阻塞；队列满丢弃并计数——追踪可丢，主链路不能堵）"""
    global _dropped
    _ensure_writer()
    try:
        _q.put_nowait(d)
    except queue.Full:
        _dropped += 1
        if _dropped % 100 == 1:
            logger.warning(f"trace 写入队列满，已累计丢弃 {_dropped} 条")


def _writer_loop() -> None:
    while True:
        batch: list[dict] = []
        try:
            item = _q.get(timeout=_FLUSH_INTERVAL)
            if item is not None:
                batch.append(item)
                while len(batch) < _BATCH_SIZE:
                    try:
                        nxt = _q.get_nowait()
                    except queue.Empty:
                        break
                    if nxt is None:
                        break
                    batch.append(nxt)
        except queue.Empty:
            pass
        if batch:
            _flush_batch(batch)


def _flush_batch(batch: list[dict]) -> None:
    try:
        conn = _conn()
        conn.executemany(_INSERT_SQL, [_extract(d) for d in batch])
        conn.commit()
    except Exception as e:  # noqa: BLE001 - SQLite 故障降级 JSONL，绝不影响主链路
        logger.warning(f"trace 批量落库失败（{len(batch)} 条降级 JSONL）: {e}")
        try:
            from docmind.trace import _append_jsonl
            for d in batch:
                _append_jsonl(d)
        except Exception:  # noqa: BLE001
            pass


def _ensure_writer() -> None:
    global _writer_started
    if _writer_started:
        return
    with _start_lock:
        if _writer_started:
            return
        threading.Thread(target=_writer_loop, daemon=True,
                         name="trace-writer").start()
        _writer_started = True


def flush() -> None:
    """把队列中待写条目同步落库（查询前调用，保证读己之写）"""
    batch: list[dict] = []
    while True:
        try:
            item = _q.get_nowait()
        except queue.Empty:
            break
        batch.append(item)
    if batch:
        _flush_batch(batch)


# ---------------- 迁移 ----------------
def _jsonl_sources() -> list[str]:
    """当前 JSONL + 轮转归档（.1 ~ .5），按从旧到新排序"""
    base = config.TRACE_LOG_PATH
    sources = []
    archives = sorted(
        (f"{base}.{i}" for i in range(5, 0, -1) if os.path.exists(f"{base}.{i}")),
        reverse=True)   # .5 最旧在前
    sources.extend(archives)
    if os.path.exists(base):
        sources.append(base)
    return sources


def migrate_jsonl_if_needed() -> int:
    """把存量 JSONL（含轮转归档）一次性导入 SQLite；trace_meta 标记防重。

    返回导入条数。幂等：标记存在即跳过；导入后 JSONL 保留原样
    （作为迁移前备份，用户可手动归档删除）。"""
    conn = _conn()
    row = conn.execute(
        "SELECT value FROM trace_meta WHERE key = 'jsonl_migrated'").fetchone()
    if row:
        return 0
    sources = _jsonl_sources()
    total = 0
    for src in sources:
        try:
            with open(src, encoding="utf-8") as f:
                lines = f.readlines()
        except OSError as e:
            logger.warning(f"trace 迁移读取 {src} 失败: {e}")
            continue
        batch = []
        for line in lines:
            try:
                batch.append(json.loads(line))
            except (json.JSONDecodeError, ValueError):
                continue
        if batch:
            try:
                conn.executemany(_INSERT_SQL, [_extract(d) for d in batch])
                conn.commit()
                total += len(batch)
            except Exception as e:  # noqa: BLE001
                logger.warning(f"trace 迁移写入 {src} 失败: {e}")
        logger.info(f"trace 迁移：{src} → SQLite（{len(batch)} 条）")
    conn.execute(
        "INSERT OR REPLACE INTO trace_meta(key, value) VALUES('jsonl_migrated', ?)",
        (time.strftime("%Y-%m-%d %H:%M:%S"),))
    conn.commit()
    return total


# ---------------- 查询路径 ----------------
def _query_ready() -> sqlite3.Connection | None:
    """查询前置：同步刷队列 + 确保迁移完成；DB 异常返回 None（调用方
    返回空聚合，管理页显示零而不是 500）"""
    try:
        flush()
        migrate_jsonl_if_needed()
        return _conn()
    except Exception as e:  # noqa: BLE001
        logger.warning(f"trace 查询前置失败: {e}")
        return None


def _row_to_dict(r) -> dict:
    """行 → 与旧 JSONL 记录同构的 dict（前端/脚本零改动）"""
    d = {
        "id": r["id"], "ts": r["ts"], "name": r["name"], "kind": r["kind"],
        "status": r["status"], "duration_ms": r["duration_ms"],
        "model": r["model"], "kb": r["kb"],
    }
    if r["input"]:
        try:
            d["input"] = json.loads(r["input"])
        except (json.JSONDecodeError, ValueError):
            d["input"] = r["input"]
    if r["output"] is not None:
        d["output"] = r["output"]
    if r["usage_input"] or r["usage_output"]:
        d["usage"] = {"input": r["usage_input"] or 0,
                      "output": r["usage_output"] or 0}
    if r["extra"]:
        try:
            d.update(json.loads(r["extra"]))
        except (json.JSONDecodeError, ValueError):
            pass
    return d


def list_filtered(kind: str = "", status: str = "", q: str = "",
                  start: str = "", end: str = "", kb: str = "",
                  page: int = 1, page_size: int = 50) -> tuple[list[dict], int]:
    """检索日志过滤 + 倒序分页。start/end 为 YYYY-MM-DD（含端点）。
    q 匹配 name / model（LIKE）。返回 (items, total)。"""
    conn = _query_ready()
    if conn is None:
        return [], 0
    where, params = ["1=1"], []
    if kind:
        where.append("kind = ?")
        params.append(kind)
    if status:
        where.append("status = ?")
        params.append(status)
    if kb:
        where.append("kb = ?")
        params.append(kb)
    if start:
        where.append("substr(ts, 1, 10) >= ?")
        params.append(start)
    if end:
        where.append("substr(ts, 1, 10) <= ?")
        params.append(end)
    if q:
        where.append("(name LIKE ? OR model LIKE ?)")
        like = f"%{q}%"
        params.extend([like, like])
    w = " AND ".join(where)
    try:
        total = conn.execute(
            f"SELECT COUNT(*) FROM trace_events WHERE {w}", params).fetchone()[0]
        rows = conn.execute(
            f"SELECT * FROM trace_events WHERE {w} "
            f"ORDER BY ts_epoch DESC, id DESC LIMIT ? OFFSET ?",
            [*params, page_size, (page - 1) * page_size]).fetchall()
        return [_row_to_dict(r) for r in rows], total
    except Exception as e:  # noqa: BLE001
        logger.warning(f"trace 列表查询失败: {e}")
        return [], 0


def recent_events(limit: int = 3000) -> list[dict]:
    """最近 N 条（overview 用量聚合用）"""
    conn = _query_ready()
    if conn is None:
        return []
    try:
        rows = conn.execute(
            "SELECT * FROM trace_events ORDER BY ts_epoch DESC, id DESC LIMIT ?",
            (limit,)).fetchall()
        return [_row_to_dict(r) for r in rows]
    except Exception as e:  # noqa: BLE001
        logger.warning(f"trace recent 查询失败: {e}")
        return []


def stage_stats(last_n: int = 5000) -> dict:
    """各检索阶段耗时样本（retrieval: 前缀 span），供 P95/均值聚合"""
    conn = _query_ready()
    if conn is None:
        return {}
    agg = {}
    try:
        rows = conn.execute(
            "SELECT name, duration_ms FROM trace_events "
            "WHERE name LIKE 'retrieval:%' AND duration_ms IS NOT NULL "
            "ORDER BY ts_epoch DESC, id DESC LIMIT ?",
            (last_n,)).fetchall()
        for r in rows:
            agg.setdefault(r["name"], []).append(float(r["duration_ms"]))
    except Exception as e:  # noqa: BLE001
        logger.warning(f"trace 阶段统计失败: {e}")
    return agg


def usage_detail(days: int) -> dict:
    """用量成本聚合：按模型 + 按天（GROUP BY 代替全文件逐行解析）。
    价目计算在 Python 侧（价目表在 admin.MODEL_PRICING，避免循环依赖）"""
    conn = _query_ready()
    if conn is None:
        return {"rows": []}
    cutoff = time.time() - days * 86400
    try:
        rows = conn.execute(
            "SELECT model, substr(ts, 1, 10) AS day, COUNT(*) AS calls, "
            "SUM(usage_input) AS inp, SUM(usage_output) AS outp "
            "FROM trace_events WHERE kind = 'generation' AND ts_epoch >= ? "
            "GROUP BY model, day",
            (cutoff,)).fetchall()
        return {"rows": [dict(r) for r in rows]}
    except Exception as e:  # noqa: BLE001
        logger.warning(f"trace 用量聚合失败: {e}")
        return {"rows": []}


def top_queries(days: int) -> list:
    """TopQuery 原始聚合（按 query_label × model 分组），成本在 Python 侧算"""
    conn = _query_ready()
    if conn is None:
        return []
    cutoff = time.time() - days * 86400
    try:
        rows = conn.execute(
            "SELECT query_label, model, COUNT(*) AS calls, "
            "SUM(usage_input) AS inp, SUM(usage_output) AS outp "
            "FROM trace_events WHERE kind = 'generation' AND ts_epoch >= ? "
            "AND query_label != '' "
            "GROUP BY query_label, model",
            (cutoff,)).fetchall()
        return [dict(r) for r in rows]
    except Exception as e:  # noqa: BLE001
        logger.warning(f"trace TopQuery 聚合失败: {e}")
        return []


def cost_last_hours(hours: float) -> dict:
    """最近 N 小时各模型 (input, output) token 合计（成本按价目表调用方算）"""
    conn = _query_ready()
    if conn is None:
        return {}
    cutoff = time.time() - hours * 3600
    try:
        rows = conn.execute(
            "SELECT model, SUM(usage_input) AS inp, SUM(usage_output) AS outp "
            "FROM trace_events WHERE kind = 'generation' AND ts_epoch >= ? "
            "GROUP BY model", (cutoff,)).fetchall()
        return {r["model"]: (r["inp"] or 0, r["outp"] or 0) for r in rows}
    except Exception as e:  # noqa: BLE001
        logger.warning(f"trace 成本聚合失败: {e}")
        return {}


def errors_last_hours(hours: float) -> int:
    conn = _query_ready()
    if conn is None:
        return 0
    cutoff = time.time() - hours * 3600
    try:
        return conn.execute(
            "SELECT COUNT(*) FROM trace_events "
            "WHERE status = 'error' AND ts_epoch >= ?",
            (cutoff,)).fetchone()[0]
    except Exception as e:  # noqa: BLE001
        logger.warning(f"trace 错误统计失败: {e}")
        return 0


def sla_stats(days: int) -> dict:
    """SLA 原料：按天 total/ok/durs 列表，分位数在调用方算"""
    conn = _query_ready()
    if conn is None:
        return {}
    cutoff = time.time() - days * 86400
    try:
        rows = conn.execute(
            "SELECT substr(ts, 1, 10) AS day, status, duration_ms "
            "FROM trace_events WHERE kind = 'generation' AND ts_epoch >= ?",
            (cutoff,)).fetchall()
    except Exception as e:  # noqa: BLE001
        logger.warning(f"trace SLA 统计失败: {e}")
        return {}
    daily = {}
    for r in rows:
        v = daily.setdefault(r["day"], {"total": 0, "ok": 0, "durs": []})
        v["total"] += 1
        if r["status"] == "ok":
            v["ok"] += 1
        if isinstance(r["duration_ms"], (int, float)):
            v["durs"].append(float(r["duration_ms"]))
    return daily


def count_refusals(days: int) -> int:
    """最近 N 天证据拒答事件数（name = evidence-refusal）"""
    conn = _query_ready()
    if conn is None:
        return 0
    cutoff = time.time() - days * 86400
    try:
        return conn.execute(
            "SELECT COUNT(*) FROM trace_events "
            "WHERE name = 'evidence-refusal' AND ts_epoch >= ?",
            (cutoff,)).fetchone()[0]
    except Exception as e:  # noqa: BLE001
        logger.warning(f"trace 拒答统计失败: {e}")
        return 0


def retention(days: int = 90) -> int:
    """删除 N 天前的事件（挂告警循环每日执行，防 trace.db 无限膨胀）"""
    try:
        conn = _conn()
        cutoff = time.time() - days * 86400
        cur = conn.execute("DELETE FROM trace_events WHERE ts_epoch < ?", (cutoff,))
        conn.commit()
        if cur.rowcount:
            logger.info(f"trace 保留策略：清理 {cur.rowcount} 条 {days} 天前事件")
        return cur.rowcount
    except Exception as e:  # noqa: BLE001
        logger.warning(f"trace 清理失败: {e}")
        return 0


def usage_summary(limit: int = 3000) -> dict:
    """最近 N 条事件的用量聚合（overview 看板）：SQL 一次算完，
    替代拉全量记录到 Python 数数（每行还带 input JSON 反序列化）。

    返回 {llm_calls, tool_calls, errors, input_tokens, output_tokens,
          daily: {day: {input, output}}}（daily 仅最近 7 天）"""
    conn = _query_ready()
    if conn is None:
        return {"llm_calls": 0, "tool_calls": 0, "errors": 0,
                "input_tokens": 0, "output_tokens": 0, "daily": {}}
    try:
        row = conn.execute(
            """SELECT
                 COALESCE(SUM(CASE WHEN kind = 'generation' THEN 1 ELSE 0 END), 0) AS llm_calls,
                 COALESCE(SUM(CASE WHEN name LIKE 'tool:%' THEN 1 ELSE 0 END), 0) AS tool_calls,
                 COALESCE(SUM(CASE WHEN status != 'ok' THEN 1 ELSE 0 END), 0) AS errors,
                 COALESCE(SUM(usage_input), 0) AS inp,
                 COALESCE(SUM(usage_output), 0) AS outp
               FROM (SELECT kind, name, status, usage_input, usage_output, ts
                     FROM trace_events ORDER BY ts_epoch DESC, id DESC LIMIT ?)""",
            (limit,)).fetchone()
        daily_rows = conn.execute(
            """SELECT substr(ts, 1, 10) AS day,
                      COALESCE(SUM(usage_input), 0) AS inp,
                      COALESCE(SUM(usage_output), 0) AS outp
               FROM (SELECT ts, usage_input, usage_output, kind
                     FROM trace_events ORDER BY ts_epoch DESC, id DESC LIMIT ?)
               WHERE kind = 'generation' AND day != ''
               GROUP BY day ORDER BY day DESC LIMIT 7""",
            (limit,)).fetchall()
        return {
            "llm_calls": row["llm_calls"],
            "tool_calls": row["tool_calls"],
            "errors": row["errors"],
            "input_tokens": row["inp"],
            "output_tokens": row["outp"],
            "daily": {r["day"]: {"input": r["inp"], "output": r["outp"]}
                      for r in reversed(daily_rows)},
        }
    except Exception as e:  # noqa: BLE001
        logger.warning(f"trace 用量汇总失败: {e}")
        return {"llm_calls": 0, "tool_calls": 0, "errors": 0,
                "input_tokens": 0, "output_tokens": 0, "daily": {}}
