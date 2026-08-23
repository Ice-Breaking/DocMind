/** 用量与成本（管理端）：按天/按模型聚合 */

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
