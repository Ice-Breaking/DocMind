"""治理 API：审计中心（列表 + CSV 导出）+ 数据备份（一键备份 + 列表）。

审计事件由各业务端点通过 store.record_audit 写入（登录/KB/文档/密钥/模型/Badcase）。
备份 = SQLite VACUUM INTO（热备，不锁库）+ 知识库文档打包 zip，存 data/backups/。
恢复方式（人工演练）：停服 → 解压覆盖 chat.db 与文档目录 → 重启。
"""
import csv
import io
import os
import sqlite3
import time
import zipfile

import fastapi
from fastapi import HTTPException
from fastapi.responses import JSONResponse, StreamingResponse

from docmind import config, store
from docmind.admin import _require_admin

BACKUP_DIR = os.path.join(config.PROJECT_ROOT, "data", "backups")


def _do_backup() -> dict:
    """创建一份备份：数据库热备 + 全部知识库文档；返回文件信息"""
    os.makedirs(BACKUP_DIR, exist_ok=True)
    name = f"backup_{time.strftime('%Y%m%d_%H%M%S')}.zip"
    path = os.path.join(BACKUP_DIR, name)
    tmp_db = path + ".db.tmp"
    # VACUUM INTO：SQLite 官方热备方式，WAL 模式下不阻塞写入
    src = sqlite3.connect(store.DB_PATH)
    try:
        src.execute("VACUUM INTO ?", (tmp_db,))
    finally:
        src.close()
    count = 0
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.write(tmp_db, "chat.db")
        count += 1
        doc_roots = [config.KNOWLEDGE_DIR,
                     os.path.join(config.PROJECT_ROOT, "data", "kb_docs")]
        for root_dir in doc_roots:
            if not os.path.isdir(root_dir):
                continue
            for root, _dirs, files in os.walk(root_dir):
                for fn in files:
                    fp = os.path.join(root, fn)
                    z.write(fp, os.path.relpath(fp, config.PROJECT_ROOT))
                    count += 1
    os.remove(tmp_db)
    return {"name": name, "size": os.path.getsize(path), "files": count}


def register_governance_routes(app) -> None:

    # ================= 审计中心 =================
    @app.get("/api/admin/audit", include_in_schema=False)
    async def _audit_list(request: fastapi.Request, actor: str = "",
                          action: str = "", days: int = 0, limit: int = 500):
        _require_admin(request, app)
        return JSONResponse(store.list_audit(actor, action, days, limit))

    @app.get("/api/admin/audit/export", include_in_schema=False)
    async def _audit_export(request: fastapi.Request, actor: str = "",
                            action: str = "", days: int = 30):
        """CSV 导出（带 UTF-8 BOM，Excel 直接打开不乱码）"""
        _require_admin(request, app)
        rows = store.list_audit(actor, action, days, limit=5000)
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(["时间", "操作人", "事件", "对象", "详情"])
        for r in rows:
            w.writerow([time.strftime("%Y-%m-%d %H:%M:%S",
                                      time.localtime(r["created_at"])),
                        r["actor"], r["action"], r["target"], r["detail"]])
        data = "\ufeff" + buf.getvalue()
        fname = f"audit_{time.strftime('%Y%m%d_%H%M%S')}.csv"
        return StreamingResponse(
            iter([data]), media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": f"attachment; filename={fname}"})

    # ================= 数据备份 =================
    @app.post("/api/admin/backup", include_in_schema=False)
    async def _create_backup(request: fastapi.Request):
        user = _require_admin(request, app)
        try:
            info = _do_backup()
        except Exception as e:  # noqa: BLE001
            raise HTTPException(status_code=500, detail=f"备份失败: {e}")
        store.record_audit(user, "backup.create", info["name"],
                           f"{info['files']} 个文件 / {info['size']} bytes")
        return JSONResponse({"ok": True, **info}, status_code=201)

    @app.get("/api/admin/backups", include_in_schema=False)
    async def _list_backups(request: fastapi.Request):
        _require_admin(request, app)
        items = []
        if os.path.isdir(BACKUP_DIR):
            for fn in sorted(os.listdir(BACKUP_DIR), reverse=True):
                fp = os.path.join(BACKUP_DIR, fn)
                if fn.endswith(".zip") and os.path.isfile(fp):
                    items.append({"name": fn,
                                  "size": os.path.getsize(fp),
                                  "created_at": os.path.getmtime(fp)})
        return JSONResponse(items)
