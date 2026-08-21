import { useCallback, useEffect, useMemo, useState } from 'react';
import { MessageOutlined, RobotOutlined } from '@ant-design/icons';
import {
  Alert,
  App,
  Collapse,
  Drawer,
  Empty,
  List,
  Modal,
  Spin,
  Tag,
  Typography,
} from 'antd';
import Bubble from '@ant-design/x/es/bubble';
import type { BubbleDataType } from '@ant-design/x/es/bubble/BubbleList';
import MarkdownContent from '../components/MarkdownContent';
import UserAvatar from '../components/UserAvatar';
import {
  fetchAssistants,
  fetchMessages,
  fetchSessions,
  type Assistant,
  type Me,
  type Message,
  type Session,
} from '../api';

/** 无助手归属会话的分组 key */
const DEFAULT_GROUP_KEY = '__default__';

interface SessionGroup {
  key: string;
  name: string;
  sessions: Session[];
}

/** 秒级时间戳 → 本地可读时间 */
function formatTime(ts: number): string {
  return new Date(ts * 1000).toLocaleString();
}

export default function SessionHistory({ me }: { me: Me }) {
  const { message: msgApi } = App.useApp();
  const [sessions, setSessions] = useState<Session[]>([]);
  const [assistants, setAssistants] = useState<Assistant[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  /* Drawer 状态 */
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [activeSession, setActiveSession] = useState<Session | null>(null);
  const [messages, setMessages] = useState<Message[]>([]);
  const [msgLoading, setMsgLoading] = useState(false);
  const [msgError, setMsgError] = useState<string | null>(null);

  /* 引用定位 Modal */
  const [locateOpen, setLocateOpen] = useState(false);
  const [locateTitle, setLocateTitle] = useState('');
  const [locateText, setLocateText] = useState('');

  /* ---- 初始加载 ---- */
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const [ss, as] = await Promise.all([fetchSessions(), fetchAssistants()]);
        if (!cancelled) {
          setSessions(ss);
          setAssistants(as);
        }
      } catch (e: unknown) {
        if (!cancelled) setError(e instanceof Error ? e.message : '加载失败');
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  /* ---- 按助手分组 ---- */
  const groups: SessionGroup[] = useMemo(() => {
    const nameById = new Map<string, string>(assistants.map((a) => [a.id, a.name]));
    const map = new Map<string, SessionGroup>();

    for (const s of sessions) {
      const aid = s.assistant_id || '';
      const key = aid || DEFAULT_GROUP_KEY;
      let g = map.get(key);
      if (!g) {
        g = {
          key,
          name: aid ? nameById.get(aid) || `未知助手（${aid.slice(0, 8)}）` : '默认助手',
          sessions: [],
        };
        map.set(key, g);
      }
      g.sessions.push(s);
    }

    const list = Array.from(map.values());
    for (const g of list) {
      g.sessions.sort((a, b) => b.updated_at - a.updated_at);
    }
    list.sort((a, b) => {
      if (a.key === DEFAULT_GROUP_KEY) return 1;
      if (b.key === DEFAULT_GROUP_KEY) return -1;
      return 0;
    });
    return list;
  }, [sessions, assistants]);

  /* ---- 打开会话详情 ---- */
  const openSession = async (s: Session) => {
    setActiveSession(s);
    setDrawerOpen(true);
    setMsgLoading(true);
    setMsgError(null);
    setMessages([]);
    try {
      const msgs = await fetchMessages(s.id);
      setMessages(msgs);
    } catch (e: unknown) {
      setMsgError(e instanceof Error ? e.message : '消息加载失败');
    } finally {
      setMsgLoading(false);
    }
  };

  /* ---- 引用定位（与对话页同款） ---- */
  const handleLocate = useCallback(
    async (filename: string, page: string | undefined) => {
      try {
        const lastUser = [...messages].reverse().find((m) => m.role === 'user');
        const params = new URLSearchParams({ doc: filename });
        if (lastUser?.content) params.set('q', String(lastUser.content));
        const r = await fetch('/api/locate?' + params.toString());
        if (r.status === 401) return;
        if (!r.ok) {
          msgApi.warning('定位失败');
          return;
        }
        const data = await r.json();
        if (!data.found) {
          msgApi.warning('未定位到引用片段');
          return;
        }
        setLocateTitle(`来源: ${filename}${page ? ` · 第${page}页` : ''}`);
        setLocateText(data.text);
        setLocateOpen(true);
      } catch {
        msgApi.warning('定位请求失败');
      }
    },
    [messages, msgApi],
  );

  /* ---- 与对话页一致的气泡角色 ---- */
  const sessionAssistant = assistants.find(
    (a) => a.id === (activeSession?.assistant_id || ''),
  );
  const roles = useMemo(
    () => ({
      user: {
        placement: 'end' as const,
        avatar: {
          icon: <UserAvatar avatar={me.avatar} name={me.user} size={28} />,
          style: { background: 'transparent' },
        },
        variant: 'filled' as const,
      },
      assistant: {
        placement: 'start' as const,
        avatar: sessionAssistant?.avatar
          ? {
              icon: (
                <UserAvatar
                  avatar={sessionAssistant.avatar}
                  name={sessionAssistant.name}
                  size={28}
                />
              ),
              style: { background: 'transparent' },
            }
          : { icon: <RobotOutlined />, style: { background: '#6366f1', color: '#fff' } },
        variant: 'filled' as const,
        messageRender: (content: string) => (
          <MarkdownContent content={content} onLocate={handleLocate} />
        ),
      },
    }),
    [me.avatar, me.user, sessionAssistant, handleLocate],
  );

  const bubbleItems: BubbleDataType[] = useMemo(
    () => messages.map((m) => ({ key: m.id, role: m.role, content: m.content })),
    [messages],
  );

  /* ---- 渲染 ---- */
  if (loading) {
    return <Spin style={{ display: 'block', margin: '120px auto' }} size="large" />;
  }

  return (
    <div className="dm-page" style={{ padding: '24px 32px', maxWidth: 1000, margin: '0 auto' }}>
      <div style={{ marginBottom: 24 }}>
        <h1 style={{ margin: 0, fontSize: 22, fontWeight: 600 }}>会话历史</h1>
        <Typography.Text type="secondary">{me.user} 的全部对话记录</Typography.Text>
      </div>

      {error ? (
        <Alert type="error" showIcon message="加载失败" description={error} />
      ) : sessions.length === 0 ? (
        <Empty
          style={{ marginTop: 80 }}
          description="暂无会话记录，去对话页开始提问吧"
        />
      ) : (
        <Collapse
          defaultActiveKey={groups.map((g) => g.key)}
          items={groups.map((g) => ({
            key: g.key,
            label: (
              <span>
                {g.name}
                <Tag style={{ marginLeft: 8 }}>{g.sessions.length} 个会话</Tag>
              </span>
            ),
            children: (
              <List
                dataSource={g.sessions}
                rowKey="id"
                renderItem={(s: Session) => (
                  <List.Item
                    style={{ cursor: 'pointer' }}
                    onClick={() => openSession(s)}
                    actions={[
                      <span key="count">
                        <MessageOutlined style={{ marginRight: 4 }} />
                        {s.msg_count} 条消息
                      </span>,
                    ]}
                  >
                    <List.Item.Meta
                      title={s.title || '（未命名会话）'}
                      description={`更新于 ${formatTime(s.updated_at)}`}
                    />
                  </List.Item>
                )}
              />
            ),
          }))}
        />
      )}

      {/* ---- 会话详情 Drawer：与对话页同款气泡样式 ---- */}
      <Drawer
        title={activeSession ? activeSession.title || '（未命名会话）' : '会话详情'}
        placement="right"
        width={640}
        open={drawerOpen}
        onClose={() => setDrawerOpen(false)}
      >
        {activeSession && (
          <Typography.Paragraph type="secondary" style={{ marginTop: -8 }}>
            {activeSession.msg_count} 条消息 · 更新于 {formatTime(activeSession.updated_at)}
          </Typography.Paragraph>
        )}

        {msgLoading ? (
          <Spin style={{ display: 'block', margin: '40px auto' }} />
        ) : msgError ? (
          <Alert type="error" showIcon message="消息加载失败" description={msgError} />
        ) : messages.length === 0 ? (
          <Empty description="该会话暂无消息" image={Empty.PRESENTED_IMAGE_SIMPLE} />
        ) : (
          <div className="dm-chat dm-history">
            <Bubble.List items={bubbleItems} roles={roles as any} />
          </div>
        )}
      </Drawer>

      {/* ---- 引用定位 Modal ---- */}
      <Modal title={locateTitle} open={locateOpen} onCancel={() => setLocateOpen(false)} footer={null} width={640}>
        <Typography.Paragraph style={{ maxHeight: 400, overflow: 'auto', whiteSpace: 'pre-wrap' }}>
          {locateText}
        </Typography.Paragraph>
      </Modal>
    </div>
  );
}
