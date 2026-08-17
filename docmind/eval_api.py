"""评测体系 + 质量监控 API：评测集 CRUD、离线跑批、质量信号聚合。

权限：仅管理员。评测在后台线程执行（rerank 逐条调 API 较慢），
前端轮询 /api/admin/eval/runs 获取状态。
质量信号：线上反馈（好评/差评/Badcase）+ 证据拒答次数 + 评测 Recall 趋势。
"""
import json
import os
import threading
import time
from datetime import datetime

import fastapi
from fastapi import HTTPException
from fastapi.responses import JSONResponse

from docmind import config, semantic_cache, store
from docmind.admin import _require_admin


def _seed_default_datasets() -> None:
    """首次启动播种内置评测集（基础集 + 困难集），幂等"""
    try:
        if store.list_eval_datasets():
            return
        from docmind.rag.eval_set import EVAL_SET, HARD_SET
        store.create_eval_dataset(
            "内置基础集", "default",
            [{"question": q, "expected": e} for q, e in EVAL_SET])
        store.create_eval_dataset(
            "内置困难集", "default",
            [{"question": q, "expected": e} for q, e in HARD_SET])
    except Exception as e:  # noqa: BLE001 - 播种失败不影响启动
        import logging
        logging.getLogger(__name__).warning(f"评测集播种失败: {e}")


def _execute_run(run_id: int) -> None:
    """后台执行一次评测：按 mode 选择检索路线，统计 Recall@top_k 与 MRR"""
    run = store.get_eval_run(run_id)
    if not run:
        return
    ds = store.get_eval_dataset(run["dataset_id"])
    if not ds:
        store.update_eval_run(run_id, status="error")
        return
    store.update_eval_run(run_id, status="running")
    t0 = time.time()
    try:
        kb_id = ds.get("kb_id") or "default"
        if kb_id == "default":
            from docmind import core
            vecstore = core._shared_state.get("store")
            retriever = core._shared_state.get("retriever")
        else:
            from docmind.rag.kb_registry import get_registry
            vecstore, retriever = get_registry().get(kb_id)

        top_k = run.get("top_k") or 4
        mode = run.get("mode") or "rerank"
        if mode == "dense":
            search = lambda q: vecstore.search(q, top_k=top_k)  # noqa: E731
        elif mode == "rrf":
            search = lambda q: retriever.search(q, top_k=top_k, rerank=False)  # noqa: E731
        else:
            search = lambda q: retriever.search(q, top_k=top_k, rerank=True)  # noqa: E731

        items = ds.get("items") or []
        details = []
        hits = 0
        mrr_sum = 0.0
        for it in items:
            q, expected = str(it.get("question") or ""), str(it.get("expected") or "")
            if not q:
                continue
            try:
                results = search(q)
            except Exception:  # noqa: BLE001 - 单条失败记为未命中
                results = []
            rank = None
            for i, r in enumerate(results, 1):
                if getattr(r, "source", "") == expected:
                    rank = i
                    break
            if rank is not None and rank <= top_k:
                hits += 1
            if rank is not None:
                mrr_sum += 1.0 / rank
            details.append({
                "question": q, "expected": expected,
                "hit_rank": rank,
                "top1": (getattr(results[0], "source", "") if results else ""),
                "top1_score": round(float(results[0].score), 4) if results else None,
            })
        total = len(details)
        store.update_eval_run(
            run_id, status="done", total=total, hits=hits,
            recall=round(hits / total, 4) if total else 0.0,
            mrr=round(mrr_sum / total, 4) if total else 0.0,
            details=details,
            duration_ms=int((time.time() - t0) * 1000))
    except Exception as e:  # noqa: BLE001
        store.update_eval_run(run_id, status="error")
        import logging
        logging.getLogger(__name__).warning(f"评测运行 {run_id} 失败: {e}")


def _count_refusals(days: int) -> int:
    """统计最近 N 天的证据拒答事件（trace 中 name=evidence-refusal）"""
    path = config.TRACE_LOG_PATH
    if not os.path.exists(path):
        return 0
    cutoff = time.time() - days * 86400
    n = 0
    try:
        with open(path, encoding="utf-8") as f:
            for line in f.readlines()[-10000:]:
                try:
                    d = json.loads(line)
                except (json.JSONDecodeError, ValueError):
                    continue
                if d.get("name") != "evidence-refusal":
                    continue
                ts = str(d.get("ts", ""))[:19]
                try:
                    if datetime.strptime(ts, "%Y-%m-%d %H:%M:%S").timestamp() >= cutoff:
                        n += 1
                except ValueError:
                    continue
    except OSError:
        pass
    return n


def register_eval_routes(app) -> None:
    _seed_default_datasets()

    # ================= 评测集 CRUD =================
    @app.get("/api/admin/eval/datasets", include_in_schema=False)
    async def _datasets(request: fastapi.Request):
        _require_admin(request, app)
        return JSONResponse(store.list_eval_datasets())

    @app.post("/api/admin/eval/datasets", include_in_schema=False)
    async def _create_dataset(request: fastapi.Request):
        _require_admin(request, app)
        body = await request.json()
        name = str(body.get("name") or "").strip()
        if not name:
            raise HTTPException(status_code=400, detail="name 必填")
        items = body.get("items") or []
        if not isinstance(items, list):
            raise HTTPException(status_code=400, detail="items 必须是数组")
        ds = store.create_eval_dataset(name, str(body.get("kb_id") or "default"), items)
        return JSONResponse(ds, status_code=201)

    @app.put("/api/admin/eval/datasets/{ds_id}", include_in_schema=False)
    async def _update_dataset(ds_id: int, request: fastapi.Request):
        _require_admin(request, app)
        body = await request.json()
        ds = store.update_eval_dataset(
            ds_id,
            name=(str(body["name"]).strip() if "name" in body else None),
            kb_id=(str(body["kb_id"]) if "kb_id" in body else None),
            items=(body["items"] if "items" in body else None))
        if not ds:
            raise HTTPException(status_code=404, detail="评测集不存在")
        return JSONResponse(ds)

    @app.delete("/api/admin/eval/datasets/{ds_id}", include_in_schema=False)
    async def _delete_dataset(ds_id: int, request: fastapi.Request):
        _require_admin(request, app)
        if not store.delete_eval_dataset(ds_id):
            raise HTTPException(status_code=404, detail="评测集不存在")
        return {"ok": True}

    # ================= 评测运行 =================
    @app.post("/api/admin/eval/datasets/{ds_id}/run", include_in_schema=False)
    async def _run_eval(ds_id: int, request: fastapi.Request):
        user = _require_admin(request, app)
        if not store.get_eval_dataset(ds_id):
            raise HTTPException(status_code=404, detail="评测集不存在")
        try:
            body = await request.json()
        except Exception:  # noqa: BLE001
            body = {}
        mode = body.get("mode") if body.get("mode") in ("dense", "rrf", "rerank") else "rerank"
        top_k = int(body.get("top_k") or 4)
        run_id = store.create_eval_run(ds_id, mode, top_k, user)
        # 后台线程执行：rerank 逐条调 API，40 条约数十秒，不能阻塞请求
        threading.Thread(target=_execute_run, args=(run_id,), daemon=True).start()
        return JSONResponse({"ok": True, "run_id": run_id}, status_code=202)

    @app.get("/api/admin/eval/runs", include_in_schema=False)
    async def _runs(request: fastapi.Request, dataset_id: int = 0, limit: int = 50):
        _require_admin(request, app)
        return JSONResponse(store.list_eval_runs(dataset_id or None, limit))

    @app.get("/api/admin/eval/runs/{run_id}", include_in_schema=False)
    async def _run_detail(run_id: int, request: fastapi.Request):
        _require_admin(request, app)
        run = store.get_eval_run(run_id)
        if not run:
            raise HTTPException(status_code=404, detail="评测运行不存在")
        return JSONResponse(run)

    # ================= 质量监控 =================
    @app.get("/api/admin/quality", include_in_schema=False)
    async def _quality(request: fastapi.Request, days: int = 30):
        """质量信号聚合：线上反馈 + 拒答次数 + 缓存 + 评测 Recall 趋势"""
        _require_admin(request, app)
        ov = store.stats_overview()
        try:
            cache = semantic_cache.stats()
        except Exception:  # noqa: BLE001
            cache = {"entries": 0, "total_hits": 0}
        # 评测趋势：按天取各 mode 的最高 recall
        runs = store.list_eval_runs(limit=200)
        by_day: dict[tuple, float] = {}
        for r in runs:
            if r.get("status") != "done":
                continue
            day = datetime.fromtimestamp(r["created_at"]).strftime("%Y-%m-%d")
            key = (day, r.get("mode") or "rerank")
            by_day[key] = max(by_day.get(key, 0.0), float(r.get("recall") or 0))
        trend = [{"date": d, "mode": m, "recall": v}
                 for (d, m), v in sorted(by_day.items())]
        return JSONResponse({
            "feedback": {
                "up": ov.get("feedback_up", 0),
                "down": ov.get("feedback_down", 0),
                "badcase_pending": ov.get("badcase_pending", 0),
            },
            "refusals": _count_refusals(days),
            "cache": cache,
            "eval_trend": trend,
        })
