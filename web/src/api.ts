/**
 * 后端 API 封装：身份 / 登出 / SSE 聊天流。
 * SSE 事件协议见 docmind/chat_stream.py：
 *   cache / thinking / token / step / error / final / done
 */

export interface Me {
  user: string;
  is_admin: boolean;
  must_change_pwd: boolean;
  avatar?: string;
  pending_avatar?: string;
}

export type ChatEventKind =
  | 'cache' | 'thinking' | 'token' | 'step' | 'error' | 'final' | 'done';

export interface ChatEvent {
  event: ChatEventKind;
  data: Record<string, any>;
}

/** 当前登录身份（未登录返回空 user） */
export async function fetchMe(): Promise<Me> {
  const r = await fetch('/api/me');
  if (!r.ok) return { user: '', is_admin: false, must_change_pwd: false };
  return r.json();
}

/** Gradio 登录：form-encoded，成功后服务端种 access-token cookie */
export async function login(username: string, password: string): Promise<boolean> {
  const fd = new URLSearchParams();
  fd.set('username', username);
  fd.set('password', password);
  const r = await fetch('/login', { method: 'POST', body: fd });
  try {
    const j = await r.json();
    return !!j.success;
  } catch {
    return false;
  }
}

export async function logout(): Promise<void> {
  await fetch('/logout');
}

/** 修改密码（首次登录强制改密场景） */
export async function changePassword(oldPassword: string, newPassword: string): Promise<void> {
  const r = await fetch('/api/change-password', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ old_password: oldPassword, new_password: newPassword }),
  });
  if (r.status === 401) throw new Error('请先登录');
  if (!r.ok) {
    const data = await r.json().catch(() => ({}));
    // detail 兼容两种形态：字符串（业务错误）/ 数组（Pydantic 422 参数校验）
    let msg = '修改失败';
    if (typeof data.detail === 'string') {
      msg = data.detail;
    } else if (Array.isArray(data.detail) && data.detail.length > 0) {
      msg = data.detail[0]?.msg || '参数校验失败';
    }
    throw new Error(msg);
  }
}

/**
 * SSE 聊天流：异步生成器逐事件 yield。
 * 用 fetch + ReadableStream 手写解析（EventSource 不支持 POST body）。
 */
export async function* chatStream(
  question: string,
  sessionId: string,
  signal?: AbortSignal,
  assistantId?: string,
  imageData?: string | string[],
  onUploadProgress?: (pct: number) => void,
): AsyncGenerator<ChatEvent> {
  const payload: Record<string, unknown> = { question, session_id: sessionId };
  if (assistantId) payload.assistant_id = assistantId;
  if (imageData) payload.image_data = imageData;   // str | str[](多图)

  // 带图 + 需要进度：走 XHR（upload.onprogress 真实上行百分比），
  // SSE 帧从 responseText 增量解析（与 fetch 路径同一解析逻辑）
  const hasImg = !!imageData && (Array.isArray(imageData) ? imageData.length > 0 : true);
  if (hasImg && onUploadProgress) {
    yield* chatStreamXHR(payload, signal, onUploadProgress);
    return;
  }
  const resp = await fetch('/api/chat/stream', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
    signal,
  });
  if (resp.status === 401) throw new Error('UNAUTHORIZED');
  if (!resp.ok || !resp.body) throw new Error('HTTP ' + resp.status);

  const reader = resp.body.getReader();
  const decoder = new TextDecoder();
  let buf = '';
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buf += decoder.decode(value, { stream: true });
    // SSE 帧以空行分隔：event: xxx\ndata: {...}\n\n
    let idx: number;
    while ((idx = buf.indexOf('\n\n')) !== -1) {
      const frame = buf.slice(0, idx);
      buf = buf.slice(idx + 2);
      let event = 'message';
      let data = '';
      for (const line of frame.split('\n')) {
        if (line.startsWith('event: ')) event = line.slice(7);
        else if (line.startsWith('data: ')) data = line.slice(6);
      }
      if (!data) continue;
      try {
        yield { event: event as ChatEventKind, data: JSON.parse(data) };
      } catch {
        // 非法 JSON 帧跳过，不中断流
      }
    }
  }
}

export interface Message {
  id: number;
  seq: number;
  role: string;
  content: string;
  raw: string;
  created_at: number;
}

/** 获取会话全部消息（不截断内容） */
export async function fetchMessages(sessionId: string): Promise<Message[]> {
  const r = await fetch(`/api/sessions/${encodeURIComponent(sessionId)}/messages`);
  if (r.status === 401) throw new Error('UNAUTHORIZED');
  if (!r.ok) throw new Error('HTTP ' + r.status);
  return r.json();
}

/* ------------------------------------------------------------------ */
/*  Session / Feedback / Suggestions                                    */
/* ------------------------------------------------------------------ */

export interface Session {
  id: string;
  title: string;
  msg_count: number;
  updated_at: number;
  last_msg?: string;
  assistant_id?: string;
}

/** 获取会话列表（按最近活动排序） */
export async function fetchSessions(): Promise<Session[]> {
  const r = await fetch('/api/sessions');
  if (r.status === 401) throw new Error('UNAUTHORIZED');
  if (!r.ok) throw new Error('HTTP ' + r.status);
  return r.json();
}

/** 删除指定会话 */
export async function deleteSession(sessionId: string): Promise<void> {
  const r = await fetch(`/api/sessions/${encodeURIComponent(sessionId)}`, {
    method: 'DELETE',
  });
  if (r.status === 401) throw new Error('UNAUTHORIZED');
  if (!r.ok) throw new Error('HTTP ' + r.status);
}

/** 提交消息点赞/点踩 */
export async function submitFeedback(
  sessionId: string,
  seq: number,
  rating: 'up' | 'down',
): Promise<void> {
  const r = await fetch('/api/feedback', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ session_id: sessionId, seq, rating }),
  });
  if (r.status === 401) throw new Error('UNAUTHORIZED');
  if (!r.ok) throw new Error('HTTP ' + r.status);
}

/** 获取会话内所有反馈（key = seq 字符串, value = "up"|"down"） */
export async function fetchFeedback(
  sessionId: string,
): Promise<Record<string, string>> {
  const r = await fetch(`/api/feedback/${encodeURIComponent(sessionId)}`);
  if (r.status === 401) throw new Error('UNAUTHORIZED');
  if (!r.ok) return {};
  return r.json();
}

/** 获取追问建议（answer 由调用方截断至 800 字符） */
export async function fetchSuggestions(
  question: string,
  answer: string,
): Promise<string[]> {
  const r = await fetch('/api/suggest', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ question, answer }),
  });
  if (r.status === 401) throw new Error('UNAUTHORIZED');
  if (!r.ok) throw new Error('HTTP ' + r.status);
  const j = await r.json();
  return j.suggestions;
}

/* ------------------------------------------------------------------ */
/*  Admin API                                                           */
/* ------------------------------------------------------------------ */

export interface AdminOverview {
  users: number;
  sessions: number;
  messages: number;
  feedback_up: number;
  feedback_down: number;
  badcase_pending: number;
  cache: { entries: number; total_hits: number };
  usage: {
    llm_calls: number;
    tool_calls: number;
    input_tokens: number;
    output_tokens: number;
    errors: number;
    daily: Record<string, { input: number; output: number }>;
  };
}

export interface Badcase {
  id: number;
  user: string;
  session: string;
  session_title: string;
  status: 'pending' | 'resolved' | 'ignored';
  note: string;
  question: string;
  answer_excerpt: string;
  created: number;
}

export interface AdminSession {
  id: string;
  user: string;
  title: string;
  msg_count: number;
  updated_at: number;
}

export interface AdminMessage {
  role: 'user' | 'assistant';
  content: string;
}

/** 管理后台：用量概览 */
export async function fetchAdminOverview(): Promise<AdminOverview> {
  const r = await fetch('/api/admin/overview');
  if (r.status === 401) throw new Error('UNAUTHORIZED');
  if (!r.ok) throw new Error('HTTP ' + r.status);
  return r.json();
}

/** 管理后台：Badcase 列表 */
export async function fetchBadcases(): Promise<Badcase[]> {
  const r = await fetch('/api/admin/badcases');
  if (r.status === 401) throw new Error('UNAUTHORIZED');
  if (!r.ok) throw new Error('HTTP ' + r.status);
  return r.json();
}

/** 管理后台：更新 Badcase 状态 */
export async function updateBadcase(fid: number, status: string, note: string): Promise<void> {
  const r = await fetch(`/api/admin/badcase/${fid}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ status, note }),
  });
  if (r.status === 401) throw new Error('UNAUTHORIZED');
  if (!r.ok) throw new Error('HTTP ' + r.status);
}

/** 管理后台：全部会话列表 */
export async function fetchAdminSessions(): Promise<AdminSession[]> {
  const r = await fetch('/api/admin/sessions');
  if (r.status === 401) throw new Error('UNAUTHORIZED');
  if (!r.ok) throw new Error('HTTP ' + r.status);
  return r.json();
}

/** 管理后台：会话消息列表 */
export async function fetchAdminMessages(sid: string): Promise<AdminMessage[]> {
  const r = await fetch(`/api/admin/sessions/${encodeURIComponent(sid)}/messages`);
  if (r.status === 401) throw new Error('UNAUTHORIZED');
  if (!r.ok) throw new Error('HTTP ' + r.status);
  return r.json();
}

/* ------------------------------------------------------------------ */
/*  Dashboard / Assistants / KnowledgeBase                              */
/* ------------------------------------------------------------------ */

export interface Assistant {
  id: string;
  name: string;
  avatar: string;        // 颜色或 emoji token
  system_prompt: string;
  kb_ids: string[];
  model_config: Record<string, unknown>;
  owner: string;
  created_at: number;
  updated_at: number;
}

export interface KnowledgeBase {
  id: string;
  name: string;
  description: string;
  doc_dir: string;
  created_at: number;
  doc_count?: number;   // /api/kbs 附带的文档统计
  doc_size?: number;    // 文档总字节数
}

export interface DashboardStats {
  total_messages: number;
  today_calls: number;
  cache_hit_rate: number;   // 0-1
  badcase_pending: number;
  recent_sessions: Session[];
}

/** 首页仪表盘统计 */
export async function fetchDashboard(): Promise<DashboardStats> {
  const r = await fetch('/api/dashboard');
  if (r.status === 401) throw new Error('UNAUTHORIZED');
  if (!r.ok) throw new Error('HTTP ' + r.status);
  return r.json();
}

/** 智能体列表 */
export async function fetchAssistants(): Promise<Assistant[]> {
  const r = await fetch('/api/assistants');
  if (r.status === 401) throw new Error('UNAUTHORIZED');
  if (!r.ok) throw new Error('HTTP ' + r.status);
  return r.json();
}

/** 创建智能体 */
export async function createAssistant(data: {
  name: string;
  avatar?: string;
  system_prompt?: string;
  kb_ids?: string[];
  model_config?: Record<string, unknown>;
}): Promise<Assistant> {
  const r = await fetch('/api/assistants', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  });
  if (r.status === 401) throw new Error('UNAUTHORIZED');
  if (!r.ok) throw new Error('HTTP ' + r.status);
  return r.json();
}

/** 更新智能体 */
export async function updateAssistant(
  id: string,
  data: Partial<{
    name: string;
    avatar: string;
    system_prompt: string;
    kb_ids: string[];
    model_config: Record<string, unknown>;
  }>,
): Promise<Assistant> {
  const r = await fetch(`/api/assistants/${encodeURIComponent(id)}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  });
  if (r.status === 401) throw new Error('UNAUTHORIZED');
  if (!r.ok) throw new Error('HTTP ' + r.status);
  return r.json();
}

/** 删除智能体 */
export async function deleteAssistant(id: string): Promise<{ ok: boolean }> {
  const r = await fetch(`/api/assistants/${encodeURIComponent(id)}`, {
    method: 'DELETE',
  });
  if (r.status === 401) throw new Error('UNAUTHORIZED');
  if (!r.ok) throw new Error('HTTP ' + r.status);
  return r.json();
}

/** 知识库列表 */
export async function fetchKbs(): Promise<KnowledgeBase[]> {
  const r = await fetch('/api/kbs');
  if (r.status === 401) throw new Error('UNAUTHORIZED');
  if (!r.ok) throw new Error('HTTP ' + r.status);
  return r.json();
}

/** 创建知识库 */
export async function createKb(data: {
  name: string;
  description?: string;
}): Promise<KnowledgeBase> {
  const r = await fetch('/api/kbs', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  });
  if (r.status === 401) throw new Error('UNAUTHORIZED');
  if (!r.ok) throw new Error('HTTP ' + r.status);
  return r.json();
}

/** 删除知识库 */
export async function deleteKb(id: string): Promise<{ ok: boolean }> {
  const r = await fetch(`/api/kbs/${encodeURIComponent(id)}`, {
    method: 'DELETE',
  });
  if (r.status === 401) throw new Error('UNAUTHORIZED');
  if (!r.ok) throw new Error('HTTP ' + r.status);
  return r.json();
}

/** 重建知识库索引（异步任务：返回 task_id，进度查 fetchIngestTasks） */
export async function reindexKb(
  id: string,
): Promise<{ ok: boolean; task_id?: number; result?: unknown }> {
  const r = await fetch(`/api/kbs/${encodeURIComponent(id)}/reindex`, {
    method: 'POST',
  });
  if (r.status === 401) throw new Error('UNAUTHORIZED');
  if (!r.ok) throw new Error('HTTP ' + r.status);
  return r.json();
}

/** 注销当前账号 */
/** 修改自己的头像（avatar token：db:{style}:{seed} 或 emoji） */
export interface PendingAvatarReview {
  username: string;
  avatar: string;
  pending_avatar: string;
  pending_avatar_at: number;
}

/** 上传自定义头像（前端已压缩），进入待审核 */
export async function uploadAvatar(blob: Blob): Promise<{ ok: boolean; pending: string }> {
  const fd = new FormData();
  fd.append('file', blob, 'avatar.png');
  const r = await fetch('/api/me/avatar-upload', { method: 'POST', body: fd });
  if (r.status === 401) throw new Error('UNAUTHORIZED');
  if (!r.ok) {
    const d = await r.json().catch(() => ({}));
    throw new Error(typeof d.detail === 'string' ? d.detail : 'HTTP ' + r.status);
  }
  return r.json();
}

/** 管理员：待审核头像队列 */
export async function fetchAvatarReviews(): Promise<PendingAvatarReview[]> {
  const r = await fetch('/api/admin/avatar-reviews');
  if (r.status === 401) throw new Error('UNAUTHORIZED');
  if (!r.ok) throw new Error('HTTP ' + r.status);
  return r.json();
}

/** 管理员：审核（approve 转正 / reject 驳回） */
export async function reviewAvatar(
  username: string,
  action: 'approve' | 'reject',
): Promise<{ ok: boolean }> {
  const r = await fetch(`/api/admin/avatar-review/${encodeURIComponent(username)}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ action }),
  });
  if (r.status === 401) throw new Error('UNAUTHORIZED');
  if (!r.ok) {
    const d = await r.json().catch(() => ({}));
    throw new Error(typeof d.detail === 'string' ? d.detail : 'HTTP ' + r.status);
  }
  return r.json();
}

export async function changeAvatar(avatar: string): Promise<{ ok: boolean }> {
  const r = await fetch('/api/me/avatar', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ avatar }),
  });
  if (r.status === 401) throw new Error('UNAUTHORIZED');
  if (!r.ok) throw new Error('HTTP ' + r.status);
  return r.json();
}

export async function deleteAccount(): Promise<{ ok: boolean }> {
  const r = await fetch('/api/me/delete', {
    method: 'POST',
  });
  if (r.status === 401) throw new Error('UNAUTHORIZED');
  if (!r.ok) throw new Error('HTTP ' + r.status);
  return r.json();
}

/* ------------------------------------------------------------------ */
/*  知识库文档                                                          */
/* ------------------------------------------------------------------ */

export interface ContentHit {
  name: string;
  count: number;
  snippets: string[];
}

export interface KbDoc {
  name: string;
  size: number;
  modified: string;   // ISO 时间
}

/** 知识库文档列表 */
export async function fetchKbDocs(kbId: string): Promise<KbDoc[]> {
  const r = await fetch(`/api/kbs/${encodeURIComponent(kbId)}/docs`);
  if (r.status === 401) throw new Error('UNAUTHORIZED');
  if (!r.ok) throw new Error('HTTP ' + r.status);
  return r.json();
}

/** 上传文档（multipart，字段名 file）；失败时解析后端 detail 抛出 */
export async function uploadKbDoc(
  kbId: string,
  file: File,
): Promise<{ ok: boolean; name: string; size: number }> {
  const fd = new FormData();
  fd.append('file', file);
  const r = await fetch(`/api/kbs/${encodeURIComponent(kbId)}/docs`, {
    method: 'POST',
    body: fd,
  });
  if (r.status === 401) throw new Error('UNAUTHORIZED');
  if (!r.ok) {
    const data = await r.json().catch(() => ({}));
    throw new Error(typeof data.detail === 'string' ? data.detail : 'HTTP ' + r.status);
  }
  return r.json();
}

/** 删除知识库文档（需随后重建索引才从检索中移除） */
export async function deleteKbDoc(kbId: string, filename: string): Promise<{ ok: boolean }> {
  const r = await fetch(
    `/api/kbs/${encodeURIComponent(kbId)}/docs/${encodeURIComponent(filename)}`,
    { method: 'DELETE' },
  );
  if (r.status === 401) throw new Error('UNAUTHORIZED');
  if (!r.ok) throw new Error('HTTP ' + r.status);
  return r.json();
}

/* ------------------------------------------------------------------ */
/*  用量与成本（管理端）                                                 */
/* ------------------------------------------------------------------ */

export interface UsageSummary {
  total_calls: number;
  total_input_tokens: number;
  total_output_tokens: number;
  total_cost: number;
}

export interface UsageByModel {
  model: string;
  calls: number;
  input_tokens: number;
  output_tokens: number;
  cost: number;
}

export interface UsageDaily {
  date: string;
  input_tokens: number;
  output_tokens: number;
  cost: number;
}

export interface UsageDetail {
  summary: UsageSummary;
  by_model: UsageByModel[];
  daily: UsageDaily[];
}

export interface TopQuery {
  query: string;
  calls: number;
  input_tokens: number;
  output_tokens: number;
  cost: number;
}

/** 用量与成本明细（按天/按模型聚合） */
export async function fetchAdminUsage(days = 30): Promise<UsageDetail> {
  const r = await fetch(`/api/admin/usage?days=${days}`);
  if (r.status === 401) throw new Error('UNAUTHORIZED');
  if (!r.ok) throw new Error('HTTP ' + r.status);
  return r.json();
}

/** 高成本 Query Top N */
export async function fetchTopQueries(
  days = 30,
  limit = 10,
): Promise<{ items: TopQuery[]; total: number }> {
  const r = await fetch(`/api/admin/usage/top-queries?days=${days}&limit=${limit}`);
  if (r.status === 401) throw new Error('UNAUTHORIZED');
  if (!r.ok) throw new Error('HTTP ' + r.status);
  return r.json();
}

/* ------------------------------------------------------------------ */
/*  检索日志（管理端）                                                   */
/* ------------------------------------------------------------------ */

export interface TraceItem {
  id?: string;
  ts: string;
  name: string;
  kind: string;
  status?: string;
  duration_ms?: number;
  model?: string;
  usage?: { input: number; output: number };
  input?: unknown;
  output?: unknown;
  [key: string]: unknown;
}

export interface TraceQuery {
  page?: number;
  page_size?: number;
  kind?: string;
  status?: string;
  q?: string;
  start?: string;   // YYYY-MM-DD
  end?: string;     // YYYY-MM-DD
}

/** trace 日志分页检索（服务端过滤） */
export async function fetchTraces(
  params: TraceQuery & { kb?: string },
): Promise<{ items: TraceItem[]; total: number }> {
  const sp = new URLSearchParams();
  Object.entries(params).forEach(([k, v]) => {
    if (v !== undefined && v !== '') sp.set(k, String(v));
  });
  const r = await fetch(`/api/admin/traces?${sp.toString()}`);
  if (r.status === 401) throw new Error('UNAUTHORIZED');
  if (!r.ok) throw new Error('HTTP ' + r.status);
  return r.json();
}

/* ------------------------------------------------------------------ */
/*  检索调优实验室                                                      */
/* ------------------------------------------------------------------ */

export interface RetrievalStage {
  stage: string;
  duration_ms: number;
  count: number;
  error?: string;
}

export interface RetrievalHit {
  rank: number;
  text: string;
  source: string;
  page?: number;
  score: number;
}

export interface RetrievalDebugResult {
  question: string;
  kb_id: string;
  route: string;
  stages: RetrievalStage[];
  hits: RetrievalHit[];
}

/** 检索调试：召回明细 + 分数 + 路线 + 各阶段耗时 */
export async function debugRetrieval(params: {
  question: string;
  kb_id?: string;
  top_k?: number;
  rerank?: boolean;
}): Promise<RetrievalDebugResult> {
  const r = await fetch('/api/retrieval/debug', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(params),
  });
  if (r.status === 401) throw new Error('UNAUTHORIZED');
  if (!r.ok) {
    const data = await r.json().catch(() => ({}));
    throw new Error(typeof data.detail === 'string' ? data.detail : 'HTTP ' + r.status);
  }
  return r.json();
}

export interface StageStat {
  stage: string;
  count: number;
  avg_ms: number;
  p95_ms: number;
}

/** 链路分析：各检索阶段的平均/P95 耗时（trace 聚合） */
export async function fetchStageStats(): Promise<{ stages: StageStat[] }> {
  const r = await fetch('/api/retrieval/stage-stats');
  if (r.status === 401) throw new Error('UNAUTHORIZED');
  if (!r.ok) throw new Error('HTTP ' + r.status);
  return r.json();
}

/* ------------------------------------------------------------------ */
/*  评测体系                                                            */
/* ------------------------------------------------------------------ */

export interface EvalSample {
  question: string;
  expected: string;
}

export interface EvalDataset {
  id: number;
  name: string;
  kb_id: string;
  items: EvalSample[];
  created_at: number;
}

export interface EvalRunDetail {
  question: string;
  expected: string;
  hit_rank: number | null;
  top1: string;
  top1_score: number | null;
}

export interface EvalRun {
  id: number;
  dataset_id: number;
  mode: string;          // dense / rrf / rerank
  top_k: number;
  status: 'pending' | 'running' | 'done' | 'error';
  recall: number;
  mrr: number;
  total: number;
  hits: number;
  miss_count?: number;
  duration_ms: number;
  created_by: string;
  created_at: number;
  details?: EvalRunDetail[];
}

export async function fetchEvalDatasets(): Promise<EvalDataset[]> {
  const r = await fetch('/api/admin/eval/datasets');
  if (r.status === 401) throw new Error('UNAUTHORIZED');
  if (!r.ok) throw new Error('HTTP ' + r.status);
  return r.json();
}

export async function createEvalDataset(data: {
  name: string;
  kb_id?: string;
  items: EvalSample[];
}): Promise<EvalDataset> {
  const r = await fetch('/api/admin/eval/datasets', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  });
  if (r.status === 401) throw new Error('UNAUTHORIZED');
  if (!r.ok) throw new Error('HTTP ' + r.status);
  return r.json();
}

export async function updateEvalDataset(
  id: number,
  data: Partial<{ name: string; kb_id: string; items: EvalSample[] }>,
): Promise<EvalDataset> {
  const r = await fetch(`/api/admin/eval/datasets/${id}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  });
  if (r.status === 401) throw new Error('UNAUTHORIZED');
  if (!r.ok) throw new Error('HTTP ' + r.status);
  return r.json();
}

export async function deleteEvalDataset(id: number): Promise<{ ok: boolean }> {
  const r = await fetch(`/api/admin/eval/datasets/${id}`, { method: 'DELETE' });
  if (r.status === 401) throw new Error('UNAUTHORIZED');
  if (!r.ok) throw new Error('HTTP ' + r.status);
  return r.json();
}

/** 启动一次评测运行（后台线程执行，202 返回 run_id） */
export async function runEval(
  datasetId: number,
  opts: { mode?: string; top_k?: number },
): Promise<{ ok: boolean; run_id: number }> {
  const r = await fetch(`/api/admin/eval/datasets/${datasetId}/run`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(opts),
  });
  if (r.status === 401) throw new Error('UNAUTHORIZED');
  if (!r.ok) throw new Error('HTTP ' + r.status);
  return r.json();
}

export async function fetchEvalRuns(datasetId = 0, limit = 50): Promise<EvalRun[]> {
  const r = await fetch(`/api/admin/eval/runs?dataset_id=${datasetId}&limit=${limit}`);
  if (r.status === 401) throw new Error('UNAUTHORIZED');
  if (!r.ok) throw new Error('HTTP ' + r.status);
  return r.json();
}

export async function fetchEvalRun(runId: number): Promise<EvalRun> {
  const r = await fetch(`/api/admin/eval/runs/${runId}`);
  if (r.status === 401) throw new Error('UNAUTHORIZED');
  if (!r.ok) throw new Error('HTTP ' + r.status);
  return r.json();
}

/* ------------------------------------------------------------------ */
/*  质量监控                                                            */
/* ------------------------------------------------------------------ */

export interface QualityData {
  feedback: { up: number; down: number; badcase_pending: number };
  refusals: number;
  cache: { entries: number; total_hits: number };
  eval_trend: { date: string; mode: string; recall: number }[];
}

/** 质量信号聚合：反馈 + 拒答 + 缓存 + 评测趋势 */
export async function fetchQuality(days = 30): Promise<QualityData> {
  const r = await fetch(`/api/admin/quality?days=${days}`);
  if (r.status === 401) throw new Error('UNAUTHORIZED');
  if (!r.ok) throw new Error('HTTP ' + r.status);
  return r.json();
}

/* ------------------------------------------------------------------ */
/*  API Key 管理                                                        */
/* ------------------------------------------------------------------ */

export interface ApiKey {
  id: number;
  name: string;
  prefix: string;
  scope_kb_ids: string[];
  created_by: string;
  created_at: number;
  expires_at: number | null;
  revoked_at: number | null;
  last_used_at: number | null;
  active: boolean;
  key?: string;   // 仅创建/轮换响应中出现一次
}

export async function fetchApiKeys(): Promise<ApiKey[]> {
  const r = await fetch('/api/admin/api-keys');
  if (r.status === 401) throw new Error('UNAUTHORIZED');
  if (!r.ok) throw new Error('HTTP ' + r.status);
  return r.json();
}

export async function createApiKey(data: {
  name: string;
  scope_kb_ids?: string[];
  expires_days?: number;
}): Promise<ApiKey> {
  const r = await fetch('/api/admin/api-keys', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  });
  if (r.status === 401) throw new Error('UNAUTHORIZED');
  if (!r.ok) throw new Error('HTTP ' + r.status);
  return r.json();
}

export async function revokeApiKey(id: number): Promise<{ ok: boolean }> {
  const r = await fetch(`/api/admin/api-keys/${id}`, { method: 'DELETE' });
  if (r.status === 401) throw new Error('UNAUTHORIZED');
  if (!r.ok) throw new Error('HTTP ' + r.status);
  return r.json();
}

export async function rotateApiKey(id: number): Promise<ApiKey> {
  const r = await fetch(`/api/admin/api-keys/${id}/rotate`, { method: 'POST' });
  if (r.status === 401) throw new Error('UNAUTHORIZED');
  if (!r.ok) throw new Error('HTTP ' + r.status);
  return r.json();
}

/* ------------------------------------------------------------------ */
/*  入库任务                                                            */
/* ------------------------------------------------------------------ */

export interface IngestTask {
  id: number;
  kb_id: string;
  filename: string;
  mode: 'upload' | 'delete' | 'reindex';
  status: 'pending' | 'running' | 'done' | 'error';
  message: string;
  created_by: string;
  created_at: number;
  updated_at: number;
}

export async function fetchIngestTasks(kbId: string, limit = 50): Promise<IngestTask[]> {
  const r = await fetch(`/api/kbs/${encodeURIComponent(kbId)}/tasks?limit=${limit}`);
  if (r.status === 401) throw new Error('UNAUTHORIZED');
  if (!r.ok) throw new Error('HTTP ' + r.status);
  return r.json();
}

/* ------------------------------------------------------------------ */
/*  模型管理                                                            */
/* ------------------------------------------------------------------ */

export interface ModelConfig {
  id: number;
  name: string;
  kind: 'llm' | 'embedding' | 'rerank';
  base_url: string;
  api_key_masked: string;
  model_name: string;
  is_active: number;
  created_by: string;
  created_at: number;
}

export async function fetchModels(kind = ''): Promise<ModelConfig[]> {
  const r = await fetch(`/api/admin/models?kind=${kind}`);
  if (r.status === 401) throw new Error('UNAUTHORIZED');
  if (!r.ok) throw new Error('HTTP ' + r.status);
  return r.json();
}

export async function createModel(data: {
  name: string;
  kind: string;
  base_url?: string;
  api_key?: string;
  model_name: string;
}): Promise<{ ok: boolean; id: number }> {
  const r = await fetch('/api/admin/models', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  });
  if (r.status === 401) throw new Error('UNAUTHORIZED');
  if (!r.ok) throw new Error('HTTP ' + r.status);
  return r.json();
}

export async function updateModel(
  id: number,
  data: Partial<{ name: string; base_url: string; api_key: string; model_name: string }>,
): Promise<{ ok: boolean }> {
  const r = await fetch(`/api/admin/models/${id}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  });
  if (r.status === 401) throw new Error('UNAUTHORIZED');
  if (!r.ok) throw new Error('HTTP ' + r.status);
  return r.json();
}

export async function deleteModel(id: number): Promise<{ ok: boolean }> {
  const r = await fetch(`/api/admin/models/${id}`, { method: 'DELETE' });
  if (r.status === 401) throw new Error('UNAUTHORIZED');
  if (!r.ok) throw new Error('HTTP ' + r.status);
  return r.json();
}

export async function activateModel(id: number): Promise<{ ok: boolean }> {
  const r = await fetch(`/api/admin/models/${id}/activate`, { method: 'POST' });
  if (r.status === 401) throw new Error('UNAUTHORIZED');
  if (!r.ok) throw new Error('HTTP ' + r.status);
  return r.json();
}

export async function testModel(
  id: number,
): Promise<{ ok: boolean; latency_ms?: number; detail: string }> {
  const r = await fetch(`/api/admin/models/${id}/test`, { method: 'POST' });
  if (r.status === 401) throw new Error('UNAUTHORIZED');
  if (!r.ok) throw new Error('HTTP ' + r.status);
  return r.json();
}

/* ------------------------------------------------------------------ */
/*  审计中心                                                            */
/* ------------------------------------------------------------------ */

export interface AuditEvent {
  id: number;
  actor: string;
  action: string;
  target: string;
  detail: string;
  created_at: number;
}

export async function fetchAudit(params: {
  actor?: string;
  action?: string;
  days?: number;
  limit?: number;
}): Promise<AuditEvent[]> {
  const sp = new URLSearchParams();
  Object.entries(params).forEach(([k, v]) => {
    if (v !== undefined && v !== '') sp.set(k, String(v));
  });
  const r = await fetch(`/api/admin/audit?${sp.toString()}`);
  if (r.status === 401) throw new Error('UNAUTHORIZED');
  if (!r.ok) throw new Error('HTTP ' + r.status);
  return r.json();
}

/** 导出审计 CSV（浏览器直接下载） */
export async function exportAuditCsv(params: {
  actor?: string;
  action?: string;
  days?: number;
}): Promise<void> {
  const sp = new URLSearchParams();
  Object.entries(params).forEach(([k, v]) => {
    if (v !== undefined && v !== '') sp.set(k, String(v));
  });
  const r = await fetch(`/api/admin/audit/export?${sp.toString()}`);
  if (r.status === 401) throw new Error('UNAUTHORIZED');
  if (!r.ok) throw new Error('HTTP ' + r.status);
  const blob = await r.blob();
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = `audit_${new Date().toISOString().slice(0, 19).replace(/[T:]/g, '')}.csv`;
  a.click();
  URL.revokeObjectURL(url);
}

/* ------------------------------------------------------------------ */
/*  备份与恢复                                                          */
/* ------------------------------------------------------------------ */

export interface BackupItem {
  name: string;
  size: number;
  created_at: number;
}

export async function fetchBackups(): Promise<BackupItem[]> {
  const r = await fetch('/api/admin/backups');
  if (r.status === 401) throw new Error('UNAUTHORIZED');
  if (!r.ok) throw new Error('HTTP ' + r.status);
  return r.json();
}

export async function createBackup(): Promise<{
  ok: boolean;
  name: string;
  size: number;
  files: number;
}> {
  const r = await fetch('/api/admin/backup', { method: 'POST' });
  if (r.status === 401) throw new Error('UNAUTHORIZED');
  if (!r.ok) throw new Error('HTTP ' + r.status);
  return r.json();
}

/* ------------------------------------------------------------------ */
/*  告警与 SLA                                                          */
/* ------------------------------------------------------------------ */

export interface AlertItem {
  id: number;
  type: string;
  severity: string;
  message: string;
  dedupe_key: string;
  status: 'open' | 'acknowledged' | 'resolved';
  created_at: number;
  acked_at: number | null;
  resolved_at: number | null;
}

export interface SlaData {
  days: number;
  total: number;
  ok: number;
  availability: number;
  p50_ms: number;
  p95_ms: number;
  daily: { date: string; total: number; availability: number; p95_ms: number }[];
}

export async function fetchAlerts(status = '', limit = 100): Promise<AlertItem[]> {
  const r = await fetch(`/api/admin/alerts?status=${status}&limit=${limit}`);
  if (r.status === 401) throw new Error('UNAUTHORIZED');
  if (!r.ok) throw new Error('HTTP ' + r.status);
  return r.json();
}

export async function evaluateAlerts(): Promise<{
  ok: boolean;
  created: { id: number; type: string; severity: string; message: string }[];
}> {
  const r = await fetch('/api/admin/alerts/evaluate', { method: 'POST' });
  if (r.status === 401) throw new Error('UNAUTHORIZED');
  if (!r.ok) throw new Error('HTTP ' + r.status);
  return r.json();
}

export async function ackAlert(id: number): Promise<{ ok: boolean }> {
  const r = await fetch(`/api/admin/alerts/${id}/ack`, { method: 'POST' });
  if (r.status === 401) throw new Error('UNAUTHORIZED');
  if (!r.ok) throw new Error('HTTP ' + r.status);
  return r.json();
}

export async function resolveAlert(id: number): Promise<{ ok: boolean }> {
  const r = await fetch(`/api/admin/alerts/${id}/resolve`, { method: 'POST' });
  if (r.status === 401) throw new Error('UNAUTHORIZED');
  if (!r.ok) throw new Error('HTTP ' + r.status);
  return r.json();
}

/* ------------------------------------------------------------------ */
/*  语音：ASR 输入 + TTS 播报                                            */
/* ------------------------------------------------------------------ */

export interface VoiceOption {
  id: string;
  label: string;
}

export async function fetchVoices(): Promise<VoiceOption[]> {
  const r = await fetch('/api/voice/voices');
  if (r.status === 401) throw new Error('UNAUTHORIZED');
  if (!r.ok) throw new Error('HTTP ' + r.status);
  return r.json();
}

/** 语音识别：上传 16k WAV，返回识别文本 */
export async function transcribeAudio(wav: Blob): Promise<string> {
  const fd = new FormData();
  fd.append('file', wav, 'rec.wav');
  const r = await fetch('/api/voice/asr', { method: 'POST', body: fd });
  if (r.status === 401) throw new Error('UNAUTHORIZED');
  if (!r.ok) {
    const d = await r.json().catch(() => ({}));
    throw new Error(typeof d.detail === 'string' ? d.detail : 'HTTP ' + r.status);
  }
  return (await r.json()).text || '';
}

/** 语音合成：返回 MP3 Blob */
export async function synthesizeSpeech(text: string, voice: string): Promise<Blob> {
  const r = await fetch('/api/voice/tts', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ text, voice }),
  });
  if (r.status === 401) throw new Error('UNAUTHORIZED');
  if (!r.ok) {
    const d = await r.json().catch(() => ({}));
    throw new Error(typeof d.detail === 'string' ? d.detail : 'HTTP ' + r.status);
  }
  return r.blob();
}

export async function fetchSla(days = 7): Promise<SlaData> {
  const r = await fetch(`/api/admin/sla?days=${days}`);
  if (r.status === 401) throw new Error('UNAUTHORIZED');
  if (!r.ok) throw new Error('HTTP ' + r.status);
  return r.json();
}

/* ------------------------------------------------------------------ */
/*  用户管理                                                            */
/* ------------------------------------------------------------------ */

export interface AdminUser {
  username: string;
  is_admin: number;
  must_change_pwd: number;
  created_at: number;
  sessions: number;
  messages: number;
  avatar?: string;
  pending_avatar?: string;
}

export async function fetchUsers(): Promise<AdminUser[]> {
  const r = await fetch('/api/admin/users');
  if (r.status === 401) throw new Error('UNAUTHORIZED');
  if (!r.ok) throw new Error('HTTP ' + r.status);
  return r.json();
}

export async function createUser(data: {
  username: string;
  password: string;
  is_admin?: boolean;
}): Promise<{ ok: boolean; username: string }> {
  const r = await fetch('/api/admin/users', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  });
  if (r.status === 401) throw new Error('UNAUTHORIZED');
  if (!r.ok) {
    const d = await r.json().catch(() => ({}));
    throw new Error(typeof d.detail === 'string' ? d.detail : 'HTTP ' + r.status);
  }
  return r.json();
}

export async function resetUserPassword(
  username: string,
  newPassword: string,
): Promise<{ ok: boolean; message: string }> {
  const r = await fetch(
    `/api/admin/users/${encodeURIComponent(username)}/reset-password`,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ new_password: newPassword }),
    },
  );
  if (r.status === 401) throw new Error('UNAUTHORIZED');
  if (!r.ok) {
    const d = await r.json().catch(() => ({}));
    throw new Error(typeof d.detail === 'string' ? d.detail : 'HTTP ' + r.status);
  }
  return r.json();
}

export async function setUserAdmin(
  username: string,
  isAdmin: boolean,
): Promise<{ ok: boolean }> {
  const r = await fetch(
    `/api/admin/users/${encodeURIComponent(username)}/admin`,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ is_admin: isAdmin }),
    },
  );
  if (r.status === 401) throw new Error('UNAUTHORIZED');
  if (!r.ok) {
    const d = await r.json().catch(() => ({}));
    throw new Error(typeof d.detail === 'string' ? d.detail : 'HTTP ' + r.status);
  }
  return r.json();
}

export interface UserQuery {
  user: string;
  session_id: string;
  session_title: string;
  question: string;
  created_at: number;
}

/** 管理员：用户提问记录（可按用户/关键词/天数过滤） */
export async function fetchAdminQueries(params: {
  user?: string;
  q?: string;
  days?: number;
  limit?: number;
}): Promise<UserQuery[]> {
  const sp = new URLSearchParams();
  Object.entries(params).forEach(([k, v]) => {
    if (v !== undefined && v !== '' && v !== 0) sp.set(k, String(v));
  });
  const r = await fetch(`/api/admin/queries?${sp.toString()}`);
  if (r.status === 401) throw new Error('UNAUTHORIZED');
  if (!r.ok) throw new Error('HTTP ' + r.status);
  return r.json();
}

export async function deleteUser(
  username: string,
): Promise<{ ok: boolean; deleted: Record<string, number> }> {
  const r = await fetch(`/api/admin/users/${encodeURIComponent(username)}`, {
    method: 'DELETE',
  });
  if (r.status === 401) throw new Error('UNAUTHORIZED');
  if (!r.ok) {
    const d = await r.json().catch(() => ({}));
    throw new Error(typeof d.detail === 'string' ? d.detail : 'HTTP ' + r.status);
  }
  return r.json();
}


function chatStreamXHR(
  payload: Record<string, unknown>,
  signal: AbortSignal | undefined,
  onUploadProgress: (pct: number) => void,
): AsyncGenerator<ChatEvent> {
  const queue: ChatEvent[] = [];
  let wake: (() => void) | null = null;
  let finished = false;
  let failure: Error | null = null;
  let buf = '';
  let lastLen = 0;

  const xhr = new XMLHttpRequest();
  xhr.open('POST', '/api/chat/stream');
  xhr.setRequestHeader('Content-Type', 'application/json');
  signal?.addEventListener('abort', () => xhr.abort());
  xhr.upload.onprogress = (e) => {
    if (e.lengthComputable && e.total > 0) {
      onUploadProgress(Math.min(100, Math.round((e.loaded / e.total) * 100)));
    }
  };
  const drain = () => {
    const text = xhr.responseText;
    buf += text.slice(lastLen);
    lastLen = text.length;
    let idx: number;
    while ((idx = buf.indexOf('\n\n')) !== -1) {
      const frame = buf.slice(0, idx);
      buf = buf.slice(idx + 2);
      let event = 'message';
      let data = '';
      for (const line of frame.split('\n')) {
        if (line.startsWith('event: ')) event = line.slice(7);
        else if (line.startsWith('data: ')) data = line.slice(6);
      }
      if (!data) continue;
      try {
        queue.push({ event: event as ChatEventKind, data: JSON.parse(data) });
      } catch {
        /* 忽略残帧 */
      }
    }
  };
  xhr.onprogress = () => { drain(); wake?.(); };
  const finish = (err?: Error) => {
    failure = err ?? null;
    finished = true;
    wake?.();
  };
  xhr.onload = () => {
    if (xhr.status === 401) finish(new Error('UNAUTHORIZED'));
    else if (xhr.status >= 400) finish(new Error('HTTP ' + xhr.status));
    else { drain(); finish(); }
  };
  xhr.onerror = () => finish(new Error('HTTP ' + xhr.status));
  xhr.onabort = () => finish();
  xhr.send(JSON.stringify(payload));

  return (async function* () {
    while (true) {
      while (queue.length) yield queue.shift()!;
      if (finished) {
        if (failure) throw failure;
        return;
      }
      await new Promise<void>((r) => { wake = r; });
    }
  })();
}
