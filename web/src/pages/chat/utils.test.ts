import { describe, expect, it } from 'vitest';
import {
  computeAssistantSeq,
  extractWarnCapsules,
  fmtSessionTime,
  groupSessions,
  newSessionId,
  splitImagesFromText,
} from './utils';

describe('newSessionId', () => {
  it('生成 sess- 前缀且唯一', () => {
    const ids = new Set(Array.from({ length: 200 }, () => newSessionId()));
    expect(ids.size).toBe(200);
    for (const id of ids) {
      expect(id).toMatch(/^sess-[a-z0-9]+-[a-z0-9]{6}$/);
    }
  });
});

describe('fmtSessionTime', () => {
  // 固定"现在"：2026-08-23 15:00 本地时间
  const now = new Date(2026, 7, 23, 15, 0, 0);

  it('今天 → HH:MM', () => {
    const ts = new Date(2026, 7, 23, 9, 5, 0).getTime() / 1000;
    expect(fmtSessionTime(ts, now)).toBe('09:05');
  });

  it('昨天 → 昨天', () => {
    const ts = new Date(2026, 7, 22, 23, 59, 0).getTime() / 1000;
    expect(fmtSessionTime(ts, now)).toBe('昨天');
  });

  it('7 天内 → M月D日', () => {
    const ts = new Date(2026, 7, 18, 8, 0, 0).getTime() / 1000;
    expect(fmtSessionTime(ts, now)).toBe('8月18日');
  });

  it('更早 → M/D', () => {
    const ts = new Date(2026, 6, 1, 8, 0, 0).getTime() / 1000;
    expect(fmtSessionTime(ts, now)).toBe('7/1');
  });
});

describe('groupSessions', () => {
  const now = new Date(2026, 7, 23, 15, 0, 0);
  const at = (d: Date) => ({ id: 'x', updated_at: d.getTime() / 1000 });
  const today = at(new Date(2026, 7, 23, 12, 0, 0));
  const yesterday = at(new Date(2026, 7, 22, 12, 0, 0));
  const inWeek = at(new Date(2026, 7, 19, 12, 0, 0));
  const older = at(new Date(2026, 6, 1, 12, 0, 0));

  it('按 今天/昨天/7天内/更早 分组并过滤空组', () => {
    const groups = groupSessions([older, today, inWeek, yesterday], now);
    expect(groups.map((g) => g.label)).toEqual(['今天', '昨天', '7 天内', '更早']);
    expect(groups.map((g) => g.items[0])).toEqual([today, yesterday, inWeek, older]);
  });

  it('updated_at 缺失落入更早', () => {
    const groups = groupSessions([{ id: 'a', updated_at: null }], now);
    expect(groups).toHaveLength(1);
    expect(groups[0].label).toBe('更早');
  });

  it('全部为空输入 → 空分组', () => {
    expect(groupSessions([], now)).toEqual([]);
  });
});

describe('computeAssistantSeq', () => {
  const msgs = [
    { key: 'u1', role: 'user' },
    { key: 'a1', role: 'assistant' },
    { key: 'u2', role: 'user' },
    { key: 'a2', role: 'assistant' },
    { key: 'u3', role: 'user' },
    { key: 'a3', role: 'assistant' },
  ];

  it('第 N 个 assistant 的 seq = 2N+1', () => {
    expect(computeAssistantSeq(msgs, 'a1')).toBe(1);
    expect(computeAssistantSeq(msgs, 'a2')).toBe(3);
    expect(computeAssistantSeq(msgs, 'a3')).toBe(5);
  });

  it('key 不存在 → null；key 类型数字/字符串互通', () => {
    expect(computeAssistantSeq(msgs, 'nope')).toBeNull();
    expect(computeAssistantSeq([{ key: 7, role: 'assistant' }], '7')).toBe(1);
  });
});

describe('splitImagesFromText', () => {
  it('剥离多图并保留正文', () => {
    const { imgs, text } = splitImagesFromText(
      '![图片](data:image/png;base64,AAA)\n\n看这张\n![x](https://a/b.png)',
    );
    expect(imgs).toEqual(['data:image/png;base64,AAA', 'https://a/b.png']);
    expect(text).toBe('看这张');
  });

  it('无图片时原样返回', () => {
    expect(splitImagesFromText('纯文本问题')).toEqual({ imgs: [], text: '纯文本问题' });
    expect(splitImagesFromText('')).toEqual({ imgs: [], text: '' });
  });
});

describe('extractWarnCapsules', () => {
  it('提取知识库无相关内容标注', () => {
    const { text, capsules } = extractWarnCapsules('前文【知识库无相关内容，以下为通识回答】后文');
    expect(text).toBe('前文后文');
    expect(capsules).toEqual([
      { cls: 'dm-capsule-warn', label: '⚠️ 知识库无相关内容，以下为通识回答' },
    ]);
  });

  it('提取通识来源标注', () => {
    const { text, capsules } = extractWarnCapsules('答案[来源: 通识知识 · 网络检索]结尾');
    expect(text).toBe('答案结尾');
    expect(capsules).toEqual([
      { cls: 'dm-capsule-warn', label: '📖 通识回答 · 未经知识库验证' },
    ]);
  });

  it('两类共存且互不影响正常引用', () => {
    const { text, capsules } = extractWarnCapsules(
      '【知识库无相关内容】A [来源: 通识知识 x] [来源: 手册.md · 第3页] B',
    );
    expect(capsules).toHaveLength(2);
    // 与线上原实现一致：移除处可能残留空格（markdown 渲染会折叠，无视觉影响）
    expect(text).toBe('A  [来源: 手册.md · 第3页] B');
  });

  it('正常文本零改动', () => {
    expect(extractWarnCapsules('普通回答')).toEqual({ text: '普通回答', capsules: [] });
  });
});
