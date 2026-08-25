import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from 'react';
import { useLocation, useNavigate } from 'react-router-dom';
import { App, Button, Modal, Space, Typography } from 'antd';
import Bubble from '@ant-design/x/es/bubble';
import type { BubbleDataType } from '@ant-design/x/es/bubble/BubbleList';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  deleteSession,
  fetchAssistants,
  fetchFeedback,
  fetchMessages,
  fetchSessions,
  logout,
  submitFeedback,
  fetchVoices,
  type Me,
} from '../api';
import { newSessionId } from './chat/utils';
import { useVoiceInput } from './chat/useVoiceInput';
import { useSpeech } from './chat/useSpeech';
import { useChatStream, type ChatStreamBridge } from './chat/useChatStream';
import { useBubbleRoles } from './chat/useBubbleRoles';
import ChatSidebar from './chat/ChatSidebar';
import ChatTopbar from './chat/ChatTopbar';
import ChatInput from './chat/ChatInput';

/* ------------------------------------------------------------------ */
/*  Chat 页面（编排层）：服务端数据/流式状态在此，渲染拆至 chat/ 子组件      */
/*    ChatSidebar（侧栏）/ ChatTopbar（顶栏）/ ChatInput（输入域）          */
/* ------------------------------------------------------------------ */

export default function Chat({ me: _me, onLogout }: { me: Me; onLogout: () => void }) {
  const { message: msgApi } = App.useApp();

  /* ---- 服务端只读数据：react-query 托管 ----
   * SSE 流式消息仍由 useChatStream 本地持有，不进查询缓存。 */
  const qc = useQueryClient();
  const sessionsQ = useQuery({ queryKey: ['sessions'], queryFn: fetchSessions });
  const assistantsQ = useQuery({ queryKey: ['assistants'], queryFn: fetchAssistants });
  const voicesQ = useQuery({ queryKey: ['voices'], queryFn: fetchVoices });
  const sessions = useMemo(() => sessionsQ.data ?? [], [sessionsQ.data]);
  const assistants = useMemo(() => assistantsQ.data ?? [], [assistantsQ.data]);
  const voiceOptions = useMemo(() => voicesQ.data ?? [], [voicesQ.data]);
  const [activeSid, setActiveSid] = useState<string>('');
  const [senderValue, setSenderValue] = useState('');
  const [sidebarOpen, setSidebarOpen] = useState(false);
  // 语音：音色 / 录音 / 播报
  const [voiceId, setVoiceId] = useState<string>(
    () => localStorage.getItem('dm_voice') || 'Cherry',
  );
  const [convSearch, setConvSearch] = useState('');
  const navigate = useNavigate();
  const location = useLocation();
  const [navOpen, setNavOpen] = useState(false);
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);   // PC:☰ 收起整个侧栏
  const [assistantId, setAssistantId] = useState<string>(
    () => localStorage.getItem('dm_assistant_id') || 'default',
  );

  // 反馈映射：按会话键缓存；仅服务端已存在的会话才请求（新建本地 sid 不发请求，
  // 对齐旧 loadFeedback 仅在选中既有会话时调用的行为）
  const feedbackEnabled = useMemo(
    () => activeSid !== '' && sessions.some((s) => s.id === activeSid),
    [sessions, activeSid],
  );
  const feedbackQ = useQuery({
    queryKey: ['feedback', activeSid],
    queryFn: () => fetchFeedback(activeSid),
    enabled: feedbackEnabled,
  });
  const feedbackMap = useMemo<Record<string, 'up' | 'down'>>(
    () => (feedbackQ.data as Record<string, 'up' | 'down'>) ?? {},
    [feedbackQ.data],
  );

  /* ---- refs ---- */
  const activeSidRef = useRef(activeSid);
  const assistantIdRef = useRef(assistantId);
  const feedbackMapRef = useRef(feedbackMap);

  // keep refs in sync
  useEffect(() => { activeSidRef.current = activeSid; }, [activeSid]);
  useEffect(() => { feedbackMapRef.current = feedbackMap; }, [feedbackMap]);
  useEffect(() => { assistantIdRef.current = assistantId; }, [assistantId]);

  /* ---- 流式发送域：SSE 链路与消息状态整体内聚（useChatStream） ---- */
  // 宿主的 handleAuthError / 会话刷新（refetchSessions）定义于其后，经 latest-ref
  // 桥回填，规避「hook ←→ 宿主回调」的声明顺序环（行为与原先直连一致）
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

  // 回填流式域回调桥（每渲染更新，hook 内经 ref 读最新闭包）
  const refetchSessions = sessionsQ.refetch;
  streamBridge.current.onAuthError = handleAuthError;
  streamBridge.current.reloadSessions = () => {
    void refetchSessions();   // 流结束后刷新侧栏（标题/时间/last_msg）
  };

  // 查询首轮 401 统一登出；其余错误静默（侧栏空态，行为对齐旧 loadSessions 的 catch 分支）
  useEffect(() => {
    if (sessionsQ.isError || assistantsQ.isError) {
      void handleAuthError(sessionsQ.error ?? assistantsQ.error);
    }
  }, [sessionsQ.isError, sessionsQ.error, assistantsQ.isError, assistantsQ.error, handleAuthError]);

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

  /* ---- init: 会话查询就绪后自动选中首个（仅执行一次）；空列表进入新对话 ---- */
  const didPickRef = useRef(false);
  useEffect(() => {
    if (didPickRef.current || !sessionsQ.isSuccess) return;
    didPickRef.current = true;
    const list = sessionsQ.data ?? [];
    if (list.length > 0) {
      setActiveSid(list[0].id);
      void loadMessages(list[0].id);
    } else {
      setActiveSid(newSessionId());
    }
  }, [sessionsQ.isSuccess, sessionsQ.data, loadMessages]);

  /* ---- switch session ---- */
  const switchSession = useCallback(
    async (sid: string) => {
      handleCancel();   // 中断流并复位（abort + setStreaming(false)）
      setSuggestions([]);
      setThinkingSteps([]);
      setActiveSid(sid);
      await loadMessages(sid);
    },
    [handleCancel, loadMessages, setSuggestions, setThinkingSteps],
  );

  /* ---- delete session ---- */
  const handleDeleteSession = useCallback(
    async (sid: string) => {
      try {
        await deleteSession(sid);
        const res = await refetchSessions();
        const list = res.data ?? [];
        if (activeSidRef.current === sid) {
          if (list.length > 0) {
            await switchSession(list[0].id);
          } else {
            setActiveSid(newSessionId());
            setMessages([]);
          }
        }
      } catch (e: any) {
        await handleAuthError(e);
      }
    },
    [refetchSessions, switchSession, handleAuthError, setMessages],
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

  /* ---- feedback：mutation 提交，成功即写查询缓存（切回该会话仍保留） ---- */
  const feedbackMut = useMutation({
    mutationFn: async (v: { seq: number; rating: 'up' | 'down' }) => {
      await submitFeedback(activeSid, v.seq, v.rating);
      return v;
    },
    onSuccess: (v) => {
      qc.setQueryData<Record<string, 'up' | 'down'>>(
        ['feedback', activeSid],
        (prev) => ({ ...(prev ?? {}), [String(v.seq)]: v.rating }),
      );
    },
    onError: (e: any) => handleAuthError(e),
  });
  const { mutate: mutateFeedback } = feedbackMut;   // v5 mutate 引用稳定
  const handleFeedback = useCallback(
    (seq: number, rating: 'up' | 'down') => mutateFeedback({ seq, rating }),
    [mutateFeedback],
  );

  /* ---- 语音输入：豆包式按住说话，松开转写，上滑取消（逻辑见 useVoiceInput） ---- */
  const voiceInput = useVoiceInput(
    (text) => setSenderValue((v) => (v ? `${v} ${text}` : text)),
    msgApi,
  );

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

  /* ---- export current session as Markdown ---- */
  const handleExport = useCallback(async () => {
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
  }, [messagesRef, msgApi]);

  /* ---- render ---- */
  return (
    <div className="dm-chat">
      <ChatSidebar
        sessions={sessions}
        activeSid={activeSid}
        isAdmin={!!_me.is_admin}
        navOpen={navOpen}
        onToggleNav={() => setNavOpen(v => !v)}
        sidebarOpen={sidebarOpen}
        sidebarCollapsed={sidebarCollapsed}
        convSearch={convSearch}
        onConvSearchChange={setConvSearch}
        onSwitchSession={(key) => { void switchSession(key); setSidebarOpen(false); }}
        onDeleteSession={(sid) => { void handleDeleteSession(sid); }}
        onNewChat={() => { handleNewChat(); setSidebarOpen(false); }}
        onNavigate={(key) => navigate(key)}
        pathname={location.pathname}
      />

      {/* Main */}
      <div className="dm-chat-main">
        <ChatTopbar
          sessions={sessions}
          activeSid={activeSid}
          activeSidReady={!!activeSidRef.current}
          assistantId={assistantId}
          assistantOptions={assistantOptions}
          onAssistantChange={(v) => {
            setAssistantId(v);
            assistantIdRef.current = v;
            localStorage.setItem('dm_assistant_id', v);
          }}
          onExport={() => { void handleExport(); }}
          sidebarCollapsed={sidebarCollapsed}
          onToggleSidebar={() => {
            if (window.innerWidth < 768) setSidebarOpen(v => !v);
            else setSidebarCollapsed(v => !v);
          }}
        />
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

        <ChatInput
          senderValue={senderValue}
          onSenderValueChange={setSenderValue}
          onSubmit={handleSend}
          onCancel={handleCancel}
          streaming={streaming}
          imageAttaches={imageAttaches}
          onImageAttachesChange={setImageAttaches}
          uploadPct={uploadPct}
          voiceId={voiceId}
          onVoiceIdChange={(v) => {
            setVoiceId(v);
            localStorage.setItem('dm_voice', v);
          }}
          voiceOptions={voiceOptions}
          voiceInput={voiceInput}
          onNewChat={() => { handleNewChat(); setSidebarOpen(false); }}
        />
      </div>
    </div>
  );
}
