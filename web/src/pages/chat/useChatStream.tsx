import { useCallback, useEffect, useRef, useState } from 'react';
import type { MessageInstance } from 'antd/es/message/interface';
import type { BubbleDataType } from '@ant-design/x/es/bubble/BubbleList';
import type { ThoughtChainItem } from '@ant-design/x/es/thought-chain/Item';
import { ApiOutlined, RobotOutlined } from '@ant-design/icons';
import { chatStream, fetchSuggestions } from '../../api';

/**
 * 流式发送域桥：useChatStream 需要「auth 处理 / 会话列表刷新」这两个由宿主
 * 定义的回调，而宿主的 handleAuthError 又需要 hook 的中断能力，存在声明
 * 顺序环。以 latest-ref 单向桥打破：hook 内始终读 `bridgeRef.current`，
 * 宿主每渲染回填最新闭包，行为与原先直连一致。
 */
export interface ChatStreamBridge {
  /** UNAUTHORIZED 时登出并中断当前流；返回 true 表示错误已被处理 */
  onAuthError: (e: any) => Promise<boolean>;
  /** 流正常结束后刷新侧栏会话列表（标题/时间/last_msg） */
  reloadSessions: () => unknown;
}

export interface UseChatStreamOpts {
  msgApi: MessageInstance;
  activeSidRef: { readonly current: string };
  assistantIdRef: { readonly current: string };
  bridgeRef: { readonly current: ChatStreamBridge };
}

/**
 * Chat 发送链路域：乐观插入用户/助手气泡 → SSE 消费（cache/thinking/token/
 * step/error/final/done）→ 断线指数退避自动重连（最多 2 次）→ 失败兜底
 * （幽灵消息清理、内联重试、整条重发）。自 Chat.tsx 原地迁出，逻辑保持
 * 一致；相关状态（messages/streaming/thinking/suggestions/failedMap/
 * imageAttaches/uploadPct）随域整体内聚于此。
 *
 * @param opts.msgApi        antd message 实例
 * @param opts.activeSidRef  当前会话 id 镜像 ref（发送时取实时值）
 * @param opts.assistantIdRef 当前助手 id 镜像 ref
 * @param opts.bridgeRef     宿主回调桥（见 ChatStreamBridge）
 */
export function useChatStream(opts: UseChatStreamOpts) {
  const { msgApi, activeSidRef, assistantIdRef, bridgeRef } = opts;

  /* ---- state ---- */
  const [messages, setMessages] = useState<BubbleDataType[]>([]);
  const [streaming, setStreaming] = useState(false);
  const [thinkingSteps, setThinkingSteps] = useState<ThoughtChainItem[]>([]);
  const [suggestions, setSuggestions] = useState<string[]>([]);
  const [lastFailedQuestion, setLastFailedQuestion] = useState<string | null>(null);
  // 失败气泡映射：assistantKey → 原问题（供内联重试）
  const [failedMap, setFailedMap] = useState<Record<string, string>>({});
  const [imageAttaches, setImageAttaches] = useState<{ dataUrl: string; base64: string }[]>([]);
  const [uploadPct, setUploadPct] = useState<number | null>(null);

  /* ---- refs ---- */
  const abortRef = useRef<AbortController | null>(null);
  const streamTokenRef = useRef('');
  const streamThinkingRef = useRef('');
  const messagesRef = useRef(messages);
  const failedMapRef = useRef(failedMap);

  // keep refs in sync
  useEffect(() => { messagesRef.current = messages; }, [messages]);
  useEffect(() => { failedMapRef.current = failedMap; }, [failedMap]);

  // cleanup on unmount
  useEffect(() => {
    return () => {
      abortRef.current?.abort();
    };
  }, []);

  /** 中断当前流并复位 controller（供宿主：登出失效/切会话/开新会话） */
  const abortActive = useCallback(() => {
    abortRef.current?.abort();
    abortRef.current = null;
  }, []);

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
                  bridgeRef.current.reloadSessions();
                  break;
                }
              }
            }
            break; // 流正常结束，退出重试循环
          } catch (e: any) {
            if (await bridgeRef.current.onAuthError(e)) return;
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
    [streaming, imageAttaches, msgApi, bridgeRef, activeSidRef, assistantIdRef],
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

  return {
    messages, setMessages, messagesRef,
    streaming,
    thinkingSteps, setThinkingSteps,
    suggestions, setSuggestions,
    lastFailedQuestion, setLastFailedQuestion,
    failedMap, setFailedMap, failedMapRef,
    imageAttaches, setImageAttaches,
    uploadPct,
    handleSend, handleRetry, handleRegenerate, handleCancel, abortActive,
  };
}

export type ChatStream = ReturnType<typeof useChatStream>;
