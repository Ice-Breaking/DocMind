"""会话令牌（auth_tokens）数据层：web_auth 内存 L1 未命中时的 SQLite 回源。

SQL 全部集中在 store 层，web_auth 只调用这里的函数（请求侧污点输入
不直接触达 SQL 执行器）。连接经包门面晚绑定获取（store._conn()）。"""
from docmind import store


def lookup_auth_token(token_hash: str) -> dict | None:
    """按令牌哈希查会话；不存在返回 None"""
    row = store._conn().execute(
        "SELECT username, expires_at FROM auth_tokens WHERE token_hash = ?",
        (token_hash,)).fetchone()
    return dict(row) if row else None


def delete_auth_token(token_hash: str) -> None:
    """删除单个令牌（过期清理）"""
    c = store._conn()
    c.execute("DELETE FROM auth_tokens WHERE token_hash = ?", (token_hash,))
    c.commit()
