"""向量索引持久化缓存：避免每次启动重复调用 embedding API（慢且费 token）。

缓存键（指纹）：
    知识库每个文件的 (文件名, 大小, mtime) + 切片参数 + embedding 模型名
    任一变化 → 指纹变化 → 缓存失效重建

存储结构（data/index/，已在 .gitignore）：
    chunks.json        切片内容与来源
    vectors.npz        向量矩阵
    fingerprint        指纹文本（全库状态 + 参数）
    manifest.json      逐文件指纹清单（增量索引的判定依据）
    global_fingerprint 全局参数指纹（切片参数/模型/schema，变化 → 全量重建）

容错：缓存文件损坏/不完整时自动回退重建，不影响启动。
"""
import hashlib
import json
import logging
import os

import numpy as np

from docmind import config
from docmind.rag.chunker import SUPPORTED_EXTS

logger = logging.getLogger(__name__)

CACHE_DIR = os.path.join(config.PROJECT_ROOT, "data", "index")

# chunk 结构/切片逻辑版本：变化时强制缓存失效
# v2 = 页码元数据；v3 = xlsx/图片入库；v4 = 结构化切片（表格保整/行分组重复表头）
SCHEMA_VERSION = "v4"


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
            f"##model={config.EMBEDDING_MODEL}##schema={SCHEMA_VERSION}"
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
        logger.warning(f"向量索引缓存写入失败: {e}")


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
        logger.warning(f"向量索引缓存读取失败，将重建: {e}")
        return None


# ---------------- 增量索引：逐文件指纹清单（manifest） ----------------
def compute_file_manifest(knowledge_dir: str | None = None) -> dict[str, str]:
    """逐文件计算指纹，返回 {文件名: sha256(name:size:mtime)[:16]}

    增量重建的判定基础：与缓存 manifest 逐文件对比，只对变化文件
    重新切片与向量化（单文件粒度，替代全局指纹的全量失效策略）。
    """
    root = knowledge_dir or config.KNOWLEDGE_DIR
    manifest = {}
    if os.path.isdir(root):
        for name in sorted(os.listdir(root)):
            if os.path.splitext(name)[1].lower() not in SUPPORTED_EXTS:
                continue
            st = os.stat(os.path.join(root, name))
            raw = f"{name}:{st.st_size}:{st.st_mtime:.0f}"
            manifest[name] = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]
    return manifest


def compute_global_fingerprint() -> str:
    """影响全部切片的全局参数指纹：切片参数 / embedding 模型 / schema 版本。
    任一变化 → 已有切片全部失效，增量复用不再成立，需全量重建"""
    basis = (f"chunk={config.CHUNK_SIZE},{config.CHUNK_OVERLAP}"
             f"##model={config.EMBEDDING_MODEL}##schema={SCHEMA_VERSION}")
    return hashlib.sha256(basis.encode("utf-8")).hexdigest()[:16]


def save_manifest(index_dir: str, manifest: dict) -> None:
    try:
        os.makedirs(index_dir, exist_ok=True)
        with open(os.path.join(index_dir, "manifest.json"), "w", encoding="utf-8") as f:
            json.dump(manifest, f, ensure_ascii=False)
    except Exception as e:  # noqa: BLE001 - 清单写失败不影响主流程（下次全量重建兜底）
        logger.warning(f"manifest 写入失败: {e}")


def load_manifest(index_dir: str) -> dict:
    """读取逐文件指纹清单；缺失/损坏返回 {}（由调用方决定是否全量重建）"""
    path = os.path.join(index_dir, "manifest.json")
    try:
        if os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                return json.load(f)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"manifest 读取失败，将全量重建: {e}")
    return {}


def save_global_fingerprint(index_dir: str, fingerprint: str) -> None:
    try:
        os.makedirs(index_dir, exist_ok=True)
        with open(os.path.join(index_dir, "global_fingerprint"), "w", encoding="utf-8") as f:
            f.write(fingerprint)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"全局指纹写入失败: {e}")


def load_global_fingerprint(index_dir: str) -> str:
    path = os.path.join(index_dir, "global_fingerprint")
    try:
        if os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                return f.read().strip()
    except OSError:
        pass
    return ""
