"""MCP 客户端：连接 stdio 模式的 MCP Server，把远程工具接入 ToolRegistry。

流程：启动 Server 子进程 → initialize → list_tools → 转成本地 Tool 注册。
工具执行时通过 session.call_tool 转发。

关键设计：连接是长生命周期资源，必须始终活在同一个事件循环里，
因此用一个后台常驻线程持有专用事件循环，所有异步操作都投递到它执行。
"""
import asyncio
import contextlib
import logging
import threading

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from docmind.agent.tools import ToolRegistry

logger = logging.getLogger(__name__)


class _BackgroundLoop:
    """后台常驻事件循环：所有 MCP 连接共享，避免跨循环使用 async 资源"""

    def __init__(self):
        self.loop = asyncio.new_event_loop()
        threading.Thread(target=self.loop.run_forever, daemon=True, name="mcp-loop").start()

    def run(self, coro):
        return asyncio.run_coroutine_threadsafe(coro, self.loop).result(timeout=60)


_runner: _BackgroundLoop | None = None


def _get_runner() -> _BackgroundLoop:
    global _runner
    if _runner is None:
        _runner = _BackgroundLoop()
    return _runner


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


def register_mcp_tools(registry: ToolRegistry, servers: dict[str, list[str]]) -> list[McpConnection]:
    """连接所有配置的 MCP Server，并把其工具注册进 registry。返回连接列表（退出时 close）"""
    runner = _get_runner()
    connections = []
    for name, command in servers.items():
        conn = McpConnection(name, command)
        try:
            runner.run(_init_and_register(conn, registry))
            connections.append(conn)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"MCP Server '{name}' 连接失败，跳过: {e}")
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
            handler=lambda args, c=conn, tn=t.name: c._call_sync(tn, args),
            source=f"mcp:{conn.name}",
        )


# 同步包装方法挂在 McpConnection 上
def _call_sync(self: McpConnection, tool_name: str, arguments: dict) -> str:
    return _get_runner().run(self.call_tool_async(tool_name, arguments))


McpConnection._call_sync = _call_sync  # type: ignore[attr-defined]
