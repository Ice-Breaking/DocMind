"""文档加载与切片：把 docs/knowledge 下的文档切成可检索的片段。

格式支持（面试可讲）：
- .md / .txt：直接读取
- .pdf：pypdf 逐页提取（纯 Python，无系统依赖）
- .docx：python-docx 提取正文段落 + 表格
- .xlsx：openpyxl 按 Sheet 提取，行数据管道符拼接（与 docx 表格策略一致）
- .png/.jpg/.jpeg/.webp：百炼多模态 OCR 抽取图中文字（结果磁盘缓存，避免重复调 API）
- 单个文件解析失败只告警跳过，不影响其余文档建库

切片策略：
- Markdown 文档优先按标题（# ~ ####）切分成语义完整的段落，再合并小段、
  拆分超长段，让每个切片尽量是一个完整的 QA 或主题
- 纯文本/PDF/Word 退回滑窗切片：CHUNK_SIZE 窗口 + CHUNK_OVERLAP 重叠，优先换行处断开
"""
import base64
import hashlib
import os
import re

from docmind import config

SUPPORTED_EXTS = {".md", ".txt", ".pdf", ".docx", ".xlsx", ".png", ".jpg", ".jpeg", ".webp"}
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp"}
OCR_CACHE_DIR = os.path.join(config.PROJECT_ROOT, "data", "ocr_cache")
_HEADING_RE = re.compile(r"(?=^#{1,4}\s)", re.MULTILINE)  # 零宽断言：切分但保留标题文本


# ---------------- 格式提取器 ----------------
def _extract_pdf_pages(path: str) -> list[tuple[int, str]]:
    """pypdf 逐页提取文本，返回 [(页号, 文本)]（页号从 1 起），供切片携带页码元数据"""
    from pypdf import PdfReader

    reader = PdfReader(path)
    pages = []
    for i, page in enumerate(reader.pages, 1):
        text = (page.extract_text() or "").strip()
        if text:
            pages.append((i, text))
    return pages


def _extract_pdf(path: str) -> str:
    """pypdf 逐页提取文本，页间用空行分隔"""
    return "\n\n".join(t for _, t in _extract_pdf_pages(path))


def _extract_docx(path: str) -> str:
    """python-docx 提取正文段落与表格（表格按行拼接）"""
    import docx

    d = docx.Document(path)
    parts = [p.text.strip() for p in d.paragraphs if p.text.strip()]
    for table in d.tables:
        for row in table.rows:
            cells = [c.text.strip() for c in row.cells if c.text.strip()]
            if cells:
                parts.append(" | ".join(cells))
    return "\n\n".join(parts)


def _extract_xlsx(path: str) -> str:
    """openpyxl 逐 Sheet 提取：行内单元格管道符拼接；大 Sheet 预分组，
    每组带 [Sheet: 名] 标记并重复表头——切片后每个分片都保有完整列语义"""
    import openpyxl

    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    parts = []
    try:
        for ws in wb.worksheets:
            rows = []
            for row in ws.iter_rows(values_only=True):
                cells = [str(c).strip() for c in row if c is not None and str(c).strip()]
                if cells:
                    rows.append(" | ".join(cells))
            if not rows:
                continue
            marker = f"[Sheet: {ws.title}]"
            header = rows[0]
            groups, cur, used = [], [marker, header], len(marker) + len(header)
            for r in rows[1:]:
                if used + len(r) + 1 > config.CHUNK_SIZE and len(cur) > 2:
                    groups.append("\n".join(cur))
                    cur, used = [marker, header], len(marker) + len(header)
                cur.append(r)
                used += len(r) + 1
            groups.append("\n".join(cur))
            parts.extend(groups)
    finally:
        wb.close()
    return "\n\n".join(parts)


def _ocr_image(path: str) -> str:
    """图片 OCR：百炼多模态抽取图中文字。按「文件名+大小+mtime」磁盘缓存，
    索引重建时不重复调 API；OCR 文本为空视为失败（抛错由建库流程告警跳过）"""
    st = os.stat(path)
    fp = hashlib.sha256(
        f"{os.path.basename(path)}:{st.st_size}:{int(st.st_mtime)}".encode()
    ).hexdigest()[:16]
    os.makedirs(OCR_CACHE_DIR, exist_ok=True)
    cache_path = os.path.join(OCR_CACHE_DIR, fp + ".txt")
    if os.path.isfile(cache_path):
        with open(cache_path, encoding="utf-8") as f:
            return f.read()

    import requests

    with open(path, "rb") as f:
        b64 = base64.b64encode(f.read()).decode()
    ext = os.path.splitext(path)[1].lstrip(".").lower()
    mime = "jpeg" if ext == "jpg" else ext
    resp = requests.post(
        config.DASHSCOPE_BASE_URL.rstrip("/") + "/chat/completions",
        headers={"Authorization": f"Bearer {config.DASHSCOPE_API_KEY}"},
        json={
            "model": config.OCR_MODEL,
            "messages": [{"role": "user", "content": [
                {"type": "image_url",
                 "image_url": {"url": f"data:image/{mime};base64,{b64}"}},
                {"type": "text", "text": "请读取图片中的全部文字并原样输出，不要添加解释。"},
            ]}],
        },
        timeout=60,
    )
    resp.raise_for_status()
    text = (resp.json()["choices"][0]["message"]["content"] or "").strip()
    if not text:
        raise ValueError("OCR 未识别到文字")
    with open(cache_path, "w", encoding="utf-8") as f:
        f.write(text)
    return text


# 二进制格式的提取器映射（懒导入，不用时不加载库）
_EXTRACTORS = {
    ".pdf": _extract_pdf,
    ".docx": _extract_docx,
    ".xlsx": _extract_xlsx,
    **{ext: _ocr_image for ext in IMAGE_EXTS},
}


def load_documents(knowledge_dir: str | None = None) -> list[dict]:
    """读取知识库目录，返回 [{source, text}]；单文件失败跳过不阻断"""
    root = knowledge_dir or config.KNOWLEDGE_DIR
    docs = []
    if not os.path.isdir(root):
        return docs
    for name in sorted(os.listdir(root)):
        ext = os.path.splitext(name)[1].lower()
        if ext not in SUPPORTED_EXTS:
            continue
        path = os.path.join(root, name)
        try:
            if ext in _EXTRACTORS:
                text = _EXTRACTORS[ext](path)
            else:
                with open(path, encoding="utf-8") as f:
                    text = f.read()
        except Exception as e:  # noqa: BLE001 - 坏文件不能阻断整个建库流程
            print(f"[警告] 文档解析失败，已跳过 {name}: {e}")
            continue
        if text.strip():
            docs.append({"source": name, "text": text.strip()})
    return docs


def _window_split(text: str) -> list[str]:
    """滑窗切片：优先换行处断开"""
    size, overlap = config.CHUNK_SIZE, config.CHUNK_OVERLAP
    chunks: list[str] = []
    start = 0
    while start < len(text):
        end = min(start + size, len(text))
        if end < len(text):
            # 在窗口内找最后一个换行，让切片尽量沿段落边界
            newline = text.rfind("\n", start + size // 2, end)
            if newline > start:
                end = newline + 1
        chunk = text[start:end].strip()
        if chunk:
            chunks.append(chunk)
        if end >= len(text):
            break
        start = max(end - overlap, start + 1)
    return chunks


def _is_table_row(line: str) -> bool:
    """表格行判定：≥2 个竖线（至少 3 列），覆盖 docx 表格/xlsx 行/Markdown 表格"""
    return line.count("|") >= 2


def _split_structural_blocks(text: str) -> list[str]:
    """把文本拆成结构块：连续表格行整体成块（不切断），其余按空行分段落"""
    blocks: list[str] = []
    para: list[str] = []
    table: list[str] = []

    def flush_para():
        if para:
            blocks.append("\n".join(para))
            para.clear()

    def flush_table():
        if table:
            blocks.append("\n".join(table))
            table.clear()

    for line in text.split("\n"):
        if _is_table_row(line):
            flush_para()
            table.append(line.strip())
        else:
            flush_table()
            if line.strip():
                para.append(line.strip())
            else:
                flush_para()
    flush_para()
    flush_table()
    return [b for b in blocks if b.strip()]


def _split_table_block(table: str) -> list[str]:
    """超大表格按行分组，每组重复首行（表头），保住列语义不丢"""
    rows = [r for r in table.split("\n") if r.strip()]
    if len(rows) <= 1:
        return [table]
    header = rows[0]
    size, chunks, group = config.CHUNK_SIZE, [], [header]
    used = len(header)
    for r in rows[1:]:
        if used + len(r) + 1 > size and len(group) > 1:
            chunks.append("\n".join(group))
            group, used = [header], len(header)
        group.append(r)
        used += len(r) + 1
    if len(group) > 1:
        chunks.append("\n".join(group))
    return chunks


def _chunk_structured(text: str) -> list[str]:
    """结构化装箱：结构块贪心合并至 CHUNK_SIZE；超大表格行分组、超大段落滑窗兜底"""
    size = config.CHUNK_SIZE
    chunks: list[str] = []
    cur = ""
    for block in _split_structural_blocks(text):
        if len(block) > size:
            if cur:
                chunks.append(cur)
                cur = ""
            if _is_table_row(block.split("\n")[0]):
                chunks.extend(_split_table_block(block))
            else:
                chunks.extend(_window_split(block))
        elif len(cur) + len(block) + 2 <= size:
            cur = f"{cur}\n\n{block}" if cur else block
        else:
            chunks.append(cur)
            cur = block
    if cur:
        chunks.append(cur)
    return [c.strip() for c in chunks if c.strip()]


def chunk_text(text: str) -> list[str]:
    """结构化切片：Markdown 先按标题分段；各段内表格整体保留（超大表格行分组
    重复表头），段落贪心装箱；无结构文本直接结构化装箱（滑窗仅作超大段落兜底）"""
    if _HEADING_RE.search(text):
        sections = [p.strip() for p in _HEADING_RE.split(text) if p.strip()]
        chunks: list[str] = []
        buffer = ""
        for sec in sections:
            if len(sec) > config.CHUNK_SIZE:
                if buffer:
                    chunks.append(buffer)
                    buffer = ""
                chunks.extend(_chunk_structured(sec))
            elif len(buffer) + len(sec) + 1 <= config.CHUNK_SIZE:
                buffer = f"{buffer}\n\n{sec}" if buffer else sec
            else:
                chunks.append(buffer)
                buffer = sec
        if buffer:
            chunks.append(buffer)
        return [c for c in chunks if c.strip()]
    return _chunk_structured(text)


def load_chunks(knowledge_dir: str | None = None) -> list[dict]:
    """加载并切片，返回 [{source, text, page?}]（text 为切片）。

    PDF 按页独立切片并携带 page 元数据（引用溯源/原文预览定位的地基）；
    其余格式不带页码。单文件失败不阻断（与 load_documents 策略一致）。
    """
    root = knowledge_dir or config.KNOWLEDGE_DIR
    result = []
    for doc in load_documents(knowledge_dir):
        ext = os.path.splitext(doc["source"])[1].lower()
        if ext == ".pdf":
            path = os.path.join(root, doc["source"])
            try:
                pages = _extract_pdf_pages(path)
            except Exception as e:  # noqa: BLE001
                print(f"[警告] PDF 按页切片失败，退回整篇切片 {doc['source']}: {e}")
                pages = [(0, doc["text"])]
            for page_no, page_text in pages:
                for piece in chunk_text(page_text):
                    item = {"source": doc["source"], "text": piece}
                    if page_no:
                        item["page"] = page_no
                    result.append(item)
        else:
            for piece in chunk_text(doc["text"]):
                result.append({"source": doc["source"], "text": piece})
    return result
