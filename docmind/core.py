"""应用装配：把 RAG、MCP、本地工具缝进手写 Agent。

这是整个项目的"接线图"：
    knowledge_search（RAG 检索） + get_current_time（本地工具）
    + MCP Server 远程工具 → ToolRegistry → ReActAgent
"""
from datetime import datetime

from docmind import config
from docmind.agent.react_agent import ReActAgent
from docmind.agent.tools import ToolRegistry
from docmind.mcp_client import register_mcp_tools
from docmind.rag.vector_store import VectorStore


def build_agent():
    """装配 Agent，返回 (agent, vector_store, mcp_connections)"""
    registry = ToolRegistry()

    # ---- RAG 知识库 ----
    store = VectorStore()
    n = store.build()
    print(f"[DocMind] 知识库加载完成：{n} 个切片")

    def knowledge_search(args: dict) -> str:
        query = args.get("query", "")
        if not query:
            return "[错误] 缺少 query 参数"
        # 阈值过滤：低于 RETRIEVE_MIN_SCORE 的切片视为无关，避免噪音进入上下文
        hits = [h for h in store.search(query) if h.score >= config.RETRIEVE_MIN_SCORE]
        if not hits:
            return "知识库中没有找到与问题相关的内容（均未通过相关性阈值）。"
        lines = []
        for i, h in enumerate(hits, 1):
            lines.append(f"[{i}] (来源: {h.source}, 相关度: {h.score:.2f})\n{h.text}")
        return "\n\n".join(lines)

    registry.register(
        name="knowledge_search",
        description="在本地知识库中语义检索，返回最相关的文档片段及来源。"
                    "回答事实性问题前必须先调用此工具。",
        parameters={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "检索问题或关键词"},
            },
            "required": ["query"],
        },
        handler=knowledge_search,
    )

    # ---- 本地小工具示例 ----
    registry.register(
        name="get_current_time",
        description="获取当前系统时间",
        parameters={"type": "object", "properties": {}},
        handler=lambda args: datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    )

    # ---- MCP 远程工具 ----
    from docmind.config import MCP_SERVERS
    connections = register_mcp_tools(registry, MCP_SERVERS)

    agent = ReActAgent(registry=registry)
    return agent, store, connections
