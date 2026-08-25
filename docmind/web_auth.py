"""自研会话认证：替代 Gradio 的 token/cookie 机制（去 Gradio 化的一部分）。

设计：
- token：secrets.token_urlsafe(32)，内存 L1 + SQLite 持久化 L2
  （重启不再全员掉线；单进程部署下完全自控）。DB 只存 sha256 哈希，
  库泄露不等于会话泄露
- cookie：dm_session，HttpOnly + SameSite=Lax + Path=/
- 登录防爆破：用户名 15 分钟 5 次锁定 + IP 15 分钟 20 次锁定
  （IP 经 contextvar 由中间件注入，见 app.py）
"""
import contextvars
import hashlib
import secrets
import threading
import time

from fastapi import HTTPException

from docmind import store

TOKEN_COOKIE = "dm_session"
TOKEN_TTL = 12 * 3600

_tokens: dict[str, tuple[str, float]] = {}   # token -> (username, expires)（L1 缓存）
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
    """清理并返回窗口内失败次数；达到阈值返回 -1 表示应锁定。
    窗口外记录清空后删除字典键——_failures 若只增不删，用户名枚举
    请求可无限撑大内存"""
    now = time.time()
    fails = [t for t in _failures.get(key, []) if now - t < window]
    if fails:
        _failures[key] = fails
    else:
        _failures.pop(key, None)
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
def _th(token: str) -> str:
    """token 的 sha256（DB 只存哈希：chat.db 备份/泄露不等于会话泄露）"""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _db_persist(token_hash: str, username: str, expires: float) -> None:
    try:
        c = store._conn()
        c.execute(
            "INSERT INTO auth_tokens(token_hash, username, expires_at) VALUES(?,?,?) "
            "ON CONFLICT(token_hash) DO UPDATE SET expires_at = excluded.expires_at",
            (token_hash, username, expires))
        c.commit()
    except Exception:  # noqa: BLE001 - 持久化失败降级纯内存模式（重启需重登）
        pass


def issue(username: str) -> str:
    token = secrets.token_urlsafe(32)
    with _lock:
        # 容量控制：清理过期
        now = time.time()
        for t in [t for t, (_u, exp) in _tokens.items() if exp < now]:
            _tokens.pop(t, None)
        expires = now + TOKEN_TTL
        _tokens[token] = (username, expires)
    _db_persist(_th(token), username, expires)
    try:
        c = store._conn()
        c.execute("DELETE FROM auth_tokens WHERE expires_at < ?", (now,))
        c.commit()
    except Exception:  # noqa: BLE001
        pass
    # 顺带清扫防爆破字典的过期键（窗口 15 分钟，登录频度下开销可忽略）
    with _lock:
        stale = [k for k, ts in _failures.items()
                 if not ts or now - ts[-1] > _LOCK_SECONDS * 2]
        for k in stale:
            _failures.pop(k, None)
    return token


def validate(token: str | None) -> str:
    if not token:
        return ""
    now = time.time()
    with _lock:
        entry = _tokens.get(token)
        if entry:
            username, expires = entry
            if now > expires:
                _tokens.pop(token, None)
                return ""
            # 滑动续期（内存 L1）；剩余不足一半时才同步 DB，
            # 免去每请求一次写放大
            new_exp = now + TOKEN_TTL
            _tokens[token] = (username, new_exp)
            if expires - now < TOKEN_TTL / 2:
                _db_persist(_th(token), username, new_exp)
            return username
    # L1 未命中（进程重启场景）：回源 DB
    try:
        row = store._conn().execute(
            "SELECT username, expires_at FROM auth_tokens WHERE token_hash = ?",
            (_th(token),)).fetchone()
    except Exception:  # noqa: BLE001
        row = None
    if not row:
        return ""
    if now > row["expires_at"]:
        try:
            c = store._conn()
            c.execute("DELETE FROM auth_tokens WHERE token_hash = ?", (_th(token),))
            c.commit()
        except Exception:  # noqa: BLE001
            pass
        return ""
    username = row["username"]
    new_exp = now + TOKEN_TTL
    with _lock:
        _tokens[token] = (username, new_exp)
    _db_persist(_th(token), username, new_exp)
    return username


def revoke(token: str | None) -> None:
    if token:
        with _lock:
            _tokens.pop(token, None)
        try:
            c = store._conn()
            c.execute("DELETE FROM auth_tokens WHERE token_hash = ?", (_th(token),))
            c.commit()
        except Exception:  # noqa: BLE001
            pass


def revoke_other_sessions(username: str, keep_token: str | None = None) -> int:
    """吊销该用户的其余全部会话，保留 keep_token（发起改密的当前会话）。
    改密成功后必须调用：否则密码虽已更换，旧会话仍凭滑动续期存活至
    12h TTL——丢失设备/被窃会话在改密后依旧可用（二轮回归盲区 J 实测）。
    返回吊销数量"""
    removed = 0
    keep_hash = _th(keep_token) if keep_token else ""
    with _lock:
        for t in [t for t, (u, _exp) in _tokens.items()
                  if u == username and t != keep_token]:
            _tokens.pop(t, None)
            removed += 1
    try:
        c = store._conn()
        c.execute(
            "DELETE FROM auth_tokens WHERE username = ? AND token_hash != ?",
            (username, keep_hash))
        c.commit()
    except Exception:  # noqa: BLE001
        pass
    return removed


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
