"""向量索引持久化缓存：避免每次启动重复调用 embedding API（慢且费 token）。

缓存判定（manifest 增量机制，替代早期"全库单指纹"方案）：
    - manifest.json      逐文件指纹清单（增量索引的判定依据）
    - global_fingerprint 全局参数指纹（切片参数/模型/schema，变化 → 全量重建）

存储目录（data/index/，已在 .gitignore）：chroma/、manifest.json、
global_fingerprint、embed_cache.db、tokenize_cache.db。
容错：缓存文件损坏/不完整时自动回退重建，不影响启动。
"""
import hashlib
import json
import logging
import os
import pathlib

from docmind import config
from docmind.rag.chunker import SUPPORTED_EXTS

logger = logging.getLogger(__name__)

CACHE_DIR = os.path.join(config.PROJECT_ROOT, "data", "index")

# chunk 结构/切片逻辑版本：变化时强制缓存失效
# v2 = 页码元数据；v3 = xlsx/图片入库；v4 = 结构化切片（表格保整/行分组重复表头）
SCHEMA_VERSION = "v4"


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
        pathlib.Path(index_dir, "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    except Exception as e:  # noqa: BLE001 - 清单写失败不影响主流程（下次全量重建兜底）
        logger.warning(f"manifest 写入失败: {e}")


def load_manifest(index_dir: str) -> dict:
    """读取逐文件指纹清单；缺失/损坏返回 {}（由调用方决定是否全量重建）"""
    path = pathlib.Path(index_dir, "manifest.json")
    try:
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:  # noqa: BLE001
        logger.warning(f"manifest 读取失败，将全量重建: {e}")
    return {}


def save_global_fingerprint(index_dir: str, fingerprint: str) -> None:
    try:
        os.makedirs(index_dir, exist_ok=True)
        pathlib.Path(index_dir, "global_fingerprint").write_text(
            fingerprint, encoding="utf-8")
    except Exception as e:  # noqa: BLE001
        logger.warning(f"全局指纹写入失败: {e}")


def load_global_fingerprint(index_dir: str) -> str:
    path = pathlib.Path(index_dir, "global_fingerprint")
    try:
        if path.exists():
            return path.read_text(encoding="utf-8").strip()
    except OSError:
        pass
    return ""
