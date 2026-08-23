/**
 * Chat 页面纯工具函数。
 *
 * 设计约束：不含任何 React / antd 依赖，输入输出皆为可序列化数据，
 * 保证可在 Vitest（jsdom/node）下直接单测。
 * 带 `now` 形参的函数默认取当前时间，测试时可注入固定时钟。
 */

/** 生成新会话 ID（本地乐观占位，落库以后端为准） */
export function newSessionId(): string {
  return `sess-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}`;
}

const pad = (n: number) => String(n).padStart(2, '0');
const sameDay = (a: Date, b: Date) => a.toDateString() === b.toDateString();

/** 会话时间格式化：今天 HH:MM / 昨天 / 7天内 M月D日 / 更早 M/D（ts 为秒） */
export function fmtSessionTime(ts: number, now: Date = new Date()): string {
  const d = new Date(ts * 1000);
  if (sameDay(d, now)) return `${pad(d.getHours())}:${pad(d.getMinutes())}`;
  const yest = new Date(now);
  yest.setDate(now.getDate() - 1);
  if (sameDay(d, yest)) return '昨天';
  if (now.getTime() - d.getTime() < 7 * 86400_000) return `${d.getMonth() + 1}月${d.getDate()}日`;
  return `${d.getMonth() + 1}/${d.getDate()}`;
}

export interface SessionGroup<T> {
  label: string;
  items: T[];
}

/**
 * 会话列表按时间分组（IM 惯例）：今天 / 昨天 / 7 天内 / 更早。
 * 空组被过滤；updated_at 缺失按 0 处理（落入"更早"）。
 */
export function groupSessions<T extends { updated_at?: number | null }>(
  sessions: T[],
  now: Date = new Date(),
): SessionGroup<T>[] {
  const startOfDay = (dt: Date) =>
    new Date(dt.getFullYear(), dt.getMonth(), dt.getDate()).getTime();
  const today0 = startOfDay(now);
  const groups: SessionGroup<T>[] = [
    { label: '今天', items: [] },
    { label: '昨天', items: [] },
    { label: '7 天内', items: [] },
    { label: '更早', items: [] },
  ];
  for (const sess of sessions) {
    const t = (sess.updated_at || 0) * 1000;
    if (t >= today0) groups[0].items.push(sess);
    else if (t >= today0 - 86400_000) groups[1].items.push(sess);
    else if (t >= today0 - 7 * 86400_000) groups[2].items.push(sess);
    else groups[3].items.push(sess);
  }
  return groups.filter((g) => g.items.length > 0);
}

/**
 * 由气泡 key 推导该条 assistant 回复对应的反馈 seq。
 * 后端 seq 按 user=0, assistant=1, user=2 … 交错编号，
 * 故第 N 个（0 起）assistant 消息的 seq = 2N+1。
 * key 不存在时返回 null。
 */
export function computeAssistantSeq(
  messages: ReadonlyArray<{ key?: string | number; role?: unknown }>,
  key: string | number,
): number | null {
  const idx = messages.findIndex((m) => String(m.key) === String(key));
  if (idx < 0) return null;
  let assistantIdx = 0;
  for (let i = 0; i <= idx; i++) {
    if (messages[i].role === 'assistant') {
      if (i === idx) break;
      assistantIdx++;
    }
  }
  return 2 * assistantIdx + 1;
}

/** 从 markdown 文本中剥离图片语法，返回图片 URL 列表与剩余正文 */
export function splitImagesFromText(content: string): { imgs: string[]; text: string } {
  const imgs: string[] = [];
  const text = (content || '')
    .replace(/!\[[^\]]*\]\(([^)]+)\)/g, (_m, url: string) => {
      imgs.push(url);
      return '';
    })
    .trim();
  return { imgs, text };
}

export interface WarnCapsule {
  cls: string;
  label: string;
}

/**
 * 提取回答中的降级标注为警示胶囊（去说明书感）：
 * 【知识库无相关内容…】与 [来源: 通识知识…] 两类，从正文中移除。
 */
export function extractWarnCapsules(content: string): {
  text: string;
  capsules: WarnCapsule[];
} {
  const capsules: WarnCapsule[] = [];
  let text = content || '';
  text = text.replace(/【(知识库无相关内容[^】]*)】/g, (_m, g: string) => {
    capsules.push({ cls: 'dm-capsule-warn', label: `⚠️ ${g}` });
    return '';
  });
  text = text.replace(/\[来源: 通识知识[^\]]*\]/g, () => {
    capsules.push({ cls: 'dm-capsule-warn', label: '📖 通识回答 · 未经知识库验证' });
    return '';
  });
  return { text, capsules };
}
