"""API Key 签发/校验/吊销（sha256 哈希落库）。

连接经包门面晚绑定获取（store._conn()），便于测试整体替换 DB。"""
import json
import time
import secrets
import hashlib
from docmind import store

# ================= API Key =================

def _hash_key(key: str) -> str:
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


def create_api_key(name: str, scope_kb_ids: list, created_by: str,
                   expires_at: float | None = None) -> dict:
    """创建 API Key：明文仅本次返回，库里只存前缀 + SHA256 哈希"""
    plain = "dm_" + secrets.token_urlsafe(24)
    c = store._conn()
    cur = c.execute(
        """INSERT INTO api_keys(name, prefix, key_hash, scope_kb_ids, created_by,
                                 created_at, expires_at)
           VALUES(?,?,?,?,?,?,?)""",
        (name, plain[:11], _hash_key(plain),
         json.dumps(scope_kb_ids or [], ensure_ascii=False),
         created_by, time.time(), expires_at))
    c.commit()
    row = dict(c.execute("SELECT * FROM api_keys WHERE id=?",
                         (cur.lastrowid,)).fetchone())
    row["scope_kb_ids"] = json.loads(row["scope_kb_ids"] or "[]")
    row["key"] = plain          # 仅此一次出现在响应里
    row.pop("key_hash", None)   # 哈希永不出库
    return row


def list_api_keys() -> list[dict]:
    c = store._conn()
    rows = c.execute("SELECT * FROM api_keys ORDER BY id DESC").fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["scope_kb_ids"] = json.loads(d["scope_kb_ids"] or "[]")
        d.pop("key_hash", None)
        now = time.time()
        d["active"] = (d["revoked_at"] is None
                       and (d["expires_at"] is None or d["expires_at"] > now))
        out.append(d)
    return out


def revoke_api_key(key_id: int) -> bool:
    c = store._conn()
    cur = c.execute("UPDATE api_keys SET revoked_at=? WHERE id=? AND revoked_at IS NULL",
                    (time.time(), key_id))
    c.commit()
    return cur.rowcount > 0


def validate_api_key(plain: str) -> dict | None:
    """开放接口鉴权：哈希匹配 + 未吊销 + 未过期；返回含 scope 的行"""
    c = store._conn()
    r = c.execute("SELECT * FROM api_keys WHERE key_hash=?",
                  (_hash_key(plain),)).fetchone()
    if not r:
        return None
    d = dict(r)
    now = time.time()
    if d["revoked_at"] is not None:
        return None
    if d["expires_at"] is not None and d["expires_at"] <= now:
        return None
    d["scope_kb_ids"] = json.loads(d["scope_kb_ids"] or "[]")
    return d


def touch_api_key(key_id: int) -> None:
    c = store._conn()
    c.execute("UPDATE api_keys SET last_used_at=? WHERE id=?", (time.time(), key_id))
    c.commit()
