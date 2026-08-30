"""向量存储：ChromaDB 持久化向量库 + 内存 numpy 回退。

选型说明：
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
import threading
import uuid
from dataclasses import dataclass

import chromadb
import numpy as np

from docmind import config
from docmind.llm import embed
from docmind.rag import cache as cache_mod
from docmind.rag.embed_cache import embed_cached
from docmind.rag.query_cache import embed_query_cached
from docmind.rag.cache import (
    compute_file_manifest,
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
        # 向量矩阵惰性加载：Chroma 正常路径检索不依赖 _matrix（HNSW 索引），
        # 启动时全量拉 embeddings 纯属浪费（大库数百 MB 内存 + 启动时间）；
        # 仅 numpy 回退路径首次使用时才经 _ensure_matrix 拉取
        self._matrix: np.ndarray | None = None
        # 归一化缓存：_refresh_matrix 后行向量方向不变，归一化结果
        # 可复用——numpy 回退路径每次查询省一遍 O(N·d) 归一化
        self._matrix_unit: np.ndarray | None = None
        # 发布锁：(chunks, version) 作为不可变快照原子发布——检索方经
        # snapshot() 一次性取齐，杜绝「旧索引号映射新 chunks」的撕裂读
        self._pub_lock = threading.Lock()
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

    @property
    def collection_name(self) -> str:
        return self._collection_name

    def snapshot(self) -> tuple[int, list[dict]]:
        """原子取 (version, chunks) 快照。

        检索方必须用同一快照内的 chunks 与 version 判定 BM25 新鲜度，
        并全程使用快照内的 chunks 列表——增量重建会整体替换 chunks，
        分两次读取可能拿到「新列表 + 旧版本号」的撕裂组合。"""
        with self._pub_lock:
            return self.version, self.chunks

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
        """从持久化索引恢复内存镜像（chunks），实现重启免重建。
        零 embedding API 调用；向量矩阵不在启动时拉取（惰性，
        见 _ensure_matrix）——Chroma 正常检索路径用不到它"""
        collection = self._get_collection()
        if collection.count() == 0:
            return
        result = collection.get(include=["documents", "metadatas"])
        chunks = []
        for doc, meta in zip(result["documents"], result["metadatas"]):
            meta = meta or {}
            page = meta.get("page")
            try:
                page = int(page) if page is not None else None
            except (TypeError, ValueError):
                page = None
            chunks.append({"text": doc, "source": str(meta.get("source", "")),
                           "page": page})
        with self._pub_lock:
            self.chunks = chunks
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
        仅在无持久化索引（chunks 直接注入等场景）时才回退实时 embed。
        顺带缓存行归一化结果（检索查询时直接乘）。"""
        self._matrix_unit = None
        if self._chroma_ready:
            try:
                result = self._get_collection().get(include=["embeddings"])
                vecs = result.get("embeddings")
                # Chroma 可能返回 ndarray：真值判断有歧义，须显式 None 检查
                if vecs is not None and len(vecs) == len(self.chunks):
                    self._matrix = np.asarray(vecs, dtype=np.float32)
                    self._matrix_unit = self._unit(self._matrix)
                    return
                logger.warning("Chroma 向量数与切片数不一致，回退实时嵌入")
            except Exception as e:  # noqa: BLE001 - 读取失败回退 embed
                logger.warning(f"从 Chroma 恢复向量矩阵失败，回退实时嵌入: {e}")
        self._matrix = (np.asarray(embed([c["text"] for c in self.chunks]),
                                   dtype=np.float32)
                        if self.chunks else None)
        self._matrix_unit = self._unit(self._matrix) if self._matrix is not None else None

    @staticmethod
    def _unit(mat: np.ndarray) -> np.ndarray:
        """行归一化（零向量保护）"""
        norms = np.linalg.norm(mat, axis=1, keepdims=True)
        norms[norms == 0] = 1.0
        return mat / norms

    # ---------------- 构建 ----------------
    def _hit_is_fresh(self, root_dir: str) -> bool:
        """缓存命中路径的新鲜度校验（防陈旧索引被 manifest「洗白」）。

        - 全局指纹不一致 → 切片参数/模型/schema 变化，全部切片失效 → 全量重建
        - 无 manifest（旧版程序建的索引）→ 无法验证内容新鲜度 → 全量重建，
          不再把陈旧内容固化为「已索引」
        - 全局指纹一致但逐文件清单背离 → 停机期间文档被修改 → 交增量重建
          做逐文件 diff（此时 manifest 存在且指纹一致，增量路径不会递归回 build）
        - 全部一致 → 真命中
        """
        cached_fp = load_global_fingerprint(self._index_dir)
        manifest_path = os.path.join(self._index_dir, "manifest.json")
        has_manifest = os.path.exists(manifest_path)
        if not has_manifest:
            logger.warning("Chroma 索引无 manifest（无法验证内容新鲜度），执行全量重建")
            return False
        if cached_fp != compute_global_fingerprint():
            logger.warning("Chroma 索引全局指纹背离（切片参数/模型/schema 变化），执行全量重建")
            return False
        cached_manifest = load_manifest(self._index_dir)
        current_manifest = compute_file_manifest(root_dir)
        drifted = {f for f in current_manifest
                   if cached_manifest.get(f) != current_manifest[f]}
        if drifted:
            logger.warning(f"Chroma 索引与文档清单背离 {len(drifted)} 个文件"
                           f"（如 {sorted(drifted)[:2]}），转增量重建")
            self.rebuild_incremental(root_dir)
            return False
        return True

    def build(self, knowledge_dir: str | None = None, use_cache: bool = True) -> int:
        """加载知识库 → 切片 → 向量化 → 写入 Chroma。返回切片数量。

        use_cache=True 且 Chroma 已有与当前指纹一致的索引时直接复用，
        避免重复调用 embedding API（慢且费 token）。
        """
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
            if dir_files <= indexed and self._hit_is_fresh(root_dir):
                with self._pub_lock:
                    self.version += 1
                logger.info(f"向量索引命中 Chroma 持久化缓存（{len(self.chunks)} 个切片，未调 API）")
                return len(self.chunks)
            missing = dir_files - indexed
            if missing:
                logger.warning(f"Chroma 索引缺失 {len(missing)} 个文件的切片"
                               f"（如 {sorted(missing)[:2]}），放弃缓存命中执行重建")

        # 全程使用局部变量构建，最后一次性原子发布 (chunks, version)——
        # 中途任何失败都不污染现有快照，检索方不会看到撕裂状态
        new_chunks = load_chunks(knowledge_dir)
        if not new_chunks:
            with self._pub_lock:
                self.chunks = []
                self.version += 1
            self._matrix = None
            self._clear_collection()
            self._chroma_ready = True
            if use_cache:
                self._persist_index_meta(knowledge_dir)
            return 0

        vectors = embed_cached(embed, [c["text"] for c in new_chunks])
        collection = self._get_collection()
        self._clear_collection()
        collection.add(
            ids=self._make_ids(new_chunks),
            embeddings=[list(map(float, v)) for v in vectors],
            documents=[c["text"] for c in new_chunks],
            metadatas=self._metadatas(new_chunks),
        )
        self._matrix = np.asarray(vectors, dtype=np.float32)
        self._matrix_unit = self._unit(self._matrix)
        self._chroma_ready = True
        with self._pub_lock:
            self.chunks = new_chunks
            self.version += 1
        if use_cache:
            self._persist_index_meta(knowledge_dir)
            logger.info(f"向量索引已重建并写入 Chroma（{len(new_chunks)} 个切片）")
        return len(new_chunks)

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

        # 删除已移除/已修改文件的旧切片（按 source 元数据过滤）。
        # 失败必须中止而非跳过：带着旧切片继续 add 会让新旧版本并存
        # 污染检索，且随后 manifest 回写把该状态固化、永不重试；
        # 中止则 manifest 保持旧版，下次重建自动补做删除（自愈）
        for fname in sorted(removed | modified):
            try:
                collection.delete(where={"source": fname})
            except Exception as e:
                logger.error(f"Chroma 删除 {fname} 的切片失败，中止本次增量"
                             f"（manifest 未回写，下次重建自动重试）: {e}")
                raise

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

        merged_chunks = ([c for c in self.chunks if c.get("source") in unchanged]
                         + new_chunks)
        self._refresh_matrix()
        self._chroma_ready = True
        # 原子发布：检索方经 snapshot() 取齐 (version, chunks)，
        # 杜绝「旧 BM25 索引号映射新 chunks」的撕裂读
        with self._pub_lock:
            self.chunks = merged_chunks
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
               query_vec: list[float] | None = None,
               allowed_sources: set[str] | None = None) -> list[SearchHit]:
        """余弦相似度检索 top-k（走 Chroma HNSW 索引；无持久化索引时回退 numpy）

        query_vec：调用方已对同一 query 文本算过的向量，传入则免重复 embed
        allowed_sources：ACL 白名单下推——Chroma 用 where 过滤（截断前生效，
        受限文档不挤占 top_k 名额），numpy 路在打分后截断前过滤"""
        chunks = self.chunks
        if not chunks:
            return []
        k = top_k or config.TOP_K
        # 查询向量走进程内 LRU（query_cache）：同题重复请求免一次 embedding
        # 网络往返；键含模型名，在线切换 embedding 模型后自动换键失效
        q = query_vec if query_vec is not None else embed_query_cached(embed, query)

        if self._chroma_ready and self._collection is not None:
            return self._search_chroma(q, k, allowed_sources)
        return self._search_numpy(q, k, chunks, allowed_sources)

    def _search_chroma(self, q: list[float], k: int,
                       allowed_sources: set[str] | None = None) -> list[SearchHit]:
        collection = self._collection
        n = collection.count()
        if n == 0:
            return []
        # ACL 下推：白名单为空 = 无任何可见文档，直接空结果（$in 空列表
        # 部分版本行为不定，不依赖它）
        if allowed_sources is not None and not allowed_sources:
            return []
        kwargs: dict = {}
        if allowed_sources is not None:
            kwargs["where"] = {"source": {"$in": sorted(allowed_sources)}}
        results = collection.query(
            query_embeddings=[list(map(float, q))],
            n_results=min(k, n),
            include=["documents", "metadatas", "distances"],
            **kwargs,
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

    def _ensure_matrix(self) -> np.ndarray | None:
        """惰性获取向量矩阵：仅 numpy 回退路径首次使用时构建，
        避免启动时全量拉 embeddings 的内存/时间开销"""
        if self._matrix is None and self.chunks:
            self._refresh_matrix()
        return self._matrix

    def _search_numpy(self, q: list[float], k: int,
                      chunks: list[dict] | None = None,
                      allowed_sources: set[str] | None = None) -> list[SearchHit]:
        """numpy 余弦回退路径：chunks 直接注入（未经 build）等场景。
        使用 _refresh_matrix 缓存的归一化矩阵；top-k 用 argpartition（O(N)）"""
        mat = self._ensure_matrix()
        if mat is None:
            return []
        chunks = chunks if chunks is not None else self.chunks
        # ACL 下推：先把无权来源的候选剔除再取 top-k（截断前过滤）
        if allowed_sources is not None:
            keep = [i for i, c in enumerate(chunks)
                    if c.get("source", "") in allowed_sources]
            if not keep:
                return []
            if len(keep) < len(chunks):
                mat = mat[keep]
                chunks = [chunks[i] for i in keep]
        qv = np.asarray(q, dtype=np.float32)
        qn = np.linalg.norm(qv)
        if qn == 0:
            return []
        # 归一化矩阵缓存命中（行数与过滤后矩阵一致）时免重复归一化
        unit = self._matrix_unit
        if unit is None or unit.shape[0] != mat.shape[0]:
            unit = self._unit(mat)
        scores = unit @ (qv / qn)
        # k 同时受 chunks 快照与矩阵行数约束：增量重建窗口期两者可能
        # 瞬态不一致（矩阵滞后于 chunks），越界取值会崩
        k = min(k, len(chunks), int(scores.shape[0]))
        if k <= 0:
            return []
        top_idx = np.argpartition(scores, -k)[-k:]
        top_idx = top_idx[np.argsort(scores[top_idx])[::-1]]
        return [
            SearchHit(
                text=chunks[i]["text"],
                source=chunks[i]["source"],
                score=float(scores[i]),
                page=chunks[i].get("page"),
            )
            for i in top_idx
        ]
