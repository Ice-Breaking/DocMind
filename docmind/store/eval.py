"""评测数据集与评测运行记录。

连接经包门面晚绑定获取（store._conn()），便于测试整体替换 DB。"""
import json
import time
from docmind import store

# ================= 评测集 / 评测运行 =================

def list_eval_datasets() -> list[dict]:
    c = store._conn()
    rows = [dict(r) for r in c.execute(
        "SELECT * FROM eval_datasets ORDER BY id").fetchall()]
    for r in rows:
        r["items"] = json.loads(r.get("items") or "[]")
    return rows


def get_eval_dataset(ds_id: int) -> dict | None:
    c = store._conn()
    r = c.execute("SELECT * FROM eval_datasets WHERE id=?", (ds_id,)).fetchone()
    if not r:
        return None
    d = dict(r)
    d["items"] = json.loads(d.get("items") or "[]")
    return d


def create_eval_dataset(name: str, kb_id: str, items: list) -> dict:
    c = store._conn()
    cur = c.execute(
        "INSERT INTO eval_datasets(name, kb_id, items, created_at) VALUES(?,?,?,?)",
        (name, kb_id or "default", json.dumps(items, ensure_ascii=False), time.time()))
    c.commit()
    return get_eval_dataset(cur.lastrowid)


def update_eval_dataset(ds_id: int, name: str | None = None,
                        kb_id: str | None = None, items: list | None = None) -> dict | None:
    c = store._conn()
    if not get_eval_dataset(ds_id):
        return None
    if name is not None:
        c.execute("UPDATE eval_datasets SET name=? WHERE id=?", (name, ds_id))
    if kb_id is not None:
        c.execute("UPDATE eval_datasets SET kb_id=? WHERE id=?", (kb_id, ds_id))
    if items is not None:
        c.execute("UPDATE eval_datasets SET items=? WHERE id=?",
                  (json.dumps(items, ensure_ascii=False), ds_id))
    c.commit()
    return get_eval_dataset(ds_id)


def delete_eval_dataset(ds_id: int) -> bool:
    c = store._conn()
    cur = c.execute("DELETE FROM eval_datasets WHERE id=?", (ds_id,))
    c.execute("DELETE FROM eval_runs WHERE dataset_id=?", (ds_id,))
    c.commit()
    return cur.rowcount > 0


def create_eval_run(dataset_id: int, mode: str, top_k: int, created_by: str) -> int:
    c = store._conn()
    cur = c.execute(
        """INSERT INTO eval_runs(dataset_id, mode, top_k, status, created_by, created_at)
           VALUES(?,?,?,?,?,?)""",
        (dataset_id, mode, top_k, "pending", created_by, time.time()))
    c.commit()
    return cur.lastrowid


def update_eval_run(run_id: int, **fields) -> None:
    c = store._conn()
    allowed = {"status", "recall", "mrr", "total", "hits", "details", "duration_ms"}
    cols, vals = [], []
    for k, v in fields.items():
        if k not in allowed:
            continue
        if k == "details" and not isinstance(v, str):
            v = json.dumps(v, ensure_ascii=False)
        cols.append(f"{k}=?")
        vals.append(v)
    if not cols:
        return
    vals.append(run_id)
    c.execute(f"UPDATE eval_runs SET {', '.join(cols)} WHERE id=?", vals)
    c.commit()


def list_eval_runs(dataset_id: int | None = None, limit: int = 50) -> list[dict]:
    c = store._conn()
    if dataset_id:
        rows = c.execute(
            "SELECT * FROM eval_runs WHERE dataset_id=? ORDER BY id DESC LIMIT ?",
            (dataset_id, limit)).fetchall()
    else:
        rows = c.execute(
            "SELECT * FROM eval_runs ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        # 列表视图不带明细（可能很大），只给命中/未命中数
        details = json.loads(d.get("details") or "[]")
        d["miss_count"] = sum(1 for x in details if not x.get("hit_rank"))
        d.pop("details", None)
        out.append(d)
    return out


def get_eval_run(run_id: int) -> dict | None:
    c = store._conn()
    r = c.execute("SELECT * FROM eval_runs WHERE id=?", (run_id,)).fetchone()
    if not r:
        return None
    d = dict(r)
    d["details"] = json.loads(d.get("details") or "[]")
    return d
