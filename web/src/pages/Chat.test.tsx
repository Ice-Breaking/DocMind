import { App as AntdApp } from 'antd';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { describe, expect, it, vi } from 'vitest';
import Chat from './Chat';
import type { Me } from '../api';

/* Chat 页面冒烟测试：mock api 层，验证挂载 / 会话加载 / SSE 发送链路。
 * 供后续 Chat.tsx 持续拆分时做行为回归的安全网。 */

vi.mock('../api', () => ({
  fetchSessions: vi.fn(async () => []),
  fetchMessages: vi.fn(async () => []),
  fetchFeedback: vi.fn(async () => ({})),
  fetchAssistants: vi.fn(async () => [
    { id: 'default', name: '默认助手', avatar: '' },
  ]),
  fetchVoices: vi.fn(async () => []),
  fetchSuggestions: vi.fn(async () => []),
  logout: vi.fn(async () => {}),
  submitFeedback: vi.fn(async () => {}),
  deleteSession: vi.fn(async () => {}),
  chatStream: vi.fn(),
}));

import { chatStream, fetchSessions } from '../api';

const me: Me = { user: 'tester', is_admin: false, must_change_pwd: false };

function renderChat() {
  // Chat 页数据层已迁 react-query（第七期）：测试需独立 QueryClient
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <AntdApp>
      <QueryClientProvider client={qc}>
        <MemoryRouter initialEntries={['/chat']}>
          <Chat me={me} onLogout={() => {}} />
        </MemoryRouter>
      </QueryClientProvider>
    </AntdApp>,
  );
}

describe('Chat 页面', () => {
  it('空会话：渲染欢迎页与推荐问题', async () => {
    renderChat();
    expect(await screen.findByText('你好，我是 DocMind')).toBeTruthy();
    expect(screen.getByText('你能做什么？')).toBeTruthy();
  });

  it('已有会话：侧栏展示标题并自动选中首个会话', async () => {
    vi.mocked(fetchSessions).mockResolvedValueOnce([
      {
        id: 's1',
        title: '知识库问答',
        msg_count: 2,
        updated_at: Math.floor(Date.now() / 1000),
      },
    ]);
    renderChat();
    // 标题同时出现在侧栏会话项与顶栏（证明首个会话被自动选中）。
    // 注意：react-query 下侧栏与「选中态」分两次提交渲染，需等待第二处出现
    await waitFor(() => {
      expect(screen.getAllByText('知识库问答').length).toBeGreaterThanOrEqual(2);
    });
  });

  it('发送消息：乐观插入用户气泡并流式接收回答', async () => {
    const user = userEvent.setup();
    vi.mocked(chatStream).mockImplementation(async function* () {
      yield { event: 'token', data: { text: '你好，' } };
      yield { event: 'final', data: { answer: '你好，我是 DocMind 助手' } };
      yield { event: 'done', data: {} };
    } as any);
    renderChat();
    // 点击欢迎页推荐问题触发 handleSend
    await user.click(await screen.findByText('你能做什么？'));
    // 用户气泡（问题原文）出现
    expect(await screen.findByText('你能做什么？', { selector: 'div,span,p' })).toBeTruthy();
    // 流式结束后最终回答由 MarkdownContent 渲染出来
    await waitFor(
      () => expect(screen.getByText(/DocMind 助手/)).toBeTruthy(),
      { timeout: 3000 },
    );
    expect(chatStream).toHaveBeenCalledTimes(1);
  });
});
