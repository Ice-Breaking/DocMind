"""助手与知识库注册表。

连接经包门面晚绑定获取（store._conn()），便于测试整体替换 DB。"""
import json
import time
import uuid
from docmind import config
from docmind import store

# ---------------------------------------------------------------- 多助手 / 知识库
def ensure_default_kb_and_assistant() -> None:
    """幂等播种默认知识库与默认助手"""
    c = store._conn()
    if not c.execute("SELECT 1 FROM knowledge_bases WHERE id='default'").fetchone():
        c.execute(
            "INSERT INTO knowledge_bases(id,name,description,doc_dir,created_at) VALUES(?,?,?,?,?)",
            ("default", "默认知识库", "系统内置知识库", config.KNOWLEDGE_DIR, time.time()))
    if not c.execute("SELECT 1 FROM assistants WHERE id='default'").fetchone():
        c.execute(
            "INSERT INTO assistants(id,name,avatar,system_prompt,kb_ids,model_config,owner,created_at,updated_at) "
            "VALUES(?,?,?,?,?,?,?,?,?)",
            ("default", "默认助手", "", "", '["default"]', "{}", "", time.time(), time.time()))
    c.commit()


# ---- Assistants CRUD ----
def list_assistants(owner: str = "") -> list[dict]:
    c = store._conn()
    rows = c.execute("SELECT * FROM assistants ORDER BY created_at").fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["kb_ids"] = json.loads(d.get("kb_ids") or "[]")
        out.append(d)
    return out


def get_assistant(aid: str) -> dict | None:
    c = store._conn()
    r = c.execute("SELECT * FROM assistants WHERE id=?", (aid,)).fetchone()
    if not r:
        return None
    d = dict(r)
    d["kb_ids"] = json.loads(d.get("kb_ids") or "[]")
    return d


def create_assistant(name: str, owner: str = "", avatar: str = "",
                     system_prompt: str = "", kb_ids: list | None = None,
                     model_config: dict | None = None) -> dict:
    c = store._conn()
    aid = str(uuid.uuid4())
    now = time.time()
    c.execute(
        "INSERT INTO assistants(id,name,avatar,system_prompt,kb_ids,model_config,owner,created_at,updated_at) "
        "VALUES(?,?,?,?,?,?,?,?,?)",
        (aid, name, avatar, system_prompt, json.dumps(kb_ids or ["default"]),
         json.dumps(model_config or {}), owner, now, now))
    c.commit()
    return get_assistant(aid)


def update_assistant(aid: str, **fields) -> dict | None:
    if aid == "default" and "name" in fields and not fields.get("name"):
        return None
    c = store._conn()
    allowed = {"name", "avatar", "system_prompt", "kb_ids", "model_config"}
    sets, vals = [], []
    for k, v in fields.items():
        if k not in allowed:
            continue
        if k in ("kb_ids", "model_config"):
            v = json.dumps(v)
        sets.append(f"{k}=?")
        vals.append(v)
    if not sets:
        return get_assistant(aid)
    sets.append("updated_at=?")
    vals.append(time.time())
    vals.append(aid)
    c.execute(f"UPDATE assistants SET {','.join(sets)} WHERE id=?", vals)
    c.commit()
    return get_assistant(aid)


def delete_assistant(aid: str) -> bool:
    if aid == "default":
        return False
    c = store._conn()
    cur = c.execute("DELETE FROM assistants WHERE id=?", (aid,))
    c.commit()
    return cur.rowcount > 0


# ---- Knowledge bases ----
def list_kbs() -> list[dict]:
    c = store._conn()
    return [dict(r) for r in c.execute(
        "SELECT * FROM knowledge_bases ORDER BY created_at").fetchall()]


def get_kb(kb_id: str) -> dict | None:
    c = store._conn()
    r = c.execute("SELECT * FROM knowledge_bases WHERE id=?", (kb_id,)).fetchone()
    return dict(r) if r else None


def create_kb(name: str, description: str = "") -> dict:
    c = store._conn()
    kb_id = str(uuid.uuid4())
    c.execute(
        "INSERT INTO knowledge_bases(id,name,description,doc_dir,created_at) VALUES(?,?,?,?,?)",
        (kb_id, name, description, f"data/kb_docs/{kb_id}", time.time()))
    c.commit()
    return get_kb(kb_id)


def delete_kb(kb_id: str) -> bool:
    if kb_id == "default":
        return False
    c = store._conn()
    cur = c.execute("DELETE FROM knowledge_bases WHERE id=?", (kb_id,))
    c.commit()
    return cur.rowcount > 0


def kb_used_by_assistants(kb_id: str) -> bool:
    """检查是否有助手绑定了该知识库"""
    c = store._conn()
    for r in c.execute("SELECT kb_ids FROM assistants").fetchall():
        try:
            if kb_id in json.loads(r["kb_ids"] or "[]"):
                return True
        except Exception:
            continue
    return False
