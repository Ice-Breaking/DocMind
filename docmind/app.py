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


from docmind.api_utils import server_error
from docmind.logging_setup import setup_logging

setup_logging()

import logging

import fastapi

from docmind import acl, config
from docmind import store as chatstore   # 别名：避免遮蔽 build_agent 返回的 VectorStore store
from docmind import web_auth
from docmind.deps import CurrentUser, RequireUser
from docmind.core import build_shared, create_agent

logger = logging.getLogger(__name__)


def create_app() -> fastapi.FastAPI:
    """装配 FastAPI 应用（全部 REST/SSE 路由 + SPA 静态托管）。

    工厂化：uvicorn docmind.app:create_app --factory 亦可启动；
    重量级初始化（知识库加载/MCP 连接）在工厂调用时执行而非 import 时——
    import docmind.app 不再有隐式副作用，__main__ 只留 uvicorn。
    """
    import asyncio
    from contextlib import asynccontextmanager

    import anyio
    from fastapi import FastAPI, HTTPException, Query
    from fastapi.responses import (FileResponse, JSONResponse,
                                   PlainTextResponse)
    from typing import Literal
    from pydantic import BaseModel, Field

    logger.info("正在装配 Agent（加载知识库、连接 MCP Server）...")
    registry, store, mcp_connections = build_shared()

    @asynccontextmanager
    async def _lifespan(_app):
        # 退出时关闭 MCP 连接（原先随进程退出靠 daemon 线程隐式消亡，
        # 显式关闭让 stdio 子进程干净退出）
        yield
        for conn in (mcp_connections or []):
            try:
                await conn.close()
            except Exception:  # noqa: BLE001
                pass

    app = FastAPI(title="DocMind", lifespan=_lifespan)

    @app.exception_handler(Exception)
    async def _unhandled_exception_handler(request: fastapi.Request,
                                           exc: Exception):
        """全局兜底：未捕获异常记完整堆栈，对外统一 500 通用文案。

        原先未处理异常返回 Starlette 裸 "Internal Server Error"（无日志
        关联），而散落的 detail=f"...{e}" 又把内部细节透给浏览器——
        两头都不对。现在：细节进日志（含 path 便于检索），对外统一文案；
        各路由已显式转 server_error 的 500 同样脱敏。"""
        logger.exception("未处理异常 path=%s", request.url.path, exc_info=exc)
        return JSONResponse({"detail": "服务器内部错误，请稍后重试"},
                            status_code=500)

    # 认证门禁：无任何账号时播种 admin（密码取 ADMIN_PASSWORD 环境变量）
    chatstore.ensure_seed_admin()

    # ---- 登录 / 登出（自研 token 会话，替代 Gradio auth） ----
    # 后端直连端口（7860）友好兜底：纯 API 模式（web/dist 不存在，compose 形态）
    # 下浏览器误访 7860 只会看到 FastAPI 默认 {"detail":"Not Found"} 裸 JSON，
    # 对非技术用户如同「系统坏了」。改为中文提示页 + 指向正确入口（80）的链接。
    # 仅当 dist 不存在时注册：dist 存在（单容器模式）时后端自己托管 SPA，
    # 根路径/未知路径已有 SPA 路由，不得遮蔽。且仅对浏览器导航
    # （Accept: text/html）生效，API 客户端仍收 JSON 保持契约兼容
    import html as _html

    _dist_dir = os.path.join(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))), "web", "dist")

    if not os.path.isdir(_dist_dir):
        _PAGE_STYLE = """<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>DocMind</title>
<style>
 body{{margin:0;min-height:100vh;display:flex;align-items:center;justify-content:center;
      font-family:system-ui,-apple-system,"PingFang SC","Microsoft YaHei",sans-serif;
      background:#f5f7fa;color:#1f2329}}
 .card{{background:#fff;border-radius:12px;box-shadow:0 2px 12px rgba(0,0,0,.08);
       padding:40px 48px;max-width:520px;text-align:center}}
 h1{{font-size:20px;margin:0 0 12px}} p{{font-size:14px;line-height:1.8;margin:8px 0;color:#4e5969}}
 code{{background:#f2f3f5;padding:2px 8px;border-radius:4px;font-size:13px}}
 a.btn{{display:inline-block;margin-top:16px;padding:10px 28px;background:#2563eb;color:#fff;
       border-radius:8px;text-decoration:none;font-size:14px}}
 a.btn:hover{{background:#1d4ed8}}
 .tip{{font-size:12px;color:#86909c;margin-top:18px}}
</style></head><body><div class="card">
{body}</div></body></html>"""

        # 场景一：浏览器直连后端调试端口（7860）——说明端口用错并给出正确入口
        _PAGE_BACKEND = _PAGE_STYLE.format(body="""<h1>这里是 DocMind 的 API 后端</h1>
<p>此端口（<code>{port}</code>）仅供程序调试直连，没有可交互的网页界面。</p>
<p>请从下面入口登录使用：</p>
<a class="btn" href="{url}">打开 DocMind（端口 80）</a>
<p class="tip">若链接不可达，请联系管理员确认 nginx 服务（frontend 容器）是否在运行。</p>""")

        # 场景二：经 nginx（80 入口）落到后端的浏览器 404/405——中性提示，
        # 不能误导用户「去 80 端口」（用户明明就在 80 上）
        _PAGE_NOTFOUND = _PAGE_STYLE.format(body="""<h1>页面不存在或请求方式不支持</h1>
<p>您访问的地址无法打开。如未进入 DocMind 主界面，请从首页进入：</p>
<a class="btn" href="{url}">返回 DocMind 首页</a>""")

        def _friendly_backend_page(request: "fastapi.Request",
                                   backend_notice: bool) -> str:
            # Host 头先 HTML 转义防注入；partition 安全取端口——
            # 经 nginx 转发的 Host 不带端口（如 "127.0.0.1"），
            # 旧版 split(":")[1] 在此越界致 500（已修复的回归）
            host = _html.escape(request.headers.get("host") or "", quote=True)
            bare, _, port = host.partition(":")
            if backend_notice:
                return _PAGE_BACKEND.format(port=port or "7860",
                                            url=f"http://{bare}/")
            return _PAGE_NOTFOUND.format(url=f"http://{bare}/")

        @app.get("/", include_in_schema=False)
        async def _backend_root(request: fastapi.Request):
            # GET / 只有直连后端才会命中（经 nginx 的 / 由 SPA 托管）
            return fastapi.responses.HTMLResponse(
                _friendly_backend_page(request, backend_notice=True))

        @app.exception_handler(404)
        @app.exception_handler(405)
        async def _friendly_404_405(request: fastapi.Request, exc):
            # 浏览器导航才给友好页；API 客户端（前端 fetch/脚本）保持 JSON 契约。
            # Host 无端口或 :80 → 请求经 nginx（用户就在正确入口），
            # 用中性 404 文案；带其他端口（如 :7860）→ 直连后端，提示换入口
            status = getattr(exc, "status_code", 404)
            if "text/html" in (request.headers.get("accept") or "").lower():
                port = (request.headers.get("host") or "").partition(":")[2]
                backend_notice = port not in ("", "80")
                return fastapi.responses.HTMLResponse(
                    _friendly_backend_page(request, backend_notice),
                    status_code=status)
            return fastapi.responses.JSONResponse(
                {"detail": getattr(exc, "detail", "Not Found")}, status_code=status)

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

        # 密码验证（PBKDF2 20 万次迭代，纯 CPU 50-100ms）与 LDAP bind
        # （同步 socket）都放线程池：登录风暴不再冻结事件循环拖垮全站
        def _verify_sync() -> str:
            if chatstore.verify_user(username, password):
                return "local"
            if ldap_auth.authenticate(username, password):
                chatstore.ensure_external_user(username)
                return "ldap"
            return ""

        via = await anyio.to_thread.run_sync(_verify_sync)
        if not via:
            web_auth.record_failure(username)
            logger.warning(f"登录失败 user={username} ip={web_auth.client_ip()}")
            raise HTTPException(status_code=400, detail="用户名或密码错误")
        web_auth.clear_failures(username)
        chatstore.record_audit(username, "login", via, ip=web_auth.client_ip())
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
            except Exception as e:  # noqa: BLE001
                logger.debug(f"http 指标记录失败 path={request.url.path}: {e}")
        return response

    @app.get("/metrics", include_in_schema=False)
    async def _metrics_route(request: fastapi.Request, token: str = ""):
        """/metrics 门禁：配置 METRICS_TOKEN 后凭 token（query 或 Bearer 头）访问；
        未配置时仅允许回环直连（本机调试 / Prometheus 同机部署零配置）。
        拒绝时返回 404 而非 401：不向探测者确认端点存在"""
        expected = os.getenv("METRICS_TOKEN", "")
        peer = request.client.host if request.client else ""
        if expected:
            provided = (token or request.headers.get("authorization", "")
                        .removeprefix("Bearer ").strip())
            ok = bool(provided) and provided == expected
        else:
            ok = peer in ("127.0.0.1", "::1")
        if not ok:
            raise HTTPException(status_code=404, detail="Not Found")
        from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
        from fastapi.responses import Response as _MR
        return _MR(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)

    # ---- 健康检查：Docker HEALTHCHECK / 监控探活（不要求登录态，只暴露内部状态） ----
    _fingerprint_cache: list[str] = []

    def _build_fingerprint() -> str:
        """构建指纹（12 位源码哈希）：部署漂移检测——比对运行容器与
        工作区代码是否一致（QA 实测发现容器跑 11 小时前旧镜像无人察觉）。

        镜像内：读构建期写入的 /app/.build_fingerprint（对镜像内源码哈希）；
        本地开发：无该文件则对当前源码实时计算（dev 指纹随编辑变化）。"""
        if _fingerprint_cache:
            return _fingerprint_cache[0]
        fp_file = os.path.join(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__))), ".build_fingerprint")
        try:
            if os.path.isfile(fp_file):
                fp = open(fp_file, encoding="utf-8").read().strip() or "dev"
            else:
                import hashlib
                import glob as _glob
                root = os.path.dirname(os.path.abspath(__file__))
                files = sorted(_glob.glob(os.path.join(root, "**", "*.py"),
                                          recursive=True))
                h = hashlib.sha256()
                for f in files:
                    h.update(open(f, "rb").read())
                fp = h.hexdigest()[:12]
        except Exception:  # noqa: BLE001 - 指纹失败不影响健康状态
            fp = "unknown"
        _fingerprint_cache.append(fp)
        return fp

    @app.get("/health", include_in_schema=False)
    async def _health():
        """Lightweight health check for Docker HEALTHCHECK and monitoring."""
        import shutil
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
            content={"status": "healthy" if all_ok else "degraded",
                     "version": _build_fingerprint(),
                     "checks": checks},
        )

    # ---- 文档预览：vendored pdf.js + 知识库原文（引用溯源直达） ----
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

    # 同步重活助手：全部经 anyio.to_thread.run_sync 调用，
    # 禁止在 async 路由体内直接执行（会冻结事件循环拖垮全站）
    def _extract_text_sync(path: str) -> str:
        from docmind.rag.chunker import _EXTRACTORS
        ext = os.path.splitext(path)[1].lower()
        if ext in _EXTRACTORS:
            return _EXTRACTORS[ext](path)
        with open(path, encoding="utf-8") as f:
            return f.read()

    def _xlsx_sheets_sync(path: str) -> list[dict]:
        import openpyxl
        MAX_ROWS, MAX_COLS = 500, 60
        wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
        try:
            sheets = []
            for ws in wb.worksheets:
                rows = []
                for i, row in enumerate(ws.iter_rows(values_only=True)):
                    if i >= MAX_ROWS:
                        break
                    rows.append(["" if c is None else str(c) for c in row[:MAX_COLS]])
                sheets.append({"name": ws.title, "rows": rows})
            return sheets
        finally:
            wb.close()

    def _convert_docx_pdf_sync(soffice: str, path: str, cache_dir: str, out_pdf: str) -> None:
        import subprocess
        r = subprocess.run(
            [soffice, "--headless", "--convert-to", "pdf", "--outdir", cache_dir, path],
            capture_output=True, timeout=120)
        if r.returncode != 0 or not os.path.isfile(out_pdf):
            raise RuntimeError("PDF 转换失败")

    # methods 含 HEAD：前端先 HEAD 探测 docx 能否转 PDF（此 App 的 .get 不自动挂 HEAD）
    @app.api_route("/files/{name}", methods=["GET", "HEAD"], include_in_schema=False)
    async def _serve_file(name: str, request: fastapi.Request, user: RequireUser,
                          as_: str = Query(default=None, alias="as")):
        # 安全校验：登录 + 文档级 ACL——受限文档(如内部机密.md)此前可经
        # 此直链被未登录用户整篇下载，完全绕过检索层的 ACL 隔离(P0 实测)
        allowed = acl.allowed_docs(user)
        safe = os.path.basename(name)  # 防路径穿越：只允许知识库目录内文件名
        if safe not in allowed:
            raise HTTPException(status_code=404)   # 与不存在同响应,不泄露存在性
        path = os.path.join(_knowledge_dir, safe)
        if not os.path.isfile(path):
            raise HTTPException(status_code=404)
        if as_ == "text":
            # 提取正文文本（docx 无 LibreOffice 时的降级通道；图片走 OCR 缓存）。
            # 解析器是同步 CPU/IO 重活（PDF/Word/Excel），必须下放线程池
            try:
                text = await anyio.to_thread.run_sync(_extract_text_sync, path)
            except HTTPException:
                raise
            except Exception as e:  # noqa: BLE001
                raise server_error("文本提取失败", e)
            return PlainTextResponse(text)
        if as_ == "sheets" and safe.lower().endswith(".xlsx"):
            # Excel 预览数据：各 Sheet 的行列 JSON（限量防超大表格拖垮前端）
            try:
                sheets = await anyio.to_thread.run_sync(_xlsx_sheets_sync, path)
            except HTTPException:
                raise
            except Exception as e:  # noqa: BLE001
                raise server_error("Excel 解析失败", e)
            return JSONResponse({"sheets": sheets})
        if as_ == "pdf" and safe.lower().endswith(".docx"):
            # LibreOffice headless 转 PDF（按源文件 mtime 缓存）；未安装 → 409，前端降级文本预览。
            # 单次转换最长 120s，同步子进程绝不能跑在事件循环里
            import shutil
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
                    await anyio.to_thread.run_sync(
                        _convert_docx_pdf_sync, soffice, path, cache_dir, out_pdf)
                except HTTPException:
                    raise
                except Exception as e:  # noqa: BLE001
                    raise server_error("转换失败", e)
            return FileResponse(out_pdf, media_type="application/pdf")
        return FileResponse(path)

    # ---- 当前用户解析：自研 token 会话（web_auth） ----
    def _check_session_access(request: fastapi.Request, session_id: str,
                              allow_missing: bool = False) -> None:
        """会话归属校验：本人或无主历史会话放行，他人会话 403。

        allow_missing：SSE 新会话场景（session 尚未落库，owner 为 None）
        放行——原先流式路由内联了另一套判定，两处语义漂移；统一入口。"""
        owner = chatstore.session_owner(session_id)
        if owner is None:
            if not allow_missing:
                raise HTTPException(status_code=404, detail="会话不存在")
            return
        if owner not in ("", web_auth.current_user(request)):
            raise HTTPException(status_code=403, detail="无权访问该会话")

    # ---- 反馈闭环：👍/👎 评价（session_id + 消息序号唯一定位，重复点击覆盖） ----
    class FeedbackIn(BaseModel):
        # 字符白名单：session_id 会进入 admin 审计页的内联事件/属性插值，
        # 任意字符入库等于给存储型 XSS 留载体（前端生成的 sess-xxx 格式
        # 天然满足 [\\w-]，存量会话不受影响——读取侧不做此校验）
        session_id: str = Field(..., min_length=1, max_length=64,
                                pattern=r"^[\w-]+$")
        seq: int
        rating: Literal["up", "down"]

    @app.post("/api/feedback", include_in_schema=False)
    async def _save_feedback(fb: FeedbackIn, request: fastapi.Request,
                             _user: RequireUser):
        if fb.rating not in ("up", "down"):
            raise HTTPException(status_code=400, detail="rating 必须是 up/down")
        _check_session_access(request, fb.session_id)
        try:
            chatstore.save_feedback(fb.session_id, fb.seq, fb.rating)
        except Exception as e:  # noqa: BLE001
            raise server_error("反馈保存失败", e)
        return {"ok": True}

    @app.get("/api/feedback/{session_id}", include_in_schema=False)
    async def _get_feedback(session_id: str, request: fastapi.Request,
                            _user: RequireUser):
        _check_session_access(request, session_id)
        return chatstore.get_feedback(session_id)

    # ---- 强制修改密码 ----
    class ChangePasswordIn(BaseModel):
        old_password: str = Field(..., min_length=1, max_length=128)
        new_password: str = Field(..., min_length=8, max_length=128)

    @app.post("/api/change-password", include_in_schema=False)
    async def _change_password(body: ChangePasswordIn, request: fastapi.Request,
                               user: CurrentUser):
        # 仅校验登录态（current_user），不能用 require_user：
        # 后者对 must_change_pwd 一律 403——若这里也走它，首登用户连改密
        # 接口本身都被拦，「强制改密」变成永久死锁（2026-08 二轮回归实测）。
        # 权限不放权：改密仍需本人会话 + 旧密码校验在 store.change_password 内部
        # 密码强度：≥8 位且同时含字母与数字（防弱口令 + 直链/撞库组合利用）
        np = body.new_password or ""
        if len(np) < 8 or not any(c.isalpha() for c in np) or not any(c.isdigit() for c in np):
            raise HTTPException(status_code=400,
                                detail="新密码需至少 8 位，且同时包含字母和数字")
        # 旧密码校验含 PBKDF2 20 万次迭代（纯 CPU），下放线程池
        ok, msg = await anyio.to_thread.run_sync(
            lambda: chatstore.change_password(user, body.old_password, body.new_password))
        if not ok:
            raise HTTPException(status_code=400, detail=msg)
        # 密码已变即吊销本人其余会话：否则旧会话凭滑动续期存活至 12h TTL，
        # 被窃会话在改密后依旧可用（二轮回归盲区 J 实测确认）；当前会话保留避免自踢
        revoked = web_auth.revoke_other_sessions(
            user, request.cookies.get(web_auth.TOKEN_COOKIE))
        chatstore.record_audit(user, "auth.password-change",
                               f"revoked_sessions={revoked}",
                               ip=web_auth.client_ip())
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
    from docmind.ragas_eval import register_ragas_routes
    register_ragas_routes(app)   # RAGAS 四指标生成质量评测（管理员）
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
    async def _locate(request: fastapi.Request, user: RequireUser,
                      doc: str, q: str = ""):
        doc = os.path.basename(doc)
        if doc not in acl.allowed_docs(user):
            return {"found": False}   # 无权文档：与"没找到"无差别，不泄露存在性
        if not q.strip():
            return {"found": False}
        # 检索含 query embedding 网络往返 + BM25 分词，下放线程池
        hits = await anyio.to_thread.run_sync(
            lambda: locate_retriever.search(q, top_k=1, rerank=False,
                                            allowed_sources={doc}))
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
    async def _suggest(body: SuggestIn, request: fastapi.Request,
                           _user: RequireUser):
        answer = body.answer.strip()
        if len(answer) < 80:   # 过短内容（报错/拒答）不生成
            return {"suggestions": []}
        key = _hashlib.sha1(answer.encode("utf-8")).hexdigest()[:16]
        cached = chatstore.get_suggestions(key)
        if cached:
            return {"suggestions": cached, "cached": True}
        # 生成走同步 LLM 调用，下放线程池（缓存未命中时必触发）
        items = await anyio.to_thread.run_sync(
            suggest_mod.generate_suggestions, body.question, answer)
        chatstore.save_suggestions(key, items)
        return {"suggestions": items, "cached": False}

    # ---- SSE 流式聊天（前后端分离新 UI 的应答协议层，与 Gradio 主链路并存） ----
    # 事件：cache/thinking/token/step/error/final/done，见 docmind/chat_stream.py
    import json as _json
    import threading as _threading
    from fastapi.responses import StreamingResponse
    from docmind import chat_stream as chat_stream_mod
    from docmind.metrics import SSE_ACTIVE_STREAMS

    class ChatIn(BaseModel):
        question: str = Field(..., min_length=1, max_length=4000)
        # session_id/assistant_id 白名单同 FeedbackIn（防 XSS 载体入库）
        session_id: str = Field(default="", max_length=64,
                                pattern=r"^[\w-]*$")
        assistant_id: str = Field(default="", max_length=64,
                                  pattern=r"^[\w-]*$")
        # 图片附件（base64，可带 data URL 前缀）：模型真看图（多模态），非仅 OCR；
        # 支持单张(str)或多张(list,上限 5)
        image_data: "str | list[str]" = Field(default="", max_length=60_000_000)

    @app.post("/api/chat/stream", include_in_schema=False)
    async def _chat_stream(body: ChatIn, request: fastapi.Request,
                           user: RequireUser):
        # 认证经 Depends 完成（user 参数）
        question = body.question.strip()
        if not question:
            raise HTTPException(status_code=400, detail="question 不能为空")
        # 会话归属校验：防重建/写入他人会话上下文（新会话尚未落库，放行）
        if body.session_id:
            _check_session_access(request, body.session_id, allow_missing=True)

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

        # 取消信号：客户端断连（CancelledError 杀掉 async gen）时通知
        # producer 停止，不再启动下一轮 LLM/工具调用——否则用户关页后
        # Agent 继续烧完整轮 token
        cancel_event = _threading.Event()

        def _produce(_emit) -> None:
            """producer 线程：跑 stream_events 并落库。

            落库放在 producer 收尾而非 gen 循环之后——客户端中途断连时
            gen 被提前终止，落库逻辑若在 gen 里会被跳过，
            整轮对话（含已产生的完整回答）不入库。⚠️ 兜底文案不入库
            （错误模板进 raw 会污染后续多轮上下文）。"""
            final_raw = ""
            try:
                req_agent = create_agent(registry, system_prompt=sp)
                for ev in chat_stream_mod.stream_events(
                        req_agent, question, body.session_id, user,
                        assistant_id=assistant_id, system_prompt=sp,
                        image_data=img_data_url, kb_ids=kb_ids):
                    if cancel_event.is_set():
                        break
                    if ev["kind"] == "final":
                        final_raw = ev["answer"]
                    _emit(ev)
                if body.session_id and final_raw and not final_raw.startswith("⚠️"):
                    try:
                        # 与主链路 persist_pair 一致（raw 纯净终答供多轮上下文重建）。
                        # 图片消息：content 内嵌 markdown 图（前端气泡展示），
                        # raw 用纯问题文本（多轮上下文重建不带 base64）。
                        # 一轮问答单事务落库：两条消息原子写入
                        chatstore.append_exchange(
                            body.session_id,
                            image_md + question, question,
                            final_raw,
                            user=user, assistant_id=assistant_id)
                    except Exception as e:  # noqa: BLE001
                        logger.warning(f"SSE 会话持久化失败: {e}")
            except Exception:  # noqa: BLE001 - producer 异常收敛为流结束
                logger.exception("SSE producer 异常")
            finally:
                _emit(None)

        async def gen():
            """async generator：producer 线程经 call_soon_threadsafe 投递
            事件到 asyncio.Queue——原同步生成器由 Starlette 的
            iterate_in_threadpool 驱动，每个 SSE chunk 一次线程池调度
            往返，叠加 producer 线程一条消息涉及 3 类线程；改 async 后
            事件直达事件循环，断连取消也更干净（CancelledError 直达
            finally）。"""
            try:
                SSE_ACTIVE_STREAMS.inc()
            except Exception:  # noqa: BLE001 - 指标故障不影响应答
                pass
            loop = asyncio.get_running_loop()
            aq: asyncio.Queue = asyncio.Queue()

            def _emit(ev) -> None:
                loop.call_soon_threadsafe(aq.put_nowait, ev)

            try:
                _threading.Thread(target=_produce, args=(_emit,),
                                  daemon=True).start()
                while True:
                    try:
                        # SSE 心跳:联网搜索/工具调用期间可能 15-40s 无 token,
                        # 中间代理与客户端易按空闲超时断连——15s 无数据即发
                        # keepalive 注释行(SSE 标准忽略)
                        ev = await asyncio.wait_for(aq.get(), timeout=15)
                    except asyncio.TimeoutError:
                        yield ": keepalive\n\n"
                        continue
                    if ev is None:
                        break
                    yield f"event: {ev['kind']}\ndata: {_json.dumps(ev, ensure_ascii=False)}\n\n"
                yield f"event: done\ndata: {_json.dumps({'session_id': body.session_id}, ensure_ascii=False)}\n\n"
            finally:
                # 任何退出路径（正常结束/客户端断连触发 CancelledError）都
                # 通知 producer 停止，防止断连后 token 持续消耗
                cancel_event.set()
                try:
                    SSE_ACTIVE_STREAMS.dec()
                except Exception:  # noqa: BLE001
                    pass

        return StreamingResponse(
            gen(), media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    @app.get("/api/sessions", include_in_schema=False)
    async def _list_sessions(request: fastapi.Request, user: RequireUser,
                             limit: int = 50, offset: int = 0):
        """会话列表（分页：limit/offset 可选，默认值保持旧行为）"""
        # 认证经 Depends 完成（user 参数）
        try:
            # 相关子查询随消息量增长变慢，下放线程池
            return await anyio.to_thread.run_sync(
                lambda: chatstore.list_sessions(
                    user=user, limit=max(1, min(limit, 200)),
                    offset=max(0, offset)))
        except Exception as e:  # noqa: BLE001
            raise server_error("会话列表获取失败", e)

    @app.delete("/api/sessions/{session_id}", include_in_schema=False)
    async def _delete_session(session_id: str, request: fastapi.Request,
                              _user: RequireUser):
        _check_session_access(request, session_id)
        try:
            chatstore.delete_session(session_id)
        except Exception as e:  # noqa: BLE001
            raise server_error("会话删除失败", e)
        return {"ok": True}

    @app.get("/api/sessions/{session_id}/export", include_in_schema=False)
    async def _export_session(session_id: str, request: fastapi.Request,
                              user: RequireUser):
        """导出会话为 Markdown（存档/分享）：user/assistant 逐轮输出，
        图片附件以链接形式保留"""
        _check_session_access(request, session_id)
        rows = await anyio.to_thread.run_sync(
            lambda: chatstore.get_messages_full(session_id) or [])
        if not rows:
            raise HTTPException(status_code=404, detail="会话不存在或无消息")
        import datetime as _dt
        from urllib.parse import quote as _quote
        # 会话标题：优先取列表中的会话（title），取不到用 id 兜底
        title = session_id
        try:
            sessions = await anyio.to_thread.run_sync(
                lambda: chatstore.list_sessions(user=user))
            for s in sessions:
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
    async def _get_messages(session_id: str, request: fastapi.Request,
                            _user: RequireUser):
        _check_session_access(request, session_id)
        try:
            return await anyio.to_thread.run_sync(
                lambda: chatstore.get_messages_full(session_id))
        except Exception as e:  # noqa: BLE001
            raise server_error("消息获取失败", e)

    # ---- GDPR 合规：个人数据导出（可携带权）与账号级联删除（被遗忘权） ----
    @app.get("/api/me/export", include_in_schema=False)
    async def _export_my_data(request: fastapi.Request, user: RequireUser):
        try:
            data = await anyio.to_thread.run_sync(chatstore.export_user_data, user)
        except Exception as e:  # noqa: BLE001
            raise server_error("数据导出失败", e)
        if "error" in data:
            raise HTTPException(status_code=404, detail=data["error"])
        return JSONResponse(content=data, headers={
            "Content-Disposition": f"attachment; filename=docmind-export-{user}.json"
        })

    @app.post("/api/me/delete", include_in_schema=False)
    async def _delete_my_account(request: fastapi.Request, user: RequireUser):
        result = await anyio.to_thread.run_sync(chatstore.delete_user_cascade, user)
        if "error" in result:
            raise HTTPException(status_code=400, detail=result["error"])
        response = JSONResponse(content={"ok": True, **result})
        # 删除后立即登出：注销 token 并清除会话 cookie
        web_auth.revoke(request.cookies.get(web_auth.TOKEN_COOKIE))
        response.delete_cookie(key=web_auth.TOKEN_COOKIE, path="/")
        return response

    # ---- React 前端静态服务(7860 直连场景;生产 nginx 时同样可用) ----
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

    return app


if __name__ == "__main__":
    # ---- 启动(uvicorn 阻塞式,信号优雅退出;MCP 连接随进程退出清理) ----
    import uvicorn

    app = create_app()
    _host = os.getenv("DOCMIND_HOST", "127.0.0.1")
    _port = int(os.getenv("DOCMIND_PORT", "7860"))
    logger.info(f"DocMind 启动: http://{_host}:{_port}")
    # 反代场景必须采信 X-Forwarded-For，否则 client.host 恒为代理 IP：
    # web_auth 的 IP 维度防爆破退化为共享全局计数（20 次失败锁死所有人 =
    # 全站登录 DoS 开关）、审计日志 IP 全部失真。
    # forwarded_allow_ips 默认仅信任本机反代；compose 部署经环境变量注入
    # FORWARDED_ALLOW_IPS=固定容器子网（与 compose 底部 networks.default 对齐，
    # 见 docker-compose.yml 注释——勿写通配 * 或照抄 Docker 默认地址池）
    uvicorn.run(app, host=_host, port=_port, log_level="warning",
                proxy_headers=True,
                forwarded_allow_ips=os.getenv("FORWARDED_ALLOW_IPS", "127.0.0.1"))
