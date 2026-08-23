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

# magic bytes 签名：防扩展名伪装（恶意/误传二进制混进解析层）
_MAGIC_SIGNATURES = {
    ".pdf": (b"%PDF-", 0),
    ".png": (b"\x89PNG", 0),
    ".jpg": (b"\xff\xd8\xff", 0),
    ".jpeg": (b"\xff\xd8\xff", 0),
    ".docx": (b"PK\x03\x04", 0),
    ".xlsx": (b"PK\x03\x04", 0),
}
_TEXT_EXT = {".md", ".txt", ".csv", ".json"}
_VERSIONS_DIR = os.path.join("data", "kb_versions")
_KEEP_VERSIONS = 3

# 对话图片上传目录（消息附件，区别于知识库文档）
_UPLOADS_DIR = os.path.join("data", "uploads")
_META_DB = os.path.join(_UPLOADS_DIR, "meta.db")


def _meta_conn():
    """附件属主元数据(独立 SQLite):文件名 → 上传者。
    /files/uploads 直链按属主隔离——否则任何登录用户可查看他人的
    对话图片(可能含隐私内容)"""
    import sqlite3 as _sq
    conn = _sq.connect(_META_DB)
    conn.execute("""CREATE TABLE IF NOT EXISTS attachments(
        fname TEXT PRIMARY KEY, owner TEXT NOT NULL, created_at REAL)""")
    return conn


def _record_upload(fname: str, owner: str) -> None:
    try:
        import time as _time
        c = _meta_conn()
        c.execute("INSERT OR REPLACE INTO attachments VALUES(?,?,?)",
                  (fname, owner or "anonymous", _time.time()))
        c.commit()
    except Exception:  # noqa: BLE001 - 元数据失败不阻断上传主流程
        pass


def save_chat_image(data_url: str, owner: str = "") -> tuple[str, str]:
    """保存对话图片，返回 (文件名, 规范化的 data URL)。
    data_url 可带 data:image/...;base64, 前缀或裸 base64"""
    import base64
    import re as _re
    import time as _time
    import uuid

    m = _re.match(r"data:(image/[\w.+-]+);base64,(.*)", data_url, _re.DOTALL)
    mime, b64 = (m.group(1), m.group(2)) if m else ("image/png", data_url)
    ext = {"image/png": ".png", "image/jpeg": ".jpg",
           "image/webp": ".webp"}.get(mime, ".png")
    fname = f"{int(_time.time() * 1000)}_{uuid.uuid4().hex[:6]}{ext}"
    os.makedirs(_UPLOADS_DIR, exist_ok=True)
    with open(os.path.join(_UPLOADS_DIR, fname), "wb") as f:
        f.write(base64.b64decode(b64))
    _record_upload(fname, owner)
    return fname, f"data:{mime};base64,{b64}"


# zip 类文档解压总量上限：防压缩炸弹(小文件声明巨大解压体积,
# 解析器解压时耗尽内存/磁盘)。docx/xlsx 均为 zip 容器
ZIP_MAX_UNCOMPRESSED = int(os.getenv("ZIP_MAX_UNCOMPRESSED",
                                     str(500 * 1024 * 1024)))   # 500MB


def _validate_content(filename: str, content: bytes) -> None:
    """内容校验：magic bytes / UTF-8 / zip 炸弹体积"""
    ext = os.path.splitext(filename)[1].lower()
    if ext in {".docx", ".xlsx"}:
        import io as _io
        import zipfile as _zf
        try:
            with _zf.ZipFile(_io.BytesIO(content)) as z:
                total = sum(i.file_size for i in z.infolist())
        except _zf.BadZipFile:
            total = 0   # 非 zip 结构由 magic bytes 检查兜底
        if total > ZIP_MAX_UNCOMPRESSED:
            raise HTTPException(
                status_code=400,
                detail="文件解压后体积异常（疑似压缩炸弹），已拒绝")
    sig = _MAGIC_SIGNATURES.get(ext)
    if sig and not content.startswith(sig[0]):
        raise HTTPException(
            status_code=400,
            detail=f"文件内容与扩展名 {ext} 不符，请检查文件是否损坏或重命名")
    if ext in _TEXT_EXT:
        try:
            content.decode("utf-8")
        except UnicodeDecodeError:
            raise HTTPException(
                status_code=400,
                detail=f"{ext} 文件须为 UTF-8 编码（Excel 导出的 CSV 常为 GBK，请另存为 UTF-8）")


def _backup_existing(kb_id: str, filename: str, doc_dir: str) -> None:
    """同名覆盖前备份旧文件（保留最近 N 版），防内容意外丢失"""
    fp = os.path.join(doc_dir, filename)
    if not os.path.isfile(fp):
        return
    vdir = os.path.join(_VERSIONS_DIR, kb_id)
    os.makedirs(vdir, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    dst = os.path.join(vdir, f"{filename}.{ts}.bak")
    try:
        os.replace(fp, dst)
        # 只保留最近 N 版
        versions = sorted(
            v for v in os.listdir(vdir) if v.startswith(f"{filename}."))
        for old in versions[:-_KEEP_VERSIONS]:
            os.remove(os.path.join(vdir, old))
    except OSError:
        pass   # 备份失败不阻断上传


def _check_quota(doc_dir: str, incoming: int) -> None:
    """KB 配额：文档数与总大小（自动重建上线后防滥用）"""
    total_bytes, count = 0, 0
    for name in os.listdir(doc_dir):
        fp = os.path.join(doc_dir, name)
        if os.path.isfile(fp):
            count += 1
            total_bytes += os.path.getsize(fp)
    from docmind import config as _config
    if count + 1 > _config.MAX_DOCS_PER_KB:
        raise HTTPException(400, detail=f"文档数超限（≤{_config.MAX_DOCS_PER_KB}），请先清理")
    if total_bytes + incoming > _config.MAX_KB_TOTAL_BYTES:
        gb = _config.MAX_KB_TOTAL_BYTES / (1024 * 1024 * 1024)
        raise HTTPException(400, detail=f"总大小超限（≤{gb:.0f}GB），请先清理")


# ---- 当前用户解析：复用 Gradio 登录 cookie（与 assistants_api.py 保持一致） ----
def _current_user(request, app) -> str:
    """自研 token 会话(web_auth),与 app.py 登录链路一致"""
    from docmind import web_auth
    return web_auth.current_user(request)

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


def _extract_html_article(html: str) -> tuple[str, str]:
    """轻量正文提取：去 script/style/nav/footer 等噪声后取 title + 文本段落。
    不引入 readability 类重依赖；内部 wiki/文档站正文结构简单，够用"""
    import re as _re

    title_m = _re.search(r"<title[^>]*>(.*?)</title>", html, _re.IGNORECASE | _re.DOTALL)
    title = (title_m.group(1).strip() if title_m else "") or "未命名"
    noise = _re.compile(
        r"<(script|style|nav|header|footer|aside|noscript)[^>]*>.*?</\1>",
        _re.IGNORECASE | _re.DOTALL)
    html = noise.sub("", html)
    html = _re.sub(r"<!--.*?-->", "", html, flags=_re.DOTALL)
    # 块级标签转换行，压缩空白
    html = _re.sub(r"<(br|/p|/div|/li|/h[1-6]|/tr)[^>]*>", "\n", html, flags=_re.IGNORECASE)
    text = _re.sub(r"<[^>]+>", " ", html)
    lines, seen = [], set()
    for ln in text.split("\n"):
        ln = _re.sub(r"\s+", " ", ln).strip()
        if len(ln) >= 8 and ln not in seen:   # 去短行/重复行（导航残留）
            seen.add(ln)
            lines.append(ln)
    return title[:60], "\n\n".join(lines[:200])   # 上限防超大页面


def register_docs_routes(app) -> None:
    """注册文档管理路由到 FastAPI app。"""

    @app.post("/api/kbs/{kb_id}/import-url", include_in_schema=False)
    async def _import_url(kb_id: str, request: fastapi.Request):
        """网页导入：抓取 URL 正文 → 存为 Markdown → 走既有入库管线（含自动重建）。
        body: {"url": "https://..."}；企业 wiki/文档站内容一键入库"""
        user = _require_user(request, app)
        doc_dir = _resolve_doc_dir(kb_id)

        try:
            body = await request.json()
        except Exception:  # noqa: BLE001
            raise HTTPException(status_code=400, detail="请求体必须是合法 JSON")
        url = str(body.get("url") or "").strip()
        if not url.startswith(("http://", "https://")):
            raise HTTPException(status_code=400, detail="url 必须以 http(s):// 开头")

        import requests as _requests

        try:
            resp = _requests.get(url, timeout=15, headers={
                "User-Agent": "Mozilla/5.0 (compatible; DocMindBot/1.0)"})
            resp.raise_for_status()
        except Exception as e:  # noqa: BLE001
            raise HTTPException(status_code=400, detail=f"网页抓取失败: {e}")

        title, text = _extract_html_article(resp.text)
        if not text.strip():
            raise HTTPException(status_code=400, detail="未能从网页提取到正文内容")

        # 文件名：域名 + 标题（截断 sanitize），存为 md 走统一管线
        from urllib.parse import urlparse
        host = urlparse(url).netloc.replace(".", "_") or "web"
        safe_title = "".join(c for c in title if c.isalnum() or c in "（）()-_" )[:40] or "未命名"
        filename = f"{host}_{safe_title}.md"
        content = f"---\nsource_url: {url}\n抓取时间: {datetime.now().isoformat()}\n---\n\n{text}".encode("utf-8")

        _check_quota(doc_dir, len(content))
        _backup_existing(kb_id, filename, doc_dir)
        with open(os.path.join(doc_dir, filename), "wb") as f:
            f.write(content)

        store.create_ingest_task(kb_id, filename, "import-url", "pending",
                                 "已抓取网页，等待重建索引后生效", user)
        store.record_audit(user, "doc.import-url", f"kb:{kb_id}/{filename}", url)
        from docmind.auto_reindex import schedule_reindex
        schedule_reindex(kb_id)
        return {"ok": True, "name": filename, "size": len(content),
                "title": title, "chars": len(text)}

    @app.get("/api/kbs/{kb_id}/docs/search", include_in_schema=False)
    async def _search_docs(kb_id: str, request: fastapi.Request, q: str = ""):
        """文档内容搜索：关键词 → 命中文档列表（含片段与次数）。
        「哪份文档提到 XX」不用逐个点开预览"""
        _require_user(request, app)
        q = (q or "").strip()
        if len(q) < 2:
            raise HTTPException(status_code=400, detail="关键词至少 2 个字符")

        from docmind.rag.kb_registry import get_registry
        result = get_registry().get(kb_id)
        if result is None or result == (None, None):
            raise HTTPException(status_code=404, detail="知识库不存在或未初始化")
        vector_store, _ = result

        hits: dict[str, dict] = {}
        for c in vector_store.chunks:
            text = c.get("text", "")
            if q in text:
                src = c.get("source", "")
                h = hits.setdefault(src, {"name": src, "count": 0, "snippets": []})
                h["count"] += 1
                if len(h["snippets"]) < 2:
                    idx = text.find(q)
                    start = max(0, idx - 30)
                    h["snippets"].append(
                        ("…" if start > 0 else "") + text[start:idx + len(q) + 50]
                        + ("…" if idx + len(q) + 50 < len(text) else ""))
        return JSONResponse(sorted(hits.values(),
                                   key=lambda x: -x["count"]))

    @app.get("/files/uploads/{name}", include_in_schema=False)
    async def _serve_upload(name: str, request: fastapi.Request):
        """对话图片附件：登录 + 属主隔离（admin 可见全部）；
        存量无属主记录的文件回退为登录可见"""
        user = _require_user(request, app)
        safe = os.path.basename(name)
        path = os.path.join(_UPLOADS_DIR, safe)
        if not os.path.isfile(path):
            raise HTTPException(status_code=404, detail="附件不存在")
        try:
            row = _meta_conn().execute(
                "SELECT owner FROM attachments WHERE fname = ?", (safe,)).fetchone()
        except Exception:  # noqa: BLE001
            row = None
        if row is not None and row["owner"] != user:
            try:
                c = store._conn()
                r = c.execute("SELECT is_admin FROM users WHERE username = ?",
                              (user,)).fetchone()
                is_admin = bool(r and r[0])
            except Exception:  # noqa: BLE001
                is_admin = False
            if not is_admin:
                raise HTTPException(status_code=404, detail="附件不存在")
        from fastapi.responses import FileResponse
        return FileResponse(path)

    @app.post("/api/ocr-image", include_in_schema=False)
    async def _ocr_image(request: fastapi.Request,
                         file: UploadFile = File(...)):
        """对话传图：图片 → 文字（复用索引侧百炼 OCR 与磁盘缓存）。
        前端把识别文本回填输入框由用户确认后发送，保持对话流不变"""
        user = _require_user(request, app)

        ext = os.path.splitext(file.filename or "")[1].lower()
        if ext not in {".png", ".jpg", ".jpeg", ".webp"}:
            raise HTTPException(status_code=400, detail="仅支持 png/jpg/jpeg/webp 图片")
        content = await file.read()
        if len(content) > _MAX_SIZE:
            raise HTTPException(status_code=400,
                                detail=f"图片过大，上限 {_MAX_SIZE // (1024*1024)} MB")

        import tempfile
        from docmind.rag.chunker import _ocr_image as _ocr
        tmp = tempfile.NamedTemporaryFile(suffix=ext, delete=False)
        try:
            tmp.write(content)
            tmp.close()
            text = _ocr(tmp.name)
        except Exception as e:  # noqa: BLE001
            raise HTTPException(status_code=400, detail=f"图片识别失败: {e}")
        finally:
            os.unlink(tmp.name)

        store.record_audit(user, "image.ocr", file.filename or "",
                           f"{len(content)} bytes")
        return {"text": text}

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
        if not content.strip():
            raise HTTPException(status_code=400, detail="文件内容为空")

        _validate_content(filename, content)
        _check_quota(doc_dir, len(content))
        _backup_existing(kb_id, filename, doc_dir)

        dest = os.path.join(doc_dir, filename)
        with open(dest, "wb") as f:
            f.write(content)

        # 入库任务追踪：文件落盘成功，等待重建索引后生效
        user = _require_user(request, app)
        store.create_ingest_task(kb_id, filename, "upload", "pending",
                                 "已上传，等待重建索引后生效", user)
        store.record_audit(user, "doc.upload", f"kb:{kb_id}/{filename}",
                           f"{len(content)} bytes")

        # 自动重建：防抖窗口后增量重建（免手动点按钮，窗口内多次上传合并）
        from docmind.auto_reindex import schedule_reindex
        schedule_reindex(kb_id)
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

        from docmind.auto_reindex import schedule_reindex
        schedule_reindex(kb_id)
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

        # 从向量存储中查找该文件的所有切片（倒排索引，避免全量扫描）
        chunks = [
            {"index": i, "text": c.get("text", ""), "page": c.get("page")}
            for i, c in vector_store.chunks_by_source(filename)
        ]

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

        from docmind.auto_reindex import schedule_reindex
        schedule_reindex(kb_id)
        return {"ok": True, "size": len(new_content.encode('utf-8'))}
