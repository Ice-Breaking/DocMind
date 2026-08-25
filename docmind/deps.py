"""FastAPI 依赖注入：认证/授权的单一来源。

此前各 API 模块各自复制 `_require_user(request, app)` / `_require_admin` /
`_current_user`（签名里的 app 从未使用，权限语义分散在 6 个文件，改动需
多处同步；voice_api 甚至跨模块 import 别人的私有函数）。收敛为三个依赖：

- current_user  ：解析登录态，不强制（游客得空串）
- require_user  ：强制登录 + 强制改密拦截（401/403 语义与原实现一致）
- require_admin ：强制管理员

路由签名声明 `user: RequireUser` 即完成校验与取值；OpenAPI 文档自动
携带安全语义。外部契约（路径/方法/状态码/响应体）完全不变。
"""
from typing import Annotated

from fastapi import Depends, HTTPException, Request

from docmind import store, web_auth


def current_user(request: Request) -> str:
    """登录态解析（不强制）：已登录返回用户名，否则空串"""
    return web_auth.current_user(request)


def require_user(request: Request) -> str:
    """强制登录；被要求强制改密的用户 403（统一委托 web_auth.require_user）"""
    return web_auth.require_user(request)


def require_admin(request: Request) -> str:
    """强制管理员（未登录 401 / 非管理员 403 / 待改密 403）"""
    user = current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="未登录")
    if not store.is_admin(user):
        raise HTTPException(status_code=403, detail="需要管理员权限")
    if store.get_must_change_pwd(user):
        raise HTTPException(status_code=403,
                            detail={"code": "MUST_CHANGE_PWD",
                                    "message": "请先修改密码"})
    return user


CurrentUser = Annotated[str, Depends(current_user)]
RequireUser = Annotated[str, Depends(require_user)]
RequireAdmin = Annotated[str, Depends(require_admin)]
