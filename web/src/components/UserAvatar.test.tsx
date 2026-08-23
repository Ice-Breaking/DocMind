import { describe, expect, it } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import UserAvatar from './UserAvatar';

describe('UserAvatar 渲染分支', () => {
  it('emoji 直接渲染为文本', () => {
    render(<UserAvatar avatar="🐱" name="张三" />);
    expect(screen.getByText('🐱')).toBeInTheDocument();
  });

  it('#色值 → 首字母色块', () => {
    render(<UserAvatar avatar="#ff5252" name="Li" />);
    const el = screen.getByText('L');
    expect(el.tagName).toBe('SPAN');
  });

  it('无 avatar → 默认首字母 U 色块', () => {
    render(<UserAvatar name="Wang" />);
    expect(screen.getByText('W')).toBeInTheDocument();
  });

  it('file: 前缀 → 服务端头像 img', () => {
    render(<UserAvatar avatar="file:abc.png" name="Chen" />);
    const img = screen.getByRole('img');
    expect(img).toHaveAttribute('src', '/api/avatar-file/abc.png');
  });

  it('db: token → 懒加载就绪前先显示首字母,就绪后替换为 SVG img', async () => {
    render(<UserAvatar avatar="db:personas:user1" name="赵鹏" />);
    // 就绪前的过渡 UI:首字母色块(而非原始 token 文本)
    expect(screen.getByText('赵')).toBeInTheDocument();

    // dicebear 分包加载完成后出现 SVG 头像
    await waitFor(
      () => {
        const img = screen.getByRole('img');
        expect(img.getAttribute('src')).toMatch(/^data:image\/svg\+xml/);
      },
      { timeout: 5000 },
    );
  });
});
