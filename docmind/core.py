"""应用装配：把 RAG、MCP、本地工具缝进手写 Agent。

这是整个项目的"接线图"：
    knowledge_search（RAG 检索） + get_current_time（本地工具）
    + MCP Server 远程工具 → ToolRegistry → ReActAgent
"""
import logging
from datetime import datetime

from docmind import acl, config
from docmind.agent.react_agent import ReActAgent
from docmind.agent.tools import ToolRegistry
from docmind.mcp_client import register_mcp_tools
from docmind.rag.hybrid import HybridRetriever
from docmind.rag.vector_store import VectorStore

logger = logging.getLogger(__name__)

# 共享运行态：build_shared 装配完成后登记 store/retriever，
# 供管理端点 /api/admin/reindex 触发知识库增量重建
_shared_state: dict = {}


def build_shared():
    """构建共享资源（registry、store、连接），启动时调用一次。
    
    返回 (registry, vector_store, mcp_connections)。
    ToolRegistry 无状态可安全共享；只有 ReActAgent（持有 history）需要每请求创建。
    """
    registry = ToolRegistry()

    # ---- RAG 知识库：向量库 + 混合检索（BM25+RRF+Rerank）----
    store = VectorStore()
    n = store.build()
    retriever = HybridRetriever(store)
    retriever.build()
    logger.info(f"知识库加载完成：{n} 个切片，混合索引已构建")
    _shared_state.update(store=store, retriever=retriever)
    try:
        from docmind.metrics import KNOWLEDGE_CHUNKS
        KNOWLEDGE_CHUNKS.set(n)
    except Exception:  # noqa: BLE001 - 指标故障不影响启动
        pass

    def knowledge_search(args: dict) -> str:
        query = args.get("query", "")
        if not query:
            return "[错误] 缺少 query 参数"
        # 文档级 ACL：只检索当前用户可见的文档；无权文档被过滤后
        # 返回与"真没有"完全相同的话术，不泄露受限文档的存在性
        allowed = acl.allowed_docs(acl.get_current_user())
        # 多 KB 路由：当前请求若挂着自定义助手（contextvar 由 app 端点设置），
        # 则检索其绑定的知识库集合；空列表回退默认单库，行为与旧链路完全一致。
        # 惰性导入 chat_stream：contextvar 归属该模块，顶层导入会循环依赖
        try:
            from docmind.chat_stream import current_kb_ids
            kb_ids = current_kb_ids.get()
        except Exception:  # noqa: BLE001 - 无上下文时按默认库处理
            kb_ids = []
        kb_ids = [k for k in kb_ids if k and k != "default"]
        if kb_ids:
            from docmind.rag.kb_registry import get_registry
            hits = get_registry().search_multi(kb_ids, query, top_k=config.TOP_K,
                                               allowed_sources=allowed)
        else:
            hits = retriever.search(query, top_k=config.TOP_K, rerank=True,
                                    allowed_sources=allowed)
        if not hits:
            if config.EVIDENCE_REFUSAL:
                # 严格模式：工具结果里直接下达拒答指令，尽早终结 ReAct 循环省 token
                return ("知识库中没有找到与问题相关的内容（未通过相关性阈值）。"
                        "当前为严格模式：请明确告知用户无法回答，禁止用通识编造。")
            return "知识库中没有找到与问题相关的内容（未通过相关性阈值）。"
        lines = []
        for i, h in enumerate(hits, 1):
            loc = f"来源: {h.source}" + (f", 第{h.page}页" if h.page else "")
            lines.append(f"[{i}] ({loc}, 相关度: {h.score:.2f})\n{h.text}")
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

    return registry, store, connections


def create_agent(registry, system_prompt: str | None = None):
    """为每个请求创建新的 Agent 实例，避免并发请求共享 history 导致上下文混乱。

    system_prompt 非空时覆盖默认 SYSTEM_PROMPT（自定义助手场景）；None 保持原行为。"""
    return ReActAgent(registry=registry, system_prompt=system_prompt)


def rebuild_knowledge_index() -> dict:
    """手动触发知识库索引增量重建（管理端点 /api/admin/reindex 调用）。

    线程安全：非阻塞锁防止并发重建导致切片/矩阵撕裂；
    重建后 store.version 递增，所有 HybridRetriever 在下次检索时
    懒重建 BM25，无需逐个手动同步（含 app 层独立的 locate_retriever）。
    """
    import threading

    store = _shared_state.get("store")
    if store is None:
        return {"error": "知识库尚未初始化"}
    lock = _shared_state.setdefault("_rebuild_lock", threading.Lock())
    if not lock.acquire(blocking=False):
        return {"error": "索引正在重建中，请稍后再试"}
    try:
        result = store.rebuild_incremental(config.KNOWLEDGE_DIR)
        try:
            from docmind.metrics import KNOWLEDGE_CHUNKS
            KNOWLEDGE_CHUNKS.set(len(store.chunks))
        except Exception:  # noqa: BLE001 - 指标故障不影响重建结果
            pass
        logger.info(f"知识库索引手动重建完成: {result}")
        return result
    except Exception as e:  # noqa: BLE001
        logger.exception("知识库索引重建失败")
        return {"error": f"重建失败: {e}"}
    finally:
        lock.release()
