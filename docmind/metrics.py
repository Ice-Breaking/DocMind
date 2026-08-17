"""Prometheus metrics for DocMind."""
from prometheus_client import Counter, Histogram, Gauge, Info

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
