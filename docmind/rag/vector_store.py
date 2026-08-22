"""向量存储：ChromaDB 持久化向量库 + 内存 numpy 回退。

选型说明（面试可讲）：
- v1 为纯内存 numpy 暴力检索 + JSONL/npz 文件缓存；本版本迁移到
  Chroma 本地持久化（PersistentClient，HNSW 余弦索引），重启无需
  重建，检索接口 search() 保持不变，上层无感知
- chunks.json/vectors.npz 文件缓存被 Chroma 持久化取代；逐文件
  manifest + 全局指纹仍保留，作为增量索引的判定依据
- 内存镜像（self.chunks + self._matrix）保留：BM25/RRF 依赖
  store.chunks，numpy 路径兼作未走 build() 注入场景的回退
- 增量索引：逐文件 manifest 对比，按 source 元数据在 Chroma 中
  删除变化文件的切片，仅对新增/修改文件重新切片与向量化
"""
import hashlib
import logging
import os
import uuid
from dataclasses import dataclass

import chromadb
import numpy as np

from docmind import config
from docmind.llm import embed
from docmind.rag import cache as cache_mod
from docmind.rag.embed_cache import embed_cached
from docmind.rag.cache import (
    compute_file_manifest,
    compute_fingerprint,
    compute_global_fingerprint,
    load_global_fingerprint,
    load_manifest,
    save_global_fingerprint,
    save_manifest,
)
from docmind.rag.chunker import chunk_single_file, load_chunks

logger = logging.getLogger(__name__)

COLLECTION_NAME = "knowledge"


@dataclass
class SearchHit:
    text: str
    source: str
    score: float
    page: int | None = None   # PDF 切片携带的页码（引用溯源/原文预览定位用）


class VectorStore:
    """Chroma 持久化向量库。

    对外接口与 v1 内存版完全一致：chunks / version / build /
    rebuild_incremental / search(query, top_k)。
    """

    def __init__(self, chunks: list[dict] | None = None,
                 collection_name: str = COLLECTION_NAME,
                 index_dir: str | None = None):
        self._collection_name = collection_name
        self._index_dir = index_dir if index_dir else cache_mod.CACHE_DIR
        self.chunks: list[dict] = list(chunks) if chunks else []
        self._matrix: np.ndarray | None = (
            np.asarray(embed([c["text"] for c in self.chunks]), dtype=np.float32)
            if self.chunks else None)
        # 版本号：每次切片集合变化 +1；HybridRetriever 据此懒重建 BM25，
        # 增量索引后所有检索器自动感知，无需逐个手动同步
        self.version: int = 0
        self._client = None
        self._collection = None
        self._chroma_ready = False
        # 尝试衔接磁盘上已有的 Chroma 索引（重启免重建）；
        # 测试常以 chunks 直接注入且 CACHE_DIR 被 monkeypatch 到临时目录，
        # 仅当未注入 chunks 时才从磁盘同步
        if not self.chunks:
            self._load_from_chroma()

    # ---------------- Chroma 连接与同步 ----------------
    def _get_collection(self):
        """惰性连接 Chroma（PersistentClient，按 index_dir 定位，线程安全）"""
        if self._client is None:
            os.makedirs(self._index_dir, exist_ok=True)
            self._client = chromadb.PersistentClient(
                path=self._index_dir + "/chroma")
            self._collection = self._client.get_or_create_collection(
                name=self._collection_name, metadata={"hnsw:space": "cosine"})
        return self._collection

    def _clear_collection(self) -> None:
        """全量重建前清空旧索引。
        注意不用 delete_collection + recreate：chromadb 1.5.x 的 Rust 后端
        在删除重建后旧句柄会失效（NotFoundError），改为按 ID 批量清空"""
        collection = self._get_collection()
        existing = collection.get(include=[])
        if existing["ids"]:
            collection.delete(ids=existing["ids"])

    def _load_from_chroma(self) -> None:
        """从持久化索引恢复内存镜像（chunks + 向量矩阵），实现重启免重建。
        向量直接从 Chroma 读出，恢复过程零 embedding API 调用"""
        collection = self._get_collection()
        if collection.count() == 0:
            return
        result = collection.get(include=["documents", "metadatas", "embeddings"])
        chunks, vectors = [], []
        for doc, meta, vec in zip(result["documents"], result["metadatas"],
                                  result["embeddings"]):
            meta = meta or {}
            page = meta.get("page")
            try:
                page = int(page) if page is not None else None
            except (TypeError, ValueError):
                page = None
            chunks.append({"text": doc, "source": str(meta.get("source", "")),
                           "page": page})
            vectors.append(vec)
        self.chunks = chunks
        self._matrix = np.asarray(vectors, dtype=np.float32) if vectors else None
        self._chroma_ready = True
        logger.info(f"从 Chroma 持久化索引恢复 {len(self.chunks)} 个切片（未调 API）")

    @staticmethod
    def _make_ids(chunks: list[dict]) -> list[str]:
        """生成唯一 ID：(source, page, 序号) 摘要 + uuid 后缀，
        避免增量删除重加时的 ID 冲突（Chroma add 重复 ID 会报错）"""
        ids = []
        for i, c in enumerate(chunks):
            basis = f"{c.get('source', '')}#{c.get('page')}#{i}"
            digest = hashlib.sha256(basis.encode("utf-8")).hexdigest()[:12]
            ids.append(f"chunk_{digest}_{uuid.uuid4().hex[:8]}")
        return ids

    @staticmethod
    def _metadatas(chunks: list[dict]) -> list[dict]:
        """Chroma 元数据：值须为 str/int/float/bool，None 转为 -1 占位"""
        metas = []
        for c in chunks:
            page = c.get("page")
            metas.append({"source": str(c.get("source", "")),
                          "page": int(page) if isinstance(page, int) else -1})
        return metas

    def _persist_index_meta(self, knowledge_dir: str | None) -> None:
        """回写逐文件清单与全局指纹（增量索引的判定依据）"""
        save_manifest(self._index_dir, compute_file_manifest(knowledge_dir))
        save_global_fingerprint(self._index_dir, compute_global_fingerprint())

    def chunks_by_source(self, source: str) -> list[tuple[int, dict]]:
        """按来源文件取全部切片 [(全局序号, chunk)]。

        倒排索引懒构建、随 version 失效重建——文档预览等按文件取切片的
        场景从 O(全部切片) 降为 O(该文件切片数)"""
        if getattr(self, "_src_idx_version", -1) != self.version:
            idx: dict[str, list[tuple[int, dict]]] = {}
            for i, c in enumerate(self.chunks):
                idx.setdefault(c.get("source", ""), []).append((i, c))
            self._src_idx = idx
            self._src_idx_version = self.version
        return self._src_idx.get(source, [])

    def _refresh_matrix(self) -> None:
        """刷新内存向量矩阵。

        Chroma 可用时直接读回已持久化的向量（零 embedding API 调用）——
        增量重建只新增/修改了部分切片，全量重嵌会让"增量"名存实亡；
        仅在无持久化索引（chunks 直接注入等场景）时才回退实时 embed"""
        if self._chroma_ready:
            try:
                result = self._get_collection().get(include=["embeddings"])
                vecs = result.get("embeddings")
                # Chroma 可能返回 ndarray：真值判断有歧义，须显式 None 检查
                if vecs is not None and len(vecs) == len(self.chunks):
                    self._matrix = np.asarray(vecs, dtype=np.float32)
                    return
                logger.warning("Chroma 向量数与切片数不一致，回退实时嵌入")
            except Exception as e:  # noqa: BLE001 - 读取失败回退 embed
                logger.warning(f"从 Chroma 恢复向量矩阵失败，回退实时嵌入: {e}")
        self._matrix = (np.asarray(embed([c["text"] for c in self.chunks]),
                                   dtype=np.float32)
                        if self.chunks else None)

    # ---------------- 构建 ----------------
    def build(self, knowledge_dir: str | None = None, use_cache: bool = True) -> int:
        """加载知识库 → 切片 → 向量化 → 写入 Chroma。返回切片数量。

        use_cache=True 且 Chroma 已有与当前指纹一致的索引时直接复用，
        避免重复调用 embedding API（慢且费 token）。
        """
        fingerprint = compute_fingerprint(knowledge_dir)
        if use_cache and self._chroma_ready:
            # 缓存命中前置校验：Chroma 实际内容必须覆盖目录全部支持文件。
            # 只信 _chroma_ready 会在索引部分丢失（写入中断/外部损坏）时
            # 跳过重建，并把缺失状态固化进 manifest
            root_dir = knowledge_dir or config.KNOWLEDGE_DIR
            from docmind.rag.chunker import SUPPORTED_EXTS
            dir_files = {n for n in os.listdir(root_dir)
                         if os.path.splitext(n)[1].lower() in SUPPORTED_EXTS} \
                if os.path.isdir(root_dir) else set()
            indexed = {c.get("source", "") for c in self.chunks}
            if dir_files <= indexed:
                self.version += 1
                # 仅当 manifest 尚不存在时才建立（首次/升级场景）；
                # 命中路径不重写已有 manifest，防止洗白与 Chroma 的背离
                if not os.path.exists(os.path.join(self._index_dir, "manifest.json")):
                    self._persist_index_meta(knowledge_dir)
                logger.info(f"向量索引命中 Chroma 持久化缓存（{len(self.chunks)} 个切片，未调 API）")
                return len(self.chunks)
            missing = dir_files - indexed
            logger.warning(f"Chroma 索引缺失 {len(missing)} 个文件的切片"
                           f"（如 {sorted(missing)[:2]}），放弃缓存命中执行重建")

        self.chunks = load_chunks(knowledge_dir)
        if not self.chunks:
            self._matrix = None
            self._clear_collection()
            self._chroma_ready = True
            self.version += 1
            if use_cache:
                self._persist_index_meta(knowledge_dir)
            return 0

        vectors = embed_cached(embed, [c["text"] for c in self.chunks])
        collection = self._get_collection()
        self._clear_collection()
        collection.add(
            ids=self._make_ids(self.chunks),
            embeddings=[list(map(float, v)) for v in vectors],
            documents=[c["text"] for c in self.chunks],
            metadatas=self._metadatas(self.chunks),
        )
        self._matrix = np.asarray(vectors, dtype=np.float32)
        self._chroma_ready = True
        self.version += 1
        if use_cache:
            self._persist_index_meta(knowledge_dir)
            logger.info(f"向量索引已重建并写入 Chroma（{len(self.chunks)} 个切片）")
        return len(self.chunks)

    def rebuild_incremental(self, knowledge_dir: str | None = None) -> dict:
        """增量重建：对比逐文件指纹清单（manifest），保留未变文件的切片，
        只对新增/修改文件重新切片并向量化，删除文件的切片从 Chroma 移除。
        返回变更统计 {"added", "removed", "modified", "unchanged", "chunks"}。

        退回全量重建的两种情况：
        - 全局参数（切片参数/模型/schema 版本）变化 → 已有切片全部失效
        - 旧索引无 manifest（无法区分哪些文件未变）
        """
        root = knowledge_dir or config.KNOWLEDGE_DIR
        current = compute_file_manifest(root)
        cached_manifest = load_manifest(self._index_dir)
        gfp = compute_global_fingerprint()

        if gfp != load_global_fingerprint(self._index_dir) or (
                not cached_manifest and self.chunks):
            logger.info("全局参数变化或缺少增量清单，执行全量重建")
            n = self.build(root)
            return {"full_rebuild": True, "chunks": n}

        added = set(current) - set(cached_manifest)
        removed = set(cached_manifest) - set(current)
        modified = {f for f in current
                    if f in cached_manifest and current[f] != cached_manifest[f]}
        unchanged = set(current) - added - modified

        # 自愈：manifest 记录"已索引"但 Chroma 实际没有切片的文件
        # （写入中断/外部损坏后被 manifest 掩盖）并入 modified 补录
        indexed_sources = {c.get("source", "") for c in self.chunks}
        orphan = {f for f in current if f not in indexed_sources}
        if orphan:
            modified |= orphan
            unchanged -= orphan
            logger.warning(f"检测到 {len(orphan)} 个文件有 manifest 记录但索引缺失，自动补录: "
                           f"{sorted(orphan)[:3]}")

        if not added and not removed and not modified:
            self._persist_index_meta(knowledge_dir)
            return {"added": 0, "removed": 0, "modified": 0,
                    "unchanged": len(unchanged), "chunks": len(self.chunks)}

        collection = self._get_collection()

        # 删除已移除/已修改文件的旧切片（按 source 元数据过滤）
        for fname in sorted(removed | modified):
            try:
                collection.delete(where={"source": fname})
            except Exception as e:  # noqa: BLE001 - 单文件删除失败不阻断整体
                logger.warning(f"Chroma 删除 {fname} 的切片失败: {e}")

        # 只对新增/修改文件重新切片并向量化
        new_chunks: list[dict] = []
        for fname in sorted(added | modified):
            new_chunks.extend(chunk_single_file(root, fname))

        if new_chunks:
            # 批量向量化优化：一次性处理所有文本，减少网络往返
            texts = [c["text"] for c in new_chunks]
            # 切片级缓存：文本未变的切片（文件内改动只影响局部切片时）免重嵌
            embeddings = embed_cached(embed, texts)

            try:
                collection.add(
                    ids=self._make_ids(new_chunks),
                    embeddings=[list(map(float, v)) for v in embeddings],
                    documents=texts,
                    metadatas=self._metadatas(new_chunks),
                )
            except Exception:
                # 回滚切片缓存：add 失败（维度/存储层拒绝）时刚写入的
                # 向量不可信，滞留会让后续重建反复命中坏缓存且无法自愈
                from docmind.rag.embed_cache import invalidate
                invalidate(texts)
                raise

        self.chunks = ([c for c in self.chunks if c.get("source") in unchanged]
                       + new_chunks)
        self._refresh_matrix()
        self._chroma_ready = True
        self.version += 1

        # 回写持久化（最后写 manifest：保证 manifest 存在时数据必然完整）
        save_manifest(self._index_dir, current)
        save_global_fingerprint(self._index_dir, gfp)
        logger.info(f"增量索引完成：+{len(added)} ~{len(modified)} -{len(removed)} "
                    f"={len(unchanged)}，共 {len(self.chunks)} 个切片")
        return {"added": len(added), "removed": len(removed), "modified": len(modified),
                "unchanged": len(unchanged), "chunks": len(self.chunks)}

    # ---------------- 检索 ----------------
    def search(self, query: str, top_k: int | None = None,
               query_vec: list[float] | None = None) -> list[SearchHit]:
        """余弦相似度检索 top-k（走 Chroma HNSW 索引；无持久化索引时回退 numpy）

        query_vec：调用方已对同一 query 文本算过的向量，传入则免重复 embed"""
        if not self.chunks:
            return []
        k = top_k or config.TOP_K
        q = query_vec if query_vec is not None else embed([query])[0]

        if self._chroma_ready and self._collection is not None:
            return self._search_chroma(q, k)
        return self._search_numpy(q, k)

    def _search_chroma(self, q: list[float], k: int) -> list[SearchHit]:
        collection = self._collection
        n = collection.count()
        if n == 0:
            return []
        results = collection.query(
            query_embeddings=[list(map(float, q))],
            n_results=min(k, n),
            include=["documents", "metadatas", "distances"],
        )
        hits = []
        if results.get("ids") and results["ids"][0]:
            for doc, meta, dist in zip(results["documents"][0],
                                       results["metadatas"][0],
                                       results["distances"][0]):
                meta = meta or {}
                page = meta.get("page")
                hits.append(SearchHit(
                    text=doc,
                    source=str(meta.get("source", "")),
                    # Chroma 余弦距离 ∈ [0,2]（0=完全一致）→ 归一为相似度
                    score=1.0 - float(dist) / 2.0,
                    page=int(page) if page is not None and page != -1 else None,
                ))
        return hits

    def _search_numpy(self, q: list[float], k: int) -> list[SearchHit]:
        """numpy 余弦回退路径：chunks 直接注入（未经 build）等场景"""
        if self._matrix is None:
            return []
        qv = np.asarray(q, dtype=np.float32)
        mat = self._matrix / np.linalg.norm(self._matrix, axis=1, keepdims=True)
        qv = qv / np.linalg.norm(qv)
        scores = mat @ qv
        top_idx = np.argsort(scores)[::-1][:k]
        return [
            SearchHit(
                text=self.chunks[i]["text"],
                source=self.chunks[i]["source"],
                score=float(scores[i]),
                page=self.chunks[i].get("page"),
            )
            for i in top_idx
        ]
