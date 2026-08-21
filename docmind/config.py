"""全局配置：从 .env 加载"""
import os
import sys

from dotenv import load_dotenv

load_dotenv()

# 项目根目录
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 百炼 API 配置
DASHSCOPE_API_KEY = os.getenv("DASHSCOPE_API_KEY", "")
DASHSCOPE_BASE_URL = os.getenv(
    "DASHSCOPE_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1"
)

# 模型配置
CHAT_MODEL = os.getenv("CHAT_MODEL", "qwen-turbo")
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "text-embedding-v3")
# 图片 OCR：百炼多模态模型（入库时抽取图中文字，结果磁盘缓存避免重复调用）
OCR_MODEL = os.getenv("OCR_MODEL", "qwen-vl-ocr")
# 最大输出 token 数：防止回复截断（qwen-turbo 默认 1500，qwen-max 支持 8000）
MAX_OUTPUT_TOKENS = int(os.getenv("MAX_OUTPUT_TOKENS", "2000"))

# Agent 配置
MAX_AGENT_STEPS = int(os.getenv("MAX_AGENT_STEPS", "8"))

# 深度思考（百炼思维链 enable_thinking）：开启后流式返回 reasoning_content，
# GUI 实时展示模型真实推理过程；模型不支持时自动降级关闭，不影响主链路
ENABLE_THINKING = os.getenv("ENABLE_THINKING", "true").strip().lower() in ("1", "true", "yes")

# 语义缓存：高频问题 embedding 相似度命中即秒回（跳过整个 Agent 链路）
SEMANTIC_CACHE = os.getenv("SEMANTIC_CACHE", "true").strip().lower() in ("1", "true", "yes")
CACHE_THRESHOLD = float(os.getenv("CACHE_THRESHOLD", "0.92"))  # 保守阈值：宁缺毋滥

# 证据拒答（RetrievalOps 核心）：开启后知识库未命中证据时确定性拒答，
# 不依赖模型自觉，防幻觉的企业问答生死线；关闭则保留旧的通识标注行为
EVIDENCE_REFUSAL = os.getenv("EVIDENCE_REFUSAL", "false").strip().lower() in ("1", "true", "yes")

# RAG 配置
KNOWLEDGE_DIR = os.path.join(PROJECT_ROOT, "docs", "knowledge")
CHUNK_SIZE = 280          # 每片最大字符数（中文 QA 类文档不宜过大，避免关键内容被稀释）
CHUNK_OVERLAP = 40        # 相邻切片重叠字符数
TOP_K = 4                 # 检索返回条数

# Rerank 过滤策略（绝对下限 + 相对头部比例，代替固定阈值）：
# 最优候选低于 MIN_TOP_SCORE 视为整体无关；其余候选需达到 max(绝对下限, 头部×比例)
RERANK_MIN_TOP_SCORE = 0.08
RERANK_ABS_FLOOR = 0.05
RERANK_RELATIVE_RATIO = 0.15

# 混合检索：Rerank 模型（百炼原生 rerank API）
RERANK_MODEL = os.getenv("RERANK_MODEL", "gte-rerank-v2")

# 联网搜索：Tavily（质量优先，需 Key，免费 1000 次/月）→ SearXNG（自托管免限量）逐级降级
TAVILY_API_KEY = os.getenv("TAVILY_API_KEY", "")
SEARXNG_URL = os.getenv("SEARXNG_URL", "")  # 如 http://localhost:8080
# 生产级搜索引擎（多引擎并发 + 结果缓存）
SERPER_API_KEY = os.getenv("SERPER_API_KEY", "")  # https://serper.dev 免费 2500次/月
BING_SEARCH_KEY = os.getenv("BING_SEARCH_KEY", "")  # Azure Bing Search API
WEB_SEARCH_TIMEOUT = int(os.getenv("WEB_SEARCH_TIMEOUT", "8"))  # 单引擎超时秒数
WEB_SEARCH_CACHE_TTL = int(os.getenv("WEB_SEARCH_CACHE_TTL", "1800"))  # 搜索结果缓存30分钟

# 调用链追踪：配置了 Langfuse 凭证则上报 Langfuse，否则降级写本地 JSONL
LANGFUSE_PUBLIC_KEY = os.getenv("LANGFUSE_PUBLIC_KEY", "")
LANGFUSE_SECRET_KEY = os.getenv("LANGFUSE_SECRET_KEY", "")
LANGFUSE_HOST = os.getenv("LANGFUSE_HOST", "http://localhost:3000")
TRACE_LOG_PATH = os.path.join(PROJECT_ROOT, "data", "trace_log.jsonl")

# 企业 LDAP 登录（两者均配置时，本地账号失败降级 LDAP，首登自动开通）
LDAP_URL = os.getenv("LDAP_URL", "")                       # 如 ldap://ldap.example.com:389
LDAP_USER_DN_TEMPLATE = os.getenv("LDAP_USER_DN_TEMPLATE", "")  # 如 uid={username},ou=people,dc=example,dc=com

# 告警阈值（告警引擎周期评估，见 docmind/alerts.py）
ALERT_INTERVAL_MIN = int(os.getenv("ALERT_INTERVAL_MIN", "10"))
ALERT_BADCASE_PENDING = int(os.getenv("ALERT_BADCASE_PENDING", "5"))
ALERT_DAILY_COST = float(os.getenv("ALERT_DAILY_COST", "10.0"))     # 元 / 24h
ALERT_ERROR_COUNT = int(os.getenv("ALERT_ERROR_COUNT", "10"))       # 次 / 1h

# MCP Server 配置：name -> 启动命令（stdio 模式）
# 用当前解释器启动子进程，保证虚拟环境里的 mcp 包可用
_PYTHON = sys.executable
MCP_SERVERS = {
    "weather": [_PYTHON, os.path.join(PROJECT_ROOT, "mcp_servers", "weather_server.py")],
}
