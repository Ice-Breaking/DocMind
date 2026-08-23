/** API Key 签发与管理 */

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
