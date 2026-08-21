"""联网搜索结果缓存层：减少重复查询、加速响应。

缓存策略：
- 内存 LRU 缓存，TTL 默认 30 分钟（时效性 vs 速度平衡）
- key = query 的 hash，避免长查询占内存
- 缓存命中直接返回，跳过所有搜索引擎调用
"""
import hashlib
import time
from collections import OrderedDict
from threading import Lock
from typing import Optional

from docmind import config

_cache: OrderedDict[str, tuple[float, list[dict]]] = OrderedDict()
_lock = Lock()
_MAX_ENTRIES = 500  # 最多缓存 500 条查询结果


def _cache_key(query: str) -> str:
    """查询的缓存键：SHA256 前16位"""
    return hashlib.sha256(query.encode()).hexdigest()[:16]


def get(query: str) -> Optional[list[dict]]:
    """从缓存获取搜索结果；过期/不存在返回 None"""
    key = _cache_key(query)
    with _lock:
        if key not in _cache:
            return None
        ts, results = _cache[key]
        if time.time() - ts > config.WEB_SEARCH_CACHE_TTL:
            del _cache[key]
            return None
        # LRU：访问时移到末尾
        _cache.move_to_end(key)
        return results


def put(query: str, results: list[dict]) -> None:
    """写入缓存；超出容量时淘汰最老的"""
    key = _cache_key(query)
    with _lock:
        _cache[key] = (time.time(), results)
        _cache.move_to_end(key)
        if len(_cache) > _MAX_ENTRIES:
            _cache.popitem(last=False)
