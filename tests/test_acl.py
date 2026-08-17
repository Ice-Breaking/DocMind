"""文档级 ACL 单测：授权判定 / 来源抽取 / 答案权限校验"""
from docmind import acl


def test_default_public(temp_db, temp_kb):
    """文档默认公开"""
    (temp_kb / "a.md").write_text("x", encoding="utf-8")
    (temp_kb / "b.md").write_text("y", encoding="utf-8")
    assert acl.allowed_docs("") == {"a.md", "b.md"}


def test_restrict_and_grant(temp_db, temp_kb):
    """受限后仅授权用户可见"""
    (temp_kb / "a.md").write_text("x", encoding="utf-8")
    (temp_kb / "secret.md").write_text("y", encoding="utf-8")
    acl.set_restricted("secret.md", True)
    assert acl.allowed_docs("") == {"a.md"}
    assert acl.allowed_docs("alice") == {"a.md"}
    acl.grant("alice", "secret.md")
    assert acl.allowed_docs("alice") == {"a.md", "secret.md"}
    assert acl.allowed_docs("bob") == {"a.md"}
    acl.revoke("alice", "secret.md")
    assert acl.allowed_docs("alice") == {"a.md"}


def test_unrestrict(temp_db, temp_kb):
    (temp_kb / "s.md").write_text("x", encoding="utf-8")
    acl.set_restricted("s.md", True)
    assert "s.md" not in acl.allowed_docs("")
    acl.set_restricted("s.md", False)
    assert "s.md" in acl.allowed_docs("")


def test_extract_sources():
    """[来源: 文件] / [来源: 文件 · 第N页] 两种格式都能抽取"""
    text = ("见 [来源: 产品手册.md] 与 [来源: 指南.pdf · 第2页]，"
            "还有 [来源: 统计.xlsx] 和 [📄 来源: 图.png]")
    srcs = acl.extract_sources(text)
    assert "产品手册.md" in srcs
    assert "指南.pdf" in srcs
    assert "统计.xlsx" in srcs


def test_answer_allowed(temp_db, temp_kb):
    """答案引用受限文档时按用户权限校验"""
    (temp_kb / "pub.md").write_text("x", encoding="utf-8")
    (temp_kb / "sec.md").write_text("y", encoding="utf-8")
    acl.set_restricted("sec.md", True)
    assert acl.answer_allowed("见 [来源: pub.md]", "")
    assert not acl.answer_allowed("见 [来源: sec.md]", "")
    acl.grant("admin", "sec.md")
    assert acl.answer_allowed("见 [来源: sec.md]", "admin")
    assert acl.answer_allowed("无引用的答案", "")   # 无引用视为允许
