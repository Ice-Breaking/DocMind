import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from 'react';
import { useLocation, useNavigate } from 'react-router-dom';
import MarkdownContent from '../components/MarkdownContent';
import {
  ApiOutlined,
  DeleteOutlined,
  DislikeOutlined,
  RightOutlined,
  LikeOutlined,
  LikeFilled,
  DislikeFilled,
  DownOutlined,
  PaperClipOutlined,
  SearchOutlined,
  DownloadOutlined,
  CloseOutlined,
  PlusOutlined,
  RobotOutlined,
  MenuOutlined,
  ReloadOutlined,
  AudioOutlined,
  PauseOutlined,
  SoundOutlined,
} from '@ant-design/icons';
import { App, Button, Image, Input, Modal, Progress, Select, Space, Typography } from 'antd';
import UserAvatar from '../components/UserAvatar';
import {
  fetchVoices,
  type VoiceOption,
} from '../api';
import Bubble from '@ant-design/x/es/bubble';
import type { BubbleDataType } from '@ant-design/x/es/bubble/BubbleList';
import Conversations from '@ant-design/x/es/conversations';
import { Menu } from 'antd';
import { buildNavItems, flattenNavKeys } from '../nav';
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
import {
  computeAssistantSeq,
  extractWarnCapsules,
  fmtSessionTime,
  groupSessions,
  newSessionId,
  splitImagesFromText,
} from './chat/utils';
import { useVoiceInput } from './chat/useVoiceInput';
import { useSpeech } from './chat/useSpeech';

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
  const [sidebarOpen, setSidebarOpen] = useState(false);
  // 失败气泡映射：assistantKey → 原问题（供内联重试）
  const [failedMap, setFailedMap] = useState<Record<string, string>>({});
  // 语音：音色 / 录音 / 播报
  const [voiceId, setVoiceId] = useState<string>(
    () => localStorage.getItem('dm_voice') || 'Cherry',
  );
  const [voiceOptions, setVoiceOptions] = useState<VoiceOption[]>([]);
  const [imageAttaches, setImageAttaches] = useState<{ dataUrl: string; base64: string }[]>([]);
  const [convSearch, setConvSearch] = useState('');
  const navigate = useNavigate();
  const location = useLocation();
  const [navOpen, setNavOpen] = useState(false);
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);   // PC:☰ 收起整个侧栏
  const navItems = useMemo(() => buildNavItems(!!_me.is_admin), [_me.is_admin]);
  const navLeafKeys = useMemo(() => flattenNavKeys(navItems), [navItems]);
  const navSelected = navLeafKeys.includes(location.pathname) ? location.pathname : '';

  const [uploadPct, setUploadPct] = useState<number | null>(null);
  const MAX_IMGS = 5;   // 单条消息最多携带图片数
  const imgInputRef = useRef<HTMLInputElement | null>(null);
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
  const failedMapRef = useRef(failedMap);
  const messagesRef = useRef(messages);
  const assistantIdRef = useRef(assistantId);

  // keep refs in sync
  useEffect(() => { activeSidRef.current = activeSid; }, [activeSid]);
  useEffect(() => { feedbackMapRef.current = feedbackMap; }, [feedbackMap]);
  useEffect(() => { failedMapRef.current = failedMap; }, [failedMap]);
  useEffect(() => {
    fetchVoices().then(setVoiceOptions).catch(() => undefined);
  }, []);
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
          ts: (m.created_at || 0) * 1000,
        } as BubbleDataType));
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
    async (question: string, overrideImage?: string) => {
      const attaches = imageAttaches;
      const imgList: string[] = overrideImage
        ? [overrideImage]
        : attaches.map((a) => a.base64);
      const hasImg = imgList.length > 0;
      const q = question.trim() || (hasImg ? '请看这些图片。' : '');
      if (!q || streaming) return;
      if (overrideImage) setImageAttaches([]);
      if (hasImg) setUploadPct(0);   // 带图发送:展示上行进度,完成后再清附件

      abortRef.current?.abort();
      const ctrl = new AbortController();
      abortRef.current = ctrl;

      const userKey = `u-${Date.now()}`;
      const assistantKey = `a-${Date.now()}`;

      setMessages((prev) => [
        ...prev,
        {
          key: userKey,
          role: 'user',
          content: hasImg
            ? imgList.map((b) => `![图片](${b})`).join('\n') + `\n\n${q}`
            : q,
          ts: Date.now(),
        } as BubbleDataType,
        { key: assistantKey, role: 'assistant', content: '', loading: true, ts: Date.now() } as BubbleDataType,
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
      const MAX_RETRIES = 2;   // 连接失败/流中断自动重连(共 3 次尝试,指数退避)

      try {
        while (retryCount <= MAX_RETRIES) {
          try {
            for await (const ev of chatStream(q, activeSidRef.current, ctrl.signal, assistantIdRef.current,
              hasImg ? imgList : undefined,
              hasImg ? (pct) => {
                setUploadPct(pct);
                if (pct >= 100) { setImageAttaches([]); setUploadPct(null); }
              } : undefined)) {
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
                  // 双保险：后端 failed 标记 + ⚠️ 前缀兜底检测
                  const isFailed = !!ev.data.failed || fullAnswer.startsWith('⚠️');
                  setFailedMap((prev) => {
                    const next = { ...prev };
                    if (isFailed) next[assistantKey] = q;
                    else delete next[assistantKey];
                    return next;
                  });
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
            const isServerError = /HTTP 5\d\d/.test(String(e?.message));
            const canRetry = (isNetworkError || isServerError) && retryCount < MAX_RETRIES;
            if (canRetry) {
              // 自动重连:连接失败或流中途断(后端未落库,整轮重来安全)。
              // 清空已显示的 partial 内容,气泡标记重连状态,指数退避后重试
              retryCount++;
              await new Promise((r) => setTimeout(r, 800 * retryCount));
              streamTokenRef.current = '';
              streamThinkingRef.current = '';
              setThinkingSteps([]);
              setMessages((prev) => prev.map((m) =>
                m.key === assistantKey
                  ? { ...m, content: `⚠️ 连接中断，正在自动重连（第 ${retryCount}/${MAX_RETRIES} 次）…`, loading: true }
                  : m));
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
    [streaming, imageAttaches, msgApi, handleAuthError, loadSessions],
  );

  /* ---- 重新生成：取最后一条 assistant 前的 user 消息重发（含图） ---- */
  const handleRegenerate = useCallback(async () => {
    if (streaming) return;
    const msgs = messagesRef.current;
    let ai = -1;
    for (let i = msgs.length - 1; i >= 0; i--) {
      if (msgs[i].role === 'assistant') { ai = i; break; }
    }
    if (ai < 0) return;
    let ui = -1;
    for (let i = ai - 1; i >= 0; i--) {
      if (msgs[i].role === 'user') { ui = i; break; }
    }
    if (ui < 0) return;
    const uContent = msgs[ui].content || '';
    const m = uContent.match(/!\[[^\]]*\]\(([^)]+)\)/);
    let b64: string | undefined;
    if (m?.[1]?.startsWith('data:')) b64 = m[1];
    else if (m?.[1]) {
      try {
        const r = await fetch(m[1]);
        const blob = await r.blob();
        b64 = await new Promise<string>((res) => {
          const fr = new FileReader();
          fr.onload = () => res(String(fr.result || ''));
          fr.readAsDataURL(blob);
        });
      } catch { b64 = undefined; }
    }
    const q = uContent.replace(/!\[[^\]]*\]\([^)]+\)/g, '').trim();
    setMessages((prev) => prev.slice(0, ui));   // 移除旧问答对（前端）
    handleSend(q || '请描述这张图片。', b64);
  }, [streaming, handleSend]);

  /* ---- cancel stream ---- */
  const handleCancel = useCallback(() => {
    abortRef.current?.abort();
    abortRef.current = null;
    setStreaming(false);
  }, []);

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

  /* ---- inline retry：失败气泡原地重发 ---- */
  const handleRetry = useCallback(
    (key: string) => {
      const q = failedMapRef.current[key];
      if (!q || streaming) return;
      setFailedMap((prev) => {
        const next = { ...prev };
        delete next[key];
        return next;
      });
      // 移除失败气泡及其用户提问，再由 handleSend 重新乐观插入
      setMessages((prev) => {
        const idx = prev.findIndex((m) => m.key === key);
        if (idx < 0) return prev;
        const next = [...prev];
        next.splice(idx, 1);
        if (idx > 0 && next[idx - 1]?.role === 'user') next.splice(idx - 1, 1);
        return next;
      });
      handleSend(q);
    },
    [streaming, handleSend],
  );

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
    setFailedMap({});
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

  /* ---- render helper: AI message content ---- */
  const renderAssistantContent = (content: string, isCurrentlyStreaming: boolean) => {
    // 去说明书感：OOD 标注 / 通识来源 从方括号纯文本提取为警示胶囊
    const { text, capsules } = extractWarnCapsules(content);
    return (
      <div>
        {capsules.map((c, i) => (
          <div key={i} className={`dm-capsule ${c.cls}`}>{c.label}</div>
        ))}
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
          <div style={{ whiteSpace: 'pre-wrap' }}>{text}</div>
        ) : (
          <MarkdownContent content={text} onLocate={handleLocate} />
        )}
      </div>
    );
  };

  /* ---- render helper: feedback footer ---- */
  const renderAssistantFooter = (_content: string, info: { key?: string | number }) => {
    const curMsgs = messagesRef.current;
    const seq = computeAssistantSeq(curMsgs, info.key ?? '');
    if (seq == null) return null;
    const seqKey = String(seq);
    const fb = feedbackMapRef.current[seqKey];
    // 失败气泡：内联重试按钮（失败在哪，按钮在哪）
    const failedQ = failedMapRef.current[String(info.key)];
    if (failedQ) {
      return (
        <div className="dm-feedback">
          <Button
            size="small"
            type="primary"
            ghost
            icon={<ReloadOutlined />}
            onClick={() => handleRetry(String(info.key))}
          >
            重试
          </Button>
        </div>
      );
    }
    const isLastMsg = curMsgs.findIndex((m) => m.key === info.key) === curMsgs.length - 1 && !streaming;
    const lastIdx = curMsgs.findIndex((m) => m.key === info.key);
    const ts = (curMsgs[lastIdx] as any)?.ts as number | undefined;
    const tsText = ts
      ? (() => { const d = new Date(ts);
          return `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`; })()
      : null;
    return (
      <div className="dm-feedback">
        {tsText && <span style={{ fontSize: 11, color: '#9a9a9a', marginRight: 4 }}>{tsText}</span>}
        {isLastMsg && (
          <Button
            type="text"
            size="small"
            title="重新生成（基于上一条问题重问）"
            icon={<ReloadOutlined />}
            onClick={handleRegenerate}
          />
        )}
        <Button
          type="text"
          size="small"
          title="播报"
          icon={
            speech?.key === String(info.key) && speech.status === 'playing' ? (
              <PauseOutlined />
            ) : (
              <SoundOutlined />
            )
          }
          loading={speech?.key === String(info.key) && speech.status === 'loading'}
          onClick={() => handleSpeak(_content, String(info.key))}
        />
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
  const currentAssistant = assistants.find((a) => a.id === assistantId);
  const bubbleRoles = {
    user: {
      placement: 'end' as const,
      avatar: {
        icon: <UserAvatar avatar={_me.avatar} name={_me.user} size={28} />,
        style: { background: 'transparent' },
      },
      variant: 'filled' as const,
      messageRender: (content: string) => {
        // 图片消息：提取 markdown 图片（当轮为 dataUrl、历史为 /files/uploads 短链），
        // 以缩略图展示 + 点击预览；避免 base64 长 URL 以文本形式露出
        const { imgs, text } = splitImagesFromText(content);
        return (
          <div>
            {imgs.length > 0 && (
              <div
                style={{
                  display: 'flex', gap: 6, flexWrap: 'wrap',
                  marginBottom: text ? 6 : 0, justifyContent: 'flex-end',
                }}
              >
                {imgs.map((u, i) => (
                  <Image
                    key={i}
                    src={u}
                    alt="图片"
                    width={imgs.length > 1 ? 150 : 200}
                    height={imgs.length > 1 ? 150 : 200}
                    style={{ borderRadius: 10, objectFit: 'cover' }}
                    preview={{ mask: '预览' }}
                  />
                ))}
              </div>
            )}
            {text && <div style={{ whiteSpace: 'pre-wrap' }}>{text}</div>}
          </div>
        );
      },
      footer: (_c: string, info: { key?: string | number }) => {
        const m = messagesRef.current.find((x) => x.key === info.key);
        if (!(m as any)?.ts) return null;
        const d = new Date((m as any).ts);
        return (
          <div style={{ fontSize: 11, color: '#9a9a9a', textAlign: 'right' }}>
            {`${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`}
          </div>
        );
      },
    },
    assistant: {
      placement: 'start' as const,
      avatar: currentAssistant?.avatar
        ? {
            icon: (
              <UserAvatar
                avatar={currentAssistant.avatar}
                name={currentAssistant.name}
                size={28}
              />
            ),
            style: { background: 'transparent' },
          }
        : { icon: <RobotOutlined />, style: { background: '#6366f1', color: '#fff' } },
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
