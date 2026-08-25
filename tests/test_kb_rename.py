"""知识库重名校验 + 重命名回归（QA 发现：同名 KB 可重复创建、无重命名端点）"""
import pytest

from docmind import store


def test_create_kb_duplicate_name_rejected(temp_db):
    """同名知识库拒绝创建（原先各自 UUID 并存，用户/助手绑定易混淆）"""
    store.create_kb("qa_dup", "first")
    with pytest.raises(ValueError, match="已存在"):
        store.create_kb("qa_dup", "second")
    kbs = [k["name"] for k in store.list_kbs()]
    assert kbs.count("qa_dup") == 1


def test_rename_kb_basic(temp_db):
    """重命名 + 描述更新；不存在的库返回 None"""
    kb = store.create_kb("qa_old")
    updated = store.rename_kb(kb["id"], "qa_new", "新描述")
    assert updated["name"] == "qa_new"
    assert updated["description"] == "新描述"
    assert store.rename_kb("nonexistent-id", "x") is None


def test_rename_kb_duplicate_name_rejected(temp_db):
    """改名为另一个已存在 KB 的名称 → ValueError"""
    k1 = store.create_kb("qa_a")
    store.create_kb("qa_b")
    with pytest.raises(ValueError, match="已存在"):
        store.rename_kb(k1["id"], "qa_b")


def test_rename_kb_keep_description(temp_db):
    """description 传 None 表示保留原描述"""
    kb = store.create_kb("qa_keep", "原始描述")
    updated = store.rename_kb(kb["id"], "qa_keep2", None)
    assert updated["name"] == "qa_keep2"
    assert updated["description"] == "原始描述"
