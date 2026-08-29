"""用户管理 API（仅管理员）：新增 / 重置密码 / 授予收回管理员 / 删除。

安全约束：
- 不能删除自己；不能删除/降级最后一个管理员（delete_user_cascade 内置保护 + 降级前计数校验）
- 管理员新建的账号强制首登改密（must_change_pwd=1）
- 所有操作写审计日志
"""
import os
import re
import time

import fastapi
from fastapi import HTTPException
from fastapi.responses import FileResponse, JSONResponse

from docmind.deps import RequireAdmin, RequireUser
from docmind import store
from docmind.docs_api import _safe_doc_path

_USERNAME_RE = re.compile(r"^[\w.@-]{2,64}$")

AVATAR_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                          "data", "avatars")
_MAX_AVATAR = 2 * 1024 * 1024


def register_users_routes(app) -> None:

    @app.post("/api/me/avatar", include_in_schema=False)
    async def _set_my_avatar(request: fastapi.Request, user: RequireUser):
        """任何登录用户（含管理员）修改自己的头像"""
        body = await request.json()
        avatar = str(body.get("avatar") or "")[:64]
        store.set_user_avatar(user, avatar)
        store.record_audit(user, "user.avatar", f"user:{user}", avatar[:24])
        return {"ok": True}

    @app.post("/api/me/avatar-upload", include_in_schema=False)
    async def _upload_avatar(request: fastapi.Request, user: RequireUser, file: fastapi.UploadFile = fastapi.File(...)):
        """上传自定义头像：存为待审核（pending_avatar），审核通过前展示旧头像"""
        data = await file.read()
        if not data:
            raise HTTPException(status_code=400, detail="空文件")
        if len(data) > _MAX_AVATAR:
            raise HTTPException(status_code=400, detail="图片过大（上限 2MB，请先压缩）")
        ok_png = data[:4] == b"\x89PNG"
        ok_jpg = data[:2] == b"\xff\xd8"
        ok_webp = data[:4] == b"RIFF" and data[8:12] == b"WEBP"
        if not (ok_png or ok_jpg or ok_webp):
            raise HTTPException(status_code=400, detail="仅支持 PNG/JPG/WebP 图片")
        ext = "png" if ok_png else ("jpg" if ok_jpg else "webp")
        os.makedirs(AVATAR_DIR, exist_ok=True)
        fname = f"pending_{user}_{int(time.time() * 1000)}.{ext}"
        _safe_doc_path(AVATAR_DIR, fname).write_bytes(data)
        store.set_pending_avatar(user, fname)
        store.record_audit(user, "user.avatar-upload", f"user:{user}", fname)
        return {"ok": True, "pending": fname}

    @app.get("/api/avatar-file/{name}", include_in_schema=False)
    async def _avatar_file(name: str, request: fastapi.Request, _user: RequireUser):
        """头像文件（登录态可读，img 标签同源自动带 cookie）"""
        safe = os.path.basename(name)
        path = os.path.join(AVATAR_DIR, safe)
        if not os.path.isfile(path):
            raise HTTPException(status_code=404, detail="头像不存在")
        media = {"png": "image/png", "jpg": "image/jpeg",
                 "webp": "image/webp"}.get(safe.rsplit(".", 1)[-1], "image/png")
        return FileResponse(path, media_type=media)

    @app.get("/api/admin/avatar-reviews", include_in_schema=False)
    async def _avatar_reviews(request: fastapi.Request, _user: RequireAdmin):
        return JSONResponse(store.list_pending_avatars())

    @app.post("/api/admin/avatar-review/{username}", include_in_schema=False)
    async def _avatar_review(username: str, request: fastapi.Request,
                         admin: RequireAdmin):
        """人工审核：approve → 待审核转正为正式头像；reject → 丢弃保留旧头像"""
        body = await request.json()
        action = str(body.get("action") or "")
        pend, _ts = store.get_pending_avatar(username)
        if not pend:
            raise HTTPException(status_code=400, detail="该用户没有待审核头像")
        if action == "approve":
            store.set_user_avatar(username, f"file:{pend}")
        else:
            try:
                os.remove(os.path.join(AVATAR_DIR, os.path.basename(pend)))
            except OSError:
                pass
        store.clear_pending_avatar(username)
        store.record_audit(admin, f"admin.avatar-{action}", f"user:{username}", pend)
        return {"ok": True}

    @app.get("/api/admin/users", include_in_schema=False)
    async def _users(request: fastapi.Request, _user: RequireAdmin):
        return JSONResponse(store.list_users_rich())

    @app.post("/api/admin/users", include_in_schema=False)
    async def _create_user(request: fastapi.Request, actor: RequireAdmin):
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
    async def _reset_pwd(username: str, request: fastapi.Request,
                         actor: RequireAdmin):
        body = await request.json()
        ok, msg = store.reset_password(username, str(body.get("new_password") or ""))
        if not ok:
            raise HTTPException(status_code=400, detail=msg)
        store.record_audit(actor, "user.reset-password", f"user:{username}")
        return {"ok": True, "message": msg}

    @app.post("/api/admin/users/{username}/admin", include_in_schema=False)
    async def _toggle_admin(username: str, request: fastapi.Request,
                            actor: RequireAdmin):
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
    async def _delete_user(username: str, request: fastapi.Request,
                           actor: RequireAdmin):
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
