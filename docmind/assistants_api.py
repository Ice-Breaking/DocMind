"""助手 / 知识库管理 REST API + 个人看板。

权限：复用 Gradio 登录 cookie（与 admin.py 相同的解析方式），未登录 401；
须先修改密码的用户返回 403（code=MUST_CHANGE_PWD）。
数据源：store（assistants / knowledge_bases / sessions / 统计）+ semantic_cache 命中率。
"""
import os

import fastapi
from fastapi import HTTPException
from fastapi.responses import JSONResponse

from docmind import semantic_cache, store


# ---- 当前用户解析：复用 Gradio 登录 cookie（与 admin.py 保持一致） ----
def _current_user(request, app) -> str:
    """自研 token 会话(web_auth),与 app.py 登录链路一致"""
    from docmind import web_auth
    return web_auth.current_user(request)

def _require_user(request, app) -> str:
    """校验登录态；被要求强制改密的用户返回 403"""
    user = _current_user(request, app)
    if not user:
        raise HTTPException(status_code=401, detail="未登录")
    if store.get_must_change_pwd(user):
        raise HTTPException(status_code=403,
                            detail={"code": "MUST_CHANGE_PWD", "message": "请先修改密码"})
    return user


async def _json_body(request) -> dict:
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        raise HTTPException(status_code=400, detail="请求体必须是合法 JSON")
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="请求体必须是 JSON 对象")
    return body


def _kb_doc_stats(kb: dict) -> dict:
    """统计 KB 文档目录的文件数与总字节数；目录不存在返回零值。"""
    doc_dir = kb.get("doc_dir") or ""
    count, size = 0, 0
    if doc_dir and os.path.isdir(doc_dir):
        try:
            for name in os.listdir(doc_dir):
                fp = os.path.join(doc_dir, name)
                if os.path.isfile(fp):
                    count += 1
                    size += os.path.getsize(fp)
        except OSError:
            pass
    return {"doc_count": count, "doc_size": size}


def _cache_hit_rate() -> float:
    """语义缓存命中率：hits/(hits+misses)；无查询数据时返回 0.0"""
    try:
        s = semantic_cache.stats()
    except Exception:  # noqa: BLE001
        return 0.0
    hits = float(s.get("total_hits") or 0)
    misses = float(s.get("misses") or 0)
    lookups = float(s.get("lookups") or 0)
    # stats() 未记录 misses/lookups 时无法计算真实命中率，返回 0.0
    if "misses" not in s and "lookups" not in s:
        return 0.0
    denom = lookups or (hits + misses)
    return round(hits / denom, 4) if denom > 0 else 0.0


def register_assistant_routes(app) -> None:
    # 幂等播种默认知识库与默认助手，保证首次使用前存在
    store.ensure_default_kb_and_assistant()

    # ================= 个人看板 =================
    @app.get("/api/dashboard", include_in_schema=False)
    async def _dashboard(request: fastapi.Request):
        user = _require_user(request, app)
        try:
            data = store.stats_for_user(user)
            data["cache_hit_rate"] = _cache_hit_rate()
            data["recent_sessions"] = store.list_sessions(user, limit=5)
        except Exception as e:  # noqa: BLE001
            raise HTTPException(status_code=500, detail=f"看板数据加载失败: {e}")
        return JSONResponse(data)

    # ================= 助手 CRUD =================
    @app.get("/api/assistants", include_in_schema=False)
    async def _list_assistants(request: fastapi.Request):
        _require_user(request, app)
        try:
            return JSONResponse(store.list_assistants())
        except Exception as e:  # noqa: BLE001
            raise HTTPException(status_code=500, detail=f"助手列表加载失败: {e}")

    @app.post("/api/assistants", include_in_schema=False)
    async def _create_assistant(request: fastapi.Request):
        user = _require_user(request, app)
        body = await _json_body(request)
        name = str(body.get("name") or "").strip()
        if not name:
            raise HTTPException(status_code=400, detail="name 必填")
        kb_ids = body.get("kb_ids")
        if kb_ids is not None and not isinstance(kb_ids, list):
            raise HTTPException(status_code=400, detail="kb_ids 必须是数组")
        model_config = body.get("model_config")
        if model_config is not None and not isinstance(model_config, dict):
            raise HTTPException(status_code=400, detail="model_config 必须是对象")
        try:
            a = store.create_assistant(
                name, owner=user, avatar=str(body.get("avatar") or ""),
                system_prompt=str(body.get("system_prompt") or ""),
                kb_ids=kb_ids, model_config=model_config)
        except Exception as e:  # noqa: BLE001
            raise HTTPException(status_code=500, detail=f"助手创建失败: {e}")
        store.record_audit(user, "assistant.create", f"assistant:{a['id']}", name)
        return JSONResponse(a, status_code=201)

    @app.get("/api/assistants/{aid}", include_in_schema=False)
    async def _get_assistant(aid: str, request: fastapi.Request):
        _require_user(request, app)
        a = store.get_assistant(aid)
        if not a:
            raise HTTPException(status_code=404, detail="助手不存在")
        return JSONResponse(a)

    @app.put("/api/assistants/{aid}", include_in_schema=False)
    async def _update_assistant(aid: str, request: fastapi.Request):
        _require_user(request, app)
        if not store.get_assistant(aid):
            raise HTTPException(status_code=404, detail="助手不存在")
        body = await _json_body(request)
        fields = {}
        for k in ("name", "avatar", "system_prompt", "kb_ids", "model_config"):
            if k in body and body[k] is not None:
                fields[k] = body[k]
        if "name" in fields and not str(fields["name"]).strip():
            raise HTTPException(status_code=400, detail="name 不能为空")
        for k in ("kb_ids", "model_config"):
            if k in fields and not isinstance(fields[k], list if k == "kb_ids" else dict):
                raise HTTPException(status_code=400, detail=f"{k} 类型非法")
        if not fields:
            return JSONResponse(store.get_assistant(aid))
        try:
            a = store.update_assistant(aid, **fields)
        except Exception as e:  # noqa: BLE001
            raise HTTPException(status_code=500, detail=f"助手更新失败: {e}")
        if not a:
            raise HTTPException(status_code=400, detail="更新参数非法")
        return JSONResponse(a)

    @app.delete("/api/assistants/{aid}", include_in_schema=False)
    async def _delete_assistant(aid: str, request: fastapi.Request):
        _require_user(request, app)
        if aid == "default":
            raise HTTPException(status_code=400, detail="默认助手不可删除")
        try:
            ok = store.delete_assistant(aid)
        except Exception as e:  # noqa: BLE001
            raise HTTPException(status_code=500, detail=f"助手删除失败: {e}")
        if not ok:
            raise HTTPException(status_code=404, detail="助手不存在")
        user = _current_user(request, app)
        store.record_audit(user, "assistant.delete", f"assistant:{aid}")
        return {"ok": True}

    # ================= 知识库 CRUD =================
    @app.get("/api/kbs", include_in_schema=False)
    async def _list_kbs(request: fastapi.Request):
        _require_user(request, app)
        try:
            kbs = store.list_kbs()
            for kb in kbs:
                kb.update(_kb_doc_stats(kb))
            return JSONResponse(kbs)
        except Exception as e:  # noqa: BLE001
            raise HTTPException(status_code=500, detail=f"知识库列表加载失败: {e}")

    @app.post("/api/kbs", include_in_schema=False)
    async def _create_kb(request: fastapi.Request):
        _require_user(request, app)
        body = await _json_body(request)
        name = str(body.get("name") or "").strip()
        if not name:
            raise HTTPException(status_code=400, detail="name 必填")
        try:
            kb = store.create_kb(name, str(body.get("description") or ""))
        except Exception as e:  # noqa: BLE001
            raise HTTPException(status_code=500, detail=f"知识库创建失败: {e}")
        user = _current_user(request, app)
        store.record_audit(user, "kb.create", f"kb:{kb['id']}", name)
        return JSONResponse(kb, status_code=201)

    @app.delete("/api/kbs/{kb_id}", include_in_schema=False)
    async def _delete_kb(kb_id: str, request: fastapi.Request):
        _require_user(request, app)
        if kb_id == "default":
            raise HTTPException(status_code=400, detail="默认知识库不可删除")
        if not store.get_kb(kb_id):
            raise HTTPException(status_code=404, detail="知识库不存在")
        if store.kb_used_by_assistants(kb_id):
            raise HTTPException(status_code=400, detail="该知识库仍被助手绑定")
        try:
            store.delete_kb(kb_id)
        except Exception as e:  # noqa: BLE001
            raise HTTPException(status_code=500, detail=f"知识库删除失败: {e}")
        try:
            from docmind.rag.kb_registry import get_registry
            get_registry().invalidate(kb_id)
        except Exception:  # noqa: BLE001
            pass
        user = _current_user(request, app)
        store.record_audit(user, "kb.delete", f"kb:{kb_id}")
        return {"ok": True}

    # ================= 入库任务 =================
    @app.get("/api/kbs/{kb_id}/tasks", include_in_schema=False)
    async def _kb_tasks(kb_id: str, request: fastapi.Request, limit: int = 50):
        _require_user(request, app)
        if not store.get_kb(kb_id):
            raise HTTPException(status_code=404, detail="知识库不存在")
        return JSONResponse(store.list_ingest_tasks(kb_id, max(1, min(limit, 200))))

    # ================= 重建索引（异步 + 任务追踪） =================
    @app.post("/api/kbs/{kb_id}/reindex", include_in_schema=False)
    async def _reindex_kb(kb_id: str, request: fastapi.Request):
        """异步重建：立即返回 task_id，后台线程执行，进度查 /api/kbs/{kb_id}/tasks"""
        user = _require_user(request, app)
        if not store.get_kb(kb_id):
            raise HTTPException(status_code=404, detail="知识库不存在")
        task_id = store.create_ingest_task(kb_id, "*", "reindex", "running",
                                           "正在重建索引…", user)
        store.record_audit(user, "kb.reindex", f"kb:{kb_id}", f"task#{task_id}")
        import threading
        threading.Thread(target=_do_reindex, args=(kb_id, task_id),
                         daemon=True).start()
        return JSONResponse({"ok": True, "task_id": task_id}, status_code=202)


def _do_reindex(kb_id: str, task_id: int) -> None:
    """后台重建线程：default 走 core 增量重建；其余库失效缓存后强制增量重建。
    成功后把该库挂起的上传/删除任务标记为已生效。"""
    try:
        if kb_id == "default":
            from docmind import core
            result = core.rebuild_knowledge_index()
            if isinstance(result, dict) and "error" in result:
                raise RuntimeError(result["error"])
        else:
            import os as _os
            from docmind.rag.kb_registry import get_registry
            reg = get_registry()
            reg.invalidate(kb_id)
            vstore, _retr = reg.get(kb_id)
            kb = store.get_kb(kb_id) or {}
            doc_dir = kb.get("doc_dir") or _os.path.join("data", "kb_docs", kb_id)
            if _os.path.isdir(doc_dir):
                result = vstore.rebuild_incremental(doc_dir)
            else:
                result = {"note": "文档目录不存在，跳过"}
        if isinstance(result, dict):
            if result.get("full_rebuild"):
                msg = f"全量重建完成，共 {result.get('chunks')} 个切片"
            elif "added" in result:
                msg = (f"增量重建：新增 {result.get('added')} / 修改 {result.get('modified')}"
                       f" / 删除 {result.get('removed')} / 未变 {result.get('unchanged')}"
                       f"，共 {result.get('chunks')} 个切片")
            else:
                msg = result.get("note") or "索引已更新"
        else:
            msg = "索引已更新"
        store.update_ingest_task(task_id, "done", msg)
        store.complete_pending_tasks(kb_id)
        # 索引已变化：清空答案缓存，防止旧缓存继续命中已删除/修改的文档内容
        try:
            from docmind import agent_reasoning_cache, semantic_cache
            n1 = semantic_cache.clear()
            n2 = agent_reasoning_cache.clear()
            if n1 or n2:
                print(f"[reindex] 知识库变更，已清空答案缓存：语义 {n1} 条 / 推理 {n2} 条")
        except Exception as e:  # noqa: BLE001 - 缓存清理失败不影响重建结果
            print(f"[reindex] 答案缓存清理失败（不影响索引）: {e}")
    except Exception as e:  # noqa: BLE001 - 后台线程异常收敛为任务失败状态
        store.update_ingest_task(task_id, "error", str(e)[:200])
