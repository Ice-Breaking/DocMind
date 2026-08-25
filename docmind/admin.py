"""管理后台：/admin 看板（用量概览 / badcase 流转 / 会话审计）。

权限：仅 is_admin 用户可访问（Gradio 登录 cookie 解析身份）。
数据源：chat.db（会话/消息/反馈）+ semantic_cache 统计 + trace_log.jsonl 用量。
"""
import os
import time

import anyio
import fastapi
from fastapi import HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from typing import Literal

from pydantic import BaseModel, Field

from docmind.deps import CurrentUser, RequireAdmin
from docmind import semantic_cache, store


def _trace_usage() -> dict:
    """从 trace SQLite 聚合用量：LLM 调用数/token/工具调用数/近 7 日趋势。
    SQL 一次聚合（原拉 3000 条完整记录到 Python 逐条数数，
    每行还带 input JSON 反序列化——overview 每次打开都执行）"""
    from docmind import trace_store
    agg = trace_store.usage_summary(limit=3000)
    agg["daily"] = dict(agg.get("daily") or {})
    return agg


# 成本估算价目表：每千 token 价格（元），input / output（alerts 成本规则共用）
MODEL_PRICING = {
    "qwen-plus": (0.004, 0.012),
    "qwen-turbo": (0.002, 0.006),
    "qwen-max": (0.02, 0.06),
    "qwen-flash": (0.001, 0.003),
    "gpt-4o": (0.01, 0.03),
    "gpt-4o-mini": (0.0006, 0.0024),
}
_DEFAULT_PRICING = (0.005, 0.015)


class BadcaseStatusIn(BaseModel):
    status: Literal["pending", "resolved", "ignored"]
    note: str = Field(default="", max_length=500)


# ---------------- trace 聚合 TTL 缓存 ----------------
# usage/top-queries 每次请求全量读 trace JSONL（最大 50MB）并逐行解析，
# 同一份数据被反复解析；按 (kind, days) 缓存聚合结果 60s（管理看板
# 对实时性不敏感），文件 mtime 变化立即失效
_AGG_TTL = 60.0
_agg_cache: dict[tuple, tuple[float, float, object]] = {}   # key -> (ts, mtime, result)


def _agg_cached(kind: str, days: int, compute):
    """带 TTL + trace.db mtime 失效的聚合缓存；compute 为无参同步重活，
    调用方须在 run_sync 内执行本函数"""
    from docmind import trace_store
    path = trace_store.DB_PATH
    try:
        mtime = os.path.getmtime(path) if os.path.exists(path) else 0.0
    except OSError:
        mtime = 0.0
    key = (kind, days)
    hit = _agg_cache.get(key)
    if hit and time.time() - hit[0] < _AGG_TTL and hit[1] == mtime:
        return hit[2]
    result = compute()
    _agg_cache[key] = (time.time(), mtime, result)
    return result


def _usage_detail_agg(days: int) -> dict:
    """用量成本聚合：SQL GROUP BY (model, day) 一次取回，价目在 Python 侧套用。
    （原实现全量读 JSONL 逐行 json.loads，50MB 文件每请求重解析一遍）"""
    from docmind import trace_store
    empty = {"summary": {"total_calls": 0, "total_input_tokens": 0,
                         "total_output_tokens": 0, "total_cost": 0},
             "by_model": [], "daily": []}
    try:
        result = trace_store.usage_detail(days)
    except Exception:  # noqa: BLE001
        return empty
    by_model = {}
    daily = {}
    total_calls = 0
    total_input = 0
    total_output = 0
    total_cost = 0.0
    for row in result.get("rows", []):
        model = row.get("model") or "unknown"
        day = row.get("day") or ""
        calls = row.get("calls") or 0
        inp = row.get("inp") or 0
        outp = row.get("outp") or 0
        pricing = MODEL_PRICING.get(model, _DEFAULT_PRICING)
        cost = (inp / 1000.0) * pricing[0] + (outp / 1000.0) * pricing[1]
        total_calls += calls
        total_input += inp
        total_output += outp
        total_cost += cost
        m = by_model.setdefault(model, {"model": model, "calls": 0,
                                        "input_tokens": 0, "output_tokens": 0,
                                        "cost": 0.0})
        m["calls"] += calls
        m["input_tokens"] += inp
        m["output_tokens"] += outp
        m["cost"] += cost
        d = daily.setdefault(day, {"date": day, "input_tokens": 0,
                                   "output_tokens": 0, "cost": 0.0})
        d["input_tokens"] += inp
        d["output_tokens"] += outp
        d["cost"] += cost
    for m in by_model.values():
        m["cost"] = round(m["cost"], 4)
    for d in daily.values():
        d["cost"] = round(d["cost"], 4)
    return {
        "summary": {
            "total_calls": total_calls,
            "total_input_tokens": total_input,
            "total_output_tokens": total_output,
            "total_cost": round(total_cost, 4),
        },
        "by_model": sorted(by_model.values(), key=lambda x: x["cost"], reverse=True),
        "daily": sorted(daily.values(), key=lambda x: x["date"]),
    }


def _top_queries_agg(days: int, limit: int) -> dict:
    """高成本 Query Top N：SQL 按 (query_label, model) GROUP BY，
    价目在 Python 侧套用后合并排序（query_label 写入时已抽取）"""
    from docmind import trace_store
    agg = {}
    for row in trace_store.top_queries(days):
        query = row.get("query_label") or ""
        model = row.get("model") or "unknown"
        calls = row.get("calls") or 0
        inp = row.get("inp") or 0
        outp = row.get("outp") or 0
        pricing = MODEL_PRICING.get(model, _DEFAULT_PRICING)
        cost = (inp / 1000.0) * pricing[0] + (outp / 1000.0) * pricing[1]
        item = agg.setdefault(query, {"query": query, "calls": 0,
                                      "input_tokens": 0, "output_tokens": 0,
                                      "cost": 0.0})
        item["calls"] += calls
        item["input_tokens"] += inp
        item["output_tokens"] += outp
        item["cost"] += cost
    items = sorted(agg.values(), key=lambda x: x["cost"], reverse=True)
    for it in items:
        it["cost"] = round(it["cost"], 4)
    limit = max(1, min(limit, 50))
    return {"items": items[:limit], "total": len(items)}


def register_admin_routes(app) -> None:
    @app.get("/api/me", include_in_schema=False)
    async def _me(request: fastapi.Request, user: CurrentUser):
        return {
            "user": user,
            "is_admin": bool(user and store.is_admin(user)),
            "must_change_pwd": bool(user and store.get_must_change_pwd(user)),
            "avatar": store.get_user_avatar(user) if user else "",
            "pending_avatar": (store.get_pending_avatar(user)[0] if user else "")
        }

    @app.get("/api/admin/overview", include_in_schema=False)
    async def _overview(request: fastapi.Request, _user: RequireAdmin):
        data = store.stats_overview()
        data["cache"] = semantic_cache.stats()
        # trace JSONL 全量读是同步 IO 重活，下放线程池
        data["usage"] = await anyio.to_thread.run_sync(_trace_usage)
        return JSONResponse(data)

    @app.get("/api/admin/badcases", include_in_schema=False)
    async def _badcases(request: fastapi.Request, _user: RequireAdmin, limit: int = 100,
                        offset: int = 0):
        """badcase 流转列表（分页：limit/offset 可选，默认值保持旧行为）"""
        return JSONResponse(await anyio.to_thread.run_sync(
            lambda: store.list_badcases(limit=max(1, min(limit, 500)),
                                        offset=max(0, offset))))

    @app.post("/api/admin/badcase/{fid}", include_in_schema=False)
    async def _badcase_update(fid: int, body: BadcaseStatusIn, request: fastapi.Request, user: RequireAdmin):
        if body.status not in ("pending", "resolved", "ignored"):
            raise HTTPException(status_code=400, detail="status 非法")
        store.set_badcase_status(fid, body.status, body.note)
        store.record_audit(user, "badcase.update", f"feedback#{fid}", body.status)
        return {"ok": True}

    @app.get("/api/admin/queries", include_in_schema=False)
    async def _queries(request: fastapi.Request, _user: RequireAdmin, user: str = "", q: str = "",
                       days: int = 0, limit: int = 500):
        """管理员查看用户提问记录：按用户/关键词/时间过滤"""
        return JSONResponse(store.list_user_queries(user, q, days, limit))

    @app.get("/api/admin/sessions", include_in_schema=False)
    async def _sessions(request: fastapi.Request, _user: RequireAdmin, limit: int = 100,
                        offset: int = 0):
        """会话审计列表（分页：limit/offset 可选，默认值保持旧行为）"""
        return JSONResponse(await anyio.to_thread.run_sync(
            lambda: store.list_all_sessions(limit=max(1, min(limit, 500)),
                                            offset=max(0, offset))))

    @app.get("/api/admin/sessions/{sid}/messages", include_in_schema=False)
    async def _session_messages(sid: str, request: fastapi.Request, _user: RequireAdmin):
        return JSONResponse(store.get_session_messages(sid))

    @app.post("/api/admin/reindex", include_in_schema=False)
    async def _reindex(request: fastapi.Request, _user: RequireAdmin):
        """手动触发知识库增量重建：逐文件 manifest 对比，只处理变化文件。
        重建含切片 + embedding 网络调用（分钟级），下放线程池——
        事件循环冻结会让全站（含 /health 探活）无响应"""
        from docmind.core import rebuild_knowledge_index
        result = await anyio.to_thread.run_sync(rebuild_knowledge_index)
        if "error" in result:
            status = 409 if "正在重建" in result["error"] else 500
            return JSONResponse({"ok": False, "result": result}, status_code=status)
        return {"ok": True, "result": result}

    # ---- 检索日志端点 ----
    @app.get("/api/admin/traces", include_in_schema=False)
    async def _traces(request: fastapi.Request, _user: RequireAdmin, page: int = 1, page_size: int = 50,
                      kind: str = "", status: str = "", q: str = "",
                      start: str = "", end: str = "", kb: str = ""):
        """检索日志：按类型/状态/关键词/时间范围/知识库过滤（start/end 为 YYYY-MM-DD），倒序分页。
        数据源为 trace SQLite（SQL 索引过滤 + LIMIT/OFFSET 分页，
        替代原全量读 JSONL 逐行解析）"""
        from docmind import trace_store
        page_size = max(1, min(page_size, 200))
        items, total = await anyio.to_thread.run_sync(
            trace_store.list_filtered,
            kind, status, q, start, end, kb, max(1, page), page_size)
        return JSONResponse({"items": items, "total": total})

    # ---- 用量成本端点（价目表见模块级 MODEL_PRICING） ----
    @app.get("/api/admin/usage", include_in_schema=False)
    async def _usage_detail(request: fastapi.Request, _user: RequireAdmin, days: int = 30):
        # SQL 聚合 + 60s TTL 缓存（mtime 失效对 SQLite 意义不大，TTL 兜底）
        result = await anyio.to_thread.run_sync(
            _agg_cached, "usage", days,
            lambda: _usage_detail_agg(days))
        return JSONResponse(result)

    @app.get("/api/admin/usage/top-queries", include_in_schema=False)
    async def _top_queries(request: fastapi.Request, _user: RequireAdmin, days: int = 30, limit: int = 10):
        """高成本 Query Top N：以 generation 记录的最后一条用户消息聚合调用数/token/成本"""
        result = await anyio.to_thread.run_sync(
            _agg_cached, "top-queries", days,
            lambda: _top_queries_agg(days, limit))
        return JSONResponse(result)

    @app.get("/admin", include_in_schema=False)
    async def _admin_page(request: fastapi.Request, _user: RequireAdmin):
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
  <button id="reindex" class="mini re" title="增量重建知识库索引（只处理变化文件）">🔄 重建索引</button>
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
// esc 必须同时转义引号：插值点含 HTML 属性(value="...")与内联事件
// (onclick="fn('${id}')")，只转义 &<> 时属性可被双引号/单引号截断注入
const esc = (s) => (s || '').replace(/&/g,'&amp;').replace(/</g,'&lt;')
  .replace(/>/g,'&gt;').replace(/"/g,'&quot;').replace(/'/g,'&#39;');

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
      <td><button class="mini ok" onclick="viewSession('${esc(s.id)}')">查看对话</button></td>
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

/* ---------- 一键重建知识库索引（增量） ---------- */
$('#reindex').onclick = async () => {
  if (!confirm('开始增量重建知识库索引？（只对变化的文件重新切片与向量化）')) return;
  const btn = $('#reindex');
  btn.disabled = true; btn.textContent = '重建中…';
  try {
    const r = await fetch('/api/admin/reindex', {method: 'POST'});
    const d = await r.json();
    if (d && d.ok) {
      const res = d.result || {};
      alert(res.full_rebuild
        ? `已全量重建，共 ${res.chunks} 个切片`
        : `增量重建完成：新增 ${res.added} / 修改 ${res.modified} / 删除 ${res.removed} / 未变 ${res.unchanged} 个文件，共 ${res.chunks} 个切片`);
    } else {
      alert('重建未完成：' + ((d && d.result && d.result.error) || ('HTTP ' + r.status)));
    }
  } catch (e) {
    alert('重建请求失败: ' + e.message);
  } finally {
    btn.disabled = false; btn.textContent = '🔄 重建索引';
  }
};

renderUsage();
</script>
</body>
</html>"""
