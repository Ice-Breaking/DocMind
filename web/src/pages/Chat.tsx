import {
  memo,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type AnchorHTMLAttributes,
} from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import type { Components } from 'react-markdown';
import {
  ApiOutlined,
  DeleteOutlined,
  DislikeOutlined,
  LikeOutlined,
  LikeFilled,
  DislikeFilled,
  PlusOutlined,
  RobotOutlined,
  UserOutlined,
} from '@ant-design/icons';
import { App, Button, Modal, Select, Space, Typography } from 'antd';
import Bubble from '@ant-design/x/es/bubble';
import type { BubbleDataType } from '@ant-design/x/es/bubble/BubbleList';
import Conversations from '@ant-design/x/es/conversations';
import type { Conversation } from '@ant-design/x/es/conversations/interface';
import Sender from '@ant-design/x/es/sender';
import ThoughtChain from '@ant-design/x/es/thought-chain';
import type { ThoughtChainItem } from '@ant-design/x/es/thought-chain/Item';
import {
  chatStream,
  deleteSession,
  fetchAssistants,
  fetchFeedback,
  fetchMessages,
  fetchSessions,
  fetchSuggestions,
  logout,
  submitFeedback,
  type Assistant,
  type Me,
  type Session,
} from '../api';

/* ------------------------------------------------------------------ */
/*  Helpers                                                            */
/* ------------------------------------------------------------------ */

/** 生成新会话 ID */
function newSessionId(): string {
  return `sess-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}`;
}

/**
 * 引用 Markdown 化：将 [来源: xxx.md · 第N页] 转为 markdown 链接，
 * 由 ReactMarkdown 渲染为 <a>，点击行为由自定义 a 组件处理。
 */
function citationToMarkdown(text: string): string {
  const regex =
    /\[来源: ([^\]\n]+?\.(?:md|txt|pdf|docx|xlsx|png|jpg|jpeg|webp))(?: · 第(\d+)页)?\]/g;
  return text.replace(regex, (_m, filename: string, page?: string) => {
    const label = `来源: ${filename}${page ? ` · 第${page}页` : ''}`;
    const target = `#source-${encodeURIComponent(filename)}${page ? `@${page}` : ''}`;
    return `[${label}](${target})`;
  });
}

/** Memoized markdown 渲染器，避免每次渲染重复解析 markdown */
const MarkdownContent = memo(function MarkdownContent({
  content,
  onLocate,
}: {
  content: string;
  onLocate: (filename: string, page: string | undefined) => void;
}) {
  const components = useMemo<Components>(
    () => ({
      a: ({ href, children }: AnchorHTMLAttributes<HTMLAnchorElement>) => {
        if (href?.startsWith('#source-')) {
          return (
            <a
              className="dm-source-link"
              href={href}
              onClick={(e) => {
                e.preventDefault();
                const payload = href.slice('#source-'.length);
                const atIdx = payload.lastIndexOf('@');
                const filename = decodeURIComponent(
                  atIdx >= 0 ? payload.slice(0, atIdx) : payload,
                );
                const page = atIdx >= 0 ? payload.slice(atIdx + 1) : undefined;
                onLocate(filename, page);
              }}
            >
              {children}
            </a>
          );
        }
        return (
          <a href={href} target="_blank" rel="noopener noreferrer">
            {children}
          </a>
        );
      },
    }),
    [onLocate],
  );
  return (
    <div className="react-markdown">
      <ReactMarkdown remarkPlugins={[remarkGfm]} components={components}>
        {citationToMarkdown(content)}
      </ReactMarkdown>
    </div>
  );
});

/* ------------------------------------------------------------------ */
/*  Component                                                          */
/* ------------------------------------------------------------------ */

export default function Chat({ me: _me, onLogout }: { me: Me; onLogout: () => void }) {
  const { message: msgApi } = App.useApp();

  /* ---- state ---- */
  const [sessions, setSessions] = useState<Session[]>([]);
  const [activeSid, setActiveSid] = useState<string>('');
  const [messages, setMessages] = useState<BubbleDataType[]>([]);
  const [streaming, setStreaming] = useState(false);
  const [thinkingSteps, setThinkingSteps] = useState<ThoughtChainItem[]>([]);
  const [suggestions, setSuggestions] = useState<string[]>([]);
  const [feedbackMap, setFeedbackMap] = useState<Record<string, 'up' | 'down'>>({});
  const [senderValue, setSenderValue] = useState('');
  const [lastFailedQuestion, setLastFailedQuestion] = useState<string | null>(null);
  const [assistants, setAssistants] = useState<Assistant[]>([]);
  const [assistantId, setAssistantId] = useState<string>(
    () => localStorage.getItem('dm_assistant_id') || 'default',
  );

  /* ---- refs ---- */
  const abortRef = useRef<AbortController | null>(null);
  const streamTokenRef = useRef('');
  const streamThinkingRef = useRef('');
  const activeSidRef = useRef(activeSid);
  const feedbackMapRef = useRef(feedbackMap);
  const messagesRef = useRef(messages);
  const assistantIdRef = useRef(assistantId);

  // keep refs in sync
  useEffect(() => { activeSidRef.current = activeSid; }, [activeSid]);
  useEffect(() => { feedbackMapRef.current = feedbackMap; }, [feedbackMap]);
  useEffect(() => { messagesRef.current = messages; }, [messages]);
  useEffect(() => { assistantIdRef.current = assistantId; }, [assistantId]);

  // cleanup on unmount
  useEffect(() => {
    return () => {
      abortRef.current?.abort();
    };
  }, []);

  /* ---- auth error helper ---- */
  const handleAuthError = useCallback(
    async (e: any) => {
      if (String(e?.message) === 'UNAUTHORIZED') {
        abortRef.current?.abort();
        abortRef.current = null;
        msgApi.warning('登录态失效，请重新登录');
        await logout();
        onLogout();
        return true;
      }
      return false;
    },
    [msgApi, onLogout],
  );

  /* ---- load sessions ---- */
  const loadSessions = useCallback(async () => {
    try {
      const list = await fetchSessions();
      setSessions(list);
      return list;
    } catch (e: any) {
      await handleAuthError(e);
      return [];
    }
  }, [handleAuthError]);

  /* ---- load messages for a session ---- */
  const loadMessages = useCallback(
    async (sid: string) => {
      try {
        const msgs = await fetchMessages(sid);
        if (activeSidRef.current !== sid) return;
        const bubbles: BubbleDataType[] = msgs.map((m) => ({
          key: String(m.id),
          role: m.role === 'user' ? 'user' : 'assistant',
          content: m.content,
        }));
        setMessages(bubbles);
      } catch (e: any) {
        if (activeSidRef.current !== sid) return;
        await handleAuthError(e);
      }
    },
    [handleAuthError],
  );

  /* ---- load feedback for a session ---- */
  const loadFeedback = useCallback(
    async (sid: string) => {
      try {
        const fb = await fetchFeedback(sid);
        if (activeSidRef.current !== sid) return;
        setFeedbackMap(fb as Record<string, 'up' | 'down'>);
      } catch {
        if (activeSidRef.current !== sid) return;
        // non-critical
      }
    },
    [],
  );

  /* ---- load assistants ---- */
  useEffect(() => {
    (async () => {
      try {
        const list = await fetchAssistants();
        setAssistants(list);
      } catch (e: any) {
        await handleAuthError(e);
      }
    })();
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  /* ---- init: load sessions & pick first ---- */
  useEffect(() => {
    (async () => {
      const list = await loadSessions();
      if (list.length > 0) {
        const first = list[0].id;
        setActiveSid(first);
        await loadMessages(first);
        await loadFeedback(first);
      } else {
        const sid = newSessionId();
        setActiveSid(sid);
      }
    })();
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  /* ---- switch session ---- */
  const switchSession = useCallback(
    async (sid: string) => {
      abortRef.current?.abort();
      abortRef.current = null;
      setStreaming(false);
      setSuggestions([]);
      setThinkingSteps([]);
      setActiveSid(sid);
      await loadMessages(sid);
      await loadFeedback(sid);
    },
    [loadMessages, loadFeedback],
  );

  /* ---- delete session ---- */
  const handleDeleteSession = useCallback(
    async (sid: string) => {
      try {
        await deleteSession(sid);
        const list = await loadSessions();
        if (activeSid === sid) {
          if (list.length > 0) {
            await switchSession(list[0].id);
          } else {
            const newSid = newSessionId();
            setActiveSid(newSid);
            setMessages([]);
            setFeedbackMap({});
          }
        }
      } catch (e: any) {
        await handleAuthError(e);
      }
    },
    [activeSid, loadSessions, switchSession, handleAuthError],
  );

  /* ---- locate citation ---- */
  const handleLocate = useCallback(
    async (filename: string, page: string | undefined) => {
      try {
        // 引用链接处拿不到原始问题：用最近一条用户消息作为定位查询
        const lastUser = [...messagesRef.current]
          .reverse()
          .find((m) => m.role === 'user');
        const params = new URLSearchParams({ doc: filename });
        if (lastUser?.content) params.set('q', String(lastUser.content));
        const r = await fetch('/api/locate?' + params.toString());
        if (r.status === 401) {
          await handleAuthError(new Error('UNAUTHORIZED'));
          return;
        }
        if (!r.ok) {
          msgApi.error('定位失败');
          return;
        }
        const data = await r.json();
        if (!data.found) {
          msgApi.warning('未定位到引用片段');
          return;
        }
        Modal.info({
          title: `来源: ${filename}${page ? ` · 第${page}页` : ''}`,
          width: 640,
          content: (
            <Typography.Paragraph
              style={{ maxHeight: 400, overflow: 'auto', whiteSpace: 'pre-wrap' }}
            >
              {data.text}
            </Typography.Paragraph>
          ),
        });
      } catch (e: any) {
        if (await handleAuthError(e)) return;
        msgApi.error('定位请求失败');
      }
    },
    [msgApi, handleAuthError],
  );

  /* ---- feedback ---- */
  const handleFeedback = useCallback(
    async (seq: number, rating: 'up' | 'down') => {
      try {
        await submitFeedback(activeSid, seq, rating);
        setFeedbackMap((prev) => ({ ...prev, [String(seq)]: rating }));
      } catch (e: any) {
        await handleAuthError(e);
      }
    },
    [activeSid, handleAuthError],
  );

  /* ---- SSE send ---- */
  const handleSend = useCallback(
    async (question: string) => {
      const q = question.trim();
      if (!q || streaming) return;

      abortRef.current?.abort();
      const ctrl = new AbortController();
      abortRef.current = ctrl;

      const userKey = `u-${Date.now()}`;
      const assistantKey = `a-${Date.now()}`;

      setMessages((prev) => [
        ...prev,
        { key: userKey, role: 'user', content: q },
        { key: assistantKey, role: 'assistant', content: '', loading: true },
      ]);
      setStreaming(true);
      setThinkingSteps([]);
      setSuggestions([]);
      setLastFailedQuestion(null);
      streamTokenRef.current = '';
      streamThinkingRef.current = '';

      let finalReceived = false;
      let errorReported = false;
      let retryCount = 0;
      const MAX_RETRIES = 1;

      try {
        while (retryCount <= MAX_RETRIES) {
          try {
            for await (const ev of chatStream(q, activeSidRef.current, ctrl.signal, assistantIdRef.current)) {
              if (ctrl.signal.aborted) break;

              switch (ev.event) {
                case 'cache': {
                  streamTokenRef.current = ev.data.answer;
                  setMessages((prev) =>
                    prev.map((m) =>
                      m.key === assistantKey
                        ? { ...m, content: ev.data.answer, loading: false }
                        : m,
                    ),
                  );
                  break;
                }
                case 'thinking': {
                  streamThinkingRef.current += ev.data.text || '';
                  const accText = streamThinkingRef.current;
                  setThinkingSteps((prev) => {
                    const updated = [...prev];
                    const lastIdx = updated.length - 1;
                    if (lastIdx >= 0 && updated[lastIdx].key === 'thinking-live') {
                      updated[lastIdx] = { ...updated[lastIdx], content: accText };
                    } else {
                      updated.push({
                        key: 'thinking-live',
                        title: '思考中',
                        content: accText,
                        status: 'pending',
                        icon: <RobotOutlined />,
                      });
                    }
                    return updated;
                  });
                  break;
                }
                case 'token': {
                  streamTokenRef.current += ev.data.text || '';
                  const accToken = streamTokenRef.current;
                  setMessages((prev) =>
                    prev.map((m) =>
                      m.key === assistantKey
                        ? { ...m, content: accToken, loading: false }
                        : m,
                    ),
                  );
                  break;
                }
                case 'step': {
                  const stepKind = ev.data.step_kind || 'step';
                  const stepText = ev.data.text || '';
                  setThinkingSteps((prev) => [
                    ...prev,
                    {
                      key: `step-${Date.now()}`,
                      title: stepKind,
                      content: stepText,
                      status: 'success',
                      icon: <ApiOutlined />,
                    },
                  ]);
                  break;
                }
                case 'error': {
                  msgApi.error(ev.data.message || '流式响应出错');
                  break;
                }
                case 'final': {
                  finalReceived = true;
                  const fullAnswer = ev.data.answer || streamTokenRef.current;
                  streamTokenRef.current = fullAnswer;
                  setMessages((prev) =>
                    prev.map((m) =>
                      m.key === assistantKey
                        ? { ...m, content: fullAnswer, loading: false }
                        : m,
                    ),
                  );
                  setThinkingSteps((prev) =>
                    prev.map((item) =>
                      item.key === 'thinking-live'
                        ? { ...item, status: 'success' as const }
                        : item,
                    ),
                  );
                  if (fullAnswer.length >= 80) {
                    fetchSuggestions(q, fullAnswer.slice(0, 800))
                      .then(setSuggestions)
                      .catch(() => {});
                  }
                  break;
                }
                case 'done': {
                  finalReceived = true;
                  loadSessions();
                  break;
                }
              }
            }
            break; // 流正常结束，退出重试循环
          } catch (e: any) {
            if (await handleAuthError(e)) return;
            if (String(e?.message) === 'AbortError' || ctrl.signal.aborted) return;

            const isNetworkError = e instanceof TypeError;
            const noTokensYet = streamTokenRef.current.length === 0;
            if (isNetworkError && noTokensYet && retryCount < MAX_RETRIES) {
              retryCount++;
              // 指数退避后自动重试一次
              await new Promise((r) => setTimeout(r, 1000 * retryCount));
              streamThinkingRef.current = '';
              setThinkingSteps([]);
              continue;
            }

            // 放弃重试：记录错误并保存失败问题供手动重试
            errorReported = true;
            msgApi.error('请求失败：' + e?.message);
            setLastFailedQuestion(q);
            if (streamTokenRef.current.length === 0) {
              // 完全失败（未收到任何内容）：移除乐观添加的用户/助手消息，
              // 避免留下无回复的"幽灵消息"；可经下方"重试"按钮重新发送
              setMessages((prev) =>
                prev.filter((m) => m.key !== userKey && m.key !== assistantKey),
              );
            }
            break;
          }
        }

        if (!finalReceived && !ctrl.signal.aborted && !errorReported) {
          msgApi.warning('连接中断，回复不完整');
        }
      } finally {
        setStreaming(false);
        abortRef.current = null;
      }
    },
    [streaming, msgApi, handleAuthError, loadSessions],
  );

  /* ---- cancel stream ---- */
  const handleCancel = useCallback(() => {
    abortRef.current?.abort();
    abortRef.current = null;
    setStreaming(false);
  }, []);

  /* ---- new chat ---- */
  const handleNewChat = useCallback(() => {
    abortRef.current?.abort();
    abortRef.current = null;
    setStreaming(false);
    setSuggestions([]);
    setThinkingSteps([]);
    const sid = newSessionId();
    setActiveSid(sid);
    setMessages([]);
    setFeedbackMap({});
  }, []);

  /* ---- assistant selector options ---- */
  const assistantOptions = useMemo(() => {
    const opts = assistants.map((a) => ({ value: a.id, label: a.name }));
    if (!opts.some((o) => o.value === assistantId)) {
      const fallback = assistantId || 'default';
      opts.unshift({
        value: fallback,
        label: fallback === 'default' ? '默认助手' : fallback,
      });
    }
    return opts;
  }, [assistants, assistantId]);

  /* ---- conversation items ---- */
  const convItems: Conversation[] = sessions.map((s) => ({
    key: s.id,
    label: s.title || s.id.slice(0, 16),
  }));

  /* ---- menu for conversations ---- */
  const convMenu = (conv: Conversation) => ({
    items: [
      {
        label: '删除',
        key: 'delete',
        icon: <DeleteOutlined />,
        danger: true,
      },
    ],
    onClick: (info: any) => {
      if (info.key === 'delete') {
        handleDeleteSession(conv.key);
      }
    },
  });

  /* ---- render helper: AI message content ---- */
  const renderAssistantContent = (content: string, isCurrentlyStreaming: boolean) => {
    return (
      <div>
        {isCurrentlyStreaming && thinkingSteps.length > 0 && (
          <div className="dm-thought-chain">
            <ThoughtChain
              items={thinkingSteps}
              collapsible
              size="small"
            />
          </div>
        )}
        {isCurrentlyStreaming ? (
          <div style={{ whiteSpace: 'pre-wrap' }}>{content || ''}</div>
        ) : (
          <MarkdownContent content={content || ''} onLocate={handleLocate} />
        )}
      </div>
    );
  };

  /* ---- render helper: feedback footer ---- */
  const renderAssistantFooter = (_content: string, info: { key?: string | number }) => {
    const curMsgs = messagesRef.current;
    const idx = curMsgs.findIndex((m) => m.key === info.key);
    if (idx < 0) return null;
    let assistantIdx = 0;
    for (let i = 0; i <= idx; i++) {
      if (curMsgs[i].role === 'assistant') {
        if (i === idx) break;
        assistantIdx++;
      }
    }
    const seq = 2 * assistantIdx + 1;
    const seqKey = String(seq);
    const fb = feedbackMapRef.current[seqKey];
    return (
      <div className="dm-feedback">
        <Button
          type="text"
          size="small"
          icon={fb === 'up' ? <LikeFilled style={{ color: '#6366f1' }} /> : <LikeOutlined />}
          onClick={() => handleFeedback(seq, 'up')}
        />
        <Button
          type="text"
          size="small"
          icon={fb === 'down' ? <DislikeFilled style={{ color: '#6366f1' }} /> : <DislikeOutlined />}
          onClick={() => handleFeedback(seq, 'down')}
        />
      </div>
    );
  };

  /* ---- bubble roles ---- */
  const bubbleRoles = {
    user: {
      placement: 'end' as const,
      avatar: { icon: <UserOutlined />, style: { background: '#6366f1' } },
      variant: 'filled' as const,
    },
    assistant: {
      placement: 'start' as const,
      avatar: { icon: <ApiOutlined />, style: { background: '#f0f0f0', color: '#6366f1' } },
      variant: 'filled' as const,
      messageRender: (content: string, _type?: any, info?: { key?: string | number }) => {
        const curMsgs = messagesRef.current;
        const isLast = !!info?.key && curMsgs.length > 0 && curMsgs[curMsgs.length - 1].key === info.key;
        return renderAssistantContent(content, isLast && streaming);
      },
      footer: (_content: string, info: { key?: string | number }) =>
        renderAssistantFooter(_content, info),
    },
  };

  /* ---- render ---- */
  return (
    <div className="dm-chat">
      {/* Sidebar */}
      <div className="dm-chat-sidebar">
        <div className="dm-chat-sidebar-header">
          <Button
            type="primary"
            icon={<PlusOutlined />}
            block
            onClick={handleNewChat}
          >
            新对话
          </Button>
          <Select
            size="small"
            value={assistantId}
            options={assistantOptions}
            onChange={(v: string) => {
              setAssistantId(v);
              assistantIdRef.current = v;
              localStorage.setItem('dm_assistant_id', v);
            }}
            style={{ width: '100%', marginTop: 8 }}
          />
        </div>
        <div className="dm-chat-sidebar-list">
          <Conversations
            items={convItems}
            activeKey={activeSid}
            onActiveChange={(key) => switchSession(key)}
            menu={convMenu}
          />
        </div>
      </div>

      {/* Main */}
      <div className="dm-chat-main">
        {/* Messages */}
        <div className="dm-chat-messages">
          <Bubble.List
            items={messages}
            roles={bubbleRoles as any}
            autoScroll
          />
        </div>

        {/* Suggestions */}
        {suggestions.length > 0 && (
          <div className="dm-suggestions">
            {suggestions.map((s, i) => (
              <Button
                key={i}
                size="small"
                onClick={() => {
                  setSuggestions([]);
                  handleSend(s);
                }}
              >
                {s}
              </Button>
            ))}
          </div>
        )}

        {/* 上次请求失败提示 */}
        {lastFailedQuestion && (
          <div style={{ textAlign: 'center', padding: '8px' }}>
            <Space>
              <span style={{ color: '#ff4d4f', fontSize: 13 }}>上次请求失败</span>
              <Button
                size="small"
                onClick={() => {
                  const q = lastFailedQuestion;
                  setLastFailedQuestion(null);
                  handleSend(q);
                }}
              >
                重试
              </Button>
              <Button size="small" type="text" onClick={() => setLastFailedQuestion(null)}>
                忽略
              </Button>
            </Space>
          </div>
        )}

        {/* Input */}
        <div className="dm-chat-input">
          <Sender
            value={senderValue}
            onChange={setSenderValue}
            onSubmit={(text) => {
              setSenderValue('');
              handleSend(text);
            }}
            onCancel={handleCancel}
            loading={streaming}
            placeholder="输入问题，Enter 发送…"
          />
        </div>
      </div>
    </div>
  );
}
