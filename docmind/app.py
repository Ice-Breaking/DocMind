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

/* Mermaid 图表容器：Gradio 将 ```mermaid 代码块渲染为 div.mermaid，
   这里给它白底 + 圆角 + 轻阴影，与气泡风格一致；渲染前显示原始代码，渲染后为 SVG */
.message.bot div.mermaid {
    margin: 10px 0 !important; padding: 12px !important;
    background: #ffffff !important; border: 1px solid #e9ecf7 !important;
    border-radius: 10px !important; box-shadow: 0 1px 4px rgba(30,41,59,.04) !important;
    overflow-x: auto !important; text-align: center !important;
}
.message.bot div.mermaid svg { max-width: 100% !important; height: auto !important; display: inline-block !important; }

/* 流式生成中：末尾闪烁光标 + 顶部"生成中"徽标（由消息稳定性检测维护 dm-streaming 类） */
.message.bot.dm-streaming [data-testid="bot"]::after {
    content: "▍"; color: #6366f1; margin-left: 1px;
    animation: dm-blink 1s steps(2, start) infinite;
}
.message.bot.dm-streaming::before {
    content: "⏳ 生成中…"; display: block;
    font-size: 11px; color: #6366f1; opacity: .85; margin-bottom: 4px;
}
@keyframes dm-blink { 50% { opacity: 0; } }

/* 回到底部悬浮按钮：用户上滑阅读期间有新内容时出现 */
.dm-to-bottom {
    position: fixed; right: 30px; bottom: 96px; z-index: 999;
    display: none; align-items: center; gap: 4px;
    padding: 7px 14px !important; border-radius: 999px !important;
    background: #6366f1 !important; color: #fff !important; border: none !important;
    font-size: 12px !important; box-shadow: 0 4px 14px rgba(99,102,241,.35) !important;
    cursor: pointer;
}
.dm-to-bottom.dm-show { display: flex; }

/* 引用可点击：[来源: 文件名 · 第N页] → 点击打开原文预览弹窗 */
.dm-source-link {
    color: #6366f1 !important; cursor: pointer;
    text-decoration: underline dotted !important; text-underline-offset: 2px;
    border-radius: 4px; transition: background .15s;
}
.dm-source-link:hover { background: #eef2ff !important; }

/* 原文预览弹窗 */
#dm-preview-overlay {
    display: none; position: fixed; inset: 0; z-index: 1000;
    background: rgba(15,23,42,.45); align-items: center; justify-content: center;
}
.dm-preview-modal {
    width: min(860px, 92vw); height: min(86vh, 900px);
    background: #fff; border-radius: 12px; overflow: hidden;
    display: flex; flex-direction: column;
    box-shadow: 0 20px 60px rgba(15,23,42,.25);
}
.dm-preview-head {
    display: flex; align-items: center; gap: 10px;
    padding: 10px 14px; border-bottom: 1px solid #e9ecf7; flex: none;
}
.dm-preview-title { font-weight: 600; font-size: 13px; flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.dm-preview-pager { display: flex; align-items: center; gap: 6px; font-size: 12px; color: #475569; }
.dm-preview-zoom { display: flex; align-items: center; gap: 4px; font-size: 12px; color: #475569; }
.dm-pv-zoom-label { min-width: 42px; text-align: center; }
.dm-pv-page-input { width: 48px; text-align: center; border: 1px solid #e2e8f0; border-radius: 6px; padding: 3px 4px; font-size: 12px; color: #334155; }
.dm-pv-btn, .dm-pv-close { border: none; background: #f1f5f9; border-radius: 6px; padding: 4px 10px; cursor: pointer; font-size: 13px; color: #334155; }
.dm-pv-btn:hover, .dm-pv-close:hover { background: #e2e8f0; }
.dm-preview-body { flex: 1; overflow: auto; padding: 12px; text-align: center; background: #f8fafc; }
.dm-preview-body canvas { box-shadow: 0 2px 10px rgba(15,23,42,.12); background: #fff; }
.dm-preview-text { text-align: left; white-space: pre-wrap; font-size: 12px; line-height: 1.7; background: #fff; padding: 14px; border-radius: 8px; margin: 0; }
.dm-preview-loading, .dm-preview-error { color: #64748b; font-size: 13px; padding: 24px; }
.dm-preview-error { color: #ef4444; }
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
// 消息稳定性检测（共享基础设施）：
// MutationObserver 记录每条 bot 消息的最近内容变化时间，暴露 window.__dmIsStable(el)。
// 用途：① 流式生成指示（dm-streaming 类 → 光标/徽标）
//       ② fold/suggest/mermaid 等后处理的"稳定性闸门"（流式中不做任何 DOM 加工，
//          既避免半成品被误当成终态，也避免流式期间高频读写 DOM 造成滚动卡顿）
(() => {
  if (window.__dmMsgStateInstalled) return;
  window.__dmMsgStateInstalled = true;
  const STABLE_MS = 1200;
  const lastChange = new WeakMap();

  function mark(el, t) {
    if (el && el.classList && el.classList.contains("message") && el.classList.contains("bot")) {
      lastChange.set(el, t);
    }
  }

  const obs = new MutationObserver((muts) => {
    const now = Date.now();
    for (const m of muts) {
      // 变化的目标节点：向上找所属 bot 消息
      const t = m.target;
      if (t && t.nodeType === 1) {
        if (t.matches && t.matches(".message.bot")) mark(t, now);
        else if (t.closest) mark(t.closest(".message.bot"), now);
      } else if (t && t.nodeType === 3 && t.parentElement && t.parentElement.closest) {
        mark(t.parentElement.closest(".message.bot"), now);
      }
      // 新增节点：本身或其子孙是 bot 消息
      if (m.addedNodes) {
        for (const n of m.addedNodes) {
          if (n.nodeType !== 1) continue;
          mark(n, now);
          if (n.querySelectorAll) n.querySelectorAll(".message.bot").forEach((x) => mark(x, now));
        }
      }
    }
  });
  obs.observe(document.body, { childList: true, subtree: true, characterData: true });

  window.__dmIsStable = (el) => {
    const t = lastChange.get(el);
    return !t || Date.now() - t > STABLE_MS;
  };

  // 维护 dm-streaming 类：CSS 据此渲染末尾闪烁光标 + 顶部"生成中"徽标
  function refresh() {
    document.querySelectorAll(".message.bot").forEach((el) => {
      el.classList.toggle("dm-streaming", !window.__dmIsStable(el));
    });
  }
  refresh();
  setInterval(refresh, 300);
})();
</script>
<script>
// 智能滚动（P0）：关闭 Gradio 原生 autoscroll 后由此接管
// 规则：用户贴底（距底 ≤100px）→ 新内容自动跟随滚底；
//       用户上滑阅读 → 绝不打扰，仅显示"↓ 回到底部"悬浮按钮
(() => {
  if (window.__dmScrollInstalled) return;
  window.__dmScrollInstalled = true;
  const STICK_PX = 100;
  let scroller = null;
  let stick = true;

  function findScroller() {
    if (scroller && document.body.contains(scroller)) return scroller;
    scroller = document.querySelector("#chatbot .bubble-wrap");
    return scroller;
  }

  // 悬浮"回到底部"按钮
  const fab = document.createElement("button");
  fab.className = "dm-to-bottom";
  fab.textContent = "↓ 回到底部";
  fab.addEventListener("click", () => {
    const sc = findScroller();
    if (!sc) return;
    stick = true;
    sc.scrollTo({ top: sc.scrollHeight, behavior: "smooth" });
    fab.classList.remove("dm-show");
  });
  document.body.appendChild(fab);

  const isAtBottom = (sc) => sc.scrollHeight - sc.scrollTop - sc.clientHeight <= STICK_PX;

  function onScroll() {
    const sc = findScroller();
    if (!sc) return;
    const atBottom = isAtBottom(sc);
    const delta = sc.scrollTop - (onScroll._last || 0);
    onScroll._last = sc.scrollTop;
    if (atBottom) {
      stick = true;
    } else if (delta < -4) {
      // 明确上滑：用户想阅读历史，停止跟随
      stick = false;
    } else if (delta > 60) {
      // 大幅下跳（折叠/展开引起的内容高度变化），以当前位置为准
      stick = false;
    }
    if (stick) fab.classList.remove("dm-show");
  }
  document.addEventListener("scroll", (e) => {
    const sc = findScroller();
    if (sc && (e.target === sc || sc.contains(e.target))) onScroll();
  }, true);

  function toBottom() {
    const sc = findScroller();
    if (sc) sc.scrollTop = sc.scrollHeight;
  }

  // 内容变化：贴底 → 跟随；上滑中 → 显示回到底部按钮
  let rafPending = false;
  const obs = new MutationObserver(() => {
    if (rafPending) return;
    rafPending = true;
    requestAnimationFrame(() => {
      rafPending = false;
      if (stick) toBottom();
      else fab.classList.add("dm-show");
    });
  });
  obs.observe(document.body, { childList: true, subtree: true, characterData: true });

  // 用户发送新问题（新 .message.user 出现）：无条件回到底部并恢复跟随
  const userObs = new MutationObserver((muts) => {
    for (const m of muts) {
      for (const n of m.addedNodes) {
        if (n.nodeType === 1 && n.querySelector && n.querySelector(".message.user")) {
          stick = true;
          fab.classList.remove("dm-show");
          requestAnimationFrame(toBottom);
          return;
        }
      }
    }
  });
  userObs.observe(document.body, { childList: true, subtree: true });
})();
</script>
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
    el.appendChild(btn);
  }
  // 事件委托：追问脚本的 el.innerHTML= 重建会把按钮节点换掉、丢失 onclick，
  // 故在 document 层统一监听点击，任何 DOM 重建后按钮依然可用
  document.addEventListener('click', (e) => {
    const btn = e.target.closest('.dm-toggle');
    if (!btn) return;
    e.stopPropagation();
    const el = btn.closest('.message.bot');
    if (!el) return;
    const wasCollapsed = el.classList.contains('dm-collapsed');
    el.classList.toggle('dm-collapsed');
    el.dataset.dmExpanded = wasCollapsed ? '1' : '';
    btn.textContent = wasCollapsed ? '⌃ 收起' : '⌄ 展开全文';
  });
  function scan() {
    document.querySelectorAll('.message.bot').forEach((el) => {
      if (window.__dmIsStable && !window.__dmIsStable(el)) return; // 流式中不加工
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
// 引导追问按钮：对每条「已完成」的 bot 消息追加固定追问建议（点击自动填入输入框并发送）。
// 历史方案是把 <!--suggest:...--> 标记写进回答再前端替换，但标记经 markdown 渲染
// 常被转义或被代码围栏吞没导致失效；追问建议本身是固定列表，故改为无标记纯追加，
// 且不再重建 innerHTML（从根上避免与折叠按钮等 DOM 加工的冲突）。
(() => {
  if (window.__dmSuggestInstalled) return;
  window.__dmSuggestInstalled = true;
  const SUGGESTIONS = [
    "能详细解释一下吗？",
    "有哪些实际应用场景？",
    "与其他技术相比有什么优势？",
  ];
  function renderSuggestions() {
    document.querySelectorAll('.message.bot').forEach((el) => {
      if (el.dataset.dmSuggestRendered) return;
      // 稳定性闸门：流式中不追加，避免用户误以为回答已完成
      if (window.__dmIsStable && !window.__dmIsStable(el)) return;
      const wrap = document.createElement('div');
      wrap.className = 'dm-suggestions';
      const title = document.createElement('div');
      title.className = 'dm-suggest-title';
      title.textContent = ' 你可能还想问：';
      wrap.appendChild(title);
      SUGGESTIONS.forEach((q) => {
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
<script>
// Mermaid 图表渲染：Gradio 会把 ```mermaid 代码块输出为 <div class="mermaid">原始代码</div>，
// 这里用 mermaid.run 对这些容器「原地渲染」。相比自行抽取围栏再 innerHTML 替换，
// 这种方式不与 Gradio/追问脚本的 DOM 重建冲突，也不会产生重复图表。
(() => {
  if (window.__dmMermaidScriptInstalled) return;
  window.__dmMermaidScriptInstalled = true;

  function ensureInited() {
    if (!window.mermaid || window.__dmMermaidInited) return;
    window.__dmMermaidInited = true;
    window.mermaid.initialize({ startOnLoad: false, theme: "default", securityLevel: "loose" });
  }

  function scan() {
    if (!window.mermaid) return;
    ensureInited();
    // 找出 Gradio 已生成、但尚未被 mermaid 处理的 .mermaid 容器（流式结束后才稳定出现）
    const nodes = Array.from(document.querySelectorAll(".message.bot div.mermaid")).filter(
      (n) => n.getAttribute("data-processed") !== "true" && n.textContent.trim()
        && (!window.__dmIsStable || window.__dmIsStable(n.closest(".message.bot")))
    );
    if (!nodes.length) return;
    // mermaid.run 原地渲染并自动打上 data-processed 标记，天然去重、无重复图表
    window.mermaid.run({ nodes: nodes }).catch(() => {});
  }
  scan();
  setInterval(scan, 600);
})();
</script>
<script>
// 文档预览（引用溯源直达）：回答里的 [来源: 文件名 · 第N页] 渲染为可点击链接，
// 点击弹窗预览原文——PDF 用 pdf.js 定位到页；md/txt 展示正文；
// docx 优先 LibreOffice 转 PDF 复用 PDF 通道（?as=pdf），未安装降级文本预览（?as=text）。
// 链接化只操作文本节点（TreeWalker），不碰 innerHTML——不会破坏 aria-label 属性、
// 不与 fold/suggest 的 DOM 重建冲突；点击用 document 级事件委托。
(() => {
  if (window.__dmPreviewInstalled) return;
  window.__dmPreviewInstalled = true;
  const SOURCE_RE = /\[来源: ([^\]\\n]+?\.(?:md|txt|pdf|docx))(?: · 第(\d+)页)?\]/g;
  const SOURCE_TEST = /\[来源: [^\]\\n]+?\.(?:md|txt|pdf|docx)(?: · 第\d+页)?\]/;
  let pdfDoc = null, pdfPage = 1, pdfTotal = 0, pdfRendering = false;
  let zoomFactor = 1, pendingPage = null;

  // ---------- 引用链接化（仅稳定消息；文本节点级替换） ----------
  function linkify(el) {
    if (window.__dmIsStable && !window.__dmIsStable(el)) return;
    const walker = document.createTreeWalker(el, NodeFilter.SHOW_TEXT, {
      acceptNode(node) {
        const parent = node.parentElement;
        if (!parent) return NodeFilter.FILTER_REJECT;
        if (parent.closest(".dm-source-link, .dm-suggestions, .dm-toggle, script, style")) {
          return NodeFilter.FILTER_REJECT;
        }
        return SOURCE_TEST.test(node.data) ? NodeFilter.FILTER_ACCEPT : NodeFilter.FILTER_SKIP;
      },
    });
    const targets = [];
    while (walker.nextNode()) targets.push(walker.currentNode);
    targets.forEach((node) => {
      const frag = document.createDocumentFragment();
      const text = node.data;
      let last = 0, m;
      SOURCE_RE.lastIndex = 0;
      while ((m = SOURCE_RE.exec(text)) !== null) {
        frag.appendChild(document.createTextNode(text.slice(last, m.index)));
        const span = document.createElement("span");
        span.className = "dm-source-link";
        span.dataset.file = m[1];
        if (m[2]) span.dataset.page = m[2];
        span.title = "点击预览原文" + (m[2] ? "（第 " + m[2] + " 页）" : "");
        span.textContent = "[📄 来源: " + m[1] + (m[2] ? " · 第" + m[2] + "页" : "") + "]";
        frag.appendChild(span);
        last = m.index + m[0].length;
      }
      frag.appendChild(document.createTextNode(text.slice(last)));
      node.parentNode.replaceChild(frag, node);
    });
  }
  function scanLinks() {
    document.querySelectorAll(".message.bot").forEach(linkify);
  }
  scanLinks();
  setInterval(scanLinks, 800);

  // ---------- 弹窗 ----------
  function ensureModal() {
    let ov = document.getElementById("dm-preview-overlay");
    if (ov) return ov;
    ov = document.createElement("div");
    ov.id = "dm-preview-overlay";
    const modal = document.createElement("div");
    modal.className = "dm-preview-modal";
    const head = document.createElement("div");
    head.className = "dm-preview-head";
    head.innerHTML = '<span class="dm-preview-title"></span>'
      + '<span class="dm-preview-zoom">'
      + '<button class="dm-pv-btn" data-act="zoom-out" title="缩小">−</button>'
      + '<span class="dm-pv-zoom-label">100%</span>'
      + '<button class="dm-pv-btn" data-act="zoom-in" title="放大">＋</button></span>'
      + '<span class="dm-preview-pager" style="display:none">'
      + '<button class="dm-pv-btn" data-act="prev">‹ 上一页</button>'
      + '<input class="dm-pv-page-input" type="number" min="1" value="1">'
      + '<span class="dm-pv-total">/ 1</span>'
      + '<button class="dm-pv-btn" data-act="next">下一页 ›</button></span>'
      + '<button class="dm-pv-close">✕ 关闭</button>';
    const body = document.createElement("div");
    body.className = "dm-preview-body";
    modal.appendChild(head);
    modal.appendChild(body);
    ov.appendChild(modal);
    document.body.appendChild(ov);
    ov.addEventListener("click", (e) => { if (e.target === ov) closePreview(); });
    head.querySelector(".dm-pv-close").addEventListener("click", closePreview);
    head.querySelector('[data-act="prev"]').addEventListener("click", () => showPdfPage(pdfPage - 1));
    head.querySelector('[data-act="next"]').addEventListener("click", () => showPdfPage(pdfPage + 1));
    head.querySelector('[data-act="zoom-in"]').addEventListener("click", () => {
      zoomFactor = Math.min(zoomFactor * 1.2, 4); updateZoomLabel(); showPdfPage(pdfPage);
    });
    head.querySelector('[data-act="zoom-out"]').addEventListener("click", () => {
      zoomFactor = Math.max(zoomFactor / 1.2, 0.4); updateZoomLabel(); showPdfPage(pdfPage);
    });
    const pageInput = head.querySelector(".dm-pv-page-input");
    const jumpTo = () => showPdfPage(parseInt(pageInput.value || "1", 10));
    pageInput.addEventListener("change", jumpTo);
    pageInput.addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); jumpTo(); } });
    return ov;
  }

  function updateZoomLabel() {
    const ov = document.getElementById("dm-preview-overlay");
    if (ov) ov.querySelector(".dm-pv-zoom-label").textContent = Math.round(zoomFactor * 100) + "%";
  }

  function closePreview() {
    const ov = document.getElementById("dm-preview-overlay");
    if (ov) ov.style.display = "none";
    if (pdfDoc) { pdfDoc.destroy(); pdfDoc = null; }
  }
  document.addEventListener("keydown", (e) => {
    const ov = document.getElementById("dm-preview-overlay");
    if (!ov || ov.style.display === "none") return;
    if (e.key === "Escape") closePreview();
    if (e.key === "ArrowLeft") showPdfPage(pdfPage - 1);
    if (e.key === "ArrowRight") showPdfPage(pdfPage + 1);
  });

  function bodyEl() { return document.querySelector("#dm-preview-overlay .dm-preview-body"); }
  function showLoading(text) {
    const b = bodyEl();
    b.innerHTML = "";
    const d = document.createElement("div");
    d.className = "dm-preview-loading";
    d.textContent = text || "加载中…";
    b.appendChild(d);
  }
  function showError(e) {
    const b = bodyEl();
    b.innerHTML = "";
    const d = document.createElement("div");
    d.className = "dm-preview-error";
    d.textContent = "⚠️ 预览失败：" + ((e && e.message) || e);
    b.appendChild(d);
  }
  function showText(text) {
    const b = bodyEl();
    b.innerHTML = "";
    const pre = document.createElement("pre");
    pre.className = "dm-preview-text";
    pre.textContent = text;
    b.appendChild(pre);
  }

  // ---------- PDF 渲染（pdf.js 懒加载，首次预览 PDF 时才拉取） ----------
  function loadPdfLib() {
    return new Promise((resolve, reject) => {
      if (window.pdfjsLib) return resolve();
      const sc = document.createElement("script");
      sc.src = "/vendor/pdf.min.js";
      sc.onload = () => {
        window.pdfjsLib.GlobalWorkerOptions.workerSrc = "/vendor/pdf.worker.min.js";
        resolve();
      };
      sc.onerror = () => reject(new Error("pdf.js 加载失败"));
      document.head.appendChild(sc);
    });
  }

  async function renderPdf(url, page) {
    const ov = document.getElementById("dm-preview-overlay");
    try {
      showLoading("PDF 加载中…");
      await loadPdfLib();
      if (pdfDoc) { pdfDoc.destroy(); pdfDoc = null; }
      pdfDoc = await window.pdfjsLib.getDocument(url).promise;
      pdfTotal = pdfDoc.numPages;
      pdfPage = Math.min(Math.max(page || 1, 1), pdfTotal);
      zoomFactor = 1; updateZoomLabel();
      ov.querySelector(".dm-preview-pager").style.display = pdfTotal > 1 ? "" : "none";
      await showPdfPage(pdfPage);
    } catch (e) {
      showError(e);
    }
  }

  async function showPdfPage(n) {
    if (!pdfDoc) return;
    if (pdfRendering) { pendingPage = n; return; }   // 渲染中收到的翻页请求排队
    n = Math.min(Math.max(n, 1), pdfTotal);
    pdfRendering = true;
    pdfPage = n;
    const ov = document.getElementById("dm-preview-overlay");
    ov.querySelector(".dm-pv-page-input").value = n;
    ov.querySelector(".dm-pv-total").textContent = "/ " + pdfTotal;
    try {
      const page = await pdfDoc.getPage(n);
      const b = bodyEl();
      b.innerHTML = "";
      const canvas = document.createElement("canvas");
      b.appendChild(canvas);
      const base = page.getViewport({ scale: 1 });
      // 适配宽度为基准，叠加用户缩放系数
      const fitScale = Math.max(Math.min((b.clientWidth - 28) / base.width, 2), 0.5);
      const scale = fitScale * zoomFactor;
      const vp = page.getViewport({ scale });
      const dpr = window.devicePixelRatio || 1;
      canvas.width = vp.width * dpr;
      canvas.height = vp.height * dpr;
      canvas.style.width = vp.width + "px";
      canvas.style.height = vp.height + "px";
      await page.render({
        canvasContext: canvas.getContext("2d"),
        viewport: vp,
        transform: dpr !== 1 ? [dpr, 0, 0, dpr, 0, 0] : null,
      }).promise;
    } catch (e) {
      showError(e);
    } finally {
      pdfRendering = false;
      if (pendingPage !== null) { const q = pendingPage; pendingPage = null; showPdfPage(q); }
    }
  }

  // ---------- 打开预览（按格式分流） ----------
  function openPreview(file, page) {
    const ov = ensureModal();
    ov.style.display = "flex";
    ov.querySelector(".dm-preview-title").textContent = "📄 " + file;
    ov.querySelector(".dm-preview-pager").style.display = "none";
    const ext = file.split(".").pop().toLowerCase();
    const url = "/files/" + encodeURIComponent(file);
    if (ext === "pdf") {
      renderPdf(url, page);
    } else if (ext === "docx") {
      showLoading("Word 文档加载中…");
      fetch(url + "?as=pdf", { method: "HEAD" }).then((r) => {
        const ct = r.headers.get("content-type") || "";
        if (r.ok && ct.includes("pdf")) return renderPdf(url + "?as=pdf", 1);
        return fetch(url + "?as=text").then((r2) => r2.ok ? r2.text()
          : Promise.reject(new Error("HTTP " + r2.status))).then(showText);
      }).catch(showError);
    } else {
      showLoading("加载中…");
      fetch(url).then((r) => r.ok ? r.text() : Promise.reject(new Error("HTTP " + r.status)))
        .then(showText).catch(showError);
    }
  }

  // 点击委托：引用链接 → 打开预览
  document.addEventListener("click", (e) => {
    const link = e.target.closest(".dm-source-link");
    if (!link) return;
    e.stopPropagation();
    openPreview(link.dataset.file, parseInt(link.dataset.page || "0", 10));
  });
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
        # 关闭原生自动滚动：流式每帧强制滚底会把正在上滑阅读的用户拽回去，
        # 改由前端智能滚动接管（贴底才跟随，上滑不打扰 + 回到底部悬浮按钮）
        autoscroll=False,
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
    #
    # Mermaid 图表库：通过 FastAPI 路由 serve 本地 JS 文件
    # （避免 CDN 不可达 + 避免 head_paths 内联 HTML 导致 </ 序列中断解析）
    # 注意：launch() 内部会重建 demo.app，因此必须用 prevent_thread_lock=True
    # 让 launch 返回后，再在新 demo.app 上注册路由
    import time
    from fastapi.responses import FileResponse
    _mermaid_dir = os.path.dirname(os.path.abspath(__file__))

    demo.launch(
        theme=theme,
        css=CUSTOM_CSS,
        head=f'<script src="/mermaid.min.js"></script>\n'
             + FOLD_SCRIPT + f"<style>{LAYOUT_CSS}</style>",
        server_name=os.getenv("GRADIO_SERVER_NAME", "127.0.0.1"),
        server_port=int(os.getenv("GRADIO_SERVER_PORT", "7860")),
        prevent_thread_lock=True,
    )

    @demo.app.get("/mermaid.min.js", include_in_schema=False)
    async def _serve_mermaid():
        return FileResponse(os.path.join(_mermaid_dir, "mermaid.min.js"),
                            media_type="application/javascript")

    # ---- 文档预览：vendored pdf.js + 知识库原文（引用溯源直达） ----
    from fastapi import HTTPException, Query
    from fastapi.responses import PlainTextResponse

    _vendor_dir = os.path.join(_mermaid_dir, "vendor")
    _knowledge_dir = config.KNOWLEDGE_DIR

    @demo.app.get("/vendor/{name}", include_in_schema=False)
    async def _serve_vendor(name: str):
        safe = os.path.basename(name)  # 防路径穿越
        path = os.path.join(_vendor_dir, safe)
        if not os.path.isfile(path):
            raise HTTPException(status_code=404)
        return FileResponse(path, media_type="application/javascript"
                            if safe.endswith(".js") else "application/octet-stream")

    # methods 含 HEAD：前端先 HEAD 探测 docx 能否转 PDF（此 App 的 .get 不自动挂 HEAD）
    @demo.app.api_route("/files/{name}", methods=["GET", "HEAD"], include_in_schema=False)
    async def _serve_file(name: str, as_: str = Query(default=None, alias="as")):
        safe = os.path.basename(name)  # 防路径穿越：只允许知识库目录内文件名
        path = os.path.join(_knowledge_dir, safe)
        if not os.path.isfile(path):
            raise HTTPException(status_code=404)
        if as_ == "text":
            # 提取正文文本（docx 无 LibreOffice 时的预览降级通道）
            from docmind.rag.chunker import _EXTRACTORS
            ext = os.path.splitext(safe)[1].lower()
            try:
                if ext in _EXTRACTORS:
                    text = _EXTRACTORS[ext](path)
                else:
                    with open(path, encoding="utf-8") as f:
                        text = f.read()
            except Exception as e:  # noqa: BLE001
                raise HTTPException(status_code=500, detail=f"文本提取失败: {e}")
            return PlainTextResponse(text)
        if as_ == "pdf" and safe.lower().endswith(".docx"):
            # LibreOffice headless 转 PDF（按源文件 mtime 缓存）；未安装 → 409，前端降级文本预览
            import shutil
            import subprocess
            soffice = shutil.which("soffice") or (
                "/Applications/LibreOffice.app/Contents/MacOS/soffice"
                if os.path.exists("/Applications/LibreOffice.app/Contents/MacOS/soffice") else None)
            if not soffice:
                raise HTTPException(status_code=409, detail="LibreOffice 未安装")
            cache_dir = os.path.join(config.PROJECT_ROOT, "data", "preview_cache")
            os.makedirs(cache_dir, exist_ok=True)
            out_pdf = os.path.join(cache_dir, os.path.splitext(safe)[0] + ".pdf")
            if not os.path.isfile(out_pdf) or os.path.getmtime(out_pdf) < os.path.getmtime(path):
                try:
                    r = subprocess.run(
                        [soffice, "--headless", "--convert-to", "pdf", "--outdir", cache_dir, path],
                        capture_output=True, timeout=120)
                except Exception as e:  # noqa: BLE001
                    raise HTTPException(status_code=500, detail=f"转换失败: {e}")
                if r.returncode != 0 or not os.path.isfile(out_pdf):
                    raise HTTPException(status_code=500, detail="PDF 转换失败")
            return FileResponse(out_pdf, media_type="application/pdf")
        return FileResponse(path)

    # 阻塞主线程（保持服务运行）
    try:
        while True:
            time.sleep(86400)
    except (KeyboardInterrupt, SystemExit):
        demo.close()
