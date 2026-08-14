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
.gradio-container { max-width: 880px !important; margin: auto !important; padding: 10px 16px !important; }
body, .gradio-container, .main {
    background: linear-gradient(180deg, #f5f7fd 0%, #fbfcfe 100%) !important;
}

/* 顶部品牌卡片：白底 + 渐变点缀条，清爽不压迫 */
.dm-header {
    background: #ffffff; border: 1px solid #e9ecf7; border-radius: 14px;
    padding: 14px 20px; margin: 2px 0 12px; position: relative; overflow: hidden;
    box-shadow: 0 1px 3px rgba(15, 23, 42, .04);
}
.dm-header::before {
    content: ""; position: absolute; top: 0; left: 0; right: 0; height: 3px;
    background: linear-gradient(90deg, #818cf8, #c4b5fd, #93c5fd);
}
.dm-title { font-size: 19px; font-weight: 700; color: #1e293b; display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.dm-badge {
    font-size: 11px; font-weight: 600; color: #6366f1;
    background: #eef2ff; border: 1px solid #e0e7ff; padding: 2px 9px; border-radius: 999px;
}
.dm-sub { margin-top: 5px; font-size: 12.5px; color: #8a94a6; line-height: 1.55; }
.dm-chips { margin-top: 10px; display: flex; gap: 7px; flex-wrap: wrap; }
.dm-chip {
    font-family: ui-monospace, "SF Mono", Menlo, monospace; font-size: 11.5px;
    background: #f4f6ff; border: 1px solid #e4e9fb; color: #5b5bd6;
    padding: 3px 11px; border-radius: 999px;
}

/* 对话区：高度随视口自适应，保证一屏完整展示 */
#chatbot {
    border: none !important; background: transparent !important; box-shadow: none !important;
    height: calc(100dvh - 330px) !important; min-height: 340px;
}
.message { max-width: 86% !important; word-break: break-word !important; overflow-wrap: anywhere !important; }
.message.user {
    background: linear-gradient(135deg, #7c89f7, #96a0fa) !important;
    color: #fff !important; border-radius: 16px 16px 4px 16px !important;
    box-shadow: 0 1px 5px rgba(124, 137, 247, .28); font-size: 14.5px;
}
.message.bot {
    background: #ffffff !important; color: #293241 !important;
    border: 1px solid #eceef6 !important; border-radius: 16px 16px 16px 4px !important;
    box-shadow: 0 1px 4px rgba(30, 41, 59, .04); line-height: 1.8; font-size: 14.5px;
    padding: 14px 18px !important;
}
.message-row { margin-bottom: 8px !important; }

/* AI 回答内的思考过程区块：浅色底独立成块，与正文分层 */
.message.bot hr { border: none !important; border-top: 1px dashed #e4e8f2 !important; margin: 12px 0 10px !important; }
.message.bot blockquote {
    background: #f7f9fd !important; border-left: 3px solid #c7d2fe !important;
    margin: 6px 0 !important; padding: 8px 12px !important; border-radius: 8px !important;
    color: #5a6478 !important; font-size: 13px !important; line-height: 1.7 !important;
}

/* Markdown 内容排版 */
.message.bot h1, .message.bot h2, .message.bot h3, .message.bot h4 {
    font-size: 15px !important; font-weight: 700 !important; color: #1e293b !important;
    margin: 10px 0 6px !important;
}
.message.bot p { margin: 6px 0 !important; }
.message.bot ul, .message.bot ol { margin: 6px 0 !important; padding-left: 22px !important; }
.message.bot li { margin: 3px 0 !important; }
.message.bot code {
    background: #f1f3fb !important; color: #6d28d9 !important;
    padding: 1px 6px !important; border-radius: 5px !important; font-size: 12.5px !important;
}
.message.bot pre { background: #f7f9fd !important; border-radius: 8px !important; padding: 10px 12px !important; overflow-x: auto; }
.message.bot table { border-collapse: collapse; margin: 8px 0; font-size: 13px; }
.message.bot th, .message.bot td { border: 1px solid #e6e9f4; padding: 5px 10px; }
.message.bot strong { color: #1e293b; }

/* 隐藏对话区滚动条（保留滚动能力） */
#chatbot { scrollbar-width: none !important; }
#chatbot * { scrollbar-width: none !important; }
#chatbot ::-webkit-scrollbar { display: none !important; width: 0 !important; }

/* 长回复折叠：默认收起 + 底部渐隐遮罩 + 展开按钮 */
.message.bot.dm-collapsed {
    max-height: 360px !important; overflow: hidden !important; position: relative !important;
}
.message.bot.dm-collapsed::after {
    content: ""; position: absolute; bottom: 0; left: 0; right: 0; height: 84px;
    background: linear-gradient(180deg, rgba(255,255,255,0) 0%, #ffffff 82%);
    pointer-events: none;
}
.dm-toggle {
    position: absolute; bottom: 10px; left: 50%; transform: translateX(-50%); z-index: 3;
    background: #eef2ff; color: #6366f1; border: 1px solid #dbe2ff;
    border-radius: 999px; padding: 5px 16px; font-size: 12.5px; font-weight: 600;
    cursor: pointer; box-shadow: 0 1px 4px rgba(99,102,241,.18);
}
.dm-toggle:hover { background: #e0e7ff; }

/* 输入区 */
#input-box textarea {
    border-radius: 12px !important; border: 1.5px solid #e6e9f4 !important;
    box-shadow: 0 1px 3px rgba(30,41,59,.03); font-size: 14.5px; background: #fff;
}
#input-box textarea:focus { border-color: #a5b4fc !important; box-shadow: 0 0 0 3px rgba(129,140,248,.14) !important; }
#send-btn {
    background: linear-gradient(135deg, #7c89f7, #96a0fa) !important;
    border: none !important; border-radius: 12px !important; color: #fff !important;
    font-weight: 600; box-shadow: 0 2px 8px rgba(124,137,247,.3);
}
#send-btn:hover { filter: brightness(1.06); }
#clear-btn {
    background: transparent !important; border: none !important;
    box-shadow: none !important; color: #9aa3b5 !important;
    font-size: 13px !important; font-weight: 500 !important;
    min-width: 72px !important;
}
#clear-btn:hover { color: #6366f1 !important; background: #f4f6ff !important; }

/* 示例问题 */
.examples .example-btn { border-radius: 999px !important; font-size: 12.5px !important; }

/* 隐藏 Gradio 默认页脚 */
footer { display: none !important; }

/* 移动端适配 */
@media (max-width: 640px) {
    .gradio-container { padding: 8px 10px !important; }
    .dm-header { padding: 11px 14px; border-radius: 12px; }
    .dm-title { font-size: 17px; }
    .dm-sub { font-size: 11.5px; }
    .dm-chip { font-size: 10.5px; padding: 2px 8px; }
    .message { max-width: 94% !important; }
    /* 手机上隐藏示例区，确保对话+输入一屏完整 */
    #examples-area { display: none !important; }
    #chatbot { height: calc(100dvh - 290px) !important; min-height: 300px; }
    #send-btn, #clear-btn { min-width: 64px !important; }
}
"""

# 长回复折叠：周期性扫描 DOM，给超高的 AI 气泡加渐隐遮罩 + 展开/收起按钮
# （流式输出会反复重建 DOM，故用定时扫描；经 launch(head=...) 注入 <head>，
#  因 gr.HTML 会过滤 script、js 参数在 SSR 模式下不可靠）
FOLD_SCRIPT = """
<script>
(() => {
  if (window.__dmFoldInstalled) return;
  window.__dmFoldInstalled = true;
  const MAX_H = 360;
  function attachToggle(el) {
    const old = el.querySelector('.dm-toggle');
    if (old) old.remove();
    const btn = document.createElement('button');
    btn.className = 'dm-toggle';
    btn.textContent = '⌄ 展开全文';
    btn.onclick = (e) => {
      e.stopPropagation();
      const wasCollapsed = el.classList.contains('dm-collapsed');
      el.classList.toggle('dm-collapsed');
      el.dataset.dmExpanded = wasCollapsed ? '1' : '';
      btn.textContent = wasCollapsed ? '⌃ 收起' : '⌄ 展开全文';
    };
    el.appendChild(btn);
  }
  function scan() {
    document.querySelectorAll('.message.bot').forEach((el) => {
      if (el.scrollHeight > MAX_H) {
        if (!el.querySelector('.dm-toggle')) attachToggle(el);
        if (!el.classList.contains('dm-collapsed') && el.dataset.dmExpanded !== '1') {
          el.classList.add('dm-collapsed');
        }
      }
    });
  }
  scan();
  setInterval(scan, 600);
})();
</script>
"""

HEADER_HTML = f"""
<div class="dm-header">
  <div class="dm-title">
    <span>🧠 DocMind</span>
    <span class="dm-badge">手写 ReAct · RAG · MCP</span>
  </div>
  <div class="dm-sub">
    回答来源标注：<b>[来源: 文件名]</b> 知识库 · <b>【模型通识】</b> 库外兜底 · <b>🔧 工具</b> 实时数据
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
        show_label=False,
        placeholder="💬 在下方输入问题，试试下面的示例～",
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
        clear = gr.Button("↺ 新对话", scale=0, min_width=72, elem_id="clear-btn")

    gr.Examples(examples=EXAMPLES, inputs=msg, label="✨ 示例问题", examples_per_page=8, elem_id="examples-area")

    def submit(question: str, history: list):
        if not question.strip():
            yield history
            return
        yield from respond_simple(question, history)

    msg.submit(submit, [msg, chatbot], chatbot).then(lambda: "", None, msg)
    send.click(submit, [msg, chatbot], chatbot).then(lambda: "", None, msg)
    clear.click(reset_chat, None, chatbot)


if __name__ == "__main__":
    # Gradio 6：theme / css 移到 launch()；折叠脚本经 head 参数注入页面
    demo.launch(theme=theme, css=CUSTOM_CSS, head=FOLD_SCRIPT)
