/** 运维：告警与 SLA */

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

export async function fetchSla(days = 7): Promise<SlaData> {
  const r = await fetch(`/api/admin/sla?days=${days}`);
  if (r.status === 401) throw new Error('UNAUTHORIZED');
  if (!r.ok) throw new Error('HTTP ' + r.status);
  return r.json();
}
