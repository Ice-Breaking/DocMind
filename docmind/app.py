"""DocMind Web 服务入口（纯 FastAPI 宿主）：装配全部 REST/SSE 路由并托管 React SPA 构建产物。

启动：python -m docmind.app
监听地址/端口可用环境变量 DOCMIND_HOST / DOCMIND_PORT 覆盖
（Docker 部署时容器内需要 0.0.0.0）。
"""
import os

# macOS venv 常缺系统根证书：必须在任何网络库（gradio/openai/aiohttp）初始化
# OpenSSL 默认证书库之前指向 certifi 证书包，否则进程内首次 TLS 会缓存空证书库，
# 导致后续 ASR websocket 报 CERTIFICATE_VERIFY_FAILED
import certifi as _certifi

os.environ["SSL_CERT_FILE"] = _certifi.where()
os.environ.setdefault("REQUESTS_CA_BUNDLE", _certifi.where())


from docmind.logging_setup import setup_logging

setup_logging()

import logging

from docmind import acl, config
from docmind import store as chatstore   # 别名：避免遮蔽 build_agent 返回的 VectorStore store
from docmind.core import build_shared, create_agent

logger = logging.getLogger(__name__)

logger.info("正在装配 Agent（加载知识库、连接 MCP Server）...")
registry, store, mcp_connections = build_shared()
tool_names = list(registry.tools.keys())

# ----------------------------------------------------------------
# 去 Gradio 化：样式/交互/界面段已删除（React SPA 为唯一前端，
# 生产由 nginx 或下方 dist 静态挂载服务；旧 Gradio UI 为死代码）

if __name__ == "__main__":
    # 认证门禁：无任何账号时播种 admin（密码取 ADMIN_PASSWORD 环境变量）
    chatstore.ensure_seed_admin()

    # ---- 纯 FastAPI 宿主（原 Gradio launch 已移除） ----
    import uvicorn
    import fastapi
    from fastapi import FastAPI
    from fastapi.responses import FileResponse
    from docmind import web_auth

    app = FastAPI(title="DocMind")

    # ---- 登录 / 登出（自研 token 会话，替代 Gradio auth） ----
    @app.post("/login", include_in_schema=False)
    async def _login(request: fastapi.Request):
        form = await request.form()
        username = str(form.get("username") or "").strip()
        password = str(form.get("password") or "")
        if not username or not password:
            raise HTTPException(status_code=400, detail="请输入用户名和密码")
        remaining = web_auth.is_locked(username)
        if remaining:
            raise HTTPException(status_code=403,
                                detail=f"失败次数过多已锁定，请 {remaining // 60 + 1} 分钟后再试")
        from docmind import ldap_auth
        via = ""
        if chatstore.verify_user(username, password):
            via = "local"
        elif ldap_auth.authenticate(username, password):
            chatstore.ensure_external_user(username)
            via = "ldap"
        if not via:
            web_auth.record_failure(username)
            logger.warning(f"登录失败 user={username} ip={web_auth.client_ip()}")
            raise HTTPException(status_code=400, detail="用户名或密码错误")
        web_auth.clear_failures(username)
        chatstore.record_audit(username, "login", via)
        token = web_auth.issue(username)
        # success 字段为 Gradio 登录契约保留：web/src/api/core.ts 以 j.success 判定成败，
        # 缺失会导致浏览器端密码正确也弹「用户名或密码错误」（2026-08 去 Gradio 化回归）
        resp = JSONResponse({"ok": True, "success": True, "user": username,
                             "must_change_pwd": chatstore.get_must_change_pwd(username)})
        resp.set_cookie(web_auth.TOKEN_COOKIE, token, httponly=True,
                        samesite="lax", max_age=web_auth.TOKEN_TTL, path="/")
        return resp

    async def _do_logout(request: fastapi.Request):
        web_auth.revoke(request.cookies.get(web_auth.TOKEN_COOKIE))
        resp = JSONResponse({"ok": True})
        resp.delete_cookie(web_auth.TOKEN_COOKIE, path="/")
        return resp

    @app.api_route("/logout", methods=["GET", "POST"], include_in_schema=False)
    async def _logout(request: fastapi.Request):
        return await _do_logout(request)

    # ---- 中间件：IP 注入 + CSRF(hostname 级) + 安全响应头 + HTTP 指标 ----
    # 纯 FastAPI 下 app 未 started,正常注册即可(原 Gradio 时代必须 hack 重建
    # stack 且 metrics 一直降级的问题一并消除)
    import time as _metrics_time
    from urllib.parse import urlparse as _urlparse
    from docmind.metrics import HTTP_LATENCY, HTTP_REQUESTS, normalize_http_path
    _trusted = {o.strip() for o in os.getenv("TRUSTED_ORIGINS", "").split(",") if o.strip()}

    @app.middleware("http")
    async def security_and_metrics(request, call_next):
        web_auth.set_client_ip(request.client.host if request.client else "")
        # CSRF:浏览器状态变更请求校验 Origin 可信(hostname 级——同主机不同
        # 端口是本机代理场景放行;恶意域名仍拦截;无 Origin 的服务端调用不受影响)
        if request.method in ("POST", "PUT", "DELETE", "PATCH"):
            origin = request.headers.get("origin", "")
            # Origin:"null" 不再放行（sandboxed iframe/file:// 场景才出现）：
            # 正常浏览器同源请求不会发 null，放行等于给沙箱逃逸留口子
            if origin and origin not in _trusted:
                host = request.headers.get("host", "")
                o_h = _urlparse(origin).hostname or ""
                h_h = _urlparse(f"http://{host}").hostname if host else ""
                if not o_h or not h_h or o_h != h_h:
                    return JSONResponse({"detail": "跨站请求被拒绝"}, status_code=403)
        # 计时起点：perf_counter 单调时钟测耗时；此前误用 time()-monotonic()
        # （两个不同基准相减），docmind_http_request_duration_seconds 全是废数据
        _t0 = _metrics_time.perf_counter()
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
        response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        # 静态资源文件名带内容 hash → 永久强缓存（发版即失效靠 html 引用更新）；
        # 入口 html 本身不缓存，保证发布后浏览器立刻拿到新版本引用
        if request.url.path.startswith(("/assets/", "/vendor/")):
            response.headers.setdefault(
                "Cache-Control", "public, max-age=31536000, immutable")
        elif request.url.path in ("/", "/index.html"):
            response.headers.setdefault("Cache-Control", "no-cache")
        if request.url.path not in ("/metrics", "/health"):
            try:
                # path 归一化：动态段(会话 id/上传文件名等)折叠为 {id}，
                # 否则每个新会话都派生新时间序列(标签基数爆炸)
                _norm = normalize_http_path(request.url.path)
                HTTP_REQUESTS.labels(method=request.method, path=_norm,
                                     status=response.status_code).inc()
                HTTP_LATENCY.labels(method=request.method,
                                    path=_norm).observe(
                    _metrics_time.perf_counter() - _t0)
            except Exception:  # noqa: BLE001
                pass
        return response

    @app.get("/metrics", include_in_schema=False)
    async def _metrics_route():
        from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
        from fastapi.responses import Response as _MR
        return _MR(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)

    # (旧 CSRF/指标中间件已并入 security_and_metrics 正常注册)

    # ---- 健康检查：Docker HEALTHCHECK / 监控探活（不要求登录态，只暴露内部状态） ----
    @app.get("/health", include_in_schema=False)
    async def _health():
        """Lightweight health check for Docker HEALTHCHECK and monitoring."""
        import shutil
        from fastapi.responses import JSONResponse
        checks = {}

        # SQLite 连通性
        try:
            chatstore._conn().execute("SELECT 1").fetchone()
            checks["database"] = "ok"
        except Exception as e:  # noqa: BLE001
            checks["database"] = f"error: {e}"

        # 磁盘剩余空间（< 100MB 告警）
        try:
            disk = shutil.disk_usage(os.path.join(config.PROJECT_ROOT, "data"))
            free_mb = disk.free / (1024 * 1024)
            checks["disk_free_mb"] = round(free_mb, 1)
            checks["disk"] = "low" if free_mb < 100 else "ok"
        except Exception as e:  # noqa: BLE001
            checks["disk"] = f"error: {e}"

        # 知识库加载状态
        try:
            checks["knowledge_chunks"] = len(store.chunks) if hasattr(store, "chunks") else 0
        except Exception:  # noqa: BLE001
            checks["knowledge_chunks"] = 0

        all_ok = all(v == "ok" or isinstance(v, (int, float)) for v in checks.values())
        return JSONResponse(
            status_code=200 if all_ok else 503,
            content={"status": "healthy" if all_ok else "degraded", "checks": checks},
        )

    # ---- 文档预览：vendored pdf.js + 知识库原文（引用溯源直达） ----
    import fastapi
    from fastapi import HTTPException, Query
    from fastapi.responses import PlainTextResponse

    _app_dir = os.path.dirname(os.path.abspath(__file__))
    _vendor_dir = os.path.join(_app_dir, "vendor")
    _knowledge_dir = config.KNOWLEDGE_DIR

    @app.get("/vendor/{name}", include_in_schema=False)
    async def _serve_vendor(name: str):
        safe = os.path.basename(name)  # 防路径穿越
        path = os.path.join(_vendor_dir, safe)
        if not os.path.isfile(path):
            raise HTTPException(status_code=404)
        return FileResponse(path, media_type="application/javascript"
                            if safe.endswith(".js") else "application/octet-stream")

    # methods 含 HEAD：前端先 HEAD 探测 docx 能否转 PDF（此 App 的 .get 不自动挂 HEAD）
    @app.api_route("/files/{name}", methods=["GET", "HEAD"], include_in_schema=False)
    async def _serve_file(name: str, request: fastapi.Request,
                          as_: str = Query(default=None, alias="as")):
        # 安全校验：登录 + 文档级 ACL——受限文档(如内部机密.md)此前可经
        # 此直链被未登录用户整篇下载，完全绕过检索层的 ACL 隔离(P0 实测)
        user = _require_active_user(request)
        allowed = acl.allowed_docs(user)
        safe = os.path.basename(name)  # 防路径穿越：只允许知识库目录内文件名
        if safe not in allowed:
            raise HTTPException(status_code=404)   # 与不存在同响应,不泄露存在性
        path = os.path.join(_knowledge_dir, safe)
        if not os.path.isfile(path):
            raise HTTPException(status_code=404)
        if as_ == "text":
            # 提取正文文本（docx 无 LibreOffice 时的降级通道；图片走 OCR 缓存）
            from docmind.rag.chunker import _EXTRACTORS
            ext = os.path.splitext(safe)[1].lower()
            try:
                if ext in _EXTRACTORS:
                    text = _EXTRACTORS[ext](path)
                else:
                    with open(path, encoding="utf-8") as f:
                        text = f.read()
            except Exception as e:  # noqa: BLE001
                raise HTTPException(status_code=500, detail=f"文本提取失败: {e}")
            return PlainTextResponse(text)
        if as_ == "sheets" and safe.lower().endswith(".xlsx"):
            # Excel 预览数据：各 Sheet 的行列 JSON（限量防超大表格拖垮前端）
            from fastapi.responses import JSONResponse
            import openpyxl
            MAX_ROWS, MAX_COLS = 500, 60
            try:
                wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
                sheets = []
                for ws in wb.worksheets:
                    rows = []
                    for i, row in enumerate(ws.iter_rows(values_only=True)):
                        if i >= MAX_ROWS:
                            break
                        rows.append(["" if c is None else str(c) for c in row[:MAX_COLS]])
                    sheets.append({"name": ws.title, "rows": rows})
                wb.close()
            except Exception as e:  # noqa: BLE001
                raise HTTPException(status_code=500, detail=f"Excel 解析失败: {e}")
            return JSONResponse({"sheets": sheets})
        if as_ == "pdf" and safe.lower().endswith(".docx"):
            # LibreOffice headless 转 PDF（按源文件 mtime 缓存）；未安装 → 409，前端降级文本预览
            import shutil
            import subprocess
            soffice = shutil.which("soffice") or (
                "/Applications/LibreOffice.app/Contents/MacOS/soffice"
                if os.path.exists("/Applications/LibreOffice.app/Contents/MacOS/soffice") else None)
            if not soffice:
                raise HTTPException(status_code=409, detail="LibreOffice 未安装")
            cache_dir = os.path.join(config.PROJECT_ROOT, "data", "preview_cache")
            os.makedirs(cache_dir, exist_ok=True)
            out_pdf = os.path.join(cache_dir, os.path.splitext(safe)[0] + ".pdf")
            if not os.path.isfile(out_pdf) or os.path.getmtime(out_pdf) < os.path.getmtime(path):
                try:
                    r = subprocess.run(
                        [soffice, "--headless", "--convert-to", "pdf", "--outdir", cache_dir, path],
                        capture_output=True, timeout=120)
                except Exception as e:  # noqa: BLE001
                    raise HTTPException(status_code=500, detail=f"转换失败: {e}")
                if r.returncode != 0 or not os.path.isfile(out_pdf):
                    raise HTTPException(status_code=500, detail="PDF 转换失败")
            return FileResponse(out_pdf, media_type="application/pdf")
        return FileResponse(path)

    # ---- 当前用户解析：自研 token 会话（web_auth） ----
    import fastapi as _fastapi

    def _current_user(request: _fastapi.Request) -> str:
        return web_auth.current_user(request)

    def _require_user(request: _fastapi.Request) -> str:
        return web_auth.require_user(request)

    def _require_active_user(request: _fastapi.Request) -> str:
        return web_auth.require_user(request)

    def _check_session_access(request: _fastapi.Request, session_id: str) -> None:
        """会话归属校验：本人或无主历史会话放行，他人会话 403"""
        owner = chatstore.session_owner(session_id)
        if owner is None:
            raise HTTPException(status_code=404, detail="会话不存在")
        if owner not in ("", _current_user(request)):
            raise HTTPException(status_code=403, detail="无权访问该会话")

    # ---- 反馈闭环：👍/👎 评价（session_id + 消息序号唯一定位，重复点击覆盖） ----
    from typing import Literal
    from pydantic import BaseModel, Field

    class FeedbackIn(BaseModel):
        session_id: str = Field(..., min_length=1, max_length=64)
        seq: int
        rating: Literal["up", "down"]

    @app.post("/api/feedback", include_in_schema=False)
    async def _save_feedback(fb: FeedbackIn, request: _fastapi.Request):
        _require_active_user(request)
        if fb.rating not in ("up", "down"):
            raise HTTPException(status_code=400, detail="rating 必须是 up/down")
        _check_session_access(request, fb.session_id)
        try:
            chatstore.save_feedback(fb.session_id, fb.seq, fb.rating)
        except Exception as e:  # noqa: BLE001
            raise HTTPException(status_code=500, detail=f"反馈保存失败: {e}")
        return {"ok": True}

    @app.get("/api/feedback/{session_id}", include_in_schema=False)
    async def _get_feedback(session_id: str, request: _fastapi.Request):
        _require_active_user(request)
        _check_session_access(request, session_id)
        return chatstore.get_feedback(session_id)

    # ---- 强制修改密码 ----
    class ChangePasswordIn(BaseModel):
        old_password: str = Field(..., min_length=1, max_length=128)
        new_password: str = Field(..., min_length=8, max_length=128)

    @app.post("/api/change-password", include_in_schema=False)
    async def _change_password(body: ChangePasswordIn, request: _fastapi.Request):
        user = _require_user(request)
        # 密码强度：≥8 位且同时含字母与数字（防弱口令 + 直链/撞库组合利用）
        np = body.new_password or ""
        if len(np) < 8 or not any(c.isalpha() for c in np) or not any(c.isdigit() for c in np):
            raise HTTPException(status_code=400,
                                detail="新密码需至少 8 位，且同时包含字母和数字")
        ok, msg = chatstore.change_password(user, body.old_password, body.new_password)
        if not ok:
            raise HTTPException(status_code=400, detail=msg)
        return {"ok": True}

    # ---- 多会话侧边栏：会话列表 + 删除 ----
    # ---- 管理后台：用量看板 / badcase 流转 / 会话审计（仅管理员） ----
    from docmind.admin import register_admin_routes
    register_admin_routes(app)
    from docmind.assistants_api import register_assistant_routes
    register_assistant_routes(app)

    from docmind.docs_api import register_docs_routes
    register_docs_routes(app)
    from docmind.retrieval_api import register_retrieval_routes
    register_retrieval_routes(app)
    from docmind.eval_api import register_eval_routes
    register_eval_routes(app)
    from docmind.platform_api import register_platform_routes
    register_platform_routes(app)
    from docmind.voice_api import register_voice_routes
    register_voice_routes(app)
    from docmind.governance_api import register_governance_routes
    register_governance_routes(app)
    from docmind.users_api import register_users_routes
    register_users_routes(app)
    from docmind import alerts as _alerts_mod
    _alerts_mod.register_alert_routes(app)
    _alerts_mod.start_loop()   # 后台周期评估告警规则（幂等）

    # ---- 引用锚点定位：按用户问题在指定文档内检索最相关片段 ----
    # （复用已构建的 VectorStore，BM25 索引独立构建一次；ACL 感知，无权返回空）
    from docmind.rag.hybrid import HybridRetriever
    locate_retriever = HybridRetriever(store)
    locate_retriever.build()

    @app.get("/api/locate", include_in_schema=False)
    async def _locate(request: _fastapi.Request, doc: str, q: str = ""):
        _require_active_user(request)
        doc = os.path.basename(doc)
        if doc not in acl.allowed_docs(_current_user(request)):
            return {"found": False}   # 无权文档：与"没找到"无差别，不泄露存在性
        if not q.strip():
            return {"found": False}
        hits = locate_retriever.search(q, top_k=1, rerank=False,
                                       allowed_sources={doc})
        if not hits:
            return {"found": False}
        return {"found": True, "text": hits[0].text, "page": hits[0].page}

    # ---- 动态追问：按问答内容生成针对性追问（答案哈希缓存，同答案不重复生成） ----
    import hashlib as _hashlib
    from docmind import suggest as suggest_mod

    class SuggestIn(BaseModel):
        question: str = Field(default="", max_length=4000)
        answer: str = Field(..., min_length=1, max_length=4000)

    @app.post("/api/suggest", include_in_schema=False)
    async def _suggest(body: SuggestIn, request: _fastapi.Request):
        _require_active_user(request)
        answer = body.answer.strip()
        if len(answer) < 80:   # 过短内容（报错/拒答）不生成
            return {"suggestions": []}
        key = _hashlib.sha1(answer.encode("utf-8")).hexdigest()[:16]
        cached = chatstore.get_suggestions(key)
        if cached:
            return {"suggestions": cached, "cached": True}
        items = suggest_mod.generate_suggestions(body.question, answer)
        chatstore.save_suggestions(key, items)
        return {"suggestions": items, "cached": False}

    # ---- SSE 流式聊天（前后端分离新 UI 的应答协议层，与 Gradio 主链路并存） ----
    # 事件：cache/thinking/token/step/error/final/done，见 docmind/chat_stream.py
    import json as _json
    from fastapi.responses import StreamingResponse
    from docmind import chat_stream as chat_stream_mod
    from docmind.metrics import SSE_ACTIVE_STREAMS

    class ChatIn(BaseModel):
        question: str = Field(..., min_length=1, max_length=4000)
        session_id: str = Field(default="", max_length=64)
        assistant_id: str = Field(default="", max_length=64)
        # 图片附件（base64，可带 data URL 前缀）：模型真看图（多模态），非仅 OCR；
        # 支持单张(str)或多张(list,上限 5)
        image_data: "str | list[str]" = Field(default="", max_length=60_000_000)

    @app.post("/api/chat/stream", include_in_schema=False)
    async def _chat_stream(body: ChatIn, request: _fastapi.Request):
        user = _require_active_user(request)   # 401/403 校验须在 try 外，避免被吞成 500
        question = body.question.strip()
        if not question:
            raise HTTPException(status_code=400, detail="question 不能为空")
        # 会话归属校验：防重建/写入他人会话上下文
        if body.session_id:
            owner = chatstore.session_owner(body.session_id)
            if owner not in (None, "", user):
                raise HTTPException(status_code=403, detail="无权访问该会话")

        # 自定义助手接入：解析绑定的 KB 列表与 system prompt；
        # 空串/"default" 走原默认链路（缓存/单库检索/内置提示词均不变）
        assistant_id = getattr(body, "assistant_id", "") or ""
        assistant = chatstore.get_assistant(assistant_id) if assistant_id else None
        kb_ids = (assistant.get("kb_ids") if assistant else []) or []
        sp = (assistant.get("system_prompt") if assistant else "") or None

        # 图片附件：落盘（前端 markdown 展示）+ data URL（多模态消息给模型看图）
        image_md = ""
        img_list: list[str] = []
        raw_imgs = body.image_data or []
        for one in (raw_imgs if isinstance(raw_imgs, list) else [raw_imgs])[:5]:
            if not one:
                continue
            from docmind.docs_api import save_chat_image
            _fname, _url = save_chat_image(one, owner=user)
            image_md += f"![图片](/files/uploads/{_fname})\n"
            img_list.append(_url)
        if image_md:
            image_md += "\n"
        img_data_url = img_list if img_list else None

        def gen():
            try:
                SSE_ACTIVE_STREAMS.inc()
            except Exception:  # noqa: BLE001 - 指标故障不影响应答
                pass
            tok = chat_stream_mod.current_kb_ids.set(kb_ids)
            try:
                final_raw = ""
                req_agent = create_agent(registry, system_prompt=sp)

                # SSE 心跳:联网搜索/工具调用期间可能 15-40s 无 token,
                # 中间代理与客户端易按空闲超时断连——producer 线程产事件,
                # 主生成器 15s 无数据即发 keepalive 注释行(SSE 标准忽略)
                import queue as _queue
                import threading as _threading

                _evq: _queue.Queue = _queue.Queue()

                def _produce():
                    try:
                        for ev in chat_stream_mod.stream_events(
                                req_agent, question, body.session_id, user,
                                assistant_id=assistant_id, system_prompt=sp,
                                image_data=img_data_url):
                            _evq.put(ev)
                    finally:
                        _evq.put(None)

                _threading.Thread(target=_produce, daemon=True).start()

                while True:
                    try:
                        ev = _evq.get(timeout=15)
                    except _queue.Empty:
                        yield ": keepalive\n\n"
                        continue
                    if ev is None:
                        break
                    if ev["kind"] == "final":
                        final_raw = ev["answer"]
                    yield f"event: {ev['kind']}\ndata: {_json.dumps(ev, ensure_ascii=False)}\n\n"
                # 落库：与主链路 persist_pair 一致（raw 纯净终答供多轮上下文重建）。
                # 图片消息：content 内嵌 markdown 图（前端气泡展示），
                # raw 用纯问题文本（多轮上下文重建不带 base64）
                if body.session_id and final_raw:
                    try:
                        chatstore.append_message(body.session_id, "user",
                                                 image_md + question, raw=question,
                                                 user=user, assistant_id=assistant_id)
                        chatstore.append_message(body.session_id, "assistant",
                                                 final_raw, raw=final_raw, user=user,
                                                 assistant_id=assistant_id)
                    except Exception as e:  # noqa: BLE001
                        logger.warning(f"SSE 会话持久化失败: {e}")
                yield f"event: done\ndata: {_json.dumps({'session_id': body.session_id}, ensure_ascii=False)}\n\n"
            finally:
                try:
                    chat_stream_mod.current_kb_ids.reset(tok)
                except Exception:  # noqa: BLE001
                    pass
                try:
                    SSE_ACTIVE_STREAMS.dec()
                except Exception:  # noqa: BLE001
                    pass

        return StreamingResponse(
            gen(), media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    @app.get("/api/sessions", include_in_schema=False)
    async def _list_sessions(request: _fastapi.Request):
        user = _require_active_user(request)   # 401/403 校验须在 try 外，避免被吞成 500
        try:
            return chatstore.list_sessions(user=user)
        except Exception as e:  # noqa: BLE001
            raise HTTPException(status_code=500, detail=f"会话列表获取失败: {e}")

    @app.delete("/api/sessions/{session_id}", include_in_schema=False)
    async def _delete_session(session_id: str, request: _fastapi.Request):
        _require_active_user(request)
        _check_session_access(request, session_id)
        try:
            chatstore.delete_session(session_id)
        except Exception as e:  # noqa: BLE001
            raise HTTPException(status_code=500, detail=f"会话删除失败: {e}")
        return {"ok": True}

    @app.get("/api/sessions/{session_id}/export", include_in_schema=False)
    async def _export_session(session_id: str, request: _fastapi.Request):
        """导出会话为 Markdown（存档/分享）：user/assistant 逐轮输出，
        图片附件以链接形式保留"""
        _require_active_user(request)
        _check_session_access(request, session_id)
        rows = chatstore.get_messages_full(session_id) or []
        if not rows:
            raise HTTPException(status_code=404, detail="会话不存在或无消息")
        import datetime as _dt
        from urllib.parse import quote as _quote
        # 会话标题：优先取列表中的会话（title），取不到用 id 兜底
        title = session_id
        try:
            for s in chatstore.list_sessions(user=_current_user(request)):
                if s.get("id") == session_id:
                    title = s.get("title") or session_id
                    break
        except Exception:  # noqa: BLE001
            pass
        lines = [f"# DocMind 对话导出：{title or session_id}",
                 f"> 导出时间：{_dt.datetime.now().strftime('%Y-%m-%d %H:%M')}", ""]
        for r in rows:
            role = r.get("role")
            content = r.get("content") or ""
            if role == "user":
                lines += ["## 🧑 用户", content, ""]
            elif role == "assistant":
                lines += ["## 🤖 DocMind", content, ""]
        from fastapi.responses import Response
        safe_title = "".join(c for c in str(title or session_id)
                             if c.isalnum() or c in "-_")[:30] or "session"
        return Response(
            content="\n".join(lines),
            media_type="text/markdown; charset=utf-8",
            headers={"Content-Disposition":
                     f"attachment; filename*=UTF-8''{_quote(f'{safe_title}.md')}"})

    @app.get("/api/sessions/{session_id}/messages", include_in_schema=False)
    async def _get_messages(session_id: str, request: _fastapi.Request):
        _require_active_user(request)
        _check_session_access(request, session_id)
        try:
            return chatstore.get_messages_full(session_id)
        except Exception as e:  # noqa: BLE001
            raise HTTPException(status_code=500, detail=f"消息获取失败: {e}")

    # ---- GDPR 合规：个人数据导出（可携带权）与账号级联删除（被遗忘权） ----
    from fastapi.responses import JSONResponse

    @app.get("/api/me/export", include_in_schema=False)
    async def _export_my_data(request: _fastapi.Request):
        user = _require_user(request)
        try:
            data = chatstore.export_user_data(user)
        except Exception as e:  # noqa: BLE001
            raise HTTPException(status_code=500, detail=f"数据导出失败: {e}")
        if "error" in data:
            raise HTTPException(status_code=404, detail=data["error"])
        return JSONResponse(content=data, headers={
            "Content-Disposition": f"attachment; filename=docmind-export-{user}.json"
        })

    @app.post("/api/me/delete", include_in_schema=False)
    async def _delete_my_account(request: _fastapi.Request):
        user = _require_user(request)
        result = chatstore.delete_user_cascade(user)
        if "error" in result:
            raise HTTPException(status_code=400, detail=result["error"])
        response = JSONResponse(content={"ok": True, **result})
        # 删除后立即登出：注销 token 并清除会话 cookie
        web_auth.revoke(request.cookies.get(web_auth.TOKEN_COOKIE))
        response.delete_cookie(key=web_auth.TOKEN_COOKIE, path="/")
        return response

    # ---- React 前端静态服务(7860 直连场景;生产 nginx 时同样可用) ----
    _dist_dir = os.path.join(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))), "web", "dist")
    if os.path.isdir(_dist_dir):
        from fastapi.staticfiles import StaticFiles

        @app.get("/", include_in_schema=False)
        async def _spa_index():
            return FileResponse(os.path.join(_dist_dir, "index.html"))

        # 注意:mount 必须先于下方 catch-all 注册,否则 /assets/* 会被
        # SPA fallback 截获返回 text/html,浏览器拒绝执行模块脚本(白屏)
        app.mount("/assets", StaticFiles(directory=os.path.join(_dist_dir, "assets")),
                  name="spa-assets")

        @app.get("/{full_path:path}", include_in_schema=False)
        async def _spa_fallback(full_path: str):
            # SPA history 路由:非 API/开放/文件路径回退 index.html
            if full_path.startswith(("api/", "open/", "files/", "vendor/",
                                      "metrics", "health", "mermaid")):
                raise HTTPException(status_code=404)
            # dist 根目录真实文件(favicon/manifest 等)直接返回,
            # 其余路径回退 index.html
            _dist_real = os.path.realpath(_dist_dir)
            candidate = os.path.realpath(os.path.join(_dist_real, full_path))
            if candidate.startswith(_dist_real + os.sep) and os.path.isfile(candidate):
                return FileResponse(candidate)
            return FileResponse(os.path.join(_dist_dir, "index.html"))

    # ---- 启动(uvicorn 阻塞式,信号优雅退出;MCP 连接随进程退出清理) ----
    _host = os.getenv("DOCMIND_HOST", "127.0.0.1")
    _port = int(os.getenv("DOCMIND_PORT", "7860"))
    logger.info(f"DocMind 启动: http://{_host}:{_port}")
    uvicorn.run(app, host=_host, port=_port, log_level="warning")

    finally_placeholder = None  # noqa
