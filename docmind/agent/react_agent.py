"""从零实现的 ReAct Agent（不依赖 LangChain / LlamaIndex）。

核心循环：
    用户提问 → LLM（带工具描述）→ 有 tool_calls 就执行并把结果喂回
    → 循环直到 LLM 给出最终回答，或达到最大步数

防护机制（面试可讲）：
1. MAX_AGENT_STEPS 限制最大推理步数，防死循环
2. 重复调用检测：连续相同的工具+参数直接打断
3. 工具异常不抛出，转为观察结果让 LLM 自我纠正
"""
from dataclasses import dataclass, field

from docmind import config
from docmind.agent.tools import ToolRegistry
from docmind.llm import chat

SYSTEM_PROMPT = """你是 DocMind，一个严谨的知识助理 Agent。

工作准则：
1. 任何事实性问题，必须先调用 knowledge_search 工具检索知识库，
   严禁跳过检索直接回答；基于检索结果回答时，末尾用 [来源: 文件名] 标注引用。
2. 若检索返回“未找到相关内容”，可以用自身通识回答，但开头必须标注
   【知识库无相关内容，以下为模型通识】，并提醒用户该回答未经知识库验证。
3. 涉及外部实时信息（天气等）时，调用对应的工具，没有合适工具时如实说明。
4. 检索结果不足以回答时，如实说明，不要猜测。
5. 回答使用中文，简洁清晰。"""


@dataclass
class AgentStep:
    """单步轨迹，用于 GUI 展示思考过程"""
    kind: str        # tool_call / tool_result / final
    text: str


@dataclass
class ReActAgent:
    registry: ToolRegistry
    history: list[dict] = field(default_factory=list)

    def ask(self, question: str):
        """处理一次提问，yield AgentStep，最后一步 kind='final' 为最终回答"""
        if not self.history:
            self.history.append({"role": "system", "content": SYSTEM_PROMPT})
        self.history.append({"role": "user", "content": question})

        openai_tools = self.registry.to_openai_tools() or None
        recent_signatures: list[str] = []   # 重复调用检测

        for _ in range(config.MAX_AGENT_STEPS):
            try:
                message = chat(self.history, tools=openai_tools)
            except Exception as e:  # noqa: BLE001 - 模型调用失败不能弄崩生成器
                error_msg = f"抱歉，模型调用失败（已自动重试过）：{e}\n请稍后重试。"
                self.history.append({"role": "assistant", "content": error_msg})
                yield AgentStep("final", error_msg)
                return

            # 模型给出最终回答
            if not message.tool_calls:
                answer = message.content or ""
                self.history.append({"role": "assistant", "content": answer})
                yield AgentStep("final", answer)
                return

            # 模型要求调用工具：先记录 assistant 消息（含 tool_calls）
            self.history.append({
                "role": "assistant",
                "content": message.content or "",
                "tool_calls": [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.function.name,
                            "arguments": tc.function.arguments,
                        },
                    }
                    for tc in message.tool_calls
                ],
            })

            for tc in message.tool_calls:
                name, args = tc.function.name, tc.function.arguments
                yield AgentStep("tool_call", f"调用工具 `{name}`，参数: {args}")

                # 防死循环：同样的调用连续出现两次则打断
                sig = f"{name}:{args}"
                if recent_signatures and recent_signatures[-1] == sig:
                    result = "[错误] 检测到重复调用同一工具，请换一种方式或直接回答"
                    recent_signatures.clear()
                else:
                    recent_signatures.append(sig)
                    result = self.registry.execute(name, args)

                yield AgentStep("tool_result", f"`{name}` 返回: {result}")
                self.history.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": result,
                })

        # 达到最大步数仍未收敛
        fallback = "抱歉，我尝试了多个步骤仍未能得出结论，请简化问题后重试。"
        self.history.append({"role": "assistant", "content": fallback})
        yield AgentStep("final", fallback)

    def reset(self) -> None:
        self.history.clear()
