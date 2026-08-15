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

    # ---- 联网搜索：多引擎降级（博查 → DDG news → DDG text）----
    def _search_bocha(query: str) -> list[dict]:
        """博查 AI 搜索：国内稳定、新鲜度高；需 BOCHA_API_KEY"""
        import requests

        resp = requests.post(
            "https://api.bochaai.com/v1/web-search",
            headers={"Authorization": f"Bearer {config.BOCHA_API_KEY}"},
            json={"query": query, "freshness": "noLimit", "summary": True, "count": 6},
            timeout=15,
        )
        resp.raise_for_status()
        pages = resp.json().get("data", {}).get("webPages", {}).get("value", [])
        return [
            {"title": p.get("name", ""), "body": p.get("snippet", ""),
             "href": p.get("url", ""), "date": p.get("datePublished", "")}
            for p in pages
        ]

    def _search_ddg(query: str) -> list[dict]:
        """DuckDuckGo：免 Key 兜底，先试 news 通道再退 text 通道"""
        from ddgs import DDGS

        try:
            results = DDGS().news(query, region="cn-zh", max_results=5, timeout=12)
        except Exception:  # noqa: BLE001 - news 通道不通则退 text
            results = DDGS().text(query, region="cn-zh", max_results=5, timeout=20)
        return [
            {"title": r.get("title", ""), "body": r.get("body", ""),
             "href": r.get("url") or r.get("href", ""), "date": r.get("date", "")}
            for r in (results or [])
        ]

    def web_search(args: dict) -> str:
        query = args.get("query", "")
        if not query:
            return "[错误] 缺少 query 参数"
        errors = []
        engines = []
        if config.BOCHA_API_KEY:
            engines.append(("博查", _search_bocha))
        engines.append(("DuckDuckGo", _search_ddg))
        results = []
        for name, fn in engines:
            try:
                results = fn(query)
                if results:
                    break
            except Exception as e:  # noqa: BLE001 - 逐引擎降级
                errors.append(f"{name}: {type(e).__name__}")
        if not results:
            detail = f"（{'; '.join(errors)}）" if errors else ""
            return f"[错误] 联网搜索暂不可用{detail}，请如实告知用户无法获取实时信息。"
        lines = []
        for i, r in enumerate(results, 1):
            date = f" ({r['date'][:10]})" if r.get("date") else ""
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
