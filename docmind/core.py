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
from docmind.rag.hybrid import HybridRetriever
from docmind.rag.vector_store import VectorStore


def build_agent():
    """装配 Agent，返回 (agent, vector_store, mcp_connections)"""
    registry = ToolRegistry()

    # ---- RAG 知识库：向量库 + 混合检索（BM25+RRF+Rerank）----
    store = VectorStore()
    n = store.build()
    retriever = HybridRetriever(store)
    retriever.build()
    print(f"[DocMind] 知识库加载完成：{n} 个切片，混合索引已构建")

    def knowledge_search(args: dict) -> str:
        query = args.get("query", "")
        if not query:
            return "[错误] 缺少 query 参数"
        hits = retriever.search(query, top_k=config.TOP_K, rerank=True)
        if not hits:
            return "知识库中没有找到与问题相关的内容（未通过相关性阈值）。"
        lines = []
        for i, h in enumerate(hits, 1):
            lines.append(f"[{i}] (来源: {h.source}, 相关度: {h.score:.2f})\n{h.text}")
        return "\n\n".join(lines)

    registry.register(
        name="knowledge_search",
        description="在本地知识库中混合检索（BM25+向量+Rerank），返回最相关的文档片段及来源。"
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

    # ---- 联网搜索：多引擎降级（Tavily → SearXNG）----
    def _search_tavily(query: str) -> list[dict]:
        """Tavily：专为 AI Agent 设计的搜索 API，新鲜度/摘要质量最佳；需 TAVILY_API_KEY"""
        import requests

        resp = requests.post(
            "https://api.tavily.com/search",
            headers={"Authorization": f"Bearer {config.TAVILY_API_KEY}"},
            json={"query": query, "max_results": 5, "search_depth": "basic"},
            timeout=20,
        )
        resp.raise_for_status()
        return [
            {"title": r.get("title", ""), "body": r.get("content", ""),
             "href": r.get("url", ""), "date": r.get("published_date") or ""}
            for r in resp.json().get("results", [])
        ]

    def _search_searxng(query: str) -> list[dict]:
        """SearXNG：自托管元搜索引擎（免费无限量）；需 SEARXNG_URL"""
        import requests

        resp = requests.get(
            f"{config.SEARXNG_URL.rstrip('/')}/search",
            params={"q": query, "format": "json", "language": "zh"},
            timeout=20,
        )
        resp.raise_for_status()
        return [
            {"title": r.get("title", ""), "body": r.get("content", ""),
             "href": r.get("url", ""), "date": ""}
            for r in resp.json().get("results", [])[:5]
        ]

    def web_search(args: dict) -> str:
        query = args.get("query", "")
        if not query:
            return "[错误] 缺少 query 参数"
        engines = []
        if config.TAVILY_API_KEY:
            engines.append(("Tavily", _search_tavily))
        if config.SEARXNG_URL:
            engines.append(("SearXNG", _search_searxng))
        if not engines:
            return "[错误] 未配置搜索引擎（TAVILY_API_KEY / SEARXNG_URL），请如实告知用户无法获取实时信息。"
        errors = []
        results = []
        for name, fn in engines:
            try:
                results = fn(query)
                if results:
                    break
            except Exception as e:  # noqa: BLE001 - 逐引擎降级
                errors.append(f"{name}: {type(e).__name__}")
        if not results:
            return f"[错误] 联网搜索暂不可用（{'; '.join(errors)}），请如实告知用户无法获取实时信息。"
        lines = []
        for i, r in enumerate(results, 1):
            date = f" ({str(r['date'])[:10]})" if r.get("date") else ""
            lines.append(f"[{i}] {r['title']}{date}\n{r['body']}\n链接: {r['href']}")
        return "\n\n".join(lines)

    registry.register(
        name="web_search",
        description="联网搜索实时信息与最新新闻报道。涉及新闻、时事、最新动态等"
                    "时效性问题时必须先调用此工具，严禁先凭自身知识作答。",
        parameters={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "搜索关键词"},
            },
            "required": ["query"],
        },
        handler=web_search,
    )

    # ---- MCP 远程工具 ----
    from docmind.config import MCP_SERVERS
    connections = register_mcp_tools(registry, MCP_SERVERS)

    agent = ReActAgent(registry=registry)
    return agent, store, connections
