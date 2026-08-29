"""Prometheus metrics for DocMind."""
import re as _re

from prometheus_client import Counter, Histogram, Gauge

# 动态资源段归一化：防止 Prometheus path 标签基数随业务 id 无限增长
# （每个新会话/文件名都会派生新时间序列 → 内存持续增长、监控查询劣化）
_PATH_SEG_ID = _re.compile(r"^(?:sess-.+|[0-9a-fA-F-]{16,}|\d{13,})$")
_PATH_SEG_FILE = _re.compile(r"^\d{10,}[\w.-]*\.\w{2,5}$")   # 时间戳_哈希.ext 类上传文件名


def normalize_http_path(path: str) -> str:
    """URL path 标签归一化：动态段折叠为 {id}。

    /api/sessions/sess-mt6m52x5-qa1fje/messages → /api/sessions/{id}/messages
    /api/feedback/sess-xxx                      → /api/feedback/{id}
    /files/uploads/1787538290201_9e3ce6.jpg     → /files/uploads/{id}
    静态段(default/docs/login 等有限集合)保持原样。"""
    return "/".join(
        "{id}" if (_PATH_SEG_ID.match(seg) or _PATH_SEG_FILE.match(seg)) else seg
        for seg in (path or "/").split("/"))

# HTTP metrics
HTTP_REQUESTS = Counter(
    'docmind_http_requests_total',
    'Total HTTP requests',
    ['method', 'path', 'status']
)
HTTP_LATENCY = Histogram(
    'docmind_http_request_duration_seconds',
    'HTTP request latency',
    ['method', 'path'],
    buckets=[0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0, 60.0]
)

# SSE metrics
SSE_ACTIVE_STREAMS = Gauge(
    'docmind_sse_active_streams',
    'Number of active SSE streams'
)

# LLM metrics
LLM_CALLS = Counter(
    'docmind_llm_calls_total',
    'Total LLM API calls',
    ['model', 'status']
)
LLM_LATENCY = Histogram(
    'docmind_llm_latency_seconds',
    'LLM API call latency',
    ['model'],
    buckets=[0.5, 1.0, 2.0, 5.0, 10.0, 20.0, 30.0, 60.0]
)
LLM_TOKENS = Counter(
    'docmind_llm_tokens_total',
    'Total LLM tokens consumed',
    ['direction']  # 'input' or 'output'
)

# Tool metrics
TOOL_CALLS = Counter(
    'docmind_tool_calls_total',
    'Total tool calls',
    ['tool', 'status']
)

# Cache metrics
CACHE_HITS = Counter(
    'docmind_semantic_cache_hits_total',
    'Semantic cache hits'
)
CACHE_MISSES = Counter(
    'docmind_semantic_cache_misses_total',
    'Semantic cache misses'
)

# Knowledge base metrics
KNOWLEDGE_CHUNKS = Gauge(
    'docmind_knowledge_chunks',
    'Number of knowledge base chunks'
)

# Error metrics
ERRORS = Counter(
    'docmind_errors_total',
    'Total errors',
    ['stage']  # 'llm', 'tool', 'stream', 'auth'
)

# 大小模型路由指标（docmind/model_router.py）：backend=local/cloud，
# reason=multimodal/tools/thinking/explicit/trivial/default/off/fallback
LLM_ROUTES = Counter(
    'docmind_llm_route_total',
    'LLM routing decisions',
    ['backend', 'reason']
)

# 查询级热缓存指标（docmind/rag/query_cache.py）：result=hit/miss
QUERY_EMBED_CACHE = Counter(
    'docmind_query_embed_cache_total',
    'Query embedding cache lookups',
    ['result']
)
RERANK_CACHE = Counter(
    'docmind_rerank_cache_total',
    'Rerank result cache lookups',
    ['result']
)
