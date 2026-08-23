import { describe, expect, it, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { App } from 'antd';
import Login from './Login';

// mock api 层:登录成功/失败两条路径
const loginMock = vi.fn();
vi.mock('../api', () => ({ login: (...args: unknown[]) => loginMock(...args) }));

function mount(onLoggedIn: () => void) {
  return render(
    <App>
      <Login onLoggedIn={onLoggedIn} />
    </App>,
  );
}

describe('Login 页冒烟', () => {
  it('渲染标题与表单', () => {
    mount(vi.fn());
    expect(screen.getByText('DocMind')).toBeInTheDocument();
    expect(screen.getByPlaceholderText('用户名')).toBeInTheDocument();
    expect(screen.getByPlaceholderText('密码')).toBeInTheDocument();
  });

  it('空表单提交被校验拦截,不触发 login', async () => {
    const onLoggedIn = vi.fn();
    const user = userEvent.setup();
    mount(onLoggedIn);
    await user.click(screen.getByRole('button', { name: '登 录' }));
    expect(loginMock).not.toHaveBeenCalled();
    expect(onLoggedIn).not.toHaveBeenCalled();
  });

  it('登录成功 → 回调 onLoggedIn', async () => {
    loginMock.mockResolvedValueOnce(true);
    const onLoggedIn = vi.fn();
    const user = userEvent.setup();
    mount(onLoggedIn);
    await user.type(screen.getByPlaceholderText('用户名'), 'alice');
    await user.type(screen.getByPlaceholderText('密码'), 'secret123');
    await user.click(screen.getByRole('button', { name: '登 录' }));
    await vi.waitFor(() => expect(onLoggedIn).toHaveBeenCalledTimes(1));
    expect(loginMock).toHaveBeenCalledWith('alice', 'secret123');
  });
});
