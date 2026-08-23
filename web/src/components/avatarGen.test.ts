import { describe, expect, it } from 'vitest';
import { avatarDataUri, ensureAvatarGen } from './avatarGen';

describe('avatarGen(dicebear 懒加载封装)', () => {
  it('非 db: token 一律返回 null', () => {
    expect(avatarDataUri('')).toBeNull();
    expect(avatarDataUri('#6366f1')).toBeNull();
    expect(avatarDataUri('🐱')).toBeNull();
    expect(avatarDataUri('file:abc.png')).toBeNull();
  });

  it('ensureAvatarGen 之后按风格+种子生成 SVG data URI', async () => {
    await ensureAvatarGen();
    const uri = avatarDataUri('db:personas:seed1');
    expect(uri).toMatch(/^data:image\/svg\+xml/);
    // 同 token 结果确定(纯函数)
    expect(avatarDataUri('db:personas:seed1')).toBe(uri);
  });

  it('全部 7 种风格均可生成', async () => {
    await ensureAvatarGen();
    for (const style of [
      'personas',
      'adventurer',
      'big-ears',
      'notionists-neutral',
      'croodles',
      'big-smile',
      'bottts',
    ]) {
      expect(avatarDataUri(`db:${style}:dm`)).toMatch(/^data:image\/svg\+xml/);
    }
  });

  it('未知风格与空种子兜底', async () => {
    await ensureAvatarGen();
    expect(avatarDataUri('db:no-such-style:x')).toBeNull();
    // 缺种子段 → 默认 'docmind'
    expect(avatarDataUri('db:bottts')).toMatch(/^data:image\/svg\+xml/);
  });
});
