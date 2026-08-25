"""告警中心 + SLA：规则引擎周期评估、告警流转（确认/解决）、服务质量统计。

规则（阈值见 config，均可 env 覆盖）：
- quality：待处理 Badcase 数 ≥ ALERT_BADCASE_PENDING
- cost：最近 24h LLM 成本 ≥ ALERT_DAILY_COST（按内置价目表估算）
- error：最近 1h trace 失败次数 ≥ ALERT_ERROR_COUNT
- ingest：最近 24h 存在失败的入库任务
同一 dedupe_key 已有 open 告警时不重复创建（避免刷屏）。
评估由后台线程每 ALERT_INTERVAL_MIN 分钟执行一次，也可手动触发。
"""
import logging
import threading
import time

import anyio
import fastapi
from fastapi import HTTPException
from fastapi.responses import JSONResponse

from docmind.deps import RequireAdmin
from docmind import config, store
from docmind.admin import MODEL_PRICING, _DEFAULT_PRICING

logger = logging.getLogger(__name__)


# ================= trace 聚合工具 =================

def _cost_last_hours(hours: float) -> float:
    """最近 N 小时 LLM 成本（SQL 按模型聚合，价目 Python 侧套用；
    原实现每轮评估全量扫描 JSONL 8000 行）"""
    from docmind import trace_store
    per_model = trace_store.cost_last_hours(hours)
    total = 0.0
    for model, (inp, outp) in per_model.items():
        pricing = MODEL_PRICING.get(model, _DEFAULT_PRICING)
        total += ((inp / 1000.0) * pricing[0]
                  + (outp / 1000.0) * pricing[1])
    return total


def _errors_last_hours(hours: float) -> int:
    from docmind import trace_store
    return trace_store.errors_last_hours(hours)


# ================= 规则引擎 =================

def _webhook_payload(alert_type: str, message: str, url: str) -> dict | None:
    """按平台构造 webhook 消息体（企微/钉钉/飞书群机器人 + 通用 JSON）"""
    kind = config.ALERT_WEBHOOK_TYPE
    if kind == "auto":
        if "qyapi.weixin.qq.com" in url:
            kind = "wecom"
        elif "oapi.dingtalk.com" in url:
            kind = "dingtalk"
        elif "open.feishu.cn" in url:
            kind = "feishu"
        else:
            kind = "generic"
    text = f"[DocMind 告警·{alert_type}] {message}"
    if kind in ("wecom", "dingtalk"):
        return {"msgtype": "text", "text": {"content": text}}
    if kind == "feishu":
        return {"msg_type": "text", "content": {"text": text}}
    return {"type": alert_type, "text": text}


def _notify_webhook(alert_type: str, message: str) -> None:
    """告警外发：POST 到配置的 Webhook（企微/钉钉/飞书/通用）。
    独立线程发送——告警主流程绝不因外发失败/超时阻塞"""
    url = config.ALERT_WEBHOOK_URL
    if not url:
        return

    def _send():
        try:
            import requests
            resp = requests.post(
                url, json=_webhook_payload(alert_type, message, url), timeout=5)
            logger.info(f"告警已外发 webhook status={resp.status_code}")
        except Exception as e:  # noqa: BLE001 - 外发失败不影响告警系统
            logger.warning(f"告警 webhook 外发失败: {e}")

    threading.Thread(target=_send, daemon=True).start()


def evaluate_all() -> list[dict]:
    """执行全部规则，返回本次新创建的告警列表"""
    created = []

    def _fire(type_, severity, message, dedupe_key):
        aid = store.create_alert(type_, severity, message, dedupe_key)
        if aid:
            created.append({"id": aid, "type": type_,
                            "severity": severity, "message": message})
            _notify_webhook(type_, f"[{severity}] {message}")

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


def _daily_cache_cleanup() -> None:
    """每日一次：清理语义/推理缓存中过期未命中的条目。

    cleanup 函数原先全仓无调用点（死代码）→ cache.db 只进不出无限膨胀；
    挂进告警周期循环复用其唤醒时机，无需新增调度线程。"""
    import threading as _th

    def _run():
        try:
            from docmind import semantic_cache
            n1 = semantic_cache.cleanup_stale_entries(days=7)
            n2 = 0
            try:
                from docmind import agent_reasoning_cache
                if hasattr(agent_reasoning_cache, "cleanup_expired"):
                    n2 = agent_reasoning_cache.cleanup_expired()
            except Exception:  # noqa: BLE001
                pass
            if n1 or n2:
                logger.info(f"周期缓存清理：语义 {n1} 条 / 推理 {n2} 条")
            try:
                from docmind import trace_store
                trace_store.retention(days=90)   # 保留策略：90 天过期
            except Exception:  # noqa: BLE001
                pass
        except Exception as e:  # noqa: BLE001 - 清理失败不影响告警主流程
            logger.warning(f"周期缓存清理失败: {e}")

    _th.Thread(target=_run, daemon=True, name="cache-cleanup").start()


def _loop():
    # 启动即先评估一次：原先先 sleep 再评估，告警要等 10 分钟才首轮检查；
    # 顺带在首轮触发缓存周期清理
    last_cleanup_day = ""
    while True:
        try:
            evaluate_all()
        except Exception as e:  # noqa: BLE001 - 后台评估绝不抛出
            logger.warning(f"告警周期评估失败: {e}")
        today = time.strftime("%Y-%m-%d")
        if today != last_cleanup_day:
            last_cleanup_day = today
            _daily_cache_cleanup()
        time.sleep(max(1, config.ALERT_INTERVAL_MIN) * 60)


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
    from docmind import trace_store
    total = ok = 0
    durations: list[float] = []
    raw = trace_store.sla_stats(days)
    for day in sorted(raw.keys()):
        v = raw[day]
        total += v["total"]
        ok += v["ok"]
        durations.extend(v["durs"])
    daily = {day: {"total": v["total"], "ok": v["ok"], "durs": v["durs"]}
             for day, v in raw.items()}

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
    async def _list_alerts(request: fastapi.Request, _user: RequireAdmin, status: str = "",
                           limit: int = 100):
        return JSONResponse(store.list_alerts(status, max(1, min(limit, 500))))

    @app.post("/api/admin/alerts/evaluate", include_in_schema=False)
    async def _evaluate(request: fastapi.Request, user: RequireAdmin):
        # evaluate_all 内含 trace JSONL 全量扫描（同步 IO），下放线程池
        created = await anyio.to_thread.run_sync(evaluate_all)
        if created:
            store.record_audit(user, "alert.evaluate", "",
                               f"新告警 {len(created)} 条")
        return JSONResponse({"ok": True, "created": created})

    @app.post("/api/admin/alerts/{aid}/ack", include_in_schema=False)
    async def _ack_alert(aid: int, request: fastapi.Request, user: RequireAdmin):
        if not store.set_alert_status(aid, "acknowledged"):
            raise HTTPException(status_code=400, detail="告警不存在或非 open 状态")
        store.record_audit(user, "alert.ack", f"alert#{aid}")
        return {"ok": True}

    @app.post("/api/admin/alerts/{aid}/resolve", include_in_schema=False)
    async def _resolve_alert(aid: int, request: fastapi.Request, user: RequireAdmin):
        if not store.set_alert_status(aid, "resolved"):
            raise HTTPException(status_code=400, detail="告警不存在或已解决")
        store.record_audit(user, "alert.resolve", f"alert#{aid}")
        return {"ok": True}

    @app.get("/api/admin/sla", include_in_schema=False)
    async def _sla(request: fastapi.Request, _user: RequireAdmin, days: int = 7):
        # sla_stats 全量扫描 trace JSONL（同步 IO），下放线程池
        return JSONResponse(await anyio.to_thread.run_sync(
            sla_stats, max(1, min(days, 30))))
