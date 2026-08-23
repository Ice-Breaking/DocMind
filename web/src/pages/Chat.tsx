import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from 'react';
import { useLocation, useNavigate } from 'react-router-dom';
import {
  DeleteOutlined,
  RightOutlined,
  DownOutlined,
  PaperClipOutlined,
  SearchOutlined,
  DownloadOutlined,
  CloseOutlined,
  PlusOutlined,
  MenuOutlined,
  AudioOutlined,
} from '@ant-design/icons';
import { App, Button, Image, Input, Modal, Progress, Select, Space, Typography } from 'antd';
import Bubble from '@ant-design/x/es/bubble';
import type { BubbleDataType } from '@ant-design/x/es/bubble/BubbleList';
import Conversations from '@ant-design/x/es/conversations';
import { Menu } from 'antd';
import { buildNavItems, flattenNavKeys } from '../nav';
import type { Conversation } from '@ant-design/x/es/conversations/interface';
import Sender from '@ant-design/x/es/sender';
import {
  deleteSession,
  fetchAssistants,
  fetchFeedback,
  fetchMessages,
  fetchSessions,
  logout,
  submitFeedback,
  fetchVoices,
  type Assistant,
  type Me,
  type Session,
  type VoiceOption,
} from '../api';
import {
  fmtSessionTime,
  groupSessions,
  newSessionId,
} from './chat/utils';
import { useVoiceInput } from './chat/useVoiceInput';
import { useSpeech } from './chat/useSpeech';
import { useChatStream, type ChatStreamBridge } from './chat/useChatStream';
import { useBubbleRoles } from './chat/useBubbleRoles';

/* ------------------------------------------------------------------ */
/*  Component                                                          */
/* ------------------------------------------------------------------ */

export default function Chat({ me: _me, onLogout }: { me: Me; onLogout: () => void }) {
  const { message: msgApi } = App.useApp();

  /* ---- state ---- */
  const [sessions, setSessions] = useState<Session[]>([]);
  const [activeSid, setActiveSid] = useState<string>('');
  const [feedbackMap, setFeedbackMap] = useState<Record<string, 'up' | 'down'>>({});
  const [senderValue, setSenderValue] = useState('');
  const [sidebarOpen, setSidebarOpen] = useState(false);
  // 语音：音色 / 录音 / 播报
  const [voiceId, setVoiceId] = useState<string>(
    () => localStorage.getItem('dm_voice') || 'Cherry',
  );
  const [voiceOptions, setVoiceOptions] = useState<VoiceOption[]>([]);
  const [convSearch, setConvSearch] = useState('');
  const navigate = useNavigate();
  const location = useLocation();
  const [navOpen, setNavOpen] = useState(false);
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);   // PC:☰ 收起整个侧栏
  const navItems = useMemo(() => buildNavItems(!!_me.is_admin), [_me.is_admin]);
  const navLeafKeys = useMemo(() => flattenNavKeys(navItems), [navItems]);
  const navSelected = navLeafKeys.includes(location.pathname) ? location.pathname : '';

  const MAX_IMGS = 5;   // 单条消息最多携带图片数
  const imgInputRef = useRef<HTMLInputElement | null>(null);
  const [assistants, setAssistants] = useState<Assistant[]>([]);
  const [assistantId, setAssistantId] = useState<string>(
    () => localStorage.getItem('dm_assistant_id') || 'default',
  );

  /* ---- refs ---- */
  const activeSidRef = useRef(activeSid);
  const assistantIdRef = useRef(assistantId);
  const feedbackMapRef = useRef(feedbackMap);

  // keep refs in sync
  useEffect(() => { activeSidRef.current = activeSid; }, [activeSid]);
  useEffect(() => { feedbackMapRef.current = feedbackMap; }, [feedbackMap]);
  useEffect(() => {
    fetchVoices().then(setVoiceOptions).catch(() => undefined);
  }, []);
  useEffect(() => { assistantIdRef.current = assistantId; }, [assistantId]);

  /* ---- 流式发送域：SSE 链路与消息状态整体内聚（useChatStream） ---- */
  // 宿主的 handleAuthError / loadSessions 定义于其后，经 latest-ref 桥回填，
  // 规避「hook ←→ 宿主回调」的声明顺序环（行为与原先直连一致）
  const streamBridge = useRef<ChatStreamBridge>({
    onAuthError: async () => false,
    reloadSessions: () => undefined,
  });
  const {
    messages, setMessages, messagesRef,
    streaming, thinkingSteps, setThinkingSteps,
    suggestions, setSuggestions,
    lastFailedQuestion, setLastFailedQuestion,
    setFailedMap, failedMapRef,
    imageAttaches, setImageAttaches,
    uploadPct,
    handleSend, handleRetry, handleRegenerate, handleCancel, abortActive,
  } = useChatStream({ msgApi, activeSidRef, assistantIdRef, bridgeRef: streamBridge });
  // 卸载时中断流已随 abortRef 内聚到 useChatStream

  /* ---- auth error helper ---- */
  const handleAuthError = useCallback(
    async (e: any) => {
      if (String(e?.message) === 'UNAUTHORIZED') {
        abortActive();
        msgApi.warning('登录态失效，请重新登录');
        await logout();
        onLogout();
        return true;
      }
      return false;
    },
    [abortActive, msgApi, onLogout],
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

  // 回填流式域回调桥（每渲染更新，hook 内经 ref 读最新闭包）
  streamBridge.current.onAuthError = handleAuthError;
  streamBridge.current.reloadSessions = loadSessions;

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
          ts: (m.created_at || 0) * 1000,
        } as BubbleDataType));
        setMessages(bubbles);
      } catch (e: any) {
        if (activeSidRef.current !== sid) return;
        await handleAuthError(e);
      }
    },
    [handleAuthError, setMessages],
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
      handleCancel();   // 中断流并复位（abort + setStreaming(false)）
      setSuggestions([]);
      setThinkingSteps([]);
      setActiveSid(sid);
      await loadMessages(sid);
      await loadFeedback(sid);
    },
    [handleCancel, loadMessages, loadFeedback, setSuggestions, setThinkingSteps],
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
    [activeSid, loadSessions, switchSession, handleAuthError, setMessages],
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
    [msgApi, handleAuthError, messagesRef],
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

  /* ---- 语音输入：豆包式按住说话，松开转写，上滑取消（逻辑见 useVoiceInput） ---- */
  const {
    recording,
    cancelMode,
    micRef,
    beginRecord,
    moveRecord,
    endRecord,
  } = useVoiceInput((text) => setSenderValue((v) => (v ? `${v} ${text}` : text)), msgApi);

  /* ---- TTS 播报（状态机与缓存见 useSpeech） ---- */
  const { speech, handleSpeak } = useSpeech(voiceId, msgApi);

  /* ---- new chat ---- */
  const handleNewChat = useCallback(() => {
    handleCancel();   // 中断流并复位（abort + setStreaming(false)）
    setSuggestions([]);
    setThinkingSteps([]);
    const sid = newSessionId();
    setActiveSid(sid);
    setMessages([]);
    setFeedbackMap({});
    setFailedMap({});
  }, [handleCancel, setMessages, setSuggestions, setThinkingSteps, setFailedMap]);

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

  /* ---- 会话列表:时间格式化 + 分组（参考 IM 惯例 Today/Yesterday/7天内/更早，实现见 chat/utils） ---- */
  const sessionGroups = useMemo(
    () => groupSessions(sessions),
    [sessions],
  );

  /* ---- conversation items（按搜索词过滤） ---- */

  const kw = convSearch.trim().toLowerCase();
  const toConv = (s: typeof sessions[number]): Conversation => ({
    key: s.id,
    label: (
      <div style={{ display: 'flex', flexDirection: 'column', gap: 2, minWidth: 0 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <span style={{ fontWeight: 600, fontSize: 13.5, overflow: 'hidden',
                         textOverflow: 'ellipsis', whiteSpace: 'nowrap', flex: 1 }}>
            {s.title || s.id.slice(0, 16)}
          </span>
          <span style={{ fontSize: 11, color: '#9a9a9a', flexShrink: 0 }}>
            {fmtSessionTime(s.updated_at || 0)}
          </span>
        </div>
        {s.last_msg && (
          <span style={{ fontSize: 12, color: '#9a9a9a', overflow: 'hidden',
                         textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
            {s.last_msg}
          </span>
        )}
      </div>
    ),
  });
  const convItems: Conversation[] = sessions
    .filter((s) => !kw || (s.title || '').toLowerCase().includes(kw))
    .map(toConv);

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

  /* ---- bubble roles：渲染配置内聚（useBubbleRoles）---- */
  const currentAssistant = assistants.find((a) => a.id === assistantId);
  const { bubbleRoles } = useBubbleRoles({
    me: _me,
    currentAssistant,
    streaming,
    thinkingSteps,
    speech,
    messagesRef,
    feedbackMapRef,
    failedMapRef,
    onLocate: handleLocate,
    onRetry: handleRetry,
    onRegenerate: handleRegenerate,
    onFeedback: handleFeedback,
    onSpeak: handleSpeak,
  });

  /* ---- render ---- */
  return (
    <div className="dm-chat">
      {/* Sidebar（移动端为抽屉，见 styles.css 媒体查询） */}
      <div className={`dm-chat-sidebar${sidebarOpen ? ' dm-open' : ''}${sidebarCollapsed ? ' dm-collapsed' : ''}`}>
        <div className="dm-chat-sidebar-header">
          <div
            className="dm-nav-head"
            role="button"
            title={navOpen ? '收起菜单' : '展开菜单'}
            onClick={() => setNavOpen(v => !v)}
          >
            <MenuOutlined className="dm-nav-toggle-icon" />
            <span className="dm-nav-brand">DocMind</span>
            <span className={`dm-nav-arrow${navOpen ? ' open' : ''}`}>
              {navOpen ? <DownOutlined /> : <RightOutlined />}
            </span>
          </div>
          {navOpen && (
            <Menu
              mode="inline"
              theme="light"
              className="dm-side-nav"
              selectedKeys={navSelected ? [navSelected] : []}
              defaultOpenKeys={navItems.filter(i => i.children).map(i => i.key)}
              items={navItems as any}
              onClick={(e) => navigate(e.key)}
            />
          )}
          <Button
            className="dm-mobile-newchat"
            type="primary" block size="small" icon={<PlusOutlined />}
            onClick={() => { handleNewChat(); setSidebarOpen(false); }}
          >
            新对话
          </Button>
          <Input
            allowClear
            size="small"
            prefix={<SearchOutlined style={{ color: '#999' }} />}
            placeholder="搜索对话"
            value={convSearch}
            onChange={(e: React.ChangeEvent<HTMLInputElement>) => setConvSearch(e.target.value)}
            style={{ marginTop: navOpen ? 8 : 6 }}
          />
        </div>
        <div className="dm-chat-sidebar-list">
          {kw ? (
            <Conversations
              items={convItems}
              activeKey={activeSid}
              onActiveChange={(key) => { switchSession(key); setSidebarOpen(false); }}
              menu={convMenu}
            />
          ) : (
            sessionGroups.map((g) => (
              <div key={g.label}>
                <div className="dm-conv-group-label">{g.label}</div>
                <Conversations
                  items={g.items.map(toConv)}
                  activeKey={activeSid}
                  onActiveChange={(key) => { switchSession(key); setSidebarOpen(false); }}
                  menu={convMenu}
                />
              </div>
            ))
          )}
        </div>
      </div>

      {/* Main */}
      <div className="dm-chat-main">
        <div className="dm-chat-topbar">
          <button
            className="dm-topbar-menu"
            onClick={() => {
              if (window.innerWidth < 768) setSidebarOpen(v => !v);
              else setSidebarCollapsed(v => !v);
            }}
            title={sidebarCollapsed ? '展开侧栏' : '收起侧栏'}
          >
            <MenuOutlined />
          </button>
          <span className="dm-topbar-title">
            {sessions.find((x) => x.id === activeSid)?.title || '新对话'}
          </span>
          <div className="dm-topbar-actions">
            <Select
              size="small"
              variant="borderless"
              value={assistantId}
              options={assistantOptions}
              onChange={(v: string) => {
                setAssistantId(v);
                assistantIdRef.current = v;
                localStorage.setItem('dm_assistant_id', v);
              }}
              style={{ width: 140 }}
            />
            <Button size="small" type="text" icon={<DownloadOutlined />}
              title="导出当前会话为 Markdown" disabled={!activeSidRef.current}
              onClick={async () => {
                const sid = activeSidRef.current;
                if (!sid) return;
                if (messagesRef.current.length === 0) {
                  msgApi.warning('当前对话暂无内容，先聊点什么再导出吧');
                  return;
                }
                try {
                  const r = await fetch(`/api/sessions/${encodeURIComponent(sid)}/export`);
                  if (!r.ok) throw new Error('导出失败');
                  const blob = await r.blob();
                  const a = document.createElement('a');
                  a.href = URL.createObjectURL(blob);
                  a.download = `docmind-${sid}.md`;
                  a.click();
                  URL.revokeObjectURL(a.href);
                  msgApi.success('已导出 Markdown');
                } catch {
                  msgApi.error('导出失败，请重试');
                }
              }}
            />
          </div>
        </div>
        {sidebarOpen && (
          <div className="dm-chat-mask" onClick={() => setSidebarOpen(false)} />
        )}
        {/* Messages */}
        <div className="dm-chat-messages">
          {messages.length === 0 && !streaming && (
            <div className="dm-welcome">
              <div className="dm-welcome-card">
                <img src="/icons/icon-192.png" alt="DocMind" className="dm-welcome-logo" />
                <div className="dm-welcome-title">你好，我是 DocMind</div>
                <div className="dm-welcome-sub">严谨的知识助理 · 回答皆有据可循</div>
                <div className="dm-welcome-feats">
                  <span>📚 知识库问答</span>
                  <span>🔍 引用溯源</span>
                  <span>🌐 联网交叉核验</span>
                </div>
              </div>
              <div className="dm-welcome-chips">
                {['你能做什么？', '什么是 RAG？', '用三句话总结知识库要点'].map((q) => (
                  <button key={q} onClick={() => handleSend(q)}>{q}</button>
                ))}
              </div>
            </div>
          )}
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
          {recording && (
            <div className={`dm-voice-overlay${cancelMode ? ' cancel' : ''}`}>
              <div className="dm-voice-overlay-tip">
                {cancelMode ? '松开手指，取消输入' : '正在聆听，松开发送 · 上滑取消'}
              </div>
            </div>
          )}
          <Sender
            value={senderValue}
            onChange={setSenderValue}
            onSubmit={(text) => {
              setSenderValue('');
              handleSend(text);
            }}
            onCancel={handleCancel}
            loading={streaming}
            placeholder={imageAttaches.length ? '可以补充文字说明，直接发送则由 AI 看图作答…' : '输入问题，Enter 发送…'}
            header={
              imageAttaches.length > 0 ? (
                <div className="dm-attach-bar" style={{ width: '100%' }}>
                  {imageAttaches.map((a, i) => (
                    <div key={i} className="dm-attach-thumb">
                      <Image
                        src={a.dataUrl}
                        alt={`附件${i + 1}`}
                        width={52}
                        height={52}
                        style={{ objectFit: 'cover', borderRadius: 8, cursor: 'pointer' }}
                        preview={{ mask: '预览' }}
                      />
                      {uploadPct == null && (
                        <CloseOutlined
                          className="dm-attach-close"
                          onClick={() => setImageAttaches((prev) => prev.filter((_, j) => j !== i))}
                        />
                      )}
                    </div>
                  ))}
                  {uploadPct != null ? (
                    <span className="dm-attach-tip">
                      <Progress type="circle" size={20} percent={uploadPct} showInfo={false} />
                      <span style={{ marginLeft: 6 }}>正在上传 {uploadPct}%</span>
                    </span>
                  ) : (
                    <span className="dm-attach-tip">
                      {imageAttaches.length}/{MAX_IMGS} 张 · 点击图片预览
                    </span>
                  )}
                </div>
              ) : undefined
            }
            actions={[
              <Select
                key="voice"
                size="small"
                variant="borderless"
                value={voiceId}
                onChange={(v) => {
                  setVoiceId(v);
                  localStorage.setItem('dm_voice', v);
                }}
                options={voiceOptions.map((o) => ({ value: o.id, label: o.label }))}
                style={{ width: 150 }}
                popupMatchSelectWidth={false}
              />,
              <button
                key="mic"
                ref={micRef}
                className={`dm-mic dm-mic-inline${recording ? ' recording' : ''}`}
                onPointerDown={beginRecord}
                onPointerMove={moveRecord}
                onPointerUp={endRecord}
                onPointerCancel={endRecord}
                onContextMenu={(e) => e.preventDefault()}
                title="按住说话"
                style={{ touchAction: 'none' }}
              >
                <AudioOutlined />
              </button>,
            ]}
            prefix={
              <>
                <button
                  className="dm-img-btn dm-newchat-btn"
                  title="开始新对话"
                  onClick={() => { handleNewChat(); setSidebarOpen(false); }}
                >
                  <PlusOutlined />
                </button>
                <button
                  className="dm-img-btn"
                  title="附加图片（AI 直接看图作答）"
                  onClick={() => imgInputRef.current?.click()}
                  style={{ marginLeft: 4 }}
                >
                  <PaperClipOutlined />
                </button>
              </>
            }
          />
          <input
            ref={imgInputRef}
            type="file"
            accept="image/png,image/jpeg,image/webp"
            style={{ display: 'none' }}
            onChange={(e) => {
              const f = e.target.files?.[0];
              e.target.value = '';
              if (!f) return;
              if (f.size > 8 * 1024 * 1024) {
                msgApi.error('图片过大（上限 8MB），请压缩后重试');
                return;
              }
              // canvas 重绘：①剥离 EXIF（手机照片含 GPS 定位/设备信息，隐私）
              // ②等比压缩到最长边 2048（原图 4096 级上传慢且浪费）
              const img = new window.Image();
              img.onload = () => {
                const MAX_EDGE = 2048;
                const scale = Math.min(1, MAX_EDGE / Math.max(img.width, img.height));
                const canvas = document.createElement('canvas');
                canvas.width = Math.round(img.width * scale);
                canvas.height = Math.round(img.height * scale);
                const ctx = canvas.getContext('2d');
                if (!ctx) {
                  msgApi.error('图片处理失败，请重试');
                  return;
                }
                // 透明 PNG 垫白底（JPEG 无透明通道）
                ctx.fillStyle = '#fff';
                ctx.fillRect(0, 0, canvas.width, canvas.height);
                ctx.drawImage(img, 0, 0, canvas.width, canvas.height);
                const dataUrl = canvas.toDataURL('image/jpeg', 0.85);
                setImageAttaches((prev) =>
                  prev.length >= MAX_IMGS
                    ? (msgApi.warning(`最多携带 ${MAX_IMGS} 张图片`), prev)
                    : [...prev, { dataUrl, base64: dataUrl }]);
              };
              img.onerror = () => msgApi.error('图片读取失败，请重试');
              img.src = URL.createObjectURL(f);
            }}
          />
        </div>
      </div>
    </div>
  );
}
