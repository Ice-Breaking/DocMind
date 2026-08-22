"""平台化 API：API Key 管理 + 开放检索端点 + 模型在线配置。

- API Key：明文仅创建时返回一次，库里只存前缀 + SHA256 哈希；支持吊销/轮换/过期
- 开放检索：POST /open/v1/retrieve，Authorization: Bearer dm_xxx，
  供企业现有系统（OA/客服/自研 Agent）集成，scope 限定可用知识库
- 模型管理：LLM / Embedding / Rerank 在线配置 + 连通性测试 + 生效切换，
  生效后 llm.py 运行时读取（聊天立即生效；Embedding 换模型需全量重建索引）
"""
import time

import fastapi
from fastapi import HTTPException
from fastapi.responses import JSONResponse

from docmind import store
from docmind.admin import _require_admin


def register_platform_routes(app) -> None:

    # ================= API Key 管理（管理端） =================
    @app.get("/api/admin/api-keys", include_in_schema=False)
    async def _list_keys(request: fastapi.Request):
        _require_admin(request, app)
        return JSONResponse(store.list_api_keys())

    @app.post("/api/admin/api-keys", include_in_schema=False)
    async def _create_key(request: fastapi.Request):
        user = _require_admin(request, app)
        body = await request.json()
        name = str(body.get("name") or "").strip()
        if not name:
            raise HTTPException(status_code=400, detail="name 必填")
        scope = body.get("scope_kb_ids") or []
        if not isinstance(scope, list):
            raise HTTPException(status_code=400, detail="scope_kb_ids 必须是数组")
        expires_at = None
        days = body.get("expires_days")
        if days:
            expires_at = time.time() + int(days) * 86400
        key = store.create_api_key(name, scope, user, expires_at)
        store.record_audit(user, "apikey.create", f"key#{key['id']}",
                           f"{name}; scope={scope or 'all'}")
        return JSONResponse(key, status_code=201)

    @app.delete("/api/admin/api-keys/{key_id}", include_in_schema=False)
    async def _revoke_key(key_id: int, request: fastapi.Request):
        _require_admin(request, app)
        if not store.revoke_api_key(key_id):
            raise HTTPException(status_code=404, detail="密钥不存在或已吊销")
        user = _require_admin(request, app)
        store.record_audit(user, "apikey.revoke", f"key#{key_id}")
        return {"ok": True}

    @app.post("/api/admin/api-keys/{key_id}/rotate", include_in_schema=False)
    async def _rotate_key(key_id: int, request: fastapi.Request):
        """轮换：吊销旧密钥并以相同名称/范围签发新密钥（明文仅本次返回）"""
        user = _require_admin(request, app)
        rows = [k for k in store.list_api_keys() if k["id"] == key_id]
        if not rows:
            raise HTTPException(status_code=404, detail="密钥不存在")
        old = rows[0]
        store.revoke_api_key(key_id)
        new = store.create_api_key(
            old["name"], old["scope_kb_ids"], user, old.get("expires_at"))
        store.record_audit(user, "apikey.rotate", f"key#{key_id}",
                           f"new key#{new['id']}")
        return JSONResponse(new, status_code=201)

    # ================= 开放检索端点（Bearer Key 鉴权） =================
    @app.post("/open/v1/retrieve", include_in_schema=False)
    async def _open_retrieve(request: fastapi.Request):
        """开放检索 API：question → 带分数的召回结果。
        body: {question, kb_id?, kb_ids?, top_k?}；scope 为空表示全部知识库"""
        auth = request.headers.get("authorization", "")
        if not auth.lower().startswith("bearer "):
            raise HTTPException(status_code=401,
                                detail="需要 Authorization: Bearer <API Key>")
        key_row = store.validate_api_key(auth[7:].strip())
        if not key_row:
            raise HTTPException(status_code=401, detail="API Key 无效、已吊销或已过期")

        try:
            body = await request.json()
        except Exception:  # noqa: BLE001
            raise HTTPException(status_code=400, detail="请求体必须是合法 JSON")
        question = str(body.get("question") or "").strip()
        if not question:
            raise HTTPException(status_code=400, detail="question 必填")
        kb_ids = body.get("kb_ids")
        if not kb_ids and body.get("kb_id"):
            kb_ids = [body["kb_id"]]
        kb_ids = [str(k) for k in (kb_ids or ["default"]) if k]

        # scope 校验：密钥授权范围外的知识库直接 403
        scope = key_row["scope_kb_ids"]
        if scope:
            denied = [k for k in kb_ids if k not in scope]
            if denied:
                raise HTTPException(status_code=403,
                                    detail=f"知识库超出密钥授权范围: {', '.join(denied)}")

        top_k = int(body.get("top_k") or 4)
        try:
            if kb_ids == ["default"]:
                from docmind import core
                retriever = core._shared_state.get("retriever")
                if retriever is None:
                    raise HTTPException(status_code=503, detail="检索器尚未就绪")
                hits = retriever.search(question, top_k=top_k, rerank=True)
            else:
                from docmind.rag.kb_registry import get_registry
                hits = get_registry().search_multi(kb_ids, question, top_k=top_k)
        except HTTPException:
            raise
        except Exception as e:  # noqa: BLE001
            raise HTTPException(status_code=500, detail=f"检索失败: {e}")

        try:
            store.touch_api_key(key_row["id"])
        except Exception:  # noqa: BLE001 - 使用时间记录失败不影响响应
            pass

        return JSONResponse({
            "question": question,
            "kb_ids": kb_ids,
            "count": len(hits),
            "hits": [
                {"rank": i, "text": h.text, "source": h.source,
                 "page": getattr(h, "page", None), "score": round(float(h.score), 4)}
                for i, h in enumerate(hits, 1)
            ],
        })

    @app.post("/open/v1/chat", include_in_schema=False)
    async def _open_chat(request: fastapi.Request):
        """开放问答 API：question → 完整回答（含引用），供企业系统嵌入集成。
        body: {question, kb_ids?}；走 Agent 主链路（检索+推理），
        不做用户级 ACL（API Key 的 scope 即授权边界），不写会话库"""
        auth = request.headers.get("authorization", "")
        if not auth.lower().startswith("bearer "):
            raise HTTPException(status_code=401,
                                detail="需要 Authorization: Bearer <API Key>")
        key_row = store.validate_api_key(auth[7:].strip())
        if not key_row:
            raise HTTPException(status_code=401, detail="API Key 无效、已吊销或已过期")

        try:
            body = await request.json()
        except Exception:  # noqa: BLE001
            raise HTTPException(status_code=400, detail="请求体必须是合法 JSON")
        question = str(body.get("question") or "").strip()
        if not question:
            raise HTTPException(status_code=400, detail="question 必填")
        kb_ids = [str(k) for k in (body.get("kb_ids") or ["default"]) if k]

        scope = key_row["scope_kb_ids"]
        if scope:
            denied = [k for k in kb_ids if k not in scope]
            if denied:
                raise HTTPException(status_code=403,
                                    detail=f"知识库超出密钥授权范围: {', '.join(denied)}")

        from docmind import core
        from docmind.chat_stream import stream_events
        registry = core._shared_state.get("registry")
        if registry is None:
            raise HTTPException(status_code=503, detail="服务尚未就绪")

        # API Key 不关联终端用户：检索不做文档级 ACL（scope 即边界），
        # 传非 default 的 assistant_id 跳过缓存读写（开放接口语义要求
        # 每次现算，且不污染用户缓存）
        from docmind import acl
        acl.set_current_user("")
        agent = core.create_agent(registry)
        kb_tok = None
        try:
            from docmind.chat_stream import current_kb_ids
            kb_tok = current_kb_ids.set([k for k in kb_ids if k != "default"])
            final_answer = ""
            for ev in stream_events(agent, question, user="",
                                     assistant_id="api"):
                if ev.get("kind") == "final" and not ev.get("failed"):
                    final_answer = ev["answer"]
            if not final_answer:
                raise HTTPException(status_code=502, detail="回答生成失败，请重试")
        except HTTPException:
            raise
        except Exception as e:  # noqa: BLE001
            raise HTTPException(status_code=500, detail=f"问答失败: {e}")
        finally:
            if kb_tok is not None:
                current_kb_ids.reset(kb_tok)

        try:
            store.touch_api_key(key_row["id"])
        except Exception:  # noqa: BLE001
            pass

        return JSONResponse({"question": question, "answer": final_answer})

    # ================= 模型在线配置（管理端） =================
    @app.get("/api/admin/models", include_in_schema=False)
    async def _list_models(request: fastapi.Request, kind: str = ""):
        _require_admin(request, app)
        return JSONResponse(store.list_models(kind or None))

    @app.post("/api/admin/models", include_in_schema=False)
    async def _create_model(request: fastapi.Request):
        user = _require_admin(request, app)
        body = await request.json()
        kind = body.get("kind")
        if kind not in ("llm", "embedding", "rerank"):
            raise HTTPException(status_code=400, detail="kind 必须是 llm/embedding/rerank")
        name = str(body.get("name") or "").strip()
        model_name = str(body.get("model_name") or "").strip()
        if not name or not model_name:
            raise HTTPException(status_code=400, detail="name 与 model_name 必填")
        m = store.create_model(name, kind, str(body.get("base_url") or ""),
                               str(body.get("api_key") or ""), model_name, user)
        store.record_audit(user, "model.create", f"model#{m['id']}",
                           f"{kind}:{model_name}")
        return JSONResponse({"ok": True, "id": m["id"]}, status_code=201)

    @app.put("/api/admin/models/{mid}", include_in_schema=False)
    async def _update_model(mid: int, request: fastapi.Request):
        _require_admin(request, app)
        if not store.get_model(mid):
            raise HTTPException(status_code=404, detail="模型不存在")
        body = await request.json()
        store.update_model(
            mid,
            name=(str(body["name"]).strip() if "name" in body else None),
            base_url=(str(body["base_url"]) if "base_url" in body else None),
            api_key=(str(body["api_key"]) if "api_key" in body else None),
            model_name=(str(body["model_name"]).strip() if "model_name" in body else None))
        return {"ok": True}

    @app.delete("/api/admin/models/{mid}", include_in_schema=False)
    async def _delete_model(mid: int, request: fastapi.Request):
        _require_admin(request, app)
        if not store.delete_model(mid):
            raise HTTPException(status_code=404, detail="模型不存在")
        user = _require_admin(request, app)
        store.record_audit(user, "model.delete", f"model#{mid}")
        return {"ok": True}

    @app.post("/api/admin/models/{mid}/activate", include_in_schema=False)
    async def _activate_model(mid: int, request: fastapi.Request):
        _require_admin(request, app)
        if not store.set_active_model(mid):
            raise HTTPException(status_code=404, detail="模型不存在")
        user = _require_admin(request, app)
        m = store.get_model(mid) or {}
        store.record_audit(user, "model.activate", f"model#{mid}",
                           f"{m.get('kind')}:{m.get('model_name')}")
        return {"ok": True}

    @app.post("/api/admin/models/{mid}/test", include_in_schema=False)
    async def _test_model(mid: int, request: fastapi.Request):
        """连通性测试：llm 发一条最小对话；embedding 向量化一个词；rerank 调精排 API"""
        _require_admin(request, app)
        m = store.get_model(mid)
        if not m:
            raise HTTPException(status_code=404, detail="模型不存在")
        from docmind import config
        base_url = m.get("base_url") or config.DASHSCOPE_BASE_URL
        api_key = m.get("api_key") or config.DASHSCOPE_API_KEY
        t0 = time.time()
        try:
            if m["kind"] == "llm":
                from openai import OpenAI
                cli = OpenAI(api_key=api_key, base_url=base_url, timeout=30.0)
                resp = cli.chat.completions.create(
                    model=m["model_name"],
                    messages=[{"role": "user", "content": "ping"}],
                    max_tokens=8)
                detail = (resp.choices[0].message.content or "").strip()[:60]
            elif m["kind"] == "embedding":
                from openai import OpenAI
                cli = OpenAI(api_key=api_key, base_url=base_url, timeout=30.0)
                resp = cli.embeddings.create(model=m["model_name"], input=["ping"])
                detail = f"向量维度 {len(resp.data[0].embedding)}"
            else:  # rerank：百炼原生 rerank API（与 hybrid.py 同一端点）
                import requests
                from docmind.rag.hybrid import RERANK_URL
                r = requests.post(
                    RERANK_URL,
                    json={"model": m["model_name"],
                          "input": {"query": "ping", "documents": ["pong"]},
                          "parameters": {"top_n": 1, "return_documents": False}},
                    headers={"Authorization": f"Bearer {api_key}"},
                    timeout=30)
                r.raise_for_status()
                detail = f"top1 相关度 {r.json()['output']['results'][0]['relevance_score']:.3f}"
            return {"ok": True,
                    "latency_ms": round((time.time() - t0) * 1000),
                    "detail": detail or "连通正常"}
        except Exception as e:  # noqa: BLE001 - 测试结果如实返回
            return JSONResponse({"ok": False, "detail": str(e)[:200]},
                                status_code=200)
