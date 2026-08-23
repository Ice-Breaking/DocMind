import { useCallback, useEffect, useState } from 'react';
import {
  App,
  Button,
  Card,
  Drawer,
  Spin,
  Table,
  Typography,
} from 'antd';
import { EyeOutlined } from '@ant-design/icons';
import MarkdownContent from '../components/MarkdownContent';
import type { ColumnsType } from 'antd/es/table';
import {
  fetchAdminMessages,
  fetchAdminSessions,
  logout,
  type AdminMessage,
  type AdminSession,
  type Me,
} from '../api';

const { Text } = Typography;

/**
 * 管理后台 — 会话审计：全量会话检索与消息回看。
 * 用量成本 / Badcase / 检索日志已拆分为独立页面（侧边栏「高级控制台」）。
 */
export default function Admin({ me, onLogout }: { me: Me; onLogout: () => void }) {
  const { message: msgApi } = App.useApp();

  const [sessions, setSessions] = useState<AdminSession[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [drawerOpen, setDrawerOpen] = useState(false);
  const [activeTitle, setActiveTitle] = useState('');
  const [messages, setMessages] = useState<AdminMessage[]>([]);
  const [msgLoading, setMsgLoading] = useState(false);

  const handleAuthError = useCallback(
    async (e: unknown) => {
      if (e instanceof Error && e.message === 'UNAUTHORIZED') {
        msgApi.warning('登录态失效，请重新登录');
        await logout();
        onLogout();
        return true;
      }
      return false;
    },
    [msgApi, onLogout],
  );

  useEffect(() => {
    (async () => {
      try {
        setSessions(await fetchAdminSessions());
      } catch (e: unknown) {
        if (!(await handleAuthError(e))) {
          setError(e instanceof Error ? e.message : String(e));
        }
      } finally {
        setLoading(false);
      }
    })();
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const openDrawer = async (s: AdminSession) => {
    setActiveTitle(s.title || '(空会话)');
    setDrawerOpen(true);
    setMsgLoading(true);
    try {
      setMessages(await fetchAdminMessages(s.id));
    } catch (e: unknown) {
      await handleAuthError(e);
    } finally {
      setMsgLoading(false);
    }
  };

  const columns: ColumnsType<AdminSession> = [
    {
      title: '活跃时间',
      dataIndex: 'updated_at',
      key: 'updated_at',
      width: 170,
      render: (ts: number) => new Date(ts * 1000).toLocaleString(),
    },
    { title: '用户', dataIndex: 'user', key: 'user', width: 120 },
    { title: '标题', dataIndex: 'title', key: 'title', ellipsis: true },
    { title: '消息数', dataIndex: 'msg_count', key: 'msg_count', width: 90 },
    {
      title: '操作',
      key: 'action',
      width: 90,
      render: (_: any, record: AdminSession) => (
        <Button size="small" icon={<EyeOutlined />} onClick={() => openDrawer(record)}>查看</Button>
      ),
    },
  ];

  return (
    <div className="dm-page" style={{ padding: '24px 32px', maxWidth: 1200, margin: '0 auto' }}>
      <div style={{ marginBottom: 24 }}>
        <h1 style={{ margin: 0, fontSize: 22, fontWeight: 600 }}>会话审计</h1>
        <Text type="secondary">管理员：{me.user}，可回看全部用户的会话记录</Text>
      </div>

      {loading ? (
        <Spin style={{ display: 'block', margin: '80px auto' }} size="large" />
      ) : error ? (
        <Card>
          <Text type="danger">加载失败：{error}</Text>
          <Button style={{ marginLeft: 12 }} onClick={() => window.location.reload()}>重试</Button>
        </Card>
      ) : (
        <Table scroll={{ x: "max-content" }}
          columns={columns}
          dataSource={sessions}
          rowKey="id"
          pagination={{ pageSize: 20, showTotal: (t) => `共 ${t} 个会话` }}
          size="small"
        />
      )}

      <Drawer
        title={`会话内容 — ${activeTitle}`}
        placement="right"
        width={600}
        open={drawerOpen}
        onClose={() => setDrawerOpen(false)}
      >
        {msgLoading ? (
          <Spin style={{ display: 'block', margin: '40px auto' }} />
        ) : (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
            {messages.map((m, i) => (
              <Card
                key={i}
                size="small"
                style={{
                  background: m.role === 'user' ? '#e6f4ff' : '#f5f5f5',
                  alignSelf: m.role === 'user' ? 'flex-start' : 'flex-end',
                  maxWidth: '85%',
                }}
              >
                <div style={{ fontWeight: 600, marginBottom: 4, fontSize: 12, color: '#666' }}>
                  {m.role === 'user' ? '用户' : '助手'}
                </div>
                <div style={{ fontSize: 13 }}>
                  <MarkdownContent content={m.content || ''} onLocate={() => {}} />
                </div>
              </Card>
            ))}
          </div>
        )}
      </Drawer>
    </div>
  );
}
