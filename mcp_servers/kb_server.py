"""DocMind 知识库 MCP Server：把平台的知识检索暴露为标准 MCP 工具。

让 Claude Desktop / Cursor / 任何 MCP 客户端都能直接查询 DocMind 知识库——
知识库从「网页应用」升级为「开发者基础设施」：研发在 IDE 里就能查内部
文档，无需切网页。检索走开放 API（POST /open/v1/retrieve），文档级 ACL
与每 Key 限流全部继承服务端策略，MCP 侧不存任何知识数据。

前置条件：
1. DocMind 服务已启动（本地默认 http://127.0.0.1:7860）
2. 管理端「API Key」页创建密钥（明文一次性展示），scope 可限定知识库

MCP 客户端配置示例（Claude Desktop / Cursor 的 mcpServers 节点）：
    {
      "mcpServers": {
        "docmind-kb": {
          "command": "/path/to/.venv/bin/python",
          "args": ["/path/to/mcp_servers/kb_server.py"],
          "env": {
            "DOCMIND_BASE": "http://127.0.0.1:7860",
            "DOCMIND_API_KEY": "dm_xxx"
          }
        }
      }
    }
"""
import os

import requests
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("docmind-kb")

_BASE = os.getenv("DOCMIND_BASE", "http://127.0.0.1:7860").rstrip("/")
_API_KEY = os.getenv("DOCMIND_API_KEY", "")


@mcp.tool()
def search_knowledge(question: str, top_k: int = 4) -> str:
    """在 DocMind 企业知识库中检索与问题最相关的文档片段。

    返回按相关度排序的片段列表，含来源文档、页码与相关度分数。
    适合在写代码/文档时查内部规范、部署手册、FAQ 等。"""
    if not _API_KEY:
        return "⚠️ 尚未配置知识库访问密钥：请在 MCP 客户端的 env 里填 DOCMIND_API_KEY（管理端 API Key 页创建）。"
    if not question.strip():
        return "⚠️ 请输入要查询的问题。"
    try:
        resp = requests.post(
            f"{_BASE}/open/v1/retrieve",
            json={"question": question.strip(), "top_k": max(1, min(int(top_k), 10))},
            headers={"Authorization": f"Bearer {_API_KEY}"},
            timeout=30,
        )
        if resp.status_code == 401:
            return "⚠️ 访问密钥无效或已过期：请到 DocMind 管理端重新创建 API Key 并更新配置。"
        if resp.status_code == 429:
            return "⚠️ 查询太频繁触发限流：请稍等片刻再试，或让管理员调整每分钟限额。"
        resp.raise_for_status()
        hits = resp.json().get("hits", [])
    except requests.ConnectionError:
        return f"⚠️ 连不上 DocMind 服务（{_BASE}）：请确认服务已启动后再试。"

    if not hits:
        return "知识库中没有找到与该问题相关的内容。可尝试换一种问法，或确认相关文档已上传并重建索引。"

    lines = [f"共找到 {len(hits)} 条相关内容：\n"]
    for i, h in enumerate(hits, 1):
        page = f" 第 {h['page']} 页" if h.get("page") else ""
        score = h.get("score")
        score_s = f"（相关度 {score:.2f}）" if isinstance(score, (int, float)) else ""
        lines.append(f"[{i}] {h.get('source', '未知来源')}{page}{score_s}")
        lines.append(f"    {h.get('text', '').strip()[:400]}")
        lines.append("")
    return "\n".join(lines).strip()


if __name__ == "__main__":
    mcp.run()
