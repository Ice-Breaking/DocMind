"""自研会话认证（替代 Gradio 宿主的登录/cookie 机制）。

设计：
- 会话 token：secrets.token_urlsafe(48)，服务端内存表 {token: (user, exp)}，
  TTL 7 天；重启失效（与原 Gradio tokens 行为一致）
- cookie：dm_session，HttpOnly + SameSite=Lax（现代 CSRF 基线）；
  上 HTTPS 生产后建议补 Secure（生产检查清单项）
- 登录链：本地账号优先 → LDAP 降级（首登自动开通），与原 _login_auth 一致
- 防爆破：用户名维度（15 分钟 5 次）+ IP 维度（15 分钟 20 次）双阈值，
  锁定期间正确密码也拒绝；IP 经 middleware 注入的 contextvar 获取
- 响应格式与 Gradio 兼容（{"success": true/false}），前端零改动
"""
import contextvars
import logging
import secrets
import threading
import time

import fastapi
from fastapi.responses import JSONResponse

from docmind import store

logger = logging.getLogger(__name__)

SESSION_COOKIE = "dm_session"
SESSION_TTL = 7 * 86400

_sessions: dict[str, tuple[str, float]] = {}
_lock = threading.Lock()

# 客户端 IP（由 app 层 security/metrics middleware 每请求注入；取不到时空串）
client_ip: contextvars.ContextVar[str] = contextvars.ContextVar("dm_client_ip",
                                                                 default="")

# ---- 防爆破（双维度）----
_LOGIN_MAX_FAILS = 5       # 同用户名 15 分钟失败 5 次 → 锁用户名
_IP_MAX_FAILS = 20         # 同 IP 15 分钟失败 20 次 → 锁 IP（防撞库扫号）
_LOGIN_LOCK_SECONDS = 900
_failures: dict[str, list[float]] = {}


def _prune_and_count(key: str) -> tuple[int, float]:
    """清理窗口外记录，返回 (剩余失败次数, 首次失败时间)"""
    now = time.time()
    fails = [t for t in _failures.get(key, [])
             if now - t < _LOGIN_LOCK_SECONDS]
    _failures[key] = fails
    return len(fails), (fails[0] if fails else now)


def _is_locked(username: str) -> str | None:
    """返回锁定原因文案；未锁返回 None"""
    ip = client_ip.get() or "unknown"
    for key, limit, what in ((f"u:{username}", _LOGIN_MAX_FAILS, "账号"),
                             (f"ip:{ip}", _IP_MAX_FAILS, "来源 IP")):
        n, first = _prune_and_count(key)
        if n >= limit:
            remain = int(_LOGIN_LOCK_SECONDS - (time.time() - first))
            return f"失败次数过多，{what}已锁定，请 {max(remain, 0) // 60 + 1} 分钟后再试"
    return None


def _record_failure(username: str) -> None:
    ip = client_ip.get() or "unknown"
    _failures.setdefault(f"u:{username}", []).append(time.time())
    _failures.setdefault(f"ip:{ip}", []).append(time.time())
    n, _ = _prune_and_count(f"u:{username}")
    logger.warning(f"登录失败 user={username} ip={ip} 第{n}次")


def _clear_failures(username: str) -> None:
    _failures.pop(f"u:{username}", None)
    _failures.pop(f"ip:{client_ip.get() or 'unknown'}", None)


# ---- 会话管理 ----
def issue(username: str) -> str:
    token = secrets.token_urlsafe(48)
    with _lock:
        # 顺手清理过期会话
        now = time.time()
        for t in [t for t, (_u, exp) in _sessions.items() if exp < now]:
            _sessions.pop(t, None)
        _sessions[token] = (username, now + SESSION_TTL)
    return token


def resolve(request) -> str:
    """从请求解析当前用户；无效/过期返回空串"""
    token = request.cookies.get(SESSION_COOKIE, "")
    if not token:
        return ""
    with _lock:
        entry = _sessions.get(token)
    if not entry:
        return ""
    username, exp = entry
    if time.time() > exp:
        with _lock:
            _sessions.pop(token, None)
        return ""
    return username


def revoke_by_request(request) -> None:
    token = request.cookies.get(SESSION_COOKIE, "")
    if token:
        with _lock:
            _sessions.pop(token, None)


# ---- 用户解析（各 API 模块共用；签名与原 _current_user(request, app)
#      兼容——多余的位置参数被忽略，调用处零改动） ----
def current_user(request, *args, **kwargs) -> str:
    return resolve(request)


def require_user(request, *args, **kwargs) -> str:
    user = resolve(request)
    if not user:
        raise fastapi.HTTPException(status_code=401, detail="未登录")
    if store.get_must_change_pwd(user):
        raise fastapi.HTTPException(
            status_code=403,
            detail={"code": "MUST_CHANGE_PWD", "message": "请先修改密码"})
    return user


def require_admin(request, *args, **kwargs) -> str:
    user = require_user(request)
    if not store.is_admin(user):
        raise fastapi.HTTPException(status_code=403, detail="需要管理员权限")
    return user


# ---- 登录/登出路由（响应格式与 Gradio 兼容，前端零改动） ----
def register_auth_routes(app) -> None:
    @app.post("/login", include_in_schema=False)
    async def _login(request: fastapi.Request):
        form = await request.form()
        username = str(form.get("username") or "")
        password = str(form.get("password") or "")
        locked = _is_locked(username)
        if locked:
            return JSONResponse({"success": False, "detail": locked},
                                status_code=429)
        from docmind import ldap_auth
        ok = store.verify_user(username, password)
        via = "local"
        if not ok and ldap_auth.authenticate(username, password):
            store.ensure_external_user(username)
            ok, via = True, "ldap"
        if not ok:
            _record_failure(username)
            return JSONResponse({"success": False,
                                 "detail": "用户名或密码错误"},
                                status_code=400)
        _clear_failures(username)
        store.record_audit(username, "login", via)
        token = issue(username)
        resp = JSONResponse({
            "success": True,
            "must_change_pwd": store.get_must_change_pwd(username)})
        resp.set_cookie(SESSION_COOKIE, token,
                        max_age=SESSION_TTL, httponly=True, samesite="lax",
                        path="/")
        return resp

    @app.get("/logout", include_in_schema=False)
    async def _logout():
        return JSONResponse({"success": True})

    @app.post("/logout", include_in_schema=False)
    async def _logout_post(request: fastapi.Request):
        revoke_by_request(request)
        resp = JSONResponse({"success": True})
        resp.delete_cookie(SESSION_COOKIE, path="/")
        return resp
