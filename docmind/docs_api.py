"""文档管理 REST API — 知识库文档的列出 / 上传 / 删除。

权限：复用 Gradio 登录 cookie（与 assistants_api.py 一致），未登录 401。
存储：通过 store.get_kb(kb_id) 取 doc_dir；不存在则自动创建。
"""
import os
from datetime import datetime, timezone

import fastapi
from fastapi import HTTPException, UploadFile, File
from fastapi.responses import JSONResponse

from docmind import store

# ---- 常量 ----
_ALLOWED_EXT = {".pdf", ".md", ".txt", ".docx", ".csv", ".json"}
_MAX_SIZE = 50 * 1024 * 1024  # 50 MB


# ---- 当前用户解析：复用 Gradio 登录 cookie（与 assistants_api.py 保持一致） ----
def _current_user(request, app) -> str:
    token = (request.cookies.get(f"access-token-{app.cookie_id}")
             or request.cookies.get(f"access-token-unsecure-{app.cookie_id}"))
    return (app.tokens.get(token) if token else None) or ""


def _require_user(request, app) -> str:
    """校验登录态；被要求强制改密的用户返回 403"""
    user = _current_user(request, app)
    if not user:
        raise HTTPException(status_code=401, detail="未登录")
    if store.get_must_change_pwd(user):
        raise HTTPException(status_code=403,
                            detail={"code": "MUST_CHANGE_PWD", "message": "请先修改密码"})
    return user


def _resolve_doc_dir(kb_id: str) -> str:
    """获取 KB 的 doc_dir，不存在时自动创建；KB 不存在抛 404。"""
    kb = store.get_kb(kb_id)
    if kb is None:
        raise HTTPException(status_code=404, detail="知识库不存在")
    doc_dir = kb.get("doc_dir") or ""
    if not doc_dir:
        doc_dir = os.path.join("data", "kb_docs", kb_id)
    os.makedirs(doc_dir, exist_ok=True)
    return doc_dir


def register_docs_routes(app) -> None:
    """注册文档管理路由到 FastAPI app。"""

    @app.get("/api/kbs/{kb_id}/docs", include_in_schema=False)
    async def _list_docs(kb_id: str, request: fastapi.Request):
        _require_user(request, app)
        doc_dir = _resolve_doc_dir(kb_id)
        items = []
        try:
            for name in sorted(os.listdir(doc_dir)):
                fp = os.path.join(doc_dir, name)
                if not os.path.isfile(fp):
                    continue
                stat = os.stat(fp)
                items.append({
                    "name": name,
                    "size": stat.st_size,
                    "modified": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(),
                })
        except OSError:
            pass
        return JSONResponse(items)

    @app.post("/api/kbs/{kb_id}/docs", include_in_schema=False)
    async def _upload_doc(kb_id: str, request: fastapi.Request,
                          file: UploadFile = File(...)):
        _require_user(request, app)
        doc_dir = _resolve_doc_dir(kb_id)

        # 文件名 sanitize
        filename = os.path.basename(file.filename or "")
        if not filename:
            raise HTTPException(status_code=400, detail="文件名为空")

        # 后缀白名单
        ext = os.path.splitext(filename)[1].lower()
        if ext not in _ALLOWED_EXT:
            raise HTTPException(
                status_code=400,
                detail=f"不支持的文件类型: {ext}，允许: {', '.join(sorted(_ALLOWED_EXT))}")

        # 读取内容 + 大小校验
        content = await file.read()
        if len(content) > _MAX_SIZE:
            raise HTTPException(status_code=400,
                                detail=f"文件过大，上限 {_MAX_SIZE // (1024*1024)} MB")

        dest = os.path.join(doc_dir, filename)
        with open(dest, "wb") as f:
            f.write(content)

        # 入库任务追踪：文件落盘成功，等待重建索引后生效
        user = _require_user(request, app)
        store.create_ingest_task(kb_id, filename, "upload", "pending",
                                 "已上传，等待重建索引后生效", user)
        store.record_audit(user, "doc.upload", f"kb:{kb_id}/{filename}",
                           f"{len(content)} bytes")
        return {"ok": True, "name": filename, "size": len(content)}

    @app.delete("/api/kbs/{kb_id}/docs/{filename}", include_in_schema=False)
    async def _delete_doc(kb_id: str, filename: str, request: fastapi.Request):
        _require_user(request, app)
        doc_dir = _resolve_doc_dir(kb_id)

        filename = os.path.basename(filename)
        if not filename:
            raise HTTPException(status_code=400, detail="文件名为空")

        fp = os.path.join(doc_dir, filename)
        if not os.path.isfile(fp):
            raise HTTPException(status_code=404, detail="文件不存在")

        os.remove(fp)
        user = _require_user(request, app)
        store.create_ingest_task(kb_id, filename, "delete", "pending",
                                 "已删除，等待重建索引后从检索移除", user)
        store.record_audit(user, "doc.delete", f"kb:{kb_id}/{filename}")
        return {"ok": True}

    @app.get("/api/kbs/{kb_id}/docs/{filename}/preview", include_in_schema=False)
    async def _preview_doc_chunks(kb_id: str, filename: str, request: fastapi.Request):
        """预览文档的切片内容（chunks）

        返回格式：
        {
            "filename": str,
            "file_type": str,
            "total_chunks": int,
            "chunks": [
                {"index": int, "text": str, "page": int or None},
                ...
            ]
        }
        """
        _require_user(request, app)

        # 获取知识库的向量存储
        from docmind.rag.kb_registry import get_registry

        kb_registry = get_registry()
        result = kb_registry.get(kb_id)

        if result is None or result == (None, None):
            raise HTTPException(status_code=404, detail="知识库不存在或未初始化")

        vector_store, _ = result  # 解包 tuple

        # 从向量存储中查找该文件的所有切片
        chunks = []
        for i, chunk in enumerate(vector_store.chunks):
            if chunk.get('source', '') == filename:
                chunks.append({
                    'index': i,
                    'text': chunk.get('text', ''),
                    'page': chunk.get('page'),
                })

        if not chunks:
            raise HTTPException(status_code=404, detail="文件尚未索引或不存在")

        ext = os.path.splitext(filename)[1].lower()

        return JSONResponse({
            'filename': filename,
            'file_type': ext,
            'total_chunks': len(chunks),
            'chunks': chunks,
        })

    @app.put("/api/kbs/{kb_id}/docs/{filename}/content", include_in_schema=False)
    async def _update_doc_content(kb_id: str, filename: str, request: fastapi.Request):
        """更新文档内容（仅支持文本类文件）

        请求体：{"content": "新内容"}
        """
        _require_user(request, app)
        doc_dir = _resolve_doc_dir(kb_id)

        filename = os.path.basename(filename)
        fp = os.path.join(doc_dir, filename)

        if not os.path.isfile(fp):
            raise HTTPException(status_code=404, detail="文件不存在")

        # 只允许编辑文本类文件
        ext = os.path.splitext(filename)[1].lower()
        editable_exts = {'.md', '.txt', '.json', '.csv'}

        if ext not in editable_exts:
            raise HTTPException(
                status_code=400,
                detail=f"不支持编辑此文件类型: {ext}，仅支持: {', '.join(sorted(editable_exts))}"
            )

        # 读取请求体
        try:
            body = await request.json()
            new_content = body.get('content', '')
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"无效的请求体: {e}")

        # 大小校验
        if len(new_content.encode('utf-8')) > _MAX_SIZE:
            raise HTTPException(
                status_code=400,
                detail=f"内容过大，上限 {_MAX_SIZE // (1024*1024)} MB"
            )

        # 写入文件
        with open(fp, 'w', encoding='utf-8') as f:
            f.write(new_content)

        # 记录审计日志
        user = _require_user(request, app)
        store.record_audit(user, "doc.edit", f"kb:{kb_id}/{filename}",
                          f"{len(new_content)} bytes")

        # 创建重新索引任务
        store.create_ingest_task(kb_id, filename, "edit", "pending",
                                "已修改，等待重建索引后生效", user)

        return {"ok": True, "size": len(new_content.encode('utf-8'))}
