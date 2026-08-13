"""MCP 客户端：连接 stdio 模式的 MCP Server，把远程工具接入 ToolRegistry。

流程：启动 Server 子进程 → initialize → list_tools → 转成本地 Tool 注册。
工具执行时通过 session.call_tool 转发。
"""
import asyncio
import contextlib

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from docmind.agent.tools import ToolRegistry


class McpConnection:
    """持有一个 MCP Server 的连接与工具列表"""

    def __init__(self, name: str, command: list[str]):
        self.name = name
        self.params = StdioServerParameters(command=command[0], args=command[1:])
        self.session: ClientSession | None = None
        self._stack: contextlib.AsyncExitStack | None = None
        self.tool_names: list[str] = []

    async def connect(self) -> None:
        stack = contextlib.AsyncExitStack()
        self._stack = stack
        read, write = await stack.enter_async_context(stdio_client(self.params))
        session = await stack.enter_async_context(ClientSession(read, write))
        await session.initialize()
        self.session = session
        tools_resp = await session.list_tools()
        self.tool_names = [t.name for t in tools_resp.tools]

    async def close(self) -> None:
        if self._stack:
            await self._stack.aclose()
            self._stack = None
            self.session = None

    async def call_tool_async(self, tool_name: str, arguments: dict) -> str:
        assert self.session is not None, f"MCP Server {self.name} 未连接"
        result = await self.session.call_tool(tool_name, arguments)
        texts = [c.text for c in result.content if hasattr(c, "text")]
        return "\n".join(texts) if texts else "(工具无返回内容)"


def _run(coro):
    """在同步的工具 handler 里跑异步调用"""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None
    if loop is not None:
        # 已在事件循环中（如 Gradio async 场景）：开新线程执行
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(asyncio.run, coro).result()
    return asyncio.run(coro)


def register_mcp_tools(registry: ToolRegistry, servers: dict[str, list[str]]) -> list[McpConnection]:
    """连接所有配置的 MCP Server，并把其工具注册进 registry。返回连接列表（退出时 close）"""
    connections = []
    for name, command in servers.items():
        conn = McpConnection(name, command)
        try:
            _run(_init_and_register(conn, registry))
            connections.append(conn)
        except Exception as e:  # noqa: BLE001
            print(f"[警告] MCP Server '{name}' 连接失败，跳过: {e}")
    return connections


async def _init_and_register(conn: McpConnection, registry: ToolRegistry) -> None:
    await conn.connect()
    tools_resp = await conn.session.list_tools()
    for t in tools_resp.tools:
        schema = t.inputSchema or {"type": "object", "properties": {}}
        registry.register(
            name=t.name,
            description=f"[MCP:{conn.name}] {t.description or ''}",
            parameters=schema,
            handler=(lambda c=conn, tn=t.name: lambda args: c._call_sync(tn, args)),
            source=f"mcp:{conn.name}",
        )


# 同步包装方法挂在 McpConnection 上
def _call_sync(self: McpConnection, tool_name: str, arguments: dict) -> str:
    return _run(self.call_tool_async(tool_name, arguments))


McpConnection._call_sync = _call_sync  # type: ignore[attr-defined]
