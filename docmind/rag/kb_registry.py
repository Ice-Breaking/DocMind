"""多知识库懒加载注册表。

默认库复用 core.build_shared() 构建的单例（零额外开销）；
非默认库在首次访问时构建（从磁盘 Chroma 恢复，零 embedding API
调用）并缓存；LRU 驱逐限制内存占用。
"""
import logging
import os
import threading
import time

from docmind import config

logger = logging.getLogger(__name__)

# 同时缓存的非默认知识库上限（默认库为 core 单例，不占名额）
MAX_LOADED_KBS = int(os.environ.get("DOCMIND_MAX_LOADED_KBS", "8"))


class _Entry:
    __slots__ = ("store", "retriever", "last_used", "active")

    def __init__(self, store, retriever):
        self.store = store
        self.retriever = retriever
        self.last_used = time.time()
        self.active = 0  # 进行中的检索引用计数（驱逐时保护在用条目）


class KBRegistry:
    def __init__(self):
        self._entries = {}        # kb_id -> _Entry
        self._lock = threading.Lock()
        self._build_locks = {}    # kb_id -> threading.Lock（每库构建锁）

    def get(self, kb_id):
        """返回 kb_id 对应的 (store, retriever)，非默认库懒加载构建。
        kb_id 为空或 'default' 时返回 core 单例（可能尚未初始化为 None）。"""
        kb_id = kb_id or "default"
        if kb_id == "default":
            from docmind import core
            return (core._shared_state.get("store"),
                    core._shared_state.get("retriever"))

        with self._lock:
            entry = self._entries.get(kb_id)
            if entry:
                entry.last_used = time.time()
                return entry.store, entry.retriever
            # 获取/创建每库构建锁（全局锁内只做字典操作，保持短临界区）
            bl = self._build_locks.setdefault(kb_id, threading.Lock())

        # 构建昂贵，放到全局锁外；每库锁防止并发重复构建
        with bl:
            with self._lock:  # 双检：拿到构建锁后可能已被其他线程构建
                entry = self._entries.get(kb_id)
                if entry:
                    entry.last_used = time.time()
                    return entry.store, entry.retriever

            store, retriever = self._build(kb_id)
            entry = _Entry(store, retriever)
            with self._lock:
                self._maybe_evict_locked()
                self._entries[kb_id] = entry
            return store, retriever

    def _build(self, kb_id):
        """构建非默认库：优先从磁盘 Chroma 恢复（零 API 调用）；
        索引为空且存在文档目录时才执行全量 build（含 embedding 调用）。"""
        from docmind.rag.hybrid import HybridRetriever
        from docmind.rag.vector_store import VectorStore

        index_dir = os.path.join(config.PROJECT_ROOT, "data", "index",
                                 "kbs", kb_id)
        store = VectorStore(collection_name=f"kb_{kb_id}", index_dir=index_dir)
        if not store.chunks:
            doc_dir = os.path.join(config.PROJECT_ROOT, "data", "kb_docs",
                                   kb_id)
            if os.path.isdir(doc_dir):
                store.build(doc_dir)
        retriever = HybridRetriever(store)
        retriever.build()
        logger.info(f"懒加载知识库 {kb_id}: {len(store.chunks)} 切片")
        return store, retriever

    def _maybe_evict_locked(self):
        """超出 MAX_LOADED_KBS 时驱逐最久未用的空闲条目。
        必须持有 self._lock 调用；进行中的检索（active > 0）不驱逐。"""
        while len(self._entries) >= MAX_LOADED_KBS:
            idle = [(k, e) for k, e in self._entries.items() if e.active == 0]
            if not idle:
                break
            victim = min(idle, key=lambda kv: kv[1].last_used)[0]
            del self._entries[victim]
            logger.info(f"LRU 驱逐知识库缓存: {victim}")

    def invalidate(self, kb_id):
        """丢弃缓存条目（重建/删除知识库后调用），下次 get() 重新构建。"""
        if not kb_id or kb_id == "default":
            return
        with self._lock:
            self._entries.pop(kb_id, None)

    def search_multi(self, kb_ids, query, top_k, allowed_sources=None,
                     query_vec=None):
        """跨多库检索：每库双路召回（不精排）→ 合并去重 → 统一精排一次。
        返回 list[SearchHit]，与单库 retriever.search 的结果类型一致。
        query_vec：已对同一 query 算过的向量，各库复用（否则每库重复 embed 一次）"""
        from docmind.rag.hybrid import filter_reranked

        kb_ids = [k for k in (kb_ids or []) if k]
        if not kb_ids:
            kb_ids = ["default"]

        candidates = []
        rerank_retriever = None  # 记录任一可用检索器，用于最后的统一精排
        for kb_id in kb_ids:
            store, retriever = self.get(kb_id)
            entry = self._hold(kb_id)  # 检索期间 active+1，防止被 LRU 驱逐
            try:
                if store is None or retriever is None:
                    continue
                # rerank=False：各库只出候选（RRF 分），精排合并后只做一次
                hits = retriever.search(
                    query, top_k=top_k * 2, rerank=False,
                    candidate_k=max(top_k * 2, 10),
                    allowed_sources=allowed_sources, query_vec=query_vec)
                candidates.extend(hits)
                rerank_retriever = retriever
            except Exception as e:  # noqa: BLE001 - 单库失败不阻断整体
                logger.warning(f"KB {kb_id} 检索失败: {e}")
            finally:
                if entry is not None:
                    entry.active -= 1

        if not candidates:
            return []

        # 按 (source, text) 去重，保留分数更高者
        seen = {}
        for h in candidates:
            key = (getattr(h, "source", ""), getattr(h, "text", ""))
            if key not in seen or h.score > seen[key].score:
                seen[key] = h
        merged = sorted(seen.values(), key=lambda h: h.score, reverse=True)
        merged = merged[: top_k * 2]
        return self._rerank_merged(rerank_retriever, merged, query, top_k,
                                   filter_reranked)

    def _hold(self, kb_id):
        """为进行中的检索给条目 active +1（防驱逐），返回 _Entry；
        默认库无缓存条目，返回 None（core 单例由应用生命周期持有）。"""
        if not kb_id or kb_id == "default":
            return None
        with self._lock:
            entry = self._entries.get(kb_id)
            if entry is not None:
                entry.active += 1
                entry.last_used = time.time()
            return entry

    def _rerank_merged(self, retriever, hits, query, top_k, filter_reranked):
        """对合并后的候选做统一精排。复用 HybridRetriever._rerank
        （不依赖实例状态）；精排失败降级为 RRF 分数排序截断。
        精排结果与单库一致地套用 filter_reranked 绝对/相对阈值过滤。"""
        if retriever is not None:
            try:
                ranked = retriever._rerank(query, hits, top_n=top_k)
                return filter_reranked(ranked)
            except Exception as e:  # noqa: BLE001 - 与单库路径相同的降级策略
                logger.warning(f"跨库统一精排失败，降级为 RRF 结果: {e}")
        return hits[:top_k]


_registry = KBRegistry()


def get_registry() -> KBRegistry:
    """进程级注册表单例"""
    return _registry
