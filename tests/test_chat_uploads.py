"""对话图片附件：存储与属主元数据回归（2026-08-24 裂图修复）。

历史 bug：_meta_conn() 未设 row_factory=sqlite3.Row，_serve_upload 里
row["owner"] 按列名取值 → TypeError → 所有带属主记录的附件 500，
前端聊天里的图片全部裂图（模型侧直接读磁盘文件故识图不受影响）。
"""
import pytest

from docmind import docs_api


@pytest.fixture
def uploads_dir(tmp_path, monkeypatch):
    """附件目录/元数据库指向临时路径，不污染真实 data/uploads"""
    up = tmp_path / "uploads"
    monkeypatch.setattr(docs_api, "_UPLOADS_DIR", str(up))
    monkeypatch.setattr(docs_api, "_META_DB", str(up / "meta.db"))
    return up


def test_save_chat_image_writes_file_and_owner(uploads_dir):
    fname, norm = docs_api.save_chat_image(
        "data:image/png;base64,aGk=", owner="ggh")          # aGk= → b"hi"
    assert fname.endswith(".png")
    assert norm.startswith("data:image/png;base64,")
    assert (uploads_dir / fname).read_bytes() == b"hi"


def test_meta_row_supports_name_access(uploads_dir):
    """回归：_serve_upload 的 row["owner"] 必须可用（修复前 TypeError）"""
    fname, _ = docs_api.save_chat_image(
        "data:image/png;base64,aGk=", owner="ggh")
    row = docs_api._meta_conn().execute(
        "SELECT owner FROM attachments WHERE fname = ?", (fname,)).fetchone()
    assert row is not None
    assert row["owner"] == "ggh"


def test_owner_isolation_query_paths(uploads_dir):
    """无属主记录的存量文件 → row 为 None → 回退登录可见（不抛异常）"""
    uploads_dir.mkdir(parents=True, exist_ok=True)   # sqlite 需目录先存在
    row = docs_api._meta_conn().execute(
        "SELECT owner FROM attachments WHERE fname = ?",
        ("not_exists.jpg",)).fetchone()
    assert row is None
