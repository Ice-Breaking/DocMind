"""MCP stdio 客户端端到端验证（一次性脚本）：
创建临时 API Key → 拉起 kb_server.py → initialize → list_tools → call_tool → 吊销密钥。"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from docmind import store  # noqa: E402


async def main():
    key_row = store.create_api_key("MCP 验证临时密钥", [], "mcp-test")
    env = {**os.environ,
           "DOCMIND_BASE": "http://127.0.0.1:7861",
           "DOCMIND_API_KEY": key_row["key"]}

    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    params = StdioServerParameters(
        command=os.path.abspath(".venv/bin/python"),
        args=[os.path.abspath("mcp_servers/kb_server.py")],
        env=env)

    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = await session.list_tools()
            print("TOOLS:", [t.name for t in tools.tools])

            result = await session.call_tool("search_knowledge", {
                "question": "端口 7860 被占用了怎么办？", "top_k": 3})
            text = result.content[0].text
            print("---tool result---")
            print(text[:600])

            result2 = await session.call_tool("search_knowledge", {
                "question": "什么是语义缓存？", "top_k": 2})
            print("---tool result 2 (first 2 lines)---")
            print("\n".join(result2.content[0].text.splitlines()[:2]))

    store.revoke_api_key(key_row["id"])
    print("临时密钥已吊销 — MCP E2E OK")


if __name__ == "__main__":
    asyncio.run(main())
