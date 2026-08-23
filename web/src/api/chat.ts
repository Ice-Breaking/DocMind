/** 聊天域：SSE 流式协议（见 docmind/chat_stream.py）+ 会话/消息/反馈/追问建议 */

export type ChatEventKind =
  | 'cache' | 'thinking' | 'token' | 'step' | 'error' | 'final' | 'done';

export interface ChatEvent {
  event: ChatEventKind;
  data: Record<string, any>;
}

/**
 * SSE 聊天流：异步生成器逐事件 yield。
 * 用 fetch + ReadableStream 手写解析（EventSource 不支持 POST body）。
 */
export async function* chatStream(
  question: string,
  sessionId: string,
  signal?: AbortSignal,
  assistantId?: string,
  imageData?: string | string[],
  onUploadProgress?: (pct: number) => void,
): AsyncGenerator<ChatEvent> {
  const payload: Record<string, unknown> = { question, session_id: sessionId };
  if (assistantId) payload.assistant_id = assistantId;
  if (imageData) payload.image_data = imageData;   // str | str[](多图)

  // 带图 + 需要进度：走 XHR（upload.onprogress 真实上行百分比），
  // SSE 帧从 responseText 增量解析（与 fetch 路径同一解析逻辑）
  const hasImg = !!imageData && (Array.isArray(imageData) ? imageData.length > 0 : true);
  if (hasImg && onUploadProgress) {
    yield* chatStreamXHR(payload, signal, onUploadProgress);
    return;
  }
  const resp = await fetch('/api/chat/stream', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
    signal,
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

export interface Message {
  id: number;
  seq: number;
  role: string;
  content: string;
  raw: string;
  created_at: number;
}

/** 获取会话全部消息（不截断内容） */
export async function fetchMessages(sessionId: string): Promise<Message[]> {
  const r = await fetch(`/api/sessions/${encodeURIComponent(sessionId)}/messages`);
  if (r.status === 401) throw new Error('UNAUTHORIZED');
  if (!r.ok) throw new Error('HTTP ' + r.status);
  return r.json();
}

/* ------------------------------------------------------------------ */
/*  Session / Feedback / Suggestions                                    */
/* ------------------------------------------------------------------ */

export interface Session {
  id: string;
  title: string;
  msg_count: number;
  updated_at: number;
  last_msg?: string;
  assistant_id?: string;
}

/** 获取会话列表（按最近活动排序） */
export async function fetchSessions(): Promise<Session[]> {
  const r = await fetch('/api/sessions');
  if (r.status === 401) throw new Error('UNAUTHORIZED');
  if (!r.ok) throw new Error('HTTP ' + r.status);
  return r.json();
}

/** 删除指定会话 */
export async function deleteSession(sessionId: string): Promise<void> {
  const r = await fetch(`/api/sessions/${encodeURIComponent(sessionId)}`, {
    method: 'DELETE',
  });
  if (r.status === 401) throw new Error('UNAUTHORIZED');
  if (!r.ok) throw new Error('HTTP ' + r.status);
}

/** 提交消息点赞/点踩 */
export async function submitFeedback(
  sessionId: string,
  seq: number,
  rating: 'up' | 'down',
): Promise<void> {
  const r = await fetch('/api/feedback', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ session_id: sessionId, seq, rating }),
  });
  if (r.status === 401) throw new Error('UNAUTHORIZED');
  if (!r.ok) throw new Error('HTTP ' + r.status);
}

/** 获取会话内所有反馈（key = seq 字符串, value = "up"|"down"） */
export async function fetchFeedback(
  sessionId: string,
): Promise<Record<string, string>> {
  const r = await fetch(`/api/feedback/${encodeURIComponent(sessionId)}`);
  if (r.status === 401) throw new Error('UNAUTHORIZED');
  if (!r.ok) return {};
  return r.json();
}

/** 获取追问建议（answer 由调用方截断至 800 字符） */
export async function fetchSuggestions(
  question: string,
  answer: string,
): Promise<string[]> {
  const r = await fetch('/api/suggest', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ question, answer }),
  });
  if (r.status === 401) throw new Error('UNAUTHORIZED');
  if (!r.ok) throw new Error('HTTP ' + r.status);
  const j = await r.json();
  return j.suggestions;
}

function chatStreamXHR(
  payload: Record<string, unknown>,
  signal: AbortSignal | undefined,
  onUploadProgress: (pct: number) => void,
): AsyncGenerator<ChatEvent> {
  const queue: ChatEvent[] = [];
  let wake: (() => void) | null = null;
  let finished = false;
  let failure: Error | null = null;
  let buf = '';
  let lastLen = 0;

  const xhr = new XMLHttpRequest();
  xhr.open('POST', '/api/chat/stream');
  xhr.setRequestHeader('Content-Type', 'application/json');
  signal?.addEventListener('abort', () => xhr.abort());
  xhr.upload.onprogress = (e) => {
    if (e.lengthComputable && e.total > 0) {
      onUploadProgress(Math.min(100, Math.round((e.loaded / e.total) * 100)));
    }
  };
  const drain = () => {
    const text = xhr.responseText;
    buf += text.slice(lastLen);
    lastLen = text.length;
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
        queue.push({ event: event as ChatEventKind, data: JSON.parse(data) });
      } catch {
        /* 忽略残帧 */
      }
    }
  };
  xhr.onprogress = () => { drain(); wake?.(); };
  const finish = (err?: Error) => {
    failure = err ?? null;
    finished = true;
    wake?.();
  };
  xhr.onload = () => {
    if (xhr.status === 401) finish(new Error('UNAUTHORIZED'));
    else if (xhr.status >= 400) finish(new Error('HTTP ' + xhr.status));
    else { drain(); finish(); }
  };
  xhr.onerror = () => finish(new Error('HTTP ' + xhr.status));
  xhr.onabort = () => finish();
  xhr.send(JSON.stringify(payload));

  return (async function* () {
    while (true) {
      while (queue.length) yield queue.shift()!;
      if (finished) {
        if (failure) throw failure;
        return;
      }
      await new Promise<void>((r) => { wake = r; });
    }
  })();
}
