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

# Agent 配置
MAX_AGENT_STEPS = int(os.getenv("MAX_AGENT_STEPS", "8"))

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

# 联网搜索：配置博查 API Key 后优先用博查（国内稳定、新鲜度高，有免费额度）；
# 未配置则回退 DuckDuckGo（免 Key，国内网络下时好时坏）
BOCHA_API_KEY = os.getenv("BOCHA_API_KEY", "")

# 调用链追踪：配置了 Langfuse 凭证则上报 Langfuse，否则降级写本地 JSONL
LANGFUSE_PUBLIC_KEY = os.getenv("LANGFUSE_PUBLIC_KEY", "")
LANGFUSE_SECRET_KEY = os.getenv("LANGFUSE_SECRET_KEY", "")
LANGFUSE_HOST = os.getenv("LANGFUSE_HOST", "http://localhost:3000")
TRACE_LOG_PATH = os.path.join(PROJECT_ROOT, "data", "trace_log.jsonl")

# MCP Server 配置：name -> 启动命令（stdio 模式）
# 用当前解释器启动子进程，保证虚拟环境里的 mcp 包可用
_PYTHON = sys.executable
MCP_SERVERS = {
    "weather": [_PYTHON, os.path.join(PROJECT_ROOT, "mcp_servers", "weather_server.py")],
}
