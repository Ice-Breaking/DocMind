"""检索调优实验室 API：调试检索链路（召回结果 + 分数 + 路线 + 各阶段耗时）。

权限：仅管理员（调优属运维操作）；ACL 照常生效（只能调试自己可见文档）。
数据源：kb_registry（多 KB 懒加载）+ trace_log.jsonl（阶段耗时统计）。
"""
import json
import os
from collections import defaultdict

import fastapi
from fastapi import HTTPException
from fastapi.responses import JSONResponse

from docmind import acl, config
from docmind.admin import _require_admin


def _get_retriever(kb_id: str):
    """取指定 KB 的检索器；default 用 core 单例，其余走懒加载注册表"""
    if not kb_id or kb_id == "default":
        from docmind import core
        return core._shared_state.get("retriever")
    from docmind.rag.kb_registry import get_registry
    _store, retriever = get_registry().get(kb_id)
    return retriever


def register_retrieval_routes(app) -> None:

    @app.post("/api/retrieval/debug", include_in_schema=False)
    async def _debug(request: fastapi.Request):
        """输入问题 → 返回召回明细（分数/来源/排名）+ 路线 + 各阶段耗时"""
        _require_admin(request, app)
        try:
            body = await request.json()
        except Exception:  # noqa: BLE001
            raise HTTPException(status_code=400, detail="请求体必须是合法 JSON")
        question = str(body.get("question") or "").strip()
        if not question:
            raise HTTPException(status_code=400, detail="question 必填")
        kb_id = str(body.get("kb_id") or "default")
        top_k = int(body.get("top_k") or config.TOP_K)
        rerank = bool(body.get("rerank", True))

        retriever = _get_retriever(kb_id)
        if retriever is None:
            raise HTTPException(status_code=503, detail="检索器尚未就绪，请稍后重试")
        allowed = acl.allowed_docs(acl.get_current_user())
        try:
            result = retriever.search_debug(
                question, top_k=top_k, rerank=rerank, allowed_sources=allowed)
        except Exception as e:  # noqa: BLE001
            raise HTTPException(status_code=500, detail=f"检索调试失败: {e}")
        result["question"] = question
        result["kb_id"] = kb_id
        return JSONResponse(result)

    @app.get("/api/retrieval/stage-stats", include_in_schema=False)
    async def _stage_stats(request: fastapi.Request):
        """链路分析：从 trace 日志聚合各检索阶段的平均/P95 耗时"""
        _require_admin(request, app)
        path = config.TRACE_LOG_PATH
        agg: dict[str, list[float]] = defaultdict(list)
        if os.path.exists(path):
            try:
                with open(path, encoding="utf-8") as f:
                    lines = f.readlines()[-5000:]
                for line in lines:
                    try:
                        d = json.loads(line)
                    except (json.JSONDecodeError, ValueError):
                        continue
                    name = str(d.get("name", ""))
                    if name.startswith("retrieval:") and isinstance(d.get("duration_ms"), (int, float)):
                        agg[name].append(float(d["duration_ms"]))
            except OSError:
                pass
        stages = []
        for name, arr in sorted(agg.items()):
            arr.sort()
            p95 = arr[min(len(arr) - 1, int(len(arr) * 0.95))] if arr else 0
            stages.append({
                "stage": name,
                "count": len(arr),
                "avg_ms": round(sum(arr) / len(arr), 1) if arr else 0,
                "p95_ms": round(p95, 1),
            })
        return JSONResponse({"stages": stages})
