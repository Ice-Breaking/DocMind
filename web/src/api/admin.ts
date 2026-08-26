/** 管理后台：概览/Badcase/仪表盘/检索日志/质量信号/审计中心/备份恢复 */

import type { Session } from './chat';

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
  first_image?: string;   // 首条 user 消息携带的图片(审计页标题列缩略)
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
/*  RAGAS 式生成质量评测                                                 */
/* ------------------------------------------------------------------ */

export interface RagasMetric {
  score: number | null;
  note?: string;   // score 为 null 时的原因（判官解析失败 / 执行异常等）
}

export interface RagasResult {
  metrics: Record<string, RagasMetric>;
  summary: { avg_score: number | null; scored_metrics: number };
  meta: { question: string; contexts: number; elapsed_ms: number };
}

/** 单条 RAGAS 四指标评估（忠实度/答案相关性/上下文精确率/召回率） */
export async function runRagas(body: {
  question: string;
  answer: string;
  contexts: string[];
  expected_answer?: string;
}): Promise<RagasResult> {
  const r = await fetch('/api/admin/eval/ragas', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  if (r.status === 401) throw new Error('UNAUTHORIZED');
  if (!r.ok) {
    let detail = 'HTTP ' + r.status;
    try {
      detail = (await r.json()).detail || detail;
    } catch { /* 非 JSON 错误体，保留默认 */ }
    throw new Error(detail);
  }
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
