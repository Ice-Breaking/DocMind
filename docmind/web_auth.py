"""自研会话认证：替代 Gradio 的 token/cookie 机制（去 Gradio 化的一部分）。

设计：
- token：secrets.token_urlsafe(32)，内存表 + 12h 滑动过期（重启失效，
  用户重登即可；单进程部署下与原 Gradio 行为一致但完全自控）
- cookie：dm_session，HttpOnly + SameSite=Lax + Path=/
- 登录防爆破：用户名 15 分钟 5 次锁定 + IP 15 分钟 20 次锁定
  （IP 经 contextvar 由中间件注入，见 app.py）
"""
import contextvars
import secrets
import threading
import time

from fastapi import HTTPException

from docmind import store

TOKEN_COOKIE = "dm_session"
TOKEN_TTL = 12 * 3600

_tokens: dict[str, tuple[str, float]] = {}   # token -> (username, expires)
_lock = threading.Lock()

# ---- 登录防爆破（用户名 + IP 双维度，内存态） ----
_LOGIN_MAX_FAILS = 5
_IP_MAX_FAILS = 20
_LOCK_SECONDS = 900
_failures: dict[str, list[float]] = {}
# 请求级客户端 IP：ContextVar 而非模块级全局——FastAPI 并发处理请求时
# 全局变量会被后到请求覆盖，A 的失败可能记到 B 的 IP 头上（IP 锁定错乱）
_client_ip: contextvars.ContextVar[str] = contextvars.ContextVar(
    "docmind_client_ip", default="")


def set_client_ip(ip: str) -> None:
    _client_ip.set((ip or "").split(",")[0].strip() or "unknown")


def client_ip() -> str:
    return _client_ip.get() or "unknown"


def _recent(key: str, window: float, max_n: int) -> int:
    """清理并返回窗口内失败次数；达到阈值返回 -1 表示应锁定"""
    now = time.time()
    fails = [t for t in _failures.get(key, []) if now - t < window]
    _failures[key] = fails
    return -1 if len(fails) >= max_n else len(fails)


def is_locked(username: str) -> int:
    """锁定中返回剩余秒数，未锁定返回 0"""
    r = _recent(f"user:{username}", _LOCK_SECONDS, _LOGIN_MAX_FAILS)
    if r == -1:
        fails = _failures.get(f"user:{username}", [])
        return int(_LOCK_SECONDS - (time.time() - fails[0])) if fails else 0
    ip_r = _recent(f"ip:{client_ip()}", _LOCK_SECONDS, _IP_MAX_FAILS)
    if ip_r == -1:
        fails = _failures.get(f"ip:{client_ip()}", [])
        return int(_LOCK_SECONDS - (time.time() - fails[0])) if fails else 0
    return 0


def record_failure(username: str) -> None:
    now = time.time()
    for key in (f"user:{username}", f"ip:{client_ip()}"):
        _failures.setdefault(key, []).append(now)


def clear_failures(username: str) -> None:
    """登录成功：清除该用户与该来源 IP 两个维度的失败记录。

    只清用户维度的话，同 IP 累计失败达阈值后（如共享出口 NAT 的办公网、
    攻击者恰与正常用户同网段），正常用户改对密码重登仍被 IP 锁拦满
    15 分钟——成功登录即视为该来源可信，两维度一并解除。"""
    _failures.pop(f"user:{username}", None)
    _failures.pop(f"ip:{client_ip()}", None)


# ---- token 生命周期 ----
def issue(username: str) -> str:
    token = secrets.token_urlsafe(32)
    with _lock:
        # 容量控制：清理过期
        now = time.time()
        for t in [t for t, (_u, exp) in _tokens.items() if exp < now]:
            _tokens.pop(t, None)
        _tokens[token] = (username, now + TOKEN_TTL)
    return token


def validate(token: str | None) -> str:
    if not token:
        return ""
    with _lock:
        entry = _tokens.get(token)
        if not entry:
            return ""
        username, expires = entry
        if time.time() > expires:
            _tokens.pop(token, None)
            return ""
        _tokens[token] = (username, time.time() + TOKEN_TTL)   # 滑动续期
        return username


def revoke(token: str | None) -> None:
    if token:
        with _lock:
            _tokens.pop(token, None)


# ---- 请求侧助手（各 API 模块共用，替代各自复制的 Gradio cookie 读取） ----
def current_user(request) -> str:
    return validate(request.cookies.get(TOKEN_COOKIE))


def require_user(request) -> str:
    user = current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="未登录")
    if store.get_must_change_pwd(user):
        raise HTTPException(status_code=403,
                            detail={"code": "MUST_CHANGE_PWD", "message": "请先修改密码"})
    return user
