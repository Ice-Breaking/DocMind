"""DocMind Web 界面（Gradio）：展示 Agent 思考过程 + 引用来源。

启动：python -m docmind.app
"""
import gradio as gr

from docmind.core import build_agent

print("[DocMind] 正在装配 Agent（加载知识库、连接 MCP Server）...")
agent, store, mcp_connections = build_agent()
tool_names = list(agent.registry.tools.keys())

# ---------------------------------------------------------------- 样式
CUSTOM_CSS = """
/* 整体背景与容器 */
.gradio-container { max-width: 880px !important; margin: auto !important; }
body, .gradio-container, .main { background: linear-gradient(180deg, #eef1fb 0%, #f8f9fd 60%) !important; }

/* 顶部品牌卡片 */
.dm-header {
    background: linear-gradient(135deg, #6366f1 0%, #8b5cf6 100%);
    border-radius: 18px; padding: 26px 28px; margin: 8px 0 18px;
    box-shadow: 0 8px 24px rgba(99, 102, 241, .28); color: #fff;
}
.dm-title { font-size: 26px; font-weight: 800; letter-spacing: .5px; }
.dm-sub { margin-top: 6px; font-size: 13.5px; opacity: .88; line-height: 1.6; }
.dm-chips { margin-top: 14px; display: flex; gap: 8px; flex-wrap: wrap; }
.dm-chip {
    font-family: ui-monospace, "SF Mono", Menlo, monospace; font-size: 12px;
    background: rgba(255,255,255,.16); border: 1px solid rgba(255,255,255,.35);
    padding: 4px 12px; border-radius: 999px; backdrop-filter: blur(4px);
}

/* 对话区 */
#chatbot { border: none !important; background: transparent !important; box-shadow: none !important; }
.message.user {
    background: linear-gradient(135deg, #6366f1, #8b5cf6) !important;
    color: #fff !important; border-radius: 18px 18px 4px 18px !important;
    box-shadow: 0 2px 10px rgba(99,102,241,.25);
}
.message.bot {
    background: #ffffff !important; color: #1f2937 !important;
    border: 1px solid #e8eaf3 !important; border-radius: 18px 18px 18px 4px !important;
    box-shadow: 0 2px 8px rgba(30, 41, 59, .05); line-height: 1.75;
}
.message-row { margin-bottom: 10px !important; }

/* 输入区 */
#input-box textarea {
    border-radius: 14px !important; border: 1.5px solid #e2e5f1 !important;
    box-shadow: 0 2px 8px rgba(30,41,59,.04); font-size: 15px;
}
#input-box textarea:focus { border-color: #8b5cf6 !important; box-shadow: 0 0 0 3px rgba(139,92,246,.15) !important; }
#send-btn {
    background: linear-gradient(135deg, #6366f1, #8b5cf6) !important;
    border: none !important; border-radius: 14px !important; color: #fff !important;
    font-weight: 600; box-shadow: 0 4px 12px rgba(99,102,241,.3);
}
#send-btn:hover { filter: brightness(1.08); }
#clear-btn {
    background: #ffffff !important; border: 1.5px solid #e2e5f1 !important;
    border-radius: 14px !important; color: #64748b !important;
}
#clear-btn:hover { border-color: #c7b9f5 !important; color: #7c3aed !important; }

/* 示例问题 */
.examples .example-btn { border-radius: 999px !important; font-size: 13px !important; }

/* 隐藏 Gradio 默认页脚 */
footer { display: none !important; }
"""

HEADER_HTML = f"""
<div class="dm-header">
  <div class="dm-title">🧠 DocMind</div>
  <div class="dm-sub">
    从零实现的知识助理 Agent —— 手写 ReAct 推理循环 · RAG 知识库检索 · MCP 工具调用<br>
    回答带来源标注：<b>[来源: 文件名]</b> = 知识库 · <b>【模型通识】</b> = 库外兜底 · <b>🔧 工具</b> = 实时数据
  </div>
  <div class="dm-chips">
    {''.join(f'<span class="dm-chip">{t}</span>' for t in tool_names)}
  </div>
</div>
"""

EXAMPLES = [
    ["什么是 RAG？它解决了什么问题？"],
    ["Agent 如何防止死循环？"],
    ["MCP 和 Function Calling 是什么关系？"],
    ["北京天气怎么样？"],
]


# ---------------------------------------------------------------- 交互逻辑
def _render_trace(lines: list[str]) -> str:
    return "\n\n".join(f"> {ln}" for ln in lines)


def respond_simple(question: str, history: list):
    """流式输出思考过程，最后给出完整回答（Gradio messages 格式）。
    任何异常都兼底为一条完整消息，避免界面停留在“思考中”"""
    trace_lines = []
    final_answer = ""
    user_msg = {"role": "user", "content": question}
    try:
        for step in agent.ask(question):
            if step.kind == "final":
                final_answer = step.text
            else:
                icon = "🔧" if step.kind == "tool_call" else "📥"
                trace_lines.append(f"{icon} {step.text}")
                partial = f"⏳ 思考中...\n\n{_render_trace(trace_lines)}"
                yield history + [user_msg, {"role": "assistant", "content": partial}]
    except Exception as e:  # noqa: BLE001
        final_answer = f"⚠️ 处理过程中出现异常：{e}\n请重试，若持续失败请检查 API 额度与网络。"
    if not final_answer:
        final_answer = "⚠️ 未获得模型回复，请重试。"
    full = final_answer
    if trace_lines:
        full += "\n\n---\n**🧠 Agent 思考过程：**\n\n" + "\n\n".join(trace_lines)
    yield history + [user_msg, {"role": "assistant", "content": full}]


def reset_chat():
    agent.reset()
    return []


# ---------------------------------------------------------------- 界面
theme = gr.themes.Soft(
    primary_hue="indigo",
    secondary_hue="violet",
    font=[gr.themes.GoogleFont("Inter"), "system-ui", "PingFang SC", "sans-serif"],
)

with gr.Blocks(title="DocMind · 知识助理 Agent") as demo:
    gr.HTML(HEADER_HTML)

    chatbot = gr.Chatbot(
        height=520,
        show_label=False,
        placeholder="💬 在下方输入问题，试试右侧的示例～",
        elem_id="chatbot",
    )

    with gr.Row(elem_id="input-row"):
        msg = gr.Textbox(
            placeholder="输入你的问题，回车发送…",
            scale=8,
            container=False,
            lines=1,
            elem_id="input-box",
        )
        send = gr.Button("发送", scale=1, min_width=80, elem_id="send-btn")
        clear = gr.Button("🗑️ 新对话", scale=1, min_width=110, elem_id="clear-btn")

    gr.Examples(examples=EXAMPLES, inputs=msg, label="✨ 示例问题", examples_per_page=8)

    def submit(question: str, history: list):
        if not question.strip():
            yield history
            return
        yield from respond_simple(question, history)

    msg.submit(submit, [msg, chatbot], chatbot).then(lambda: "", None, msg)
    send.click(submit, [msg, chatbot], chatbot).then(lambda: "", None, msg)
    clear.click(reset_chat, None, chatbot)


if __name__ == "__main__":
    # Gradio 6：theme / css 从 Blocks 构造器移到了 launch()
    demo.launch(theme=theme, css=CUSTOM_CSS)
