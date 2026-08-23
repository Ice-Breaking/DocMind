/** 知识库：CRUD / 文档列表上传 / 入库任务 */

export interface KnowledgeBase {
  id: string;
  name: string;
  description: string;
  doc_dir: string;
  created_at: number;
  doc_count?: number;   // /api/kbs 附带的文档统计
  doc_size?: number;    // 文档总字节数
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
