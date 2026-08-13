"""DocMind Web 界面（Gradio）：展示 Agent 思考过程 + 引用来源。

启动：python -m docmind.app
"""
import gradio as gr

from docmind.core import build_agent

print("[DocMind] 正在装配 Agent（加载知识库、连接 MCP Server）...")
agent, store, mcp_connections = build_agent()
tool_names = list(agent.registry.tools.keys())


def _render_trace(lines: list[str]) -> str:
    return "\n\n".join(f"> {ln}" for ln in lines)


def respond_simple(question: str, history: list):
    """流式输出思考过程，最后给出完整回答"""
    trace_lines = []
    final_answer = ""
    for step in agent.ask(question):
        if step.kind == "final":
            final_answer = step.text
        else:
            icon = "🔧" if step.kind == "tool_call" else "📥"
            trace_lines.append(f"{icon} {step.text}")
            partial = f"⏳ 思考中...\n\n{_render_trace(trace_lines)}"
            yield history + [[question, partial]]
    full = final_answer
    if trace_lines:
        full += "\n\n---\n**🧠 Agent 思考过程：**\n\n" + "\n\n".join(trace_lines)
    yield (history[:-1] + [[question, full]]) if history else [[question, full]]


def reset_chat():
    agent.reset()
    return []


with gr.Blocks(title="DocMind") as demo:
    gr.Markdown(
        "# 🧠 DocMind\n"
        "从零实现的知识助理 Agent：**手写 ReAct 循环 + RAG 知识库检索 + MCP 工具调用**\n\n"
        f"已注册工具：`{'` `'.join(tool_names)}`"
    )
    chatbot = gr.Chatbot(height=480, show_copy_button=True)
    with gr.Row():
        msg = gr.Textbox(
            placeholder="试试：DocMind 的检索流程是怎样的？/ 北京天气怎么样？",
            scale=8,
            container=False,
        )
        clear = gr.Button("🗑️ 新对话", scale=1)

    msg.submit(respond_simple, [msg, chatbot], chatbot).then(
        lambda: "", None, msg
    )
    clear.click(reset_chat, None, chatbot)


if __name__ == "__main__":
    demo.launch()
