/** 评测：数据集与运行记录 + 检索调优实验室 */

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
