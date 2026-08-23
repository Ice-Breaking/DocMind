/** 智能体（助手）注册表 CRUD */

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
