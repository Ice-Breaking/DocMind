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
/* 整体背景（容器尺寸/布局规则在 LAYOUT_CSS 中经 head 注入） */
body, .gradio-container, .main {
    background: linear-gradient(180deg, #f5f7fd 0%, #fbfcfe 100%) !important;
}

/* 顶部品牌卡片：白底 + 渐变点缀条，清爽不压迫 */
.dm-header {
    background: #ffffff; border: 1px solid #e9ecf7; border-radius: 14px;
    padding: 14px 20px; margin: 0; position: relative; overflow: hidden;
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

/* 对话区：去掉边框背景，高度由全局弹性布局接管（LAYOUT_CSS），内部消息列表可滚动 */
#chatbot {
    border: none !important; background: transparent !important; box-shadow: none !important;
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
    background: #ffffff !important; border: 1.5px solid #e6e9f4 !important;
    border-radius: 12px !important; color: #6366f1 !important;
    max-width: 46px !important; min-width: 46px !important; padding: 0 !important;
    font-size: 20px !important; font-weight: 400 !important; box-shadow: 0 1px 3px rgba(30,41,59,.04);
}
#clear-btn:hover { border-color: #a5b4fc !important; background: #f4f6ff !important; }

/* 示例问题：PC 端自动换行，移动端单行横向滑动，任何分辨率都可见；不显示标题 */
#examples-area .gallery-item { border-radius: 999px !important; font-size: 12.5px !important; white-space: nowrap; }
#examples-area .label { display: none !important; }

/* 输入区：+ 按钮与输入框垂直居中对齐 */
#input-row { align-items: center !important; }
#clear-btn { align-self: center !important; }

/* 隐藏 Gradio 默认页脚 */
footer { display: none !important; }
"""

# 全局布局 CSS：必须经 launch(head=...) 注入。
# 原因：launch(css=) 的样式会被 Gradio 重写并限定到 .contain 作用域内，
# 带 .gradio-container 前缀的选择器（锁定容器、块排序等）会永远失配。
LAYOUT_CSS = """
/* ============ 全局：一屏弹性布局（PC/移动端一致） ============ */
gradio-app, .gradio-container {
    height: 100dvh !important; max-height: 100dvh !important;
    overflow: hidden !important; box-sizing: border-box !important;
    width: 100% !important; max-width: 880px !important; margin: 0 auto !important;
}
body { overflow-x: hidden !important; }
/* 逐层贯通 flex + 宽度护栏：min-width:0 防止内容把布局撑宽 */
.gradio-container .main,
.gradio-container .wrap,
.gradio-container main.contain,
.gradio-container .column {
    height: 100% !important; display: flex !important;
    flex-direction: column !important; min-height: 0 !important;
    min-width: 0 !important; max-width: 100% !important; box-sizing: border-box;
}
.gradio-container .main { padding: 0 !important; }
.gradio-container { padding: 10px 16px !important; }
.gradio-container .column { gap: 8px !important; }
.gradio-container .column > .block,
.gradio-container .column > .row {
    flex: 0 0 auto; min-width: 0 !important; max-width: 100% !important;
}
/* 块顺序：头部 → 示例区 → 对话区 → 输入区（PC/移动端一致）。
   examples/chatbot/input 的 elem_id 直接挂在块自身，按 ID 直接定位 */
.gradio-container .column > .block:has(.dm-header) { order: 1; }
.gradio-container .column > #examples-area { order: 2; }
.gradio-container .column > #chatbot { order: 3; }
.gradio-container .column > #input-row { order: 4; }
/* 对话区拉伸填满剩余空间，消息在内部滚动 */
.gradio-container .column > #chatbot {
    flex: 1 1 auto !important; min-height: 160px !important; height: auto !important;
    display: flex !important; flex-direction: column !important; min-width: 0 !important;
}
.gradio-container .column > #chatbot > * { flex: 1 1 auto !important; min-height: 0 !important; min-width: 0 !important; }
/* 长内容防溢出：气泡内换行兜底，代码块内部横滑，不撑宽页面 */
.message { overflow-wrap: anywhere !important; word-break: break-word !important; min-width: 0 !important; }
.message.bot { overflow: hidden !important; }
.message.bot pre { max-width: 100% !important; overflow-x: auto !important; white-space: pre-wrap; word-break: break-all; }
.message.bot table { display: block; max-width: 100%; overflow-x: auto; }

/* ============ 移动端（≤ 640px，基准 375×667）：压缩间距与字号 ============ */
@media (max-width: 640px) {
    .gradio-container { padding: 6px 8px !important; }
    .gradio-container .column { gap: 6px !important; }
    /* 头部压缩 */
    .dm-header { padding: 8px 12px; border-radius: 12px; margin: 0; }
    .dm-title { font-size: 16px; }
    .dm-sub { font-size: 10px; margin-top: 2px; }
    .dm-chips { margin-top: 6px; gap: 4px; flex-wrap: nowrap; overflow: hidden; }
    .dm-chip { font-size: 9px; padding: 1px 6px; }
    .message { max-width: 94% !important; }
    /* 示例区：无标题，单行横滑 */
    #examples-area { margin: 0 !important; }
    #examples-area .gallery {
        display: flex !important; flex-wrap: nowrap !important;
        overflow-x: auto !important; scrollbar-width: none !important;
    }
    #examples-area .gallery::-webkit-scrollbar { display: none !important; }
    #examples-area .gallery-item { flex: 0 0 auto !important; font-size: 11px !important; padding: 4px 10px !important; }
    /* 输入区：+ 按钮与输入框严格垂直居中 */
    #input-row { gap: 6px !important; display: flex !important; align-items: center !important; }
    #clear-btn { max-width: 42px !important; min-width: 42px !important; height: 42px !important; padding: 0 !important; }
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
        clear = gr.Button("+", scale=0, elem_id="clear-btn")
        msg = gr.Textbox(
            placeholder="输入你的问题，回车发送…",
            scale=8,
            container=False,
            lines=1,
            elem_id="input-box",
        )
        send = gr.Button("发送", scale=1, min_width=80, elem_id="send-btn")

    gr.Examples(examples=EXAMPLES, inputs=msg, label=None, examples_per_page=8, elem_id="examples-area")

    def submit(question: str, history: list):
        if not question.strip():
            yield history
            return
        yield from respond_simple(question, history)

    msg.submit(submit, [msg, chatbot], chatbot).then(lambda: "", None, msg)
    send.click(submit, [msg, chatbot], chatbot).then(lambda: "", None, msg)
    clear.click(reset_chat, None, chatbot)


if __name__ == "__main__":
    # Gradio 6：theme / css 移到 launch()；折叠脚本与全局布局样式经 head 注入
    # （head 注入的内容不会被 Gradio 的 CSS 作用域重写）
    demo.launch(theme=theme, css=CUSTOM_CSS, head=FOLD_SCRIPT + f"<style>{LAYOUT_CSS}</style>")
