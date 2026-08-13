"""工具注册表：统一管理局部工具与 MCP 远程工具。

设计要点（面试可讲）：
- 所有工具统一转成 OpenAI function calling 的 JSON Schema 描述
- 本地工具直接函数调用；MCP 工具走 MCP 客户端转发
- 工具执行失败返回错误字符串而不是抛异常，让 LLM 有机会自我纠正
"""
import json
from dataclasses import dataclass, field
from typing import Any, Callable


@dataclass
class Tool:
    name: str
    description: str
    parameters: dict                      # JSON Schema
    handler: Callable[[dict], Any]        # args(dict) -> 结果
    source: str = "local"                 # local / mcp:<server>


@dataclass
class ToolRegistry:
    tools: dict[str, Tool] = field(default_factory=dict)

    def register(
        self,
        name: str,
        description: str,
        parameters: dict,
        handler: Callable[[dict], Any],
        source: str = "local",
    ) -> None:
        self.tools[name] = Tool(name, description, parameters, handler, source)

    def tool(self, description: str, parameters: dict):
        """装饰器写法：@registry.tool("描述", {...schema...})"""
        def deco(fn: Callable):
            self.register(fn.__name__, description, parameters, fn)
            return fn
        return deco

    def to_openai_tools(self) -> list[dict]:
        """转成 function calling 格式"""
        return [
            {
                "type": "function",
                "function": {
                    "name": t.name,
                    "description": t.description,
                    "parameters": t.parameters,
                },
            }
            for t in self.tools.values()
        ]

    def execute(self, name: str, arguments: str | dict) -> str:
        """执行工具，统一返回字符串；异常转成错误提示供 LLM 反思"""
        tool = self.tools.get(name)
        if tool is None:
            return f"[错误] 未知工具: {name}"
        try:
            args = json.loads(arguments) if isinstance(arguments, str) else arguments
            result = tool.handler(args or {})
            return result if isinstance(result, str) else json.dumps(result, ensure_ascii=False)
        except Exception as e:  # noqa: BLE001 - 故意宽泛捕获，交给 LLM 处理
            return f"[错误] 工具 {name} 执行失败: {e}"
