"""DocMind Web 界面（Gradio）：展示 Agent 思考过程 + 引用来源。

启动：python -m docmind.app
监听地址/端口可用环境变量 GRADIO_SERVER_NAME / GRADIO_SERVER_PORT 覆盖
（Docker 部署时容器内需要 0.0.0.0）。
"""
import os

import gradio as gr

from docmind import config
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

/* 示例问题：芯片按钮点击直发，常驻可见 */
#examples-area { gap: 6px !important; flex-wrap: wrap !important; }
#examples-area button.ex-chip {
    border-radius: 999px !important; font-size: 12.5px !important; white-space: nowrap;
    background: #ffffff !important; border: 1px solid #e8ebf6 !important;
    color: #475569 !important; box-shadow: 0 1px 2px rgba(30, 41, 59, .03) !important;
    padding: 6px 14px !important;
}
#examples-area button.ex-chip:hover {
    border-color: #c7d2fe !important; color: #6366f1 !important; background: #f4f6ff !important;
}

/* 输入区：+ 按钮与输入框垂直居中对齐 */
#input-row { align-items: center !important; }
#clear-btn { align-self: center !important; }

/* 隐藏 Gradio 默认页脚 */
footer { display: none !important; }

/* 追问建议区：淡紫底 + 圆角按钮 */
.dm-suggestions {
    margin-top: 14px !important; padding: 10px 12px !important;
    background: #f7f9fd !important; border-radius: 10px !important;
    border: 1px solid #eef2ff !important;
}
.dm-suggest-title {
    font-size: 12.5px !important; color: #6366f1 !important;
    font-weight: 600 !important; margin-bottom: 8px !important;
}
.dm-suggest-btn {
    display: block !important; width: 100% !important;
    text-align: left !important; margin: 4px 0 !important;
    background: #ffffff !important; border: 1px solid #e4e9fb !important;
    color: #475569 !important; border-radius: 8px !important;
    padding: 8px 12px !important; font-size: 13px !important;
    cursor: pointer !important; transition: all 0.15s !important;
}
.dm-suggest-btn:hover {
    background: #f4f6ff !important; border-color: #c7d2fe !important;
    color: #6366f1 !important;
}
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
.gradio-container .main > .wrap,
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
/* 对话区内部滚动链：空态遮罩不占高，.wrapper/.bubble-wrap 接管滚动，长回复不截断 */
.gradio-container .column > #chatbot > .wrap { flex: 0 0 auto !important; height: auto !important; }
.gradio-container .column > #chatbot .wrapper {
    display: flex !important; flex-direction: column !important;
    flex: 1 1 auto !important; min-height: 0 !important;
}
.gradio-container .column > #chatbot .bubble-wrap {
    flex: 1 1 auto !important; min-height: 0 !important;
    height: auto !important; overflow-y: auto !important;
}
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
    /* 示例区：单列全宽芯片，点击直发，触控友好 */
    #examples-area { margin: 0 !important; flex-direction: column !important; gap: 6px !important; }
    #examples-area button.ex-chip {
        width: 100% !important; text-align: left !important;
        justify-content: flex-start !important; padding: 8px 12px !important;
        font-size: 12px !important; border-radius: 10px !important;
    }
    #examples-area button.ex-chip:active {
        background: #f4f6ff !important; border-color: #c7d2fe !important;
    }
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
<script>
// 引导追问按钮：扫描 <!--suggest:问题--> 标记，渲染为可点击按钮
(() => {
  if (window.__dmSuggestInstalled) return;
  window.__dmSuggestInstalled = true;
  const SUGGEST_RE = /<!--suggest:([^>]+)-->/g;
  function renderSuggestions() {
    document.querySelectorAll('.message.bot').forEach((el) => {
      if (el.dataset.dmSuggestRendered) return;
      const html = el.innerHTML;
      if (!html.includes('<!--suggest:')) return;
      const suggestions = [];
      let m;
      while ((m = SUGGEST_RE.exec(html)) !== null) {
        suggestions.push(m[1]);
      }
      if (!suggestions.length) return;
      // 移除原始标记
      el.innerHTML = html.replace(/<!--suggest:[^>]+-->/g, '');
      // 渲染追问按钮区
      const wrap = document.createElement('div');
      wrap.className = 'dm-suggestions';
      const title = document.createElement('div');
      title.className = 'dm-suggest-title';
      title.textContent = ' 你可能还想问：';
      wrap.appendChild(title);
      suggestions.forEach((q) => {
        const btn = document.createElement('button');
        btn.className = 'dm-suggest-btn';
        btn.textContent = q;
        btn.onclick = () => {
          const input = document.querySelector('#input-box textarea');
          if (input) {
            input.value = q;
            input.dispatchEvent(new Event('input', { bubbles: true }));
            const sendBtn = document.querySelector('#send-btn');
            if (sendBtn) sendBtn.click();
          }
        };
        wrap.appendChild(btn);
      });
      el.appendChild(wrap);
      el.dataset.dmSuggestRendered = '1';
    });
  }
  renderSuggestions();
  setInterval(renderSuggestions, 800);
})();
</script>
"""

HEADER_HTML = f"""
<div class="dm-header">
  <div class="dm-title">
    <span>🧠 DocMind</span>
    <span class="dm-badge">{'手写 ReAct · RAG · MCP' + (' · 深度思考' if config.ENABLE_THINKING else '')}</span>
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
    "💡 什么是 RAG？它解决了什么问题？",
    "🛡️ Agent 如何防止死循环？",
    "🔌 MCP 和 Function Calling 是什么关系？",
    "🌤️ 北京天气怎么样？",
]


# ---------------------------------------------------------------- 交互逻辑
def _render_trace(lines: list[str]) -> str:
    return "\n\n".join(f"> {ln}" for ln in lines)


def respond_simple(question: str, history: list):
    """流式渲染：模型思维链实时展示 → 逐 token 打字效果 → 工具轨迹。
    任何异常都兼底为一条完整消息，避免界面停留在“思考中”"""
    trace_lines = []
    reasoning_parts = []   # 模型真实思维链（reasoning_content）增量累积
    final_answer = ""
    partial = ""           # 流式累积的回答正文
    thinking = True        # 是否处于“深度思考中”状态（收到正文 token 即结束）
    user_msg = {"role": "user", "content": question}

    def reasoning_quote() -> str:
        """思维链渲染：思考中实时全文；完成后截断，避免淹没正文"""
        if not reasoning_parts:
            return ""
        text = "".join(reasoning_parts)
        if not thinking and len(text) > 300:
            text = text[:300] + "…"
        return f"\n> 💭 **模型思维链**：{text}\n"

    def render() -> str:
        head = "🤔 深度思考中…" if thinking else "<sub>✓ 深度思考已完成</sub>\n\n"
        tail = ""
        if trace_lines:
            tail = "\n\n---\n**🧠 Agent 思考过程：**\n\n" + "\n\n".join(trace_lines)
        return head + reasoning_quote() + partial + tail

    try:
        yield history + [user_msg, {"role": "assistant", "content": "🤔 深度思考中…"}]
        for step in agent.ask(question):
            if step.kind == "token":
                thinking = False
                partial += step.text
                yield history + [user_msg, {"role": "assistant", "content": render()}]
            elif step.kind == "thinking":
                reasoning_parts.append(step.text)
                yield history + [user_msg, {"role": "assistant", "content": render()}]
            elif step.kind == "final":
                final_answer = step.text
            else:
                thinking = False
                icon = "🔧" if step.kind == "tool_call" else "📥"
                trace_lines.append(f"{icon} {step.text}")
                yield history + [user_msg, {"role": "assistant", "content": render()}]
    except Exception as e:  # noqa: BLE001
        final_answer = f"⚠️ 处理过程中出现异常：{e}\n请重试，若持续失败请检查 API 额度与网络。"
    if not final_answer:
        final_answer = partial or "⚠️ 未获得模型回复，请重试。"
    thinking = False   # 思考结束，让思维链按截断策略渲染
    full = f"<sub>✓ 深度思考已完成</sub>\n\n{reasoning_quote()}{final_answer}"
    if trace_lines:
        full += "\n\n---\n** Agent 思考过程：**\n\n" + "\n\n".join(trace_lines)
    # 追问建议：用特殊标记 <!--suggest:问题--> 让 JS 渲染为可点击按钮
    suggestions = [
        "能详细解释一下吗？",
        "有哪些实际应用场景？",
        "与其他技术相比有什么优势？",
    ]
    for s in suggestions:
        full += f"\n<!--suggest:{s}-->"
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

    # 示例问题芯片：点击直接发送（常驻可见，对话中也可快速追问）
    with gr.Row(elem_id="examples-area"):
        example_buttons = [gr.Button(ex, elem_classes="ex-chip", scale=1) for ex in EXAMPLES]

    # 注意：必须绑定真正的 generator function（yield 在函数体内）。
    # Gradio 6 用 isgeneratorfunction 识别流式，lambda 返回 generator 对象不满足，
    # 会被当普通值 postprocess 而报 messages format 错误。
    def make_example_handler(ex):
        def handler(history):
            yield from respond_simple(ex, history)
        return handler

    def submit(question: str, history: list):
        if not question.strip():
            yield history
            return
        yield from respond_simple(question, history)

    msg.submit(submit, [msg, chatbot], chatbot).then(lambda: "", None, msg)
    send.click(submit, [msg, chatbot], chatbot).then(lambda: "", None, msg)
    clear.click(reset_chat, None, chatbot)
    for btn, ex in zip(example_buttons, EXAMPLES):
        btn.click(make_example_handler(ex), inputs=chatbot, outputs=chatbot)


if __name__ == "__main__":
    # Gradio 6：theme / css 移到 launch()；折叠脚本与全局布局样式经 head 注入
    # （head 注入的内容不会被 Gradio 的 CSS 作用域重写）
    demo.launch(
        theme=theme,
        css=CUSTOM_CSS,
        head=FOLD_SCRIPT + f"<style>{LAYOUT_CSS}</style>",
        server_name=os.getenv("GRADIO_SERVER_NAME", "127.0.0.1"),
        server_port=int(os.getenv("GRADIO_SERVER_PORT", "7860")),
    )
