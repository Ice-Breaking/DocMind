"""文档加载与切片：把 docs/knowledge 下的文本切成可检索的片段。

切片策略（面试可讲）：
- Markdown 文档优先按标题（# ~ ####）切分成语义完整的段落，再合并小段、
  拆分超长段，让每个切片尽量是一个完整的 QA 或主题
- 纯文本退回滑窗切片：CHUNK_SIZE 窗口 + CHUNK_OVERLAP 重叠，优先换行处断开
"""
import os
import re

from docmind import config

SUPPORTED_EXTS = {".md", ".txt"}
_HEADING_RE = re.compile(r"(?=^#{1,4}\s)", re.MULTILINE)  # 零宽断言：切分但保留标题文本


def load_documents(knowledge_dir: str | None = None) -> list[dict]:
    """读取知识库目录，返回 [{source, text}]"""
    root = knowledge_dir or config.KNOWLEDGE_DIR
    docs = []
    if not os.path.isdir(root):
        return docs
    for name in sorted(os.listdir(root)):
        ext = os.path.splitext(name)[1].lower()
        if ext not in SUPPORTED_EXTS:
            continue
        path = os.path.join(root, name)
        with open(path, encoding="utf-8") as f:
            docs.append({"source": name, "text": f.read().strip()})
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
