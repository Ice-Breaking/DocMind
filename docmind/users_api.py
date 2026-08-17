"""用户管理 API（仅管理员）：新增 / 重置密码 / 授予收回管理员 / 删除。

安全约束：
- 不能删除自己；不能删除/降级最后一个管理员（delete_user_cascade 内置保护 + 降级前计数校验）
- 管理员新建的账号强制首登改密（must_change_pwd=1）
- 所有操作写审计日志
"""
import re

import fastapi
from fastapi import HTTPException
from fastapi.responses import JSONResponse

from docmind import store
from docmind.admin import _require_admin

_USERNAME_RE = re.compile(r"^[\w.@-]{2,64}$")


def register_users_routes(app) -> None:

    @app.get("/api/admin/users", include_in_schema=False)
    async def _users(request: fastapi.Request):
        _require_admin(request, app)
        return JSONResponse(store.list_users_rich())

    @app.post("/api/admin/users", include_in_schema=False)
    async def _create_user(request: fastapi.Request):
        actor = _require_admin(request, app)
        body = await request.json()
        username = str(body.get("username") or "").strip()
        password = str(body.get("password") or "")
        is_admin = bool(body.get("is_admin"))
        if not _USERNAME_RE.match(username):
            raise HTTPException(status_code=400,
                                detail="用户名仅支持字母/数字/._@-，长度 2-64")
        if len(password) < 8:
            raise HTTPException(status_code=400, detail="密码至少 8 个字符")
        if not store.create_user(username, password):
            raise HTTPException(status_code=409, detail="用户名已存在")
        if is_admin:
            store.set_admin(username, True)
        # 管理员代建账号：强制首登改密
        c = store._conn()
        c.execute("UPDATE users SET must_change_pwd=1 WHERE username=?", (username,))
        c.commit()
        store.record_audit(actor, "user.create", f"user:{username}",
                           "admin" if is_admin else "user")
        return JSONResponse({"ok": True, "username": username}, status_code=201)

    @app.post("/api/admin/users/{username}/reset-password", include_in_schema=False)
    async def _reset_pwd(username: str, request: fastapi.Request):
        actor = _require_admin(request, app)
        body = await request.json()
        ok, msg = store.reset_password(username, str(body.get("new_password") or ""))
        if not ok:
            raise HTTPException(status_code=400, detail=msg)
        store.record_audit(actor, "user.reset-password", f"user:{username}")
        return {"ok": True, "message": msg}

    @app.post("/api/admin/users/{username}/admin", include_in_schema=False)
    async def _toggle_admin(username: str, request: fastapi.Request):
        actor = _require_admin(request, app)
        body = await request.json()
        grant = bool(body.get("is_admin"))
        if not grant and store.is_admin(username) and store.count_admins() <= 1:
            raise HTTPException(status_code=400, detail="不能收回最后一个管理员的权限")
        if username == actor and not grant:
            raise HTTPException(status_code=400, detail="不能取消自己的管理员权限")
        if not store.set_admin(username, grant):
            raise HTTPException(status_code=404, detail="用户不存在")
        store.record_audit(actor, "user.set-admin", f"user:{username}",
                           "grant" if grant else "revoke")
        return {"ok": True}

    @app.delete("/api/admin/users/{username}", include_in_schema=False)
    async def _delete_user(username: str, request: fastapi.Request):
        actor = _require_admin(request, app)
        if username == actor:
            raise HTTPException(status_code=400, detail="不能删除当前登录的自己")
        try:
            stats = store.delete_user_cascade(username)
        except Exception as e:  # noqa: BLE001 - 最后一个管理员保护等
            raise HTTPException(status_code=400, detail=str(e))
        if not stats:
            raise HTTPException(status_code=404, detail="用户不存在")
        store.record_audit(actor, "user.delete", f"user:{username}", str(stats))
        return {"ok": True, "deleted": stats}
