import { useEffect, useState } from 'react';
import type { CSSProperties } from 'react';
import { avatarDataUri, ensureAvatarGen } from './avatarGen';

/**
 * DiceBear 自托管风格集合:多元人物 / 卡通 / 萌童 / 极简线描(MIT,本地生成零外部依赖)。
 * 生成器本体经 avatarGen 动态加载(约 600KB 独立分包),首屏不引入;
 * 此处仅暴露风格 id 集合供选择器做归属判断。
 */
export const DB_STYLES: Record<string, true> = {
  personas: true,
  adventurer: true,
  'big-ears': true,
  'notionists-neutral': true,
  croodles: true,
  'big-smile': true,
  bottts: true,
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

/**
 * 统一头像渲染:
 * - db: 前缀 → DiceBear 本地 SVG(生成器懒加载,就绪前先以首字母色块过渡)
 * - emoji → 直接渲染
 * - #色值 / 空 → 首字母色块(兼容存量)
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
  const isDb = !!avatar && avatar.startsWith('db:');
  const fileSrc = avatar && avatar.startsWith('file:')
    ? `/api/avatar-file/${avatar.slice(5)}`
    : null;

  const [uri, setUri] = useState<string | null>(() => (isDb ? avatarDataUri(avatar!) : null));
  useEffect(() => {
    if (!isDb) return;
    let alive = true;
    ensureAvatarGen().then(() => {
      if (alive) setUri(avatarDataUri(avatar!));
    });
    return () => { alive = false; };
  }, [isDb, avatar]);

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
  if (avatar && !avatar.startsWith('#') && !isDb) {
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
