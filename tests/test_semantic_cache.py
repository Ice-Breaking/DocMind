"""语义缓存单测：命中阈值 / 写入去重 / 统计"""
from docmind import semantic_cache


def test_save_and_lookup_hit(temp_db):
    vec = [1.0, 0.0, 0.0]
    semantic_cache.save("什么是 MCP？", "答案正文", vec)
    hit = semantic_cache.lookup([1.0, 0.0, 0.0])       # 完全相同 → 命中
    assert hit is not None
    q, a, _id = hit
    assert q == "什么是 MCP？" and a == "答案正文"


def test_lookup_similar_hit(temp_db):
    semantic_cache.save("什么是 MCP？", "答案", [1.0, 0.1, 0.0])
    hit = semantic_cache.lookup([1.0, 0.12, 0.01])     # 高相似度 → 命中
    assert hit is not None


def test_lookup_dissimilar_miss(temp_db):
    semantic_cache.save("什么是 MCP？", "答案", [1.0, 0.0, 0.0])
    hit = semantic_cache.lookup([0.0, 1.0, 0.0])       # 正交 → 未命中
    assert hit is None


def test_save_dedup_same_question(temp_db):
    semantic_cache.save("q", "旧答案", [1.0, 0, 0])
    semantic_cache.save("q", "新答案", [1.0, 0, 0])
    assert semantic_cache.stats()["entries"] == 1
    assert semantic_cache.lookup([1.0, 0, 0])[1] == "新答案"


def test_hits_counter(temp_db):
    semantic_cache.save("q", "a", [1.0, 0, 0])
    semantic_cache.lookup([1.0, 0, 0])
    semantic_cache.lookup([1.0, 0, 0])
    assert semantic_cache.stats()["total_hits"] == 2


def test_delete_entry(temp_db):
    semantic_cache.save("to_delete", "临时答案", [1.0, 0.0, 0.0])
    before = semantic_cache.stats()["entries"]
    # 找到刚写入的条目 id
    hit = semantic_cache.lookup([1.0, 0.0, 0.0])
    assert hit is not None
    entry_id = hit[2]
    # 删除
    semantic_cache.delete_entry(entry_id)
    # lookup 返回 None
    assert semantic_cache.lookup([1.0, 0.0, 0.0]) is None
    # 条目数减少 1
    assert semantic_cache.stats()["entries"] == before - 1
