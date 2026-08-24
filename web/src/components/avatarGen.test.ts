import { describe, expect, it } from 'vitest';
import { avatarDataUri, ensureAllStyles, ensureStyle } from './avatarGen';

describe('avatarGen(dicebear 按风格懒加载封装)', () => {
  it('非 db: token 一律返回 null', () => {
    expect(avatarDataUri('')).toBeNull();
    expect(avatarDataUri('#6366f1')).toBeNull();
    expect(avatarDataUri('🐱')).toBeNull();
    expect(avatarDataUri('file:abc.png')).toBeNull();
  });

  it('ensureStyle 之后按风格+种子生成 SVG data URI', async () => {
    await expect(ensureStyle('personas')).resolves.toBe(true);
    const uri = avatarDataUri('db:personas:seed1');
    expect(uri).toMatch(/^data:image\/svg\+xml/);
    // 同 token 结果确定(纯函数)
    expect(avatarDataUri('db:personas:seed1')).toBe(uri);
  });

  it('全部 7 种风格均可生成', async () => {
    await expect(ensureAllStyles()).resolves.toEqual([true, true, true, true, true, true, true]);
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
    // 未知风格不发请求、resolve(false)
    await expect(ensureStyle('no-such-style')).resolves.toBe(false);
    expect(avatarDataUri('db:no-such-style:x')).toBeNull();
    await expect(ensureStyle('bottts')).resolves.toBe(true);
    // 缺种子段 → 默认 'docmind'
    expect(avatarDataUri('db:bottts')).toMatch(/^data:image\/svg\+xml/);
  });

  it('风格分包重复加载幂等(缓存命中)', async () => {
    await expect(ensureStyle('croodles')).resolves.toBe(true);
    await expect(ensureStyle('croodles')).resolves.toBe(true);
    expect(avatarDataUri('db:croodles:again')).toMatch(/^data:image\/svg\+xml/);
  });
});
