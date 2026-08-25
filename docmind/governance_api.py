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
from fastapi.responses import JSONResponse, StreamingResponse

from docmind.deps import RequireAdmin
from docmind.api_utils import server_error
from docmind import config, store

BACKUP_DIR = os.path.join(config.PROJECT_ROOT, "data", "backups")


def _do_backup() -> dict:
    """创建一份备份：数据库热备 + 全部知识库文档；返回文件信息

    chat.db（用户/会话/审计）与 trace.db（调用链，不可重建）均走
    VACUUM INTO 热备；cache.db 语义缓存可重建，有意不备份。"""
    os.makedirs(BACKUP_DIR, exist_ok=True)
    name = f"backup_{time.strftime('%Y%m%d_%H%M%S')}.zip"
    path = os.path.join(BACKUP_DIR, name)
    count = 0
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        # 数据库热备（先落临时文件再入 zip）
        from docmind import trace_store
        for label, db_path in (("chat.db", store.DB_PATH),
                               ("trace.db", trace_store.DB_PATH)):
            if not os.path.exists(db_path):
                continue
            tmp_db = path + f".{label}.tmp"
            src = sqlite3.connect(db_path)
            try:
                src.execute("VACUUM INTO ?", (tmp_db,))
            finally:
                src.close()
            z.write(tmp_db, label)
            os.remove(tmp_db)
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
    return {"name": name, "size": os.path.getsize(path), "files": count}


def register_governance_routes(app) -> None:

    # ================= 审计中心 =================
    @app.get("/api/admin/audit", include_in_schema=False)
    async def _audit_list(request: fastapi.Request, _user: RequireAdmin, actor: str = "",
                          action: str = "", days: int = 0, limit: int = 500,
                          offset: int = 0):
        """审计事件列表（分页：limit/offset 可选，默认值保持旧行为）"""
        return JSONResponse(store.list_audit(actor, action, days, limit,
                                             offset=max(0, offset)))

    @app.get("/api/admin/audit/export", include_in_schema=False)
    async def _audit_export(request: fastapi.Request, _user: RequireAdmin, actor: str = "",
                            action: str = "", days: int = 30):
        """CSV 导出（带 UTF-8 BOM，Excel 直接打开不乱码）"""
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
    async def _create_backup(request: fastapi.Request, user: RequireAdmin):
        try:
            info = _do_backup()
        except Exception as e:  # noqa: BLE001
            raise server_error("备份失败", e)
        store.record_audit(user, "backup.create", info["name"],
                           f"{info['files']} 个文件 / {info['size']} bytes")
        return JSONResponse({"ok": True, **info}, status_code=201)

    @app.get("/api/admin/backups", include_in_schema=False)
    async def _list_backups(request: fastapi.Request, _user: RequireAdmin):
        items = []
        if os.path.isdir(BACKUP_DIR):
            for fn in sorted(os.listdir(BACKUP_DIR), reverse=True):
                fp = os.path.join(BACKUP_DIR, fn)
                if fn.endswith(".zip") and os.path.isfile(fp):
                    items.append({"name": fn,
                                  "size": os.path.getsize(fp),
                                  "created_at": os.path.getmtime(fp)})
        return JSONResponse(items)

    # ================= 配置热加载 =================
    @app.get("/api/admin/config/reloadable", include_in_schema=False)
    async def _get_reloadable_configs(request: fastapi.Request, _user: RequireAdmin):
        """获取可热加载的配置项"""
        from docmind import config_reload
        return JSONResponse(config_reload.get_reloadable_configs())

    @app.post("/api/admin/config/reload", include_in_schema=False)
    async def _reload_config(request: fastapi.Request, user: RequireAdmin):
        """热加载配置（从 .env 重新读取）"""
        from docmind import config_reload
        changes = config_reload.reload_config()
        store.record_audit(user, "config.reload", "",
                           f"变更 {len(changes)} 项配置")
        return JSONResponse({"ok": True, "changes": changes})
