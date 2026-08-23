"""DocMind Web 界面（Gradio）：展示 Agent 思考过程 + 引用来源。

启动：python -m docmind.app
监听地址/端口可用环境变量 GRADIO_SERVER_NAME / GRADIO_SERVER_PORT 覆盖
（Docker 部署时容器内需要 0.0.0.0）。
"""
import os

# macOS venv 常缺系统根证书：必须在任何网络库（gradio/openai/aiohttp）初始化
# OpenSSL 默认证书库之前指向 certifi 证书包，否则进程内首次 TLS 会缓存空证书库，
# 导致后续 ASR websocket 报 CERTIFICATE_VERIFY_FAILED
import certifi as _certifi

os.environ["SSL_CERT_FILE"] = _certifi.where()
os.environ.setdefault("REQUESTS_CA_BUNDLE", _certifi.where())

import gradio as gr

from docmind.logging_setup import setup_logging

setup_logging()

import logging

from docmind import acl, config, semantic_cache
from docmind import store as chatstore   # 别名：避免遮蔽 build_agent 返回的 VectorStore store
from docmind.agent.react_agent import SYSTEM_PROMPT
from docmind.core import build_shared, create_agent
from docmind.llm import embed

logger = logging.getLogger(__name__)

logger.info("正在装配 Agent（加载知识库、连接 MCP Server）...")
registry, store, mcp_connections = build_shared()
tool_names = list(registry.tools.keys())

# ---------------------------------------------------------------- 样式
from docmind.ui_assets import CUSTOM_CSS, LAYOUT_CSS, FOLD_SCRIPT

# 全局布局 CSS：必须经 launch(head=...) 注入。
# 原因：launch(css=) 的样式会被 Gradio 重写并限定到 .contain 作用域内，
# 带 .gradio-container 前缀的选择器（锁定容器、块排序等）会永远失配。

# 长回复折叠：周期性扫描 DOM，给超高的 AI 气泡加渐隐遮罩 + 展开/收起按钮
# （流式输出会反复重建 DOM，故用定时扫描；经 launch(head=...) 注入 <head>，
#  因 gr.HTML 会过滤 script、js 参数在 SSR 模式下不可靠）

HEADER_HTML = f"""
<div class="dm-header">
  <div class="dm-title">
    <button id="dm-sessions-toggle" title="会话历史">☰ 会话</button>
    <span>🧠 DocMind</span>
    <span class="dm-badge">{'手写 ReAct · RAG · MCP' + (' · 深度思考' if config.ENABLE_THINKING else '')}</span>
  </div>
  <div class="dm-sub">
    回答来源标注：<b>[来源: 文件名]</b> 知识库 · <b>【模型通识】</b> 库外兜底 · <b>🔧 工具</b> 实时数据
  </div>
  <div class="dm-chips">
    {''.join(f'<span class="dm-chip">{t}</span>' for t in tool_names)}
  </div>
</div>
"""

EXAMPLES = [
    "💡 什么是 RAG？它解决了什么问题？",
    "🛡️ Agent 如何防止死循环？",
    "🔌 MCP 和 Function Calling 是什么关系？",
    "🌤️ 北京天气怎么样？",
]


# ---------------------------------------------------------------- 交互逻辑
def _render_trace(lines: list[str]) -> str:
    return "\n\n".join(f"> {ln}" for ln in lines)


def respond_simple(question: str, history: list, user: str = "", session_id: str = "", raw_out: dict | None = None):
    """流式渲染：模型思维链实时展示 → 逐 token 打字效果 → 工具轨迹。
    任何异常都兼底为一条完整消息，避免界面停留在“思考中”"""
    acl.set_current_user(user)   # 文档级 ACL：检索/缓存按当前用户过滤
    # 每请求创建独立 Agent 实例，避免并发请求共享 history 导致上下文混乱
    req_agent = create_agent(registry)
    # 从 DB 重建多轮上下文（与 SSE 链路一致，无单例状态污染）
    if session_id:
        try:
            pairs = chatstore.load_raw_pairs(session_id)
            if pairs:
                req_agent.history.append({"role": "system", "content": SYSTEM_PROMPT})
                req_agent.history.extend({"role": r, "content": c} for r, c in pairs)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"上下文重建失败: {e}")
    trace_lines = []
    reasoning_parts = []   # 模型真实思维链（reasoning_content）增量累积
    final_answer = ""
    partial = ""           # 流式累积的回答正文
    thinking = True        # 是否处于“深度思考中”状态（收到正文 token 即结束）
    user_msg = {"role": "user", "content": question}
    q_vec = None

    # 语义缓存：高频问题秒回，跳过整个 Agent 链路（多轮追问相似度低自然 miss）
    if config.SEMANTIC_CACHE:
        try:
            q_vec = embed([question])[0]
            hit = semantic_cache.lookup(q_vec)
        except Exception as e:  # noqa: BLE001 - 缓存故障不阻塞主链路
            hit = None
            logger.warning(f"语义缓存查询失败: {e}")
        if hit and not acl.answer_allowed(hit[1], user):
            hit = None   # 缓存答案引用了当前用户无权的受限文档 → 防跨用户泄露
        if hit:
            cq, cached_answer, _ = hit
            if raw_out is not None:
                raw_out["raw"] = cached_answer   # 供 persist_pair 落库
            full = (f"<sub>⚡ 语义缓存命中 · 秒回（相似问题：{cq}）</sub>\n\n"
                    f"{cached_answer}")
            yield history + [user_msg, {"role": "assistant", "content": full}]
            return

    def reasoning_quote() -> str:
        """思维链渲染：思考中实时全文；完成后截断，避免淹没正文"""
        if not reasoning_parts:
            return ""
        text = "".join(reasoning_parts)
        if not thinking and len(text) > 300:
            text = text[:300] + "…"
        return f"\n> 💭 **模型思维链**：{text}\n"

    def render() -> str:
        head = "🤔 深度思考中…" if thinking else "<sub>✓ 深度思考已完成</sub>\n\n"
        tail = ""
        if trace_lines:
            tail = "\n\n---\n**🧠 Agent 思考过程：**\n\n" + "\n\n".join(trace_lines)
        return head + reasoning_quote() + partial + tail

    try:
        yield history + [user_msg, {"role": "assistant", "content": "🤔 深度思考中…"}]
        for step in req_agent.ask(question):
            if step.kind == "token":
                thinking = False
                partial += step.text
                yield history + [user_msg, {"role": "assistant", "content": render()}]
            elif step.kind == "thinking":
                reasoning_parts.append(step.text)
                yield history + [user_msg, {"role": "assistant", "content": render()}]
            elif step.kind == "final":
                final_answer = step.text
            else:
                thinking = False
                icon = {"tool_call": "🔧", "tool_result": "📥", "rewrite": "🔁",
                        "guard": "🛡️"}.get(step.kind, "📥")
                trace_lines.append(f"{icon} {step.text}")
                yield history + [user_msg, {"role": "assistant", "content": render()}]
    except Exception as e:  # noqa: BLE001
        final_answer = f"⚠️ 处理过程中出现异常：{e}\n请重试，若持续失败请检查 API 额度与网络。"
    if not final_answer:
        final_answer = partial or "⚠️ 未获得模型回复，请重试。"
    if raw_out is not None:
        raw_out["raw"] = final_answer   # 供 persist_pair 落库（缓存命中/正常均适用）
    # 写语义缓存：实时类工具（天气/时间）与错误兜底答案不入缓存，防过期数据；
    # web_search 交叉核验不排除——稳定知识类问题的联网佐证可缓存（真正实时的是天气/时间）
    _TIME_SENSITIVE = {"get_weather", "get_current_time"}
    if (config.SEMANTIC_CACHE and q_vec is not None and final_answer
            and not final_answer.startswith("⚠️")
            and not (req_agent.last_tools & _TIME_SENSITIVE)
            and acl.answer_allowed(final_answer, user)):   # 引用受限文档不入缓存
        try:
            semantic_cache.save(question, final_answer, q_vec)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"语义缓存写入失败: {e}")
    thinking = False   # 思考结束，让思维链按截断策略渲染
    full = f"<sub>✓ 深度思考已完成</sub>\n\n{reasoning_quote()}{final_answer}"
    if trace_lines:
        full += "\n\n---\n** Agent 思考过程：**\n\n" + "\n\n".join(trace_lines)
    yield history + [user_msg, {"role": "assistant", "content": full}]


def reset_chat():
    # 每请求创建独立 Agent，无需 reset 全局状态
    return []


# ---------------------------------------------------------------- 界面
theme = gr.themes.Soft(
    primary_hue="indigo",
    secondary_hue="violet",
    font=[gr.themes.GoogleFont("Inter"), "system-ui", "PingFang SC", "sans-serif"],
)

with gr.Blocks(title="DocMind · 知识助理 Agent") as demo:
    gr.HTML(HEADER_HTML)

    chatbot = gr.Chatbot(
        show_label=False,
        placeholder="💬 在下方输入问题，试试下面的示例～",
        elem_id="chatbot",
        # 关闭原生自动滚动：流式每帧强制滚底会把正在上滑阅读的用户拽回去，
        # 改由前端智能滚动接管（贴底才跟随，上滑不打扰 + 回到底部悬浮按钮）
        autoscroll=False,
    )

    # 会话 ID：前端引导脚本从 localStorage 生成/读取并写入此框（CSS 隐藏，
    # 不用 visible=False——它不渲染 DOM，JS 无法写值）
    session_box = gr.Textbox(elem_id="session-id")
    load_history_btn = gr.Button("load", elem_id="load-history-btn")

    with gr.Row(elem_id="input-row"):
        clear = gr.Button("+", scale=0, elem_id="clear-btn")
        msg = gr.Textbox(
            placeholder="输入你的问题，回车发送…",
            scale=8,
            container=False,
            lines=1,
            elem_id="input-box",
        )
        send = gr.Button("发送", scale=1, min_width=80, elem_id="send-btn")

    # 示例问题芯片：点击直接发送（常驻可见，对话中也可快速追问）
    with gr.Row(elem_id="examples-area"):
        example_buttons = [gr.Button(ex, elem_classes="ex-chip", scale=1) for ex in EXAMPLES]

    # 注意：必须绑定真正的 generator function（yield 在函数体内）。
    # Gradio 6 用 isgeneratorfunction 识别流式，lambda 返回 generator 对象不满足，
    # 会被当普通值 postprocess 而报 messages format 错误。
    def persist_pair(session_id, question, final_history, user="", raw_answer=""):
        """本轮用户问题 + 最终回答落库（失败不影响主链路）。

        assistant 同时存渲染版 content（展示）与 raw（纯净终答，
        供切换会话时恢复 LLM 多轮上下文）。
        """
        if not session_id or not final_history:
            return
        try:
            clean = raw_answer or ""
            chatstore.append_message(session_id, "user", question, user=user)
            chatstore.append_message(session_id, "assistant",
                                     final_history[-1]["content"], raw=clean, user=user)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"会话持久化失败: {e}")

    def make_example_handler(ex):
        def handler(history, session_id, request: gr.Request):
            user = request.username or ""
            # 强制改密门禁：未改密用户不得经 Gradio 链路发起对话（防绕过）
            if user and chatstore.get_must_change_pwd(user):
                yield history + [{"role": "assistant", "content": "⚠️ 请先修改密码后再使用"}]
                return
            last = history
            raw_out = {}
            for h in respond_simple(ex, history, user=user, session_id=session_id, raw_out=raw_out):
                last = h
                yield h
            persist_pair(session_id, ex, last, user=user, raw_answer=raw_out.get("raw", ""))
        return handler

    def submit(question: str, history: list, session_id: str, request: gr.Request):
        if not question.strip():
            yield history
            return
        user = request.username or ""
        # 强制改密门禁：未改密用户不得经 Gradio 链路发起对话（防绕过）
        if user and chatstore.get_must_change_pwd(user):
            yield history + [{"role": "assistant", "content": "⚠️ 请先修改密码后再使用"}]
            return
        last = history
        raw_out = {}
        for h in respond_simple(question, history, user=user, session_id=session_id, raw_out=raw_out):
            last = h
            yield h
        persist_pair(session_id, question, last, user=user, raw_answer=raw_out.get("raw", ""))

    def load_history(session_id, request: gr.Request):
        """恢复历史对话（页面加载/侧边栏切换均走这里）。

        回填 Chatbot 展示内容。Agent 多轮上下文由 respond_simple 在每次请求时
        从 DB 重建，无需在此预加载到全局 Agent（已改为每请求独立 Agent）。
        """
        if not session_id:
            return []
        try:
            owner = chatstore.session_owner(session_id)
            user = request.username or ""
            if owner not in (None, "", user):
                return []   # 他人会话：不恢复（sidebar 也不会列出）
            return chatstore.load_session(session_id)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"会话恢复失败: {e}")
            return []

    msg.submit(submit, [msg, chatbot, session_box], chatbot).then(lambda: "", None, msg)
    send.click(submit, [msg, chatbot, session_box], chatbot).then(lambda: "", None, msg)
    clear.click(reset_chat, None, chatbot)   # 新 session_id 由前端引导脚本接管
    for btn, ex in zip(example_buttons, EXAMPLES):
        btn.click(make_example_handler(ex), inputs=[chatbot, session_box], outputs=chatbot)
    # 页面加载：引导脚本写入 session_id 后程序化点击此按钮恢复历史
    load_history_btn.click(load_history, session_box, chatbot)



if __name__ == "__main__":
    # Gradio 6：theme / css 移到 launch()；折叠脚本与全局布局样式经 head 注入
    # （head 注入的内容不会被 Gradio 的 CSS 作用域重写）
    # 认证门禁：无任何账号时播种 admin（密码取 ADMIN_PASSWORD 环境变量）
    chatstore.ensure_seed_admin()

    # 登录防爆破：同一用户名 15 分钟内失败 5 次即锁定（内存计数，
    # 重启清零——单进程部署下足够；锁定期间一律拒绝，包括正确密码）
    _LOGIN_MAX_FAILS = 5
    _LOGIN_LOCK_SECONDS = 900
    _LOGIN_IP_MAX_FAILS = 20   # 同 IP 窗口内失败上限(防撞库;高于单账号阈值)
    _login_failures: dict[str, list[float]] = {}
    _login_ip_failures: dict[str, list[float]] = {}
    import contextvars as _cvars
    _client_ip: _cvars.ContextVar[str] = _cvars.ContextVar("dm_login_ip", default="")

    def _is_login_locked(username: str) -> int:
        """锁定中返回剩余秒数，未锁定返回 0"""
        import time as _time
        fails = [t for t in _login_failures.get(username, [])
                 if _time.time() - t < _LOGIN_LOCK_SECONDS]
        _login_failures[username] = fails
        if len(fails) >= _LOGIN_MAX_FAILS:
            return int(_LOGIN_LOCK_SECONDS - (_time.time() - fails[0]))
        return 0

    def _login_auth(username: str, password: str) -> bool:
        """登录链：本地账号优先；配置了企业 LDAP 时本地失败降级 LDAP 绑定，
        LDAP 首登自动开通本地账号（is_admin 默认关，需管理员另行授权）"""
        import time as _time
        from docmind import ldap_auth
        ip = _client_ip.get("") or "unknown"
        # IP 维度：同 IP 窗口内失败过多 → 拒绝（防换用户名撞库；锁号 DoS 由
        # 账号维度 15 分钟自愈兜底）
        ip_fails = [t for t in _login_ip_failures.get(ip, [])
                    if _time.time() - t < _LOGIN_LOCK_SECONDS]
        _login_ip_failures[ip] = ip_fails
        if len(ip_fails) >= _LOGIN_IP_MAX_FAILS:
            logger.warning(f"IP 登录失败过多已拒绝 ip={ip} user={username}")
            return False
        remaining = _is_login_locked(username)
        if remaining:
            logger.warning(f"登录锁定中，拒绝尝试 user={username} 剩余{remaining}s")
            return False
        if chatstore.verify_user(username, password):
            _login_failures.pop(username, None)   # 成功即清零
            chatstore.record_audit(username, "login", "local")
            return True
        if ldap_auth.authenticate(username, password):
            _login_failures.pop(username, None)
            chatstore.ensure_external_user(username)
            chatstore.record_audit(username, "login", "ldap")
            return True
        # 记录失败（本地与 LDAP 均未通过）
        _login_failures.setdefault(username, []).append(_time.time())
        _login_ip_failures.setdefault(ip, []).append(_time.time())
        fails = len(_login_failures[username])
        logger.warning(f"登录失败 user={username} 第{fails}次"
                       + ("（已锁定15分钟）" if fails >= _LOGIN_MAX_FAILS else ""))
        return False
    #
    # Mermaid 图表库：通过 FastAPI 路由 serve 本地 JS 文件
    # （避免 CDN 不可达 + 避免 head_paths 内联 HTML 导致 </ 序列中断解析）
    # 注意：launch() 内部会重建 demo.app，因此必须用 prevent_thread_lock=True
    # 让 launch 返回后，再在新 demo.app 上注册路由
    import time
    from fastapi.responses import FileResponse
    _mermaid_dir = os.path.dirname(os.path.abspath(__file__))

    demo.launch(
        theme=theme,
        css=CUSTOM_CSS,
        auth=_login_auth,
        auth_message="DocMind 知识助理 · 请登录后使用",
        head=f'<script src="/mermaid.min.js"></script>\n'
             + FOLD_SCRIPT + f"<style>{LAYOUT_CSS}</style>",
        server_name=os.getenv("GRADIO_SERVER_NAME", "127.0.0.1"),
        server_port=int(os.getenv("GRADIO_SERVER_PORT", "7860")),
        prevent_thread_lock=True,
    )

    # ---- 安全中间件：CSRF Origin 校验 + 基础安全响应头 ----
    # CSRF：全站 cookie 认证，浏览器发起的状态变更请求校验 Origin 与
    # Host 同源（现代浏览器 SameSite=Lax 默认已缓解，此为显式第二道防线）；
    # 无 Origin 头的调用（curl/服务端集成，开放 API 走 Bearer）不受影响
    # 注意:launch 后 app 已 started,常规 @app.middleware 注册会 RuntimeError
    # (与下方 metrics 中间件同样的降级命运)——改用重建 middleware stack 注入
    from urllib.parse import urlparse as _urlparse
    from fastapi.responses import JSONResponse as _JSONResponse
    from starlette.middleware import Middleware as _Middleware
    from starlette.middleware.base import BaseHTTPMiddleware as _BaseMW

    async def _security_dispatch(request, call_next):
        # CSRF:浏览器状态变更请求校验 Origin 与 Host 同源
        if request.method in ("POST", "PUT", "DELETE", "PATCH"):
            origin = request.headers.get("origin", "")
            if origin:
                host = request.headers.get("host", "")
                if host and _urlparse(origin).netloc != host:
                    return _JSONResponse({"detail": "跨站请求被拒绝"}, status_code=403)
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
        response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        return response

    try:
        demo.app.user_middleware.insert(
            0, _Middleware(_BaseMW, dispatch=_security_dispatch))
        demo.app.middleware_stack = demo.app.build_middleware_stack()
    except Exception as _e:  # noqa: BLE001 - 注入失败不阻断启动,记录降级
        print(f"[security] 中间件注入降级: {_e}")

    # ---- Prometheus 监控：/metrics 指标导出 + HTTP 请求埋点 ----
    # （不要求登录态：供 Prometheus 服务端抓取；只暴露计数器，不含敏感数据）
    import time as _metrics_time
    from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
    from fastapi.responses import Response as _MetricsResponse
    from docmind.metrics import HTTP_LATENCY, HTTP_REQUESTS

    @demo.app.get("/metrics", include_in_schema=False)
    async def _metrics():
        return _MetricsResponse(content=generate_latest(),
                                media_type=CONTENT_TYPE_LATEST)

    try:
        @demo.app.middleware("http")
        @demo.app.middleware("http")
        async def csrf_origin_guard(request, call_next):
            """CSRF 防护：写请求校验 Origin 同源。

            全站 cookie 认证无 CSRF token——浏览器默认 SameSite=Lax 已
            缓解大部分跨站 POST，此处对显式携带 Origin 的跨站写请求再
            拒一道（Origin 缺失的同源表单/服务端调用不受影响）"""
            if request.method not in ("GET", "HEAD", "OPTIONS"):
                origin = request.headers.get("origin")
                if origin and origin != "null":
                    from urllib.parse import urlparse as _up
                    o_host = _up(origin).netloc
                    h_host = request.headers.get("host", "")
                    if o_host and o_host != h_host:
                        return JSONResponse(
                            {"detail": "跨站请求被拒绝"},
                            status_code=403)
            return await call_next(request)

        async def metrics_middleware(request, call_next):
            try:
                _client_ip.set((request.client.host if request.client else "") or "")
            except Exception:  # noqa: BLE001 - IP 记录失败不影响请求
                pass
            # CSRF 缓解：浏览器跨站写请求会自动携带 cookie(全站 cookie 认证)，
            # Origin 与 Host 不一致即拒绝；服务端间调用(无 Origin 头)与
            # 同源前端不受影响，开放 API 用 Bearer 认证同样不受影响
            if request.method in ("POST", "PUT", "DELETE"):
                _origin = request.headers.get("origin")
                _host = request.headers.get("host")
                if _origin and _host:
                    from urllib.parse import urlparse as _urlparse
                    try:
                        _o_netloc = _urlparse(_origin).netloc
                        if _o_netloc and _o_netloc != _host:
                            from fastapi.responses import JSONResponse as _JR
                            return _JR({"detail": "跨站请求已被拒绝"}, status_code=403)
                    except Exception:  # noqa: BLE001 - 解析异常放行，不阻断主链路
                        pass
            if request.url.path in ("/metrics", "/health"):
                return await call_next(request)   # 探活/抓取自身不计入指标
            start = _metrics_time.time()
            response = await call_next(request)
            try:
                duration = _metrics_time.time() - start
                HTTP_REQUESTS.labels(method=request.method, path=request.url.path,
                                     status=response.status_code).inc()
                HTTP_LATENCY.labels(method=request.method,
                                    path=request.url.path).observe(duration)
            except Exception:  # noqa: BLE001 - 指标故障绝不影响请求
                pass
            return response
    except RuntimeError:
        # 新版 starlette 禁止应用启动后注入中间件 → HTTP 请求埋点优雅降级，
        # /metrics 仍导出其余指标；启动绝不能因监控失败而中断
        logging.getLogger(__name__).warning(
            "当前 starlette 不支持启动后注入中间件，HTTP 请求指标已降级")

    @demo.app.get("/mermaid.min.js", include_in_schema=False)
    async def _serve_mermaid():
        return FileResponse(os.path.join(_mermaid_dir, "mermaid.min.js"),
                            media_type="application/javascript")

    # ---- 健康检查：Docker HEALTHCHECK / 监控探活（不要求登录态，只暴露内部状态） ----
    @demo.app.get("/health", include_in_schema=False)
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

    _vendor_dir = os.path.join(_mermaid_dir, "vendor")
    _knowledge_dir = config.KNOWLEDGE_DIR

    @demo.app.get("/vendor/{name}", include_in_schema=False)
    async def _serve_vendor(name: str):
        safe = os.path.basename(name)  # 防路径穿越
        path = os.path.join(_vendor_dir, safe)
        if not os.path.isfile(path):
            raise HTTPException(status_code=404)
        return FileResponse(path, media_type="application/javascript"
                            if safe.endswith(".js") else "application/octet-stream")

    # methods 含 HEAD：前端先 HEAD 探测 docx 能否转 PDF（此 App 的 .get 不自动挂 HEAD）
    @demo.app.api_route("/files/{name}", methods=["GET", "HEAD"], include_in_schema=False)
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

    # ---- 当前用户解析：复用 Gradio 登录 cookie（access-token-{cookie_id}） ----
    import fastapi as _fastapi

    def _current_user(request: _fastapi.Request) -> str:
        token = (request.cookies.get(f"access-token-{demo.app.cookie_id}")
                 or request.cookies.get(f"access-token-unsecure-{demo.app.cookie_id}"))
        return (demo.app.tokens.get(token) if token else None) or ""

    def _require_user(request: _fastapi.Request) -> str:
        """自定义 /api/* 路由不受 Gradio 登录页保护，需自行校验登录态"""
        user = _current_user(request)
        if not user:
            raise HTTPException(status_code=401, detail="未登录")
        return user

    def _require_active_user(request: _fastapi.Request) -> str:
        """Like _require_user but also blocks users who must change their password."""
        user = _require_user(request)
        if chatstore.get_must_change_pwd(user):
            raise HTTPException(status_code=403, detail={"code": "MUST_CHANGE_PWD", "message": "请先修改密码"})
        return user

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

    @demo.app.post("/api/feedback", include_in_schema=False)
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

    @demo.app.get("/api/feedback/{session_id}", include_in_schema=False)
    async def _get_feedback(session_id: str, request: _fastapi.Request):
        _require_active_user(request)
        _check_session_access(request, session_id)
        return chatstore.get_feedback(session_id)

    # ---- 强制修改密码 ----
    class ChangePasswordIn(BaseModel):
        old_password: str = Field(..., min_length=1, max_length=128)
        new_password: str = Field(..., min_length=8, max_length=128)

    @demo.app.post("/api/change-password", include_in_schema=False)
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
    register_admin_routes(demo.app)
    from docmind.assistants_api import register_assistant_routes
    register_assistant_routes(demo.app)

    from docmind.docs_api import register_docs_routes
    register_docs_routes(demo.app)
    from docmind.retrieval_api import register_retrieval_routes
    register_retrieval_routes(demo.app)
    from docmind.eval_api import register_eval_routes
    register_eval_routes(demo.app)
    from docmind.platform_api import register_platform_routes
    register_platform_routes(demo.app)
    from docmind.voice_api import register_voice_routes
    register_voice_routes(demo.app)
    from docmind.governance_api import register_governance_routes
    register_governance_routes(demo.app)
    from docmind.users_api import register_users_routes
    register_users_routes(demo.app)
    from docmind import alerts as _alerts_mod
    _alerts_mod.register_alert_routes(demo.app)
    _alerts_mod.start_loop()   # 后台周期评估告警规则（幂等）

    # ---- 引用锚点定位：按用户问题在指定文档内检索最相关片段 ----
    # （复用已构建的 VectorStore，BM25 索引独立构建一次；ACL 感知，无权返回空）
    from docmind.rag.hybrid import HybridRetriever
    locate_retriever = HybridRetriever(store)
    locate_retriever.build()

    @demo.app.get("/api/locate", include_in_schema=False)
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

    @demo.app.post("/api/suggest", include_in_schema=False)
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

    @demo.app.post("/api/chat/stream", include_in_schema=False)
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
                for ev in chat_stream_mod.stream_events(
                        req_agent, question, body.session_id, user,
                        assistant_id=assistant_id, system_prompt=sp,
                        image_data=img_data_url):
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

    @demo.app.get("/api/sessions", include_in_schema=False)
    async def _list_sessions(request: _fastapi.Request):
        user = _require_active_user(request)   # 401/403 校验须在 try 外，避免被吞成 500
        try:
            return chatstore.list_sessions(user=user)
        except Exception as e:  # noqa: BLE001
            raise HTTPException(status_code=500, detail=f"会话列表获取失败: {e}")

    @demo.app.delete("/api/sessions/{session_id}", include_in_schema=False)
    async def _delete_session(session_id: str, request: _fastapi.Request):
        _require_active_user(request)
        _check_session_access(request, session_id)
        try:
            chatstore.delete_session(session_id)
        except Exception as e:  # noqa: BLE001
            raise HTTPException(status_code=500, detail=f"会话删除失败: {e}")
        return {"ok": True}

    @demo.app.get("/api/sessions/{session_id}/export", include_in_schema=False)
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
                lines += [f"## 🧑 用户", content, ""]
            elif role == "assistant":
                lines += [f"## 🤖 DocMind", content, ""]
        from fastapi.responses import Response
        safe_title = "".join(c for c in str(title or session_id)
                             if c.isalnum() or c in "-_")[:30] or "session"
        return Response(
            content="\n".join(lines),
            media_type="text/markdown; charset=utf-8",
            headers={"Content-Disposition":
                     f"attachment; filename*=UTF-8''{_quote(f'{safe_title}.md')}"})

    @demo.app.get("/api/sessions/{session_id}/messages", include_in_schema=False)
    async def _get_messages(session_id: str, request: _fastapi.Request):
        _require_active_user(request)
        _check_session_access(request, session_id)
        try:
            return chatstore.get_messages_full(session_id)
        except Exception as e:  # noqa: BLE001
            raise HTTPException(status_code=500, detail=f"消息获取失败: {e}")

    # ---- GDPR 合规：个人数据导出（可携带权）与账号级联删除（被遗忘权） ----
    from fastapi.responses import JSONResponse

    @demo.app.get("/api/me/export", include_in_schema=False)
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

    @demo.app.post("/api/me/delete", include_in_schema=False)
    async def _delete_my_account(request: _fastapi.Request):
        user = _require_user(request)
        result = chatstore.delete_user_cascade(user)
        if "error" in result:
            raise HTTPException(status_code=400, detail=result["error"])
        response = JSONResponse(content={"ok": True, **result})
        # 删除后立即登出：注销服务端 token 并清除两种 Gradio 登录 cookie 形态
        token = (request.cookies.get(f"access-token-{demo.app.cookie_id}")
                 or request.cookies.get(f"access-token-unsecure-{demo.app.cookie_id}"))
        if token:
            demo.app.tokens.pop(token, None)
        for _name in (f"access-token-{demo.app.cookie_id}",
                      f"access-token-unsecure-{demo.app.cookie_id}"):
            response.delete_cookie(key=_name, path="/")
        return response

    import signal as _signal

    _shutdown_requested = False

    def _handle_signal(signum, frame):
        global _shutdown_requested
        logger.info(f"收到信号 {signum}，开始优雅关闭...")
        _shutdown_requested = True

    _signal.signal(_signal.SIGTERM, _handle_signal)
    _signal.signal(_signal.SIGINT, _handle_signal)

    logger.info("服务已启动，按 Ctrl+C 或发送 SIGTERM 优雅关闭")
    try:
        while not _shutdown_requested:
            time.sleep(1)
    except (KeyboardInterrupt, SystemExit):
        pass
    finally:
        logger.info("正在清理资源...")
        try:
            for conn in mcp_connections:
                if hasattr(conn, 'close'):
                    conn.close()
        except Exception:
            pass
        try:
            demo.close()
        except Exception:
            pass
        logger.info("已优雅关闭")
