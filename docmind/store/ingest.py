"""文档摄取任务队列：状态流转与服务重启兜底。

连接经包门面晚绑定获取（store._conn()），便于测试整体替换 DB。"""
import time
from docmind import store

# ================= 入库任务 =================

def create_ingest_task(kb_id: str, filename: str, mode: str, status: str,
                       message: str, created_by: str) -> int:
    c = store._conn()
    cur = c.execute(
        """INSERT INTO ingest_tasks(kb_id, filename, mode, status, message,
                                     created_by, created_at, updated_at)
           VALUES(?,?,?,?,?,?,?,?)""",
        (kb_id, filename, mode, status, message, created_by, time.time(), time.time()))
    c.commit()
    return cur.lastrowid


def update_ingest_task(task_id: int, status: str, message: str = "") -> None:
    c = store._conn()
    c.execute("UPDATE ingest_tasks SET status=?, message=?, updated_at=? WHERE id=?",
              (status, message, time.time(), task_id))
    c.commit()


def list_ingest_tasks(kb_id: str, limit: int = 50) -> list[dict]:
    c = store._conn()
    return [dict(r) for r in c.execute(
        "SELECT * FROM ingest_tasks WHERE kb_id=? ORDER BY id DESC LIMIT ?",
        (kb_id, limit)).fetchall()]


def complete_pending_tasks(kb_id: str) -> None:
    """索引重建成功后：把该库待生效的上传/删除任务标记为完成"""
    c = store._conn()
    c.execute(
        """UPDATE ingest_tasks SET status='done', message='索引已生效', updated_at=?
           WHERE kb_id=? AND status='pending' AND mode IN ('upload','delete')""",
        (time.time(), kb_id))
    c.commit()
