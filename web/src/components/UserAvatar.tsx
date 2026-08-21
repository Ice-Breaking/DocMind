import { useMemo } from 'react';
import type { CSSProperties } from 'react';
import { createAvatar } from '@dicebear/core';
import { adventurer, bigEars, bigSmile, bottts, croodles, notionistsNeutral, personas } from '@dicebear/collection';

/** DiceBear 自托管风格：多元人物 / 卡通 / 萌童 / 极简线描（MIT，本地生成零外部依赖） */
export const DB_STYLES: Record<string, any> = {
  personas,
  adventurer,
  'big-ears': bigEars,
  'notionists-neutral': notionistsNeutral,
  croodles,
  'big-smile': bigSmile,
  bottts,
};

export const DB_STYLE_LABELS: { id: string; label: string }[] = [
  { id: 'personas', label: '多元人物' },
  { id: 'adventurer', label: '卡通人物' },
  { id: 'big-ears', label: '可爱萌童' },
  { id: 'notionists-neutral', label: '极简线描' },
  { id: 'croodles', label: '萌系小生物' },
  { id: 'big-smile', label: '贱萌可爱' },
  { id: 'bottts', label: '可爱机器人' },
];

/** avatar token 形如 db:{style}:{seed} → 本地生成 SVG data URI */
export function avatarDataUri(token: string): string | null {
  if (!token || !token.startsWith('db:')) return null;
  const parts = token.split(':');
  const style = DB_STYLES[parts[1]];
  if (!style) return null;
  const seed = parts.slice(2).join(':') || 'docmind';
  try {
    const svg = createAvatar(style, { seed, size: 128 }).toString();
    return `data:image/svg+xml;utf8,${encodeURIComponent(svg)}`;
  } catch {
    return null;
  }
}

/**
 * 统一头像渲染：
 * - db: 前缀 → DiceBear 本地 SVG
 * - emoji → 直接渲染
 * - #色值 / 空 → 首字母色块（兼容存量）
 */
export default function UserAvatar({
  avatar,
  name,
  size = 32,
}: {
  avatar?: string;
  name?: string;
  size?: number;
}) {
  const fileSrc = avatar && avatar.startsWith('file:')
    ? `/api/avatar-file/${avatar.slice(5)}`
    : null;
  const uri = useMemo(() => avatarDataUri(avatar || ''), [avatar]);
  const base: CSSProperties = {
    width: size,
    height: size,
    borderRadius: '50%',
    display: 'inline-flex',
    alignItems: 'center',
    justifyContent: 'center',
    flex: 'none',
    overflow: 'hidden',
  };
  if (fileSrc) {
    return <img src={fileSrc} alt="avatar" style={{ ...base, background: '#fff', objectFit: 'cover' }} />;
  }
  if (uri) {
    return <img src={uri} alt="avatar" style={{ ...base, background: '#fff' }} />;
  }
  if (avatar && !avatar.startsWith('#')) {
    return (
      <span style={{ ...base, fontSize: size * 0.58, background: '#f4f6ff' }}>
        {avatar}
      </span>
    );
  }
  const color = avatar && avatar.startsWith('#') ? avatar : '#6366f1';
  return (
    <span
      style={{
        ...base,
        background: color,
        color: '#fff',
        fontWeight: 600,
        fontSize: size * 0.45,
      }}
    >
      {(name || 'U').slice(0, 1).toUpperCase()}
    </span>
  );
}
