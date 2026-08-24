"""对话图片附件：存储/属主元数据/安全校验回归。

历史 bug 一（裂图）：_meta_conn() 未设 row_factory=sqlite3.Row，_serve_upload 里
row["owner"] 按列名取值 → TypeError → 所有带属主记录的附件 500。
历史 bug 二（无校验）：save_chat_image 不限大小、不嗅探内容，伪装成图片的
任意文件可落盘并经 /files/uploads 直链分发。
"""
import base64
import pytest
from fastapi import HTTPException

from docmind import docs_api


def _b64(raw: bytes) -> str:
    return base64.b64encode(raw).decode()


# 最小可嗅探图片头（完整合法图片无需构造——校验只看 magic bytes）
PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 16
JPG = b"\xff\xd8\xff\xe0" + b"0" * 16
WEBP = b"RIFF\x24\x00\x00\x00WEBP" + b"0" * 8


@pytest.fixture
def uploads_dir(tmp_path, monkeypatch):
    """附件目录/元数据库指向临时路径，不污染真实 data/uploads；
    _meta_conn 按路径感知缓存——切路径自动换连接，用例间天然隔离"""
    up = tmp_path / "uploads"
    monkeypatch.setattr(docs_api, "_UPLOADS_DIR", str(up))
    monkeypatch.setattr(docs_api, "_META_DB", str(up / "meta.db"))
    return up


def test_save_chat_image_writes_file_and_owner(uploads_dir):
    fname, norm = docs_api.save_chat_image(
        f"data:image/png;base64,{_b64(PNG)}", owner="ggh")
    assert fname.endswith(".png")
    assert norm.startswith("data:image/png;base64,")
    assert (uploads_dir / fname).read_bytes() == PNG


def test_meta_row_supports_name_access(uploads_dir):
    """回归：_serve_upload 的 row["owner"] 必须可用（修复前 TypeError）"""
    fname, _ = docs_api.save_chat_image(
        f"data:image/png;base64,{_b64(PNG)}", owner="ggh")
    row = docs_api._meta_conn().execute(
        "SELECT owner FROM attachments WHERE fname = ?", (fname,)).fetchone()
    assert row is not None
    assert row["owner"] == "ggh"


def test_meta_conn_reuses_connection_per_thread(uploads_dir):
    """连接缓存：同线程同路径复用同一连接；目录不存在时自动建（原实现炸）"""
    assert docs_api._meta_conn() is docs_api._meta_conn()


def test_owner_isolation_query_paths(uploads_dir):
    """无属主记录的存量文件 → row 为 None → 回退登录可见（不抛异常）"""
    row = docs_api._meta_conn().execute(
        "SELECT owner FROM attachments WHERE fname = ?",
        ("not_exists.jpg",)).fetchone()
    assert row is None


def test_reject_non_image_payload(uploads_dir):
    """伪装图片的文本/二进制内容必须被拒（原先直接落盘）"""
    with pytest.raises(HTTPException) as ei:
        docs_api.save_chat_image("data:image/png;base64,aGk=", owner="ggh")
    assert ei.value.status_code == 400
    # 且没有半截文件残留
    assert list(uploads_dir.glob("*")) in ([], [uploads_dir / "meta.db"])


def test_reject_mime_mismatch(uploads_dir):
    """声明 png 实际 jpeg → 400 疑似伪装"""
    with pytest.raises(HTTPException) as ei:
        docs_api.save_chat_image(
            f"data:image/png;base64,{_b64(JPG)}", owner="ggh")
    assert ei.value.status_code == 400


@pytest.mark.parametrize("raw,ext", [(PNG, ".png"), (JPG, ".jpg"), (WEBP, ".webp")])
def test_sniffed_type_drives_extension(uploads_dir, raw, ext):
    """裸 base64（无 data: 前缀）：按内容嗅探定类型与扩展名"""
    fname, url = docs_api.save_chat_image(_b64(raw), owner="ggh")
    assert fname.endswith(ext)
    assert url.startswith("data:image/")


def test_reject_oversized(uploads_dir, monkeypatch):
    monkeypatch.setattr(docs_api, "_CHAT_IMAGE_MAX_BYTES", 16)
    with pytest.raises(HTTPException) as ei:
        docs_api.save_chat_image(
            f"data:image/png;base64,{_b64(PNG)}", owner="ggh")   # 24B > 16B
    assert ei.value.status_code == 413
