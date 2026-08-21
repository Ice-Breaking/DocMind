import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from 'react';
import MarkdownContent from '../components/MarkdownContent';
import {
  ApiOutlined,
  DeleteOutlined,
  DislikeOutlined,
  LikeOutlined,
  LikeFilled,
  DislikeFilled,
  PlusOutlined,
  RobotOutlined,
  MenuOutlined,
  ReloadOutlined,
  AudioOutlined,
  PauseOutlined,
  SoundOutlined,
} from '@ant-design/icons';
import { App, Button, Modal, Select, Space, Typography } from 'antd';
import { blobToWav16k } from '../voice';
import UserAvatar from '../components/UserAvatar';
import {
  fetchVoices,
  transcribeAudio,
  synthesizeSpeech,
  type VoiceOption,
} from '../api';
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
  const [recording, setRecording] = useState(false);
  const audioRef = useRef<HTMLAudioElement | null>(null);
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

  /* ---- 语音输入：豆包式按住说话，松开转写，上滑取消 ---- */
  const micRef = useRef<HTMLButtonElement | null>(null);

  // 安卓长按时浏览器会把手势劫持去滚动/弹菜单，触发 pointercancel 导致录音中断。
  // React 的 touch 监听是 passive 的无法 preventDefault，故用原生非被动监听阻断劫持。
  useEffect(() => {
    const el = micRef.current;
    if (!el) return;
    const prevent = (e: TouchEvent) => e.preventDefault();
    el.addEventListener('touchstart', prevent, { passive: false });
    el.addEventListener('touchmove', prevent, { passive: false });
    return () => {
      el.removeEventListener('touchstart', prevent);
      el.removeEventListener('touchmove', prevent);
    };
  }, []);

  const recRef2 = useRef<{
    rec: MediaRecorder | null;
    chunks: Blob[];
    startY: number;
    cancel: boolean;
    stopped: boolean;
  } | null>(null);
  const [cancelMode, setCancelMode] = useState(false);

  const beginRecord = async (e: React.PointerEvent) => {
    if (recRef2.current) return;
    e.preventDefault();
    // 指针捕获：触摸期间即使手指移出按钮，move/up 仍派发到本元素，
    // 避免移动端 pointerleave 误触发"提前松手"导致录不到音
    try {
      (e.currentTarget as Element).setPointerCapture(e.pointerId);
    } catch {
      /* 不支持捕获的环境降级原行为 */
    }
    const st: {
      rec: MediaRecorder | null;
      chunks: Blob[];
      startY: number;
      cancel: boolean;
      stopped: boolean;
    } = { rec: null, chunks: [], startY: e.clientY, cancel: false, stopped: false };
    recRef2.current = st;
    setRecording(true);
    setCancelMode(false);
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      if (st.stopped) {
        // 授权期间已松手：直接清理，不识别
        stream.getTracks().forEach((t) => t.stop());
        recRef2.current = null;
        setRecording(false);
        return;
      }
      const rec = new MediaRecorder(stream);
      st.rec = rec;
      rec.ondataavailable = (ev) => {
        if (ev.data.size > 0) st.chunks.push(ev.data);
      };
      rec.onstop = async () => {
        stream.getTracks().forEach((t) => t.stop());
        const wasCancel = st.cancel;
        const chunks = st.chunks;
        recRef2.current = null;
        setRecording(false);
        setCancelMode(false);
        if (wasCancel) {
          msgApi.info('已取消语音输入');
          return;
        }
        try {
          const wav = await blobToWav16k(new Blob(chunks, { type: rec.mimeType }));
          const text = await transcribeAudio(wav);
          if (text) setSenderValue((v) => (v ? `${v} ${text}` : text));
          else msgApi.warning('没有听清，请再试一次');
        } catch (err: any) {
          msgApi.error(err?.message || '语音识别失败');
        }
      };
      rec.start();
    } catch {
      recRef2.current = null;
      setRecording(false);
      msgApi.error('无法访问麦克风，请检查浏览器权限');
    }
  };

  const moveRecord = (e: React.PointerEvent) => {
    const st = recRef2.current;
    if (!st) return;
    const cancel = st.startY - e.clientY > 48;
    if (cancel !== st.cancel) {
      st.cancel = cancel;
      setCancelMode(cancel);
    }
  };

  const endRecord = () => {
    const st = recRef2.current;
    if (!st) return;
    st.stopped = true;
    if (st.rec && st.rec.state !== 'inactive') st.rec.stop();
  };

  /* ---- TTS 播报：点击播放 / 再点暂停 / 再点续播；客户端缓存加速重播 ---- */
  const audioCacheRef = useRef<Map<string, string>>(new Map());
  const [speech, setSpeech] = useState<{
    key: string;
    status: 'loading' | 'playing' | 'paused';
  } | null>(null);
  const speechRef = useRef(speech);
  useEffect(() => {
    speechRef.current = speech;
  }, [speech]);

  const handleSpeak = useCallback(
    async (content: string, key: string) => {
      const cur = speechRef.current;
      if (cur && cur.key === key) {
        const a = audioRef.current;
        if (cur.status === 'playing') {
          a?.pause(); // onpause 事件更新状态为 paused
          return;
        }
        if (cur.status === 'paused') {
          a?.play().catch(() => setSpeech(null));
          return;
        }
        return; // loading 中忽略
      }
      audioRef.current?.pause();
      setSpeech({ key, status: 'loading' });
      try {
        const cacheKey = `${voiceId}|${key}`;
        let url = audioCacheRef.current.get(cacheKey);
        if (!url) {
          const blob = await synthesizeSpeech(content, voiceId);
          url = URL.createObjectURL(blob);
          audioCacheRef.current.set(cacheKey, url); // 会话内重播零等待
        }
        const audio = new Audio(url);
        audioRef.current = audio;
        audio.onplay = () => setSpeech({ key, status: 'playing' });
        audio.onpause = () =>
          setSpeech((s) => (s && s.key === key ? { key, status: 'paused' } : s));
        audio.onended = () => setSpeech((s) => (s && s.key === key ? null : s));
        audio.onerror = () => setSpeech((s) => (s && s.key === key ? null : s));
        await audio.play();
      } catch (e: any) {
        msgApi.error(e?.message || '播报失败');
        setSpeech(null);
      }
    },
    [voiceId, msgApi],
  );

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
    // 去说明书感：OOD 标注 / 通识来源 从方括号纯文本提取为警示胶囊
    let text = content || '';
    const capsules: { cls: string; label: string }[] = [];
    text = text.replace(/【(知识库无相关内容[^】]*)】/g, (_m, g: string) => {
      capsules.push({ cls: 'dm-capsule-warn', label: `⚠️ ${g}` });
      return '';
    });
    text = text.replace(/\[来源: 通识知识[^\]]*\]/g, () => {
      capsules.push({ cls: 'dm-capsule-warn', label: '📖 通识回答 · 未经知识库验证' });
      return '';
    });
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
    return (
      <div className="dm-feedback">
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
      <div className={`dm-chat-sidebar${sidebarOpen ? ' dm-open' : ''}`}>
        <div className="dm-chat-sidebar-header">
          <Button
            type="primary"
            icon={<PlusOutlined />}
            block
            onClick={() => { handleNewChat(); setSidebarOpen(false); }}
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
            onActiveChange={(key) => { switchSession(key); setSidebarOpen(false); }}
            menu={convMenu}
          />
        </div>
      </div>

      {/* Main */}
      <div className="dm-chat-main">
        <div className="dm-chat-mobile-toggle">
          <button onClick={() => setSidebarOpen(v => !v)}>
            <MenuOutlined /> 会话列表
          </button>
          <button
            style={{ marginLeft: 'auto', color: '#6366f1', fontWeight: 600 }}
            onClick={() => { handleNewChat(); setSidebarOpen(false); }}
          >
            <PlusOutlined /> 新对话
          </button>
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
          <div className="dm-voice-bar">
            <Select
              size="small"
              value={voiceId}
              onChange={(v) => {
                setVoiceId(v);
                localStorage.setItem('dm_voice', v);
              }}
              options={voiceOptions.map((o) => ({ value: o.id, label: o.label }))}
              style={{ width: 220 }}
              placeholder="播报音色"
            />
            <button
              ref={micRef}
              className={`dm-mic${recording ? ' recording' : ''}`}
              onPointerDown={beginRecord}
              onPointerMove={moveRecord}
              onPointerUp={endRecord}
              onPointerCancel={endRecord}
              onContextMenu={(e) => e.preventDefault()}
              style={{ touchAction: 'none' }}
            >
              <AudioOutlined /> {recording ? (cancelMode ? '松开取消' : '松开发送') : '按住说话'}
            </button>
            <button className="dm-newchat-mobile" onClick={handleNewChat}>
              <PlusOutlined /> 新对话
            </button>
          </div>
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
            placeholder="输入问题，Enter 发送…"
          />
        </div>
      </div>
    </div>
  );
}
