/**
 * 后端 API 封装：身份 / 登出 / SSE 聊天流。
 * SSE 事件协议见 docmind/chat_stream.py：
 *   cache / thinking / token / step / error / final / done
 */

export interface Me {
  user: string;
  is_admin: boolean;
}

export type ChatEventKind =
  | 'cache' | 'thinking' | 'token' | 'step' | 'error' | 'final' | 'done';

export interface ChatEvent {
  event: ChatEventKind;
  data: Record<string, any>;
}

/** 当前登录身份（未登录返回空 user） */
export async function fetchMe(): Promise<Me> {
  const r = await fetch('/api/me');
  if (!r.ok) return { user: '', is_admin: false };
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

/**
 * SSE 聊天流：异步生成器逐事件 yield。
 * 用 fetch + ReadableStream 手写解析（EventSource 不支持 POST body）。
 */
export async function* chatStream(
  question: string,
  sessionId: string,
): AsyncGenerator<ChatEvent> {
  const resp = await fetch('/api/chat/stream', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ question, session_id: sessionId }),
  });
  if (resp.status === 401) throw new Error('UNAUTHORIZED');
  if (!resp.ok || !resp.body) throw new Error('HTTP ' + resp.status);

  const reader = resp.body.getReader();
  const decoder = new TextDecoder();
  let buf = '';
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buf += decoder.decode(value, { stream: true });
    // SSE 帧以空行分隔：event: xxx\ndata: {...}\n\n
    let idx: number;
    while ((idx = buf.indexOf('\n\n')) !== -1) {
      const frame = buf.slice(0, idx);
      buf = buf.slice(idx + 2);
      let event = 'message';
      let data = '';
      for (const line of frame.split('\n')) {
        if (line.startsWith('event: ')) event = line.slice(7);
        else if (line.startsWith('data: ')) data = line.slice(6);
      }
      if (!data) continue;
      try {
        yield { event: event as ChatEventKind, data: JSON.parse(data) };
      } catch {
        // 非法 JSON 帧跳过，不中断流
      }
    }
  }
}
