"""管理后台：/admin 看板（用量概览 / badcase 流转 / 会话审计）。

权限：仅 is_admin 用户可访问（Gradio 登录 cookie 解析身份）。
数据源：chat.db（会话/消息/反馈）+ semantic_cache 统计 + trace_log.jsonl 用量。
"""
import json
import os
import time
from collections import defaultdict

import fastapi
from fastapi import HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel

from docmind import config, semantic_cache, store


def _current_user(request, app) -> str:
    token = (request.cookies.get(f"access-token-{app.cookie_id}")
             or request.cookies.get(f"access-token-unsecure-{app.cookie_id}"))
    return (app.tokens.get(token) if token else None) or ""


def _require_admin(request, app) -> str:
    user = _current_user(request, app)
    if not user:
        raise HTTPException(status_code=401, detail="未登录")
    if not store.is_admin(user):
        raise HTTPException(status_code=403, detail="需要管理员权限")
    return user


def _trace_usage() -> dict:
    """从本地 trace 日志聚合用量：LLM 调用数/token/工具调用数/近 7 日趋势"""
    path = config.TRACE_LOG_PATH
    agg = {"llm_calls": 0, "tool_calls": 0, "input_tokens": 0,
           "output_tokens": 0, "errors": 0, "daily": defaultdict(lambda: [0, 0])}
    if not os.path.exists(path):
        return agg
    try:
        with open(path, encoding="utf-8") as f:
            lines = f.readlines()[-3000:]   # 只统计最近 3000 条
        for line in lines:
            try:
                d = json.loads(line)
            except json.JSONDecodeError:
                continue
            day = str(d.get("ts", ""))[:10]
            if d.get("status") != "ok":
                agg["errors"] += 1
            if d.get("kind") == "generation":
                agg["llm_calls"] += 1
                usage = d.get("usage") or {}
                ti, to = usage.get("input", 0), usage.get("output", 0)
                agg["input_tokens"] += ti
                agg["output_tokens"] += to
                if day:
                    agg["daily"][day][0] += ti
                    agg["daily"][day][1] += to
            elif str(d.get("name", "")).startswith("tool:"):
                agg["tool_calls"] += 1
    except OSError:
        pass
    agg["daily"] = {k: {"input": v[0], "output": v[1]}
                    for k, v in sorted(agg["daily"].items())[-7:]}
    return agg


class BadcaseStatusIn(BaseModel):
    status: str          # pending / resolved / ignored
    note: str = ""


def register_admin_routes(app) -> None:
    @app.get("/api/me", include_in_schema=False)
    async def _me(request: fastapi.Request):
        user = _current_user(request, app)
        return {"user": user, "is_admin": bool(user and store.is_admin(user))}

    @app.get("/api/admin/overview", include_in_schema=False)
    async def _overview(request: fastapi.Request):
        _require_admin(request, app)
        data = store.stats_overview()
        data["cache"] = semantic_cache.stats()
        data["usage"] = _trace_usage()
        return JSONResponse(data)

    @app.get("/api/admin/badcases", include_in_schema=False)
    async def _badcases(request: fastapi.Request):
        _require_admin(request, app)
        return JSONResponse(store.list_badcases())

    @app.post("/api/admin/badcase/{fid}", include_in_schema=False)
    async def _badcase_update(fid: int, body: BadcaseStatusIn, request: fastapi.Request):
        _require_admin(request, app)
        if body.status not in ("pending", "resolved", "ignored"):
            raise HTTPException(status_code=400, detail="status 非法")
        store.set_badcase_status(fid, body.status, body.note)
        return {"ok": True}

    @app.get("/api/admin/sessions", include_in_schema=False)
    async def _sessions(request: fastapi.Request):
        _require_admin(request, app)
        return JSONResponse(store.list_all_sessions())

    @app.get("/api/admin/sessions/{sid}/messages", include_in_schema=False)
    async def _session_messages(sid: str, request: fastapi.Request):
        _require_admin(request, app)
        return JSONResponse(store.get_session_messages(sid))

    @app.get("/admin", include_in_schema=False)
    async def _admin_page(request: fastapi.Request):
        _require_admin(request, app)
        return HTMLResponse(ADMIN_HTML)


# ---------------- 看板页面（自包含 HTML，fetch 上述 API 渲染） ----------------
ADMIN_HTML = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<title>DocMind 管理后台</title>
<style>
  :root { --pri: #6366f1; --bg: #f4f6fb; --card: #fff; --line: #e9ecf7; --tx: #1e293b; --sub: #94a3b8; }
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { font-family: "PingFang SC", system-ui, sans-serif; background: var(--bg); color: var(--tx); }
  .head { display: flex; align-items: center; gap: 12px; padding: 14px 24px; background: var(--card); border-bottom: 1px solid var(--line); }
  .head h1 { font-size: 16px; }
  .head a { margin-left: auto; color: var(--pri); text-decoration: none; font-size: 13px; }
  .tabs { display: flex; gap: 8px; padding: 14px 24px 0; }
  .tabs button { border: none; background: transparent; padding: 8px 16px; font-size: 13px; cursor: pointer; color: var(--sub); border-bottom: 2px solid transparent; }
  .tabs button.on { color: var(--pri); border-bottom-color: var(--pri); font-weight: 600; }
  .body { padding: 16px 24px; }
  .cards { display: grid; grid-template-columns: repeat(auto-fill, minmax(160px, 1fr)); gap: 12px; margin-bottom: 16px; }
  .card { background: var(--card); border: 1px solid var(--line); border-radius: 12px; padding: 14px 16px; }
  .card .v { font-size: 22px; font-weight: 700; color: var(--pri); }
  .card .k { font-size: 12px; color: var(--sub); margin-top: 4px; }
  table { width: 100%; border-collapse: collapse; background: var(--card); border: 1px solid var(--line); border-radius: 12px; overflow: hidden; font-size: 13px; }
  th { text-align: left; padding: 10px 12px; background: #eef2ff; color: #4338ca; font-size: 12px; }
  td { padding: 10px 12px; border-top: 1px solid var(--line); vertical-align: top; }
  .pill { display: inline-block; padding: 2px 10px; border-radius: 999px; font-size: 11px; }
  .pill.pending { background: #fef3c7; color: #b45309; }
  .pill.resolved { background: #d1fae5; color: #047857; }
  .pill.ignored { background: #e2e8f0; color: #64748b; }
  .note { width: 100%; margin-top: 6px; border: 1px solid var(--line); border-radius: 6px; padding: 4px 8px; font-size: 12px; }
  .mini { border: none; border-radius: 6px; padding: 3px 10px; font-size: 12px; cursor: pointer; margin-right: 4px; }
  .mini.ok { background: #d1fae5; color: #047857; }
  .mini.ig { background: #e2e8f0; color: #64748b; }
  .mini.re { background: #fef3c7; color: #b45309; }
  .excerpt { color: var(--sub); font-size: 12px; margin-top: 4px; max-width: 420px; }
  .daily { display: flex; gap: 8px; align-items: flex-end; height: 90px; margin-top: 8px; }
  .bar { flex: 1; background: linear-gradient(180deg, #818cf8, #6366f1); border-radius: 4px 4px 0 0; position: relative; min-height: 2px; }
  .bar span { position: absolute; bottom: -18px; left: 0; right: 0; text-align: center; font-size: 10px; color: var(--sub); }
  .section-title { font-size: 13px; font-weight: 600; margin: 18px 0 8px; }
  .empty { color: var(--sub); font-size: 13px; padding: 24px; text-align: center; background: var(--card); border-radius: 12px; border: 1px solid var(--line); }
</style>
</head>
<body>
<div class="head">
  <h1>📊 DocMind 管理后台</h1>
  <span id="who" style="font-size:12px;color:var(--sub)"></span>
  <a href="/">← 返回对话</a>
</div>
<div class="tabs">
  <button data-tab="usage" class="on">用量看板</button>
  <button data-tab="badcase">Badcase 流转</button>
  <button data-tab="audit">会话审计</button>
</div>
<div class="body" id="content"></div>

<script>
const $ = (sel) => document.querySelector(sel);
const fmt = (ts) => ts ? new Date(ts * 1000).toLocaleString('zh-CN', {month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit'}) : '';
const esc = (s) => (s || '').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');

async function api(path, opt) {
  const r = await fetch(path, opt);
  if (r.status === 401) { location.href = '/'; return null; }
  if (!r.ok) throw new Error('HTTP ' + r.status);
  return r.json();
}

/* ---------- 用量看板 ---------- */
async function renderUsage() {
  const d = await api('/api/admin/overview');
  if (!d) return;
  const u = d.usage, days = Object.entries(u.daily || {});
  const maxTok = Math.max(1, ...days.map(([, v]) => v.input + v.output));
  $('#content').innerHTML = `
  <div class="cards">
    <div class="card"><div class="v">${d.users}</div><div class="k">用户数</div></div>
    <div class="card"><div class="v">${d.sessions}</div><div class="k">会话数</div></div>
    <div class="card"><div class="v">${d.messages}</div><div class="k">消息数</div></div>
    <div class="card"><div class="v">👍 ${d.feedback_up} / 👎 ${d.feedback_down}</div><div class="k">反馈（好评/差评）</div></div>
    <div class="card"><div class="v" style="color:${d.badcase_pending ? '#b45309' : 'var(--pri)'}">${d.badcase_pending}</div><div class="k">待处理 Badcase</div></div>
    <div class="card"><div class="v">${u.llm_calls}</div><div class="k">LLM 调用次数</div></div>
    <div class="card"><div class="v">${u.tool_calls}</div><div class="k">工具调用次数</div></div>
    <div class="card"><div class="v">${(u.input_tokens/1000).toFixed(1)}k / ${(u.output_tokens/1000).toFixed(1)}k</div><div class="k">Token 用量（入/出）</div></div>
    <div class="card"><div class="v">${d.cache.entries} / ${d.cache.total_hits}</div><div class="k">语义缓存（条目/命中）</div></div>
    <div class="card"><div class="v">${u.errors}</div><div class="k">调用失败数</div></div>
  </div>
  <div class="section-title">近 7 日 Token 用量趋势</div>
  ${days.length ? `<div class="daily">${days.map(([day, v]) =>
      `<div class="bar" style="height:${Math.max(4, (v.input+v.output)/maxTok*100)}%"
            title="${day}: 入 ${v.input} / 出 ${v.output}"><span>${day.slice(5)}</span></div>`).join('')}</div>`
    : '<div class="empty">暂无用量数据</div>'}`;
}

/* ---------- Badcase 流转 ---------- */
async function renderBadcase() {
  const list = await api('/api/admin/badcases');
  if (!list) return;
  if (!list.length) { $('#content').innerHTML = '<div class="empty">🎉 暂无 👎 反馈</div>'; return; }
  $('#content').innerHTML = `<table><tr>
    <th>时间</th><th>用户</th><th>问题</th><th>回答节选</th><th>状态</th><th>操作</th></tr>
    ${list.map(b => `<tr>
      <td>${fmt(b.created)}</td><td>${esc(b.user)}</td>
      <td>${esc(b.question)}<div class="excerpt">会话：${esc(b.session_title || b.session)}</div></td>
      <td><div class="excerpt">${esc(b.answer_excerpt)}…</div>
          <input class="note" placeholder="处理备注…" value="${esc(b.note)}" data-id="${b.id}"></td>
      <td><span class="pill ${b.status}">${{pending:'待处理',resolved:'已解决',ignored:'已忽略'}[b.status]||b.status}</span></td>
      <td><button class="mini ok" onclick="setStatus(${b.id},'resolved')">已解决</button>
          <button class="mini ig" onclick="setStatus(${b.id},'ignored')">忽略</button>
          <button class="mini re" onclick="setStatus(${b.id},'pending')">重开</button></td>
    </tr>`).join('')}</table>`;
}
window.setStatus = async (id, status) => {
  const note = document.querySelector(`.note[data-id="${id}"]`);
  await api('/api/admin/badcase/' + id, {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({status, note: note ? note.value : ''})});
  renderBadcase();
};

/* ---------- 会话审计 ---------- */
async function renderAudit() {
  const list = await api('/api/admin/sessions');
  if (!list) return;
  $('#content').innerHTML = `<table><tr>
    <th>最近活跃</th><th>用户</th><th>会话标题</th><th>消息数</th><th></th></tr>
    ${list.map(s => `<tr>
      <td>${fmt(s.updated_at)}</td><td>${esc(s.user)}</td>
      <td>${esc(s.title || '(空会话)')}</td><td>${s.msg_count}</td>
      <td><button class="mini ok" onclick="viewSession('${s.id}')">查看对话</button></td>
    </tr>`).join('')}</table><div id="conv" style="margin-top:14px"></div>`;
}
window.viewSession = async (sid) => {
  const msgs = await api(`/api/admin/sessions/${sid}/messages`);
  $('#conv').innerHTML = `<div class="section-title">会话内容（节选 300 字/条）</div>` +
    msgs.map(m => `<div class="card" style="margin-bottom:8px">
      <b style="color:${m.role === 'user' ? '#6366f1' : '#047857'}">${m.role === 'user' ? '🙋 用户' : '🤖 助手'}</b>
      <div class="excerpt" style="max-width:none;margin-top:4px">${esc(m.content)}</div></div>`).join('');
};

/* ---------- 标签切换 ---------- */
const renderers = {usage: renderUsage, badcase: renderBadcase, audit: renderAudit};
document.querySelectorAll('.tabs button').forEach(b => b.onclick = () => {
  document.querySelectorAll('.tabs button').forEach(x => x.classList.remove('on'));
  b.classList.add('on');
  renderers[b.dataset.tab]();
});
api('/api/me').then(d => { if (d) $('#who').textContent = '👑 ' + d.user; });
renderUsage();
</script>
</body>
</html>"""
