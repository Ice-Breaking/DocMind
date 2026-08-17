import { useRef, useState } from 'react';
import {
  ApiOutlined,
  LogoutOutlined,
  RobotOutlined,
  SendOutlined,
} from '@ant-design/icons';
import { App, Button, Card, Input, Layout, Tag, Typography } from 'antd';
import { chatStream, logout, type ChatEvent, type Me } from '../api';

/**
 * 主页壳：顶栏 + SSE 链路冒烟测试区。
 * 完整对话页（Ant Design X：会话列表/思维链/引用溯源/追问）在下一步实现。
 */
export default function Home({ me, onLogout }: { me: Me; onLogout: () => void }) {
  const [question, setQuestion] = useState('');
  const [events, setEvents] = useState<ChatEvent[]>([]);
  const [running, setRunning] = useState(false);
  const sessionId = useRef(`web-${Math.random().toString(36).slice(2, 10)}`);
  const { message } = App.useApp();

  const smoke = async () => {
    const q = question.trim();
    if (!q || running) return;
    setRunning(true);
    setEvents([]);
    try {
      for await (const ev of chatStream(q, sessionId.current)) {
        setEvents((prev) => [...prev, ev]);
      }
    } catch (e: any) {
      if (String(e?.message) === 'UNAUTHORIZED') {
        message.warning('登录态失效，请重新登录');
        await logout();
        onLogout();
        return;
      }
      message.error('请求失败：' + e?.message);
    } finally {
      setRunning(false);
    }
  };

  const finalEv = events.find((e) => e.event === 'final');

  return (
    <Layout className="dm-home">
      <Layout.Header className="dm-header">
        <div className="dm-brand">
          <RobotOutlined /> DocMind
        </div>
        <div>
          <Tag color="purple">{me.user}</Tag>
          {me.is_admin && <Tag color="gold">管理员</Tag>}
          <Button
            type="text"
            icon={<LogoutOutlined />}
            onClick={async () => {
              await logout();
              onLogout();
            }}
          >
            退出
          </Button>
        </div>
      </Layout.Header>
      <Layout.Content className="dm-content">
        <Card
          title={
            <span>
              <ApiOutlined /> SSE 链路冒烟测试（完整对话页开发中）
            </span>
          }
        >
          <div className="dm-smoke-input">
            <Input.TextArea
              value={question}
              onChange={(e) => setQuestion(e.target.value)}
              placeholder="输入问题验证登录态 + SSE 流式链路，回车发送…"
              autoSize={{ minRows: 1, maxRows: 3 }}
              onPressEnter={(e) => {
                if (!e.shiftKey) {
                  e.preventDefault();
                  smoke();
                }
              }}
            />
            <Button
              type="primary"
              icon={<SendOutlined />}
              loading={running}
              onClick={smoke}
            >
              发送
            </Button>
          </div>

          {events.length > 0 && (
            <div className="dm-smoke-events">
              <Typography.Text type="secondary">
                事件流（{events.length} 条）：
              </Typography.Text>
              <div className="dm-smoke-track">
                {events.map((ev, i) => (
                  <Tag key={i} color={
                    { cache: 'orange', thinking: 'default', token: 'blue',
                      step: 'cyan', error: 'red', final: 'green', done: 'purple' }[ev.event]
                  }>
                    {ev.event}
                    {ev.event === 'step' ? `:${ev.data.step_kind}` : ''}
                  </Tag>
                ))}
              </div>
              {finalEv && (
                <Card size="small" className="dm-smoke-answer" title="终答（final）">
                  <Typography.Paragraph
                    ellipsis={{ rows: 6, expandable: true }}
                    style={{ marginBottom: 0, whiteSpace: 'pre-wrap' }}
                  >
                    {finalEv.data.answer}
                  </Typography.Paragraph>
                </Card>
              )}
            </div>
          )}
        </Card>
      </Layout.Content>
    </Layout>
  );
}
