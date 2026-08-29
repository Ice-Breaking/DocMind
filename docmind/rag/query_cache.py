"""查询级热缓存：embedding 向量与 rerank 精排结果（进程内 LRU）。

与既有缓存的分层（面试可讲——按「确定性 + 失效成本」逐层设计）：
- tokenize_cache    CPU 级：切片分词结果，进程内
- embed_cache       文档侧：切片向量，SQLite 持久化（重建免重复调 API）
- semantic_cache    答案级：完整问答对，相似度匹配（非精确）
- query_cache(本模块) 查询侧：同题重复请求跳过 embedding / rerank 两次网络往返

正确性依据：embedding 与 rerank 对同一 (model, 输入) 是确定性函数。
键里带模型名——「模型管理」在线切换模型后旧条目换键自然失效，无需清库；
rerank 键含候选文本指纹——知识库内容变化 → 候选集变化 → 键变化，同样天然失效。
键一律用 SHA-256 指纹而非原文：条目不驻留用户问题明文，也省键内存。

只缓存成功结果：rerank 失败/熔断路径不写缓存，异常语义不变；
熔断期间命中缓存的热点问题仍可正常精排（缓存先于熔断检查）。
"""
import copy
import hashlib
import os
import threading
from collections import OrderedDict

from docmind import config, metrics


def _enabled(name: str) -> bool:
    return os.getenv(name, "true").strip().lower() in ("1", "true", "yes")


QUERY_EMBED_CACHE_ENABLED = _enabled("QUERY_EMBED_CACHE")
RERANK_CACHE_ENABLED = _enabled("RERANK_CACHE")


class LruCache:
    """线程安全 LRU：容量上限防内存膨胀，get 命中即晋升。"""

    def __init__(self, maxsize: int):
        self._maxsize = maxsize
        self._data: OrderedDict = OrderedDict()
        self._lock = threading.Lock()

    def get(self, key):
        with self._lock:
            if key not in self._data:
                return False, None
            self._data.move_to_end(key)
            return True, self._data[key]

    def put(self, key, value) -> None:
        with self._lock:
            self._data[key] = value
            self._data.move_to_end(key)
            if len(self._data) > self._maxsize:
                self._data.popitem(last=False)

    def clear(self) -> None:
        with self._lock:
            self._data.clear()

    def __len__(self) -> int:
        with self._lock:
            return len(self._data)


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


_query_vec_cache = LruCache(maxsize=int(os.getenv("QUERY_EMBED_CACHE_SIZE", "4096")))
_rerank_cache = LruCache(maxsize=int(os.getenv("RERANK_CACHE_SIZE", "1024")))


def embed_query_cached(embed_fn, query: str) -> list[float]:
    """单条查询向量的带缓存版本；开关关闭时直通（同代码 A/B 压测用）"""
    if not QUERY_EMBED_CACHE_ENABLED:
        return embed_fn([query])[0]
    key = (config.EMBEDDING_MODEL, _sha(query))
    hit, vec = _query_vec_cache.get(key)
    if hit:
        metrics.QUERY_EMBED_CACHE.labels(result="hit").inc()
        return vec
    metrics.QUERY_EMBED_CACHE.labels(result="miss").inc()
    vec = embed_fn([query])[0]
    _query_vec_cache.put(key, vec)
    return vec


def rerank_cached(rerank_fn, query: str, candidates, top_n: int) -> list:
    """rerank_fn(query, candidates, top_n) 的带缓存版本。

    缓存命中时返回条目的浅拷贝列表——调用方拿到的是独立对象，
    原地改属性也不会污染缓存条目（SearchHit 字段均为标量，浅拷贝即够）。"""
    if not RERANK_CACHE_ENABLED or not candidates:
        return rerank_fn(query, candidates, top_n)
    docs_fp = _sha("\x1f".join(h.text for h in candidates))
    key = (config.RERANK_MODEL, _sha(query), docs_fp, len(candidates), top_n)
    hit, rows = _rerank_cache.get(key)
    if hit:
        metrics.RERANK_CACHE.labels(result="hit").inc()
        return [copy.copy(h) for h in rows]
    metrics.RERANK_CACHE.labels(result="miss").inc()
    ranked = rerank_fn(query, candidates, top_n)
    # 存拷贝：miss 时返回给调用方的是原对象，原地修改不得渗入缓存条目
    _rerank_cache.put(key, [copy.copy(h) for h in ranked])
    return ranked
