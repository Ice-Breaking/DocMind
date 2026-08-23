/** 用户与账号：个人资料/头像审核 + 管理员用户管理 */

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
