"""结构化切片单测：表格保整 / 行分组重复表头 / 标题分段 / xlsx 预分组"""
import pytest

from docmind.rag import chunker


def test_small_table_intact():
    """小表格整体进一个切片，不被腰斩"""
    doc = "说明。\n\n姓名 | 年龄 | 城市\n张三 | 30 | 北京\n李四 | 25 | 上海\n\n结尾。"
    chunks = chunker.chunk_text(doc)
    assert any("张三" in c and "李四" in c and "姓名" in c for c in chunks)


def test_big_table_grouped_with_header():
    """超大表格按行分组，每组重复表头、不超 CHUNK_SIZE"""
    from docmind import config
    table = "列A | 列B | 列C\n" + "\n".join(
        f"值{i}A | 值{i}B | 值{i}C" for i in range(80))
    groups = chunker._split_table_block(table)
    assert len(groups) > 1
    assert all(g.startswith("列A | 列B | 列C") for g in groups)
    assert all(len(g) <= config.CHUNK_SIZE for g in groups)
    total_rows = sum(len(g.split("\n")) - 1 for g in groups)
    assert total_rows == 80


def test_md_heading_split():
    """Markdown 按标题分段：足够长的段落各自成片不混淆
    （小段会被设计性合并装箱，故用超过合并阈值的段落验证）"""
    from docmind import config
    long_a = "内容甲。" * (config.CHUNK_SIZE // 3)
    long_b = "内容乙。" * (config.CHUNK_SIZE // 3)
    md = f"# 甲\n\n{long_a}\n\n# 乙\n\n{long_b}"
    chunks = chunker.chunk_text(md)
    assert any("内容甲" in c and "内容乙" not in c for c in chunks)
    assert any("内容乙" in c and "内容甲" not in c for c in chunks)


def test_md_small_sections_merged():
    """小段落合并装箱（设计行为）：两个小段进同一切片"""
    md = "# 甲\n\n内容甲。\n\n# 乙\n\n内容乙。"
    chunks = chunker.chunk_text(md)
    assert any("内容甲" in c and "内容乙" in c for c in chunks)


def test_load_chunks_md(temp_kb):
    """load_chunks 端到端：md 文件 → 带 source 的切片"""
    (temp_kb / "a.md").write_text("# 标题\n\n这是正文内容。", encoding="utf-8")
    chunks = chunker.load_chunks(str(temp_kb))
    assert len(chunks) >= 1
    assert all(c["source"] == "a.md" for c in chunks)
    assert any("这是正文内容" in c["text"] for c in chunks)


def test_xlsx_pre_grouping(temp_kb):
    """xlsx 提取预分组：每组带 [Sheet: 名] + 表头"""
    openpyxl = pytest.importorskip("openpyxl")
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "清单"
    ws.append(["名称", "数量"])
    for i in range(5):
        ws.append([f"项目{i}", i])
    wb.save(temp_kb / "t.xlsx")
    text = chunker._extract_xlsx(str(temp_kb / "t.xlsx"))
    assert "[Sheet: 清单]" in text
    assert "名称 | 数量" in text
    # 小表不分组：只有一个 Sheet 标记
    assert text.count("[Sheet: 清单]") == 1


def test_unsupported_ext_skipped(temp_kb):
    """不支持的扩展名被跳过"""
    (temp_kb / "a.md").write_text("内容", encoding="utf-8")
    (temp_kb / "b.xyz").write_text("不处理", encoding="utf-8")
    docs = chunker.load_documents(str(temp_kb))
    assert [d["source"] for d in docs] == ["a.md"]
