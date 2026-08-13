"""文档加载与切片：把 docs/knowledge 下的文本切成可检索的片段。

切片策略（面试可讲）：
- 按 CHUNK_SIZE 滑窗切片，CHUNK_OVERLAP 重叠，避免语义被拦腰截断
- 优先在换行处断开，尽量保持段落完整
"""
import os

from docmind import config

SUPPORTED_EXTS = {".md", ".txt"}


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


def chunk_text(text: str) -> list[str]:
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


def load_chunks(knowledge_dir: str | None = None) -> list[dict]:
    """加载并切片，返回 [{source, text}]（text 为切片）"""
    result = []
    for doc in load_documents(knowledge_dir):
        for piece in chunk_text(doc["text"]):
            result.append({"source": doc["source"], "text": piece})
    return result
