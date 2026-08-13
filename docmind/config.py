"""全局配置：从 .env 加载"""
import os

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
CHUNK_SIZE = 500          # 每片最大字符数
CHUNK_OVERLAP = 80        # 相邻切片重叠字符数
TOP_K = 4                 # 检索返回条数

# MCP Server 配置：name -> 启动命令（stdio 模式）
MCP_SERVERS = {
    "weather": ["python", os.path.join(PROJECT_ROOT, "mcp_servers", "weather_server.py")],
}
