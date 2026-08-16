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
from datetime import date

from docmind import config
from docmind import trace
from docmind.agent.tools import ToolRegistry
from docmind.llm import _brief_messages, chat_stream

SYSTEM_PROMPT = f"""你是 DocMind，一个严谨的知识助理 Agent。今天是 {date.today().isoformat()}。

工作准则：
1. 任何事实性问题，必须先调用 knowledge_search 工具检索知识库，
   严禁跳过检索直接回答；基于检索结果回答时，末尾用 [来源: 文件名] 标注引用；
   检索结果含页码时写成 [来源: 文件名 · 第N页]（用户可点击直达原文该页）。
2. 若检索返回“未找到相关内容”，可以用自身通识回答，但开头必须标注
   【知识库无相关内容，以下为模型通识】，并提醒用户该回答未经知识库验证；
   若知识库无相关内容而改用联网检索结果作答，开头同样必须标注
   【知识库无相关内容，以下基于联网检索】。
3. 你的训练知识存在截止时间（可能早于今天）：回答时效性问题时必须明确说明
   知识覆盖到何时；严禁编造、推测或转述你知识截止之后的任何事件与报道。
4. 所有事实性问题（知识、时事、动态等）：完成知识库检索后，必须再调用 web_search
   获取联网信息交叉核对时效性，然后综合两方面结果作答；天气调用天气工具。
   引用搜索结果时注明来源链接与日期，并提醒用户自行核实。
5. 检索结果不足以回答时，如实说明，不要猜测。
6. 回答使用中文，简洁清晰。
7. 回答结构：先用一两句话给出核心结论；具体分析分条展开（有数据时附数值
   与场景解读）；存在不确定性或风险时明确提示；最后用一个引导性问题结尾，
   邀请用户继续深入。
8. 数据可视化：当回答涉及流程、架构、对比、关系等结构化信息时，应插入一个
   Mermaid 图表辅助说明。图表必须用「三反引号 + mermaid 语言标记」的代码块
   完整包裹，开头一行 ```mermaid、结尾一行 ```，两者缺一不可，格式严格如下：
   ```mermaid
   flowchart TD
       A[用户提问] --> B[Agent 判断]
       B --> C[检索知识库]
       C --> D[生成回答]
   ```
   图表类型建议：流程/步骤用 flowchart TD；架构/模块关系用 flowchart 或 graph LR；
   对比/分类用 mindmap。图表须简洁（节点≤8 个）、语法正确、可独立渲染，
   与正文互补而非重复；切勿输出裸的 mermaid 语法而漏掉代码围栏。"""


# OOD 透明度标注守卫：评测发现 LLM 偶发漏标【知识库无相关内容】（依从性非确定），
# 在 Agent 侧做确定性后处理兜底——KB 检索为空且终答无标注时自动补标。
# 标注文本与 system prompt 规则 2 保持一致。
_OOD_MARKER_KB_EMPTY = "【知识库无相关内容，以下为模型通识】"
_OOD_MARKER_WEB = "【知识库无相关内容，以下基于联网检索】"
_OOD_MARKER_KEY = "知识库无相关内容"      # 命中任一变体即视为已标注
_KB_NO_HIT_KEY = "未通过相关性阈值"        # knowledge_search 空结果的判定锚点
_KB_HIT_KEY = "[1] ("                    # knowledge_search 有结果的格式锚点


@dataclass
class AgentStep:
    """单步轨迹，用于 GUI 展示思考过程"""
    kind: str        # thinking / token / tool_call / tool_result / final
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
        # OOD 守卫状态：KB 是否被调用/是否命中、是否用过联网搜索
        kb_called = kb_hit = web_used = False

        for _ in range(config.MAX_AGENT_STEPS):
            # 流式生成：边生成边 yield token 增量，结束后重建完整消息
            content_parts: list[str] = []
            tool_calls_acc: dict[int, dict] = {}
            usage = None
            try:
                with trace.span("llm-chat", kind="generation", model=config.CHAT_MODEL,
                                input=_brief_messages(self.history)) as ctx:
                    for chunk in chat_stream(self.history, tools=openai_tools,
                                             enable_thinking=config.ENABLE_THINKING):
                        if getattr(chunk, "usage", None):
                            usage = chunk.usage
                        if not chunk.choices:
                            continue
                        delta = chunk.choices[0].delta
                        if delta is None:
                            continue
                        # 思维链增量：不进 history（百炼多轮要求 assistant 只含正文）
                        reasoning = getattr(delta, "reasoning_content", None)
                        if reasoning:
                            yield AgentStep("thinking", reasoning)
                        if delta.content:
                            content_parts.append(delta.content)
                            yield AgentStep("token", delta.content)
                        if delta.tool_calls:
                            for tc in delta.tool_calls:
                                acc = tool_calls_acc.setdefault(
                                    tc.index, {"id": "", "name": "", "arguments": ""}
                                )
                                if tc.id:
                                    acc["id"] = tc.id
                                if tc.function:
                                    if tc.function.name:
                                        acc["name"] += tc.function.name
                                    if tc.function.arguments:
                                        acc["arguments"] += tc.function.arguments
                    answer = "".join(content_parts)
                    tool_names = [v["name"] for v in tool_calls_acc.values()]
                    ctx["output"] = (answer or f"[调用工具: {tool_names}]")[:300]
                    if usage:
                        ctx["usage"] = {"input": usage.prompt_tokens, "output": usage.completion_tokens}
            except Exception as e:  # noqa: BLE001 - 模型调用失败不能弄崩生成器
                error_msg = f"抱歉，模型调用失败（已自动重试过）：{e}\n请稍后重试。"
                self.history.append({"role": "assistant", "content": error_msg})
                yield AgentStep("final", error_msg)
                return

            # 模型给出最终回答（无工具调用）
            if not tool_calls_acc:
                # OOD 透明度守卫：KB 检索过但为空、且终答未带任何标注 → 自动补标
                if kb_called and not kb_hit and _OOD_MARKER_KEY not in answer:
                    marker = _OOD_MARKER_WEB if web_used else _OOD_MARKER_KB_EMPTY
                    answer = f"{marker}\n\n{answer}"
                self.history.append({"role": "assistant", "content": answer})
                yield AgentStep("final", answer)
                return

            # 模型要求调用工具：先记录 assistant 消息（含 tool_calls）
            ordered_calls = [tool_calls_acc[i] for i in sorted(tool_calls_acc)]
            self.history.append({
                "role": "assistant",
                "content": answer,
                "tool_calls": [
                    {
                        "id": acc["id"] or f"call_{i}",
                        "type": "function",
                        "function": {"name": acc["name"], "arguments": acc["arguments"]},
                    }
                    for i, acc in enumerate(ordered_calls)
                ],
            })

            for i, acc in enumerate(ordered_calls):
                name, args = acc["name"], acc["arguments"]
                yield AgentStep("tool_call", f"调用工具 `{name}`，参数: {args}")

                # 防死循环：同样的调用连续出现两次则打断
                sig = f"{name}:{args}"
                if recent_signatures and recent_signatures[-1] == sig:
                    result = "[错误] 检测到重复调用同一工具，请换一种方式或直接回答"
                    recent_signatures.clear()
                else:
                    recent_signatures.append(sig)
                    with trace.span(f"tool:{name}", input=args) as tctx:
                        result = self.registry.execute(name, args)
                        tctx["output"] = result[:300]

                yield AgentStep("tool_result", f"`{name}` 返回: {result}")
                self.history.append({
                    "role": "tool",
                    "tool_call_id": acc["id"] or f"call_{i}",
                    "content": result,
                })
                # OOD 守卫状态更新（多次调用时任一命中即算命中）
                if name == "knowledge_search":
                    kb_called = True
                    if _KB_HIT_KEY in result and _KB_NO_HIT_KEY not in result:
                        kb_hit = True
                elif name == "web_search" and not result.startswith("[错误]"):
                    web_used = True

        # 达到最大步数仍未收敛
        fallback = "抱歉，我尝试了多个步骤仍未能得出结论，请简化问题后重试。"
        self.history.append({"role": "assistant", "content": fallback})
        yield AgentStep("final", fallback)

    def reset(self) -> None:
        self.history.clear()
