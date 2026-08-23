/** 身份与认证：登录态 / 登录 / 登出 / 改密 */

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
