"""文档加载与切片：把 docs/knowledge 下的文本/PDF/Word 切成可检索的片段。

格式支持（面试可讲）：
- .md / .txt：直接读取
- .pdf：pypdf 逐页提取（纯 Python，无系统依赖）
- .docx：python-docx 提取正文段落 + 表格
- 单个文件解析失败只告警跳过，不影响其余文档建库

切片策略：
- Markdown 文档优先按标题（# ~ ####）切分成语义完整的段落，再合并小段、
  拆分超长段，让每个切片尽量是一个完整的 QA 或主题
- 纯文本/PDF/Word 退回滑窗切片：CHUNK_SIZE 窗口 + CHUNK_OVERLAP 重叠，优先换行处断开
"""
import os
import re

from docmind import config

SUPPORTED_EXTS = {".md", ".txt", ".pdf", ".docx"}
_HEADING_RE = re.compile(r"(?=^#{1,4}\s)", re.MULTILINE)  # 零宽断言：切分但保留标题文本


# ---------------- 格式提取器 ----------------
def _extract_pdf(path: str) -> str:
    """pypdf 逐页提取文本，页间用空行分隔"""
    from pypdf import PdfReader

    reader = PdfReader(path)
    pages = []
    for page in reader.pages:
        text = page.extract_text() or ""
        if text.strip():
            pages.append(text.strip())
    return "\n\n".join(pages)


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


# 二进制格式的提取器映射（懒导入，不用时不加载库）
_EXTRACTORS = {
    ".pdf": _extract_pdf,
    ".docx": _extract_docx,
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


def chunk_text(text: str) -> list[str]:
    """语义切片：先按标题分段落，小段合并、超长段再滑窗拆分"""
    if _HEADING_RE.search(text):
        sections = [p.strip() for p in _HEADING_RE.split(text) if p.strip()]
        chunks: list[str] = []
        buffer = ""
        for sec in sections:
            if len(sec) > config.CHUNK_SIZE:
                if buffer:
                    chunks.append(buffer)
                    buffer = ""
                chunks.extend(_window_split(sec))
            elif len(buffer) + len(sec) + 1 <= config.CHUNK_SIZE:
                buffer = f"{buffer}\n\n{sec}" if buffer else sec
            else:
                chunks.append(buffer)
                buffer = sec
        if buffer:
            chunks.append(buffer)
        return [c for c in chunks if c.strip()]
    return _window_split(text)


def load_chunks(knowledge_dir: str | None = None) -> list[dict]:
    """加载并切片，返回 [{source, text}]（text 为切片）"""
    result = []
    for doc in load_documents(knowledge_dir):
        for piece in chunk_text(doc["text"]):
            result.append({"source": doc["source"], "text": piece})
    return result
