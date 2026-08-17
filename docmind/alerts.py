"""告警中心 + SLA：规则引擎周期评估、告警流转（确认/解决）、服务质量统计。

规则（阈值见 config，均可 env 覆盖）：
- quality：待处理 Badcase 数 ≥ ALERT_BADCASE_PENDING
- cost：最近 24h LLM 成本 ≥ ALERT_DAILY_COST（按内置价目表估算）
- error：最近 1h trace 失败次数 ≥ ALERT_ERROR_COUNT
- ingest：最近 24h 存在失败的入库任务
同一 dedupe_key 已有 open 告警时不重复创建（避免刷屏）。
评估由后台线程每 ALERT_INTERVAL_MIN 分钟执行一次，也可手动触发。
"""
import json
import logging
import os
import threading
import time
from collections import defaultdict
from datetime import datetime

import fastapi
from fastapi import HTTPException
from fastapi.responses import JSONResponse

from docmind import config, store
from docmind.admin import MODEL_PRICING, _DEFAULT_PRICING, _require_admin

logger = logging.getLogger(__name__)


# ================= trace 聚合工具 =================

def _iter_traces(max_lines: int = 8000):
    path = config.TRACE_LOG_PATH
    if not os.path.exists(path):
        return
    try:
        with open(path, encoding="utf-8") as f:
            lines = f.readlines()[-max_lines:]
    except OSError:
        return
    for line in lines:
        try:
            yield json.loads(line)
        except (json.JSONDecodeError, ValueError):
            continue


def _ts_epoch(d: dict) -> float | None:
    try:
        return datetime.strptime(str(d.get("ts", ""))[:19],
                                 "%Y-%m-%d %H:%M:%S").timestamp()
    except ValueError:
        return None


def _cost_last_hours(hours: float) -> float:
    cutoff = time.time() - hours * 3600
    total = 0.0
    for d in _iter_traces():
        if d.get("kind") != "generation":
            continue
        ts = _ts_epoch(d)
        if ts is None or ts < cutoff:
            continue
        usage = d.get("usage") or {}
        pricing = MODEL_PRICING.get(d.get("model", ""), _DEFAULT_PRICING)
        total += ((usage.get("input", 0) / 1000.0) * pricing[0]
                  + (usage.get("output", 0) / 1000.0) * pricing[1])
    return total


def _errors_last_hours(hours: float) -> int:
    cutoff = time.time() - hours * 3600
    n = 0
    for d in _iter_traces():
        if d.get("status") != "error":
            continue
        ts = _ts_epoch(d)
        if ts is not None and ts >= cutoff:
            n += 1
    return n


# ================= 规则引擎 =================

def evaluate_all() -> list[dict]:
    """执行全部规则，返回本次新创建的告警列表"""
    created = []

    def _fire(type_, severity, message, dedupe_key):
        aid = store.create_alert(type_, severity, message, dedupe_key)
        if aid:
            created.append({"id": aid, "type": type_,
                            "severity": severity, "message": message})

    # 1. 质量：待处理 Badcase 积压
    try:
        ov = store.stats_overview()
        pending = int(ov.get("badcase_pending") or 0)
        if pending >= config.ALERT_BADCASE_PENDING:
            _fire("quality", "warning",
                  f"待处理 Badcase 达 {pending} 条（阈值 {config.ALERT_BADCASE_PENDING}），请及时流转处理",
                  "quality:badcase-pending")
    except Exception as e:  # noqa: BLE001 - 单规则失败不影响其余
        logger.warning(f"告警规则 quality 失败: {e}")

    # 2. 成本：24h 成本超阈值
    try:
        cost = _cost_last_hours(24)
        if cost >= config.ALERT_DAILY_COST:
            _fire("cost", "warning",
                  f"最近 24h LLM 成本 ¥{cost:.2f}，超过阈值 ¥{config.ALERT_DAILY_COST:.2f}",
                  "cost:24h")
    except Exception as e:  # noqa: BLE001
        logger.warning(f"告警规则 cost 失败: {e}")

    # 3. 稳定性：1h 错误次数
    try:
        errors = _errors_last_hours(1)
        if errors >= config.ALERT_ERROR_COUNT:
            _fire("error", "critical",
                  f"最近 1h 链路失败 {errors} 次（阈值 {config.ALERT_ERROR_COUNT}），请查看检索日志",
                  "error:1h")
    except Exception as e:  # noqa: BLE001
        logger.warning(f"告警规则 error 失败: {e}")

    # 4. 入库：24h 内失败任务
    try:
        c = store._conn()
        n = c.execute(
            """SELECT COUNT(*) FROM ingest_tasks
               WHERE status='error' AND updated_at>=?""",
            (time.time() - 86400,)).fetchone()[0]
        if n > 0:
            _fire("ingest", "warning",
                  f"最近 24h 有 {n} 个入库任务失败，请到知识库页重试",
                  "ingest:failed")
    except Exception as e:  # noqa: BLE001
        logger.warning(f"告警规则 ingest 失败: {e}")

    return created


_loop_started = False


def _loop():
    while True:
        time.sleep(max(1, config.ALERT_INTERVAL_MIN) * 60)
        try:
            evaluate_all()
        except Exception as e:  # noqa: BLE001 - 后台评估绝不抛出
            logger.warning(f"告警周期评估失败: {e}")


def start_loop() -> None:
    """启动后台评估线程（幂等：重复注册路由不会重复起线程）"""
    global _loop_started
    if _loop_started:
        return
    _loop_started = True
    threading.Thread(target=_loop, daemon=True, name="alert-loop").start()


# ================= SLA 统计 =================

def sla_stats(days: int = 7) -> dict:
    """SLA 口径：generation 调用的可用率（status=ok 占比）与耗时分位数，
    并按天给出最近 N 天趋势"""
    cutoff = time.time() - days * 86400
    total = ok = 0
    durations: list[float] = []
    daily: dict[str, dict] = defaultdict(lambda: {"total": 0, "ok": 0, "durs": []})
    for d in _iter_traces():
        if d.get("kind") != "generation":
            continue
        ts = _ts_epoch(d)
        if ts is None or ts < cutoff:
            continue
        day = str(d.get("ts", ""))[:10]
        dur = d.get("duration_ms")
        total += 1
        daily[day]["total"] += 1
        if d.get("status") == "ok":
            ok += 1
            daily[day]["ok"] += 1
        if isinstance(dur, (int, float)):
            durations.append(float(dur))
            daily[day]["durs"].append(float(dur))

    def _pct(arr: list[float], p: float) -> float:
        if not arr:
            return 0.0
        arr = sorted(arr)
        return round(arr[min(len(arr) - 1, int(len(arr) * p))], 1)

    trend = []
    for day in sorted(daily.keys()):
        v = daily[day]
        trend.append({
            "date": day,
            "total": v["total"],
            "availability": round(v["ok"] / v["total"], 4) if v["total"] else 1.0,
            "p95_ms": _pct(v["durs"], 0.95),
        })
    return {
        "days": days,
        "total": total,
        "ok": ok,
        "availability": round(ok / total, 4) if total else 1.0,
        "p50_ms": _pct(durations, 0.50),
        "p95_ms": _pct(durations, 0.95),
        "daily": trend,
    }


# ================= 路由 =================

def register_alert_routes(app) -> None:

    @app.get("/api/admin/alerts", include_in_schema=False)
    async def _list_alerts(request: fastapi.Request, status: str = "",
                           limit: int = 100):
        _require_admin(request, app)
        return JSONResponse(store.list_alerts(status, max(1, min(limit, 500))))

    @app.post("/api/admin/alerts/evaluate", include_in_schema=False)
    async def _evaluate(request: fastapi.Request):
        user = _require_admin(request, app)
        created = evaluate_all()
        if created:
            store.record_audit(user, "alert.evaluate", "",
                               f"新告警 {len(created)} 条")
        return JSONResponse({"ok": True, "created": created})

    @app.post("/api/admin/alerts/{aid}/ack", include_in_schema=False)
    async def _ack_alert(aid: int, request: fastapi.Request):
        user = _require_admin(request, app)
        if not store.set_alert_status(aid, "acknowledged"):
            raise HTTPException(status_code=400, detail="告警不存在或非 open 状态")
        store.record_audit(user, "alert.ack", f"alert#{aid}")
        return {"ok": True}

    @app.post("/api/admin/alerts/{aid}/resolve", include_in_schema=False)
    async def _resolve_alert(aid: int, request: fastapi.Request):
        user = _require_admin(request, app)
        if not store.set_alert_status(aid, "resolved"):
            raise HTTPException(status_code=400, detail="告警不存在或已解决")
        store.record_audit(user, "alert.resolve", f"alert#{aid}")
        return {"ok": True}

    @app.get("/api/admin/sla", include_in_schema=False)
    async def _sla(request: fastapi.Request, days: int = 7):
        _require_admin(request, app)
        return JSONResponse(sla_stats(max(1, min(days, 30))))
