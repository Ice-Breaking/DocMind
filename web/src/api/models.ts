/** 模型管理：LLM / Embedding / Rerank 注册表 */

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
