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

        # 文档级 ACL：只检索当前用户可见的文档
        allowed = acl.allowed_docs(acl.get_current_user())

        # 多 KB 路由
        try:
            from docmind.chat_stream import current_kb_ids
            kb_ids = current_kb_ids.get()
        except Exception:  # noqa: BLE001
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
                return ("知识库中没有找到与问题相关的内容（未通过相关性阈值）。"
                        "当前为严格模式：请明确告知用户无法回答，禁止用通识编造。")
            return "知识库中没有找到与问题相关的内容（未通过相关性阈值）。"

        # 新增：文档时效性检测
        from docmind.doc_freshness import check_document_freshness

        # 检查最旧和最新文档的年份
        freshness_checks = []
        for h in hits:
            freshness = check_document_freshness(h.source, h.text[:200])
            freshness_checks.append(freshness)

        # 找出最严重的过期风险
        max_risk = 'none'
        max_risk_doc = None
        for i, f in enumerate(freshness_checks):
            if f['expire_risk'] == 'high':
                max_risk = 'high'
                max_risk_doc = f
                break
            elif f['expire_risk'] == 'medium' and max_risk != 'high':
                max_risk = 'medium'
                max_risk_doc = f

        # 构建检索结果
        lines = []

        # 添加时效性警告（如果有高/中风险）
        if max_risk in ['high', 'medium']:
            warning = max_risk_doc['warning_message']
            if max_risk_doc['should_web_search']:
                warning += '\n\n💡 系统建议：文档已过期，建议上传最新文档或联网搜索核实（工具会自动尝试）'
            lines.append(f"⚠️ 时效性警告：{warning}\n")

        # 添加检索结果
        for i, h in enumerate(hits, 1):
            freshness = freshness_checks[i-1]
            loc = f"来源: {h.source}"
            if h.page:
                loc += f", 第{h.page}页"
            if freshness['doc_year']:
                loc += f" ({freshness['doc_year']}年文档)"
            lines.append(f"[{i}] ({loc}, 相关度: {h.score:.2f})\n{h.text}")

        result = "\n\n".join(lines)

        # 如果最高风险文档需要联网，在结果中明确标注
        if max_risk_doc and max_risk_doc['should_web_search']:
            result += "\n\n⚠️ 重要提示：上述内容来自过期文档，请务必调用 web_search 工具核实最新信息"

        return result

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

    # ---- 联网搜索：多引擎并发降级 + 结果缓存 + 超时优化 ----
    def _search_tavily(query: str) -> list[dict]:
        """Tavily：专为 AI Agent 设计的搜索 API，新鲜度/摘要质量最佳；需 TAVILY_API_KEY"""
        import requests

        resp = requests.post(
            "https://api.tavily.com/search",
            headers={"Authorization": f"Bearer {config.TAVILY_API_KEY}"},
            json={"query": query, "max_results": 5, "search_depth": "basic"},
            timeout=config.WEB_SEARCH_TIMEOUT,
        )
        resp.raise_for_status()
        return [
            {"title": r.get("title", ""), "body": r.get("content", ""),
             "href": r.get("url", ""), "date": r.get("published_date") or ""}
            for r in resp.json().get("results", [])
        ]

    def _search_serper(query: str) -> list[dict]:
        """Serper.dev：Google Search API 包装，免费 2500次/月，质量接近 Tavily"""
        import requests

        resp = requests.post(
            "https://google.serper.dev/search",
            headers={"X-API-KEY": config.SERPER_API_KEY},
            json={"q": query, "num": 5, "gl": "cn", "hl": "zh-cn"},
            timeout=config.WEB_SEARCH_TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()
        results = []
        for r in data.get("organic", [])[:5]:
            results.append({
                "title": r.get("title", ""),
                "body": r.get("snippet", ""),
                "href": r.get("link", ""),
                "date": r.get("date", ""),
            })
        return results

    def _search_bing(query: str) -> list[dict]:
        """Azure Bing Search API：企业级稳定性，需订阅"""
        import requests

        resp = requests.get(
            "https://api.bing.microsoft.com/v7.0/search",
            headers={"Ocp-Apim-Subscription-Key": config.BING_SEARCH_KEY},
            params={"q": query, "count": 5, "mkt": "zh-CN"},
            timeout=config.WEB_SEARCH_TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()
        results = []
        for r in data.get("webPages", {}).get("value", [])[:5]:
            results.append({
                "title": r.get("name", ""),
                "body": r.get("snippet", ""),
                "href": r.get("url", ""),
                "date": r.get("datePublished", "")[:10] if "datePublished" in r else "",
            })
        return results

    def _search_searxng(query: str) -> list[dict]:
        """SearXNG：自托管元搜索引擎（免费无限量）；需 SEARXNG_URL"""
        import requests

        resp = requests.get(
            f"{config.SEARXNG_URL.rstrip('/')}/search",
            params={"q": query, "format": "json", "language": "zh"},
            timeout=config.WEB_SEARCH_TIMEOUT,
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

        # 1) 缓存命中直接返回
        from docmind import web_search_cache
        cached = web_search_cache.get(query)
        if cached:
            lines = []
            for i, r in enumerate(cached, 1):
                date = f" ({r['date'][:10]})" if r.get("date") else ""
                lines.append(f"[{i}] {r['title']}{date}\n{r['body']}\n链接: {r['href']}")
            return "\n\n".join(lines)

        # 2) 构建引擎列表：优先级 Tavily > Serper > Bing > SearXNG
        engines = []
        if config.TAVILY_API_KEY:
            engines.append(("Tavily", _search_tavily))
        if config.SERPER_API_KEY:
            engines.append(("Serper", _search_serper))
        if config.BING_SEARCH_KEY:
            engines.append(("Bing", _search_bing))
        if config.SEARXNG_URL:
            engines.append(("SearXNG", _search_searxng))

        if not engines:
            return "[错误] 未配置搜索引擎（TAVILY_API_KEY / SERPER_API_KEY / BING_SEARCH_KEY / SEARXNG_URL），请如实告知用户无法获取实时信息。"

        # 3) 并发调用多引擎（最多前2个），首个成功即返回
        import concurrent.futures
        errors = []
        results = []

        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
            futures = {executor.submit(fn, query): name for name, fn in engines[:2]}
            for future in concurrent.futures.as_completed(futures, timeout=config.WEB_SEARCH_TIMEOUT + 2):
                name = futures[future]
                try:
                    results = future.result()
                    if results:
                        # 写缓存
                        web_search_cache.put(query, results)
                        break
                except Exception as e:  # noqa: BLE001 - 单引擎失败继续尝试下一个
                    errors.append(f"{name}: {type(e).__name__}")

        # 4) 并发失败则同步降级到剩余引擎
        if not results:
            for name, fn in engines[2:]:
                try:
                    results = fn(query)
                    if results:
                        web_search_cache.put(query, results)
                        break
                except Exception as e:  # noqa: BLE001
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
        description="联网搜索实时信息与最新新闻报道。涉及新闻、时事、最新动态、"
                    "当前/今年/今天/最近等时效性问题时必须调用此工具，"
                    "严禁先凭自身知识作答。所有事实性问题完成知识库检索后，"
                    "也应调用此工具交叉核对时效性。",
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
