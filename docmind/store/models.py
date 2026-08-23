"""模型接入注册表：LLM/Embedding/Rerank 配置与激活项。

连接经包门面晚绑定获取（store._conn()），便于测试整体替换 DB。"""
import time
from docmind import store

# ================= 模型配置 =================

def list_models(kind: str | None = None) -> list[dict]:
    c = store._conn()
    if kind:
        rows = c.execute("SELECT * FROM models WHERE kind=? ORDER BY id", (kind,)).fetchall()
    else:
        rows = c.execute("SELECT * FROM models ORDER BY kind, id").fetchall()
    out = []
    for r in rows:
        d = dict(r)
        # API Key 脱敏：只保留尾 4 位
        k = d.get("api_key") or ""
        d["api_key_masked"] = ("****" + k[-4:]) if k else ""
        d.pop("api_key", None)
        out.append(d)
    return out


def get_model(model_id: int) -> dict | None:
    c = store._conn()
    r = c.execute("SELECT * FROM models WHERE id=?", (model_id,)).fetchone()
    return dict(r) if r else None


def create_model(name: str, kind: str, base_url: str, api_key: str,
                 model_name: str, created_by: str) -> dict:
    c = store._conn()
    cur = c.execute(
        """INSERT INTO models(name, kind, base_url, api_key, model_name, is_active,
                               created_by, created_at)
           VALUES(?,?,?,?,?,?,?,?)""",
        (name, kind, base_url, api_key, model_name, 0, created_by, time.time()))
    c.commit()
    return get_model(cur.lastrowid)


def update_model(model_id: int, **fields) -> dict | None:
    c = store._conn()
    allowed = {"name", "base_url", "api_key", "model_name"}
    cols, vals = [], []
    for k, v in fields.items():
        if k in allowed and v is not None:
            cols.append(f"{k}=?")
            vals.append(v)
    if cols:
        vals.append(model_id)
        c.execute(f"UPDATE models SET {', '.join(cols)} WHERE id=?", vals)
        c.commit()
    return get_model(model_id)


def delete_model(model_id: int) -> bool:
    c = store._conn()
    cur = c.execute("DELETE FROM models WHERE id=?", (model_id,))
    c.commit()
    return cur.rowcount > 0


def set_active_model(model_id: int) -> bool:
    """同 kind 内唯一生效：先清零再置位"""
    c = store._conn()
    r = c.execute("SELECT kind FROM models WHERE id=?", (model_id,)).fetchone()
    if not r:
        return False
    c.execute("UPDATE models SET is_active=0 WHERE kind=?", (r["kind"],))
    c.execute("UPDATE models SET is_active=1 WHERE id=?", (model_id,))
    c.commit()
    return True


def get_active_model(kind: str) -> dict | None:
    c = store._conn()
    r = c.execute("SELECT * FROM models WHERE kind=? AND is_active=1",
                  (kind,)).fetchone()
    return dict(r) if r else None
