"""向量索引持久化缓存：避免每次启动重复调用 embedding API（慢且费 token）。

缓存键（指纹）：
    知识库每个文件的 (文件名, 大小, mtime) + 切片参数 + embedding 模型名
    任一变化 → 指纹变化 → 缓存失效重建

存储结构（data/index/，已在 .gitignore）：
    chunks.json    切片内容与来源
    vectors.npz    向量矩阵
    fingerprint    指纹文本

容错：缓存文件损坏/不完整时自动回退重建，不影响启动。
"""
import hashlib
import json
import os

import numpy as np

from docmind import config
from docmind.rag.chunker import SUPPORTED_EXTS

CACHE_DIR = os.path.join(config.PROJECT_ROOT, "data", "index")


def compute_fingerprint(knowledge_dir: str | None = None) -> str:
    """知识库文件状态 + 影响切片/向量的配置 → 指纹"""
    root = knowledge_dir or config.KNOWLEDGE_DIR
    entries = []
    if os.path.isdir(root):
        for name in sorted(os.listdir(root)):
            if os.path.splitext(name)[1].lower() not in SUPPORTED_EXTS:
                continue
            st = os.stat(os.path.join(root, name))
            entries.append(f"{name}:{st.st_size}:{int(st.st_mtime)}")
    basis = "|".join(entries) + f"##chunk={config.CHUNK_SIZE},{config.CHUNK_OVERLAP}" \
            f"##model={config.EMBEDDING_MODEL}"
    return hashlib.sha256(basis.encode("utf-8")).hexdigest()[:16]


def save_cache(fingerprint: str, chunks: list[dict], matrix: np.ndarray) -> None:
    """写缓存：先写数据文件，最后写指纹（保证指纹存在时数据必然完整）"""
    try:
        os.makedirs(CACHE_DIR, exist_ok=True)
        with open(os.path.join(CACHE_DIR, "chunks.json"), "w", encoding="utf-8") as f:
            json.dump(chunks, f, ensure_ascii=False)
        np.savez_compressed(os.path.join(CACHE_DIR, "vectors.npz"), matrix=matrix)
        with open(os.path.join(CACHE_DIR, "fingerprint"), "w", encoding="utf-8") as f:
            f.write(fingerprint)
    except Exception as e:  # noqa: BLE001 - 缓存写失败不影响主流程
        print(f"[警告] 向量索引缓存写入失败: {e}")


def load_cache(fingerprint: str) -> tuple[list[dict], np.ndarray] | None:
    """指纹匹配且文件完整时返回 (chunks, matrix)，否则 None"""
    try:
        fp_path = os.path.join(CACHE_DIR, "fingerprint")
        if not os.path.exists(fp_path):
            return None
        with open(fp_path, encoding="utf-8") as f:
            if f.read().strip() != fingerprint:
                return None
        with open(os.path.join(CACHE_DIR, "chunks.json"), encoding="utf-8") as f:
            chunks = json.load(f)
        matrix = np.load(os.path.join(CACHE_DIR, "vectors.npz"))["matrix"]
        if len(chunks) != len(matrix) or not chunks:
            return None
        return chunks, matrix
    except Exception as e:  # noqa: BLE001 - 缓存损坏回退重建
        print(f"[警告] 向量索引缓存读取失败，将重建: {e}")
        return None
