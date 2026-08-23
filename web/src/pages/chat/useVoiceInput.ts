import { useEffect, useRef, useState } from 'react';
import type { PointerEvent as ReactPointerEvent } from 'react';
import type { MessageInstance } from 'antd/es/message/interface';
import { blobToWav16k } from '../../voice';
import { transcribeAudio } from '../../api';

interface RecState {
  rec: MediaRecorder | null;
  chunks: Blob[];
  startY: number;
  cancel: boolean;
  stopped: boolean;
}

/**
 * 语音输入：豆包式按住说话，松开转写，上滑取消。
 *
 * 自 Chat.tsx 原地迁出，行为保持一致：
 * - 安卓长按手势劫持阻断（原生非被动 touch 监听）
 * - 指针捕获防止移出按钮后丢事件
 * - 授权期间已松手 → 静默丢弃；上滑 >48px → 取消
 *
 * @param onText  转写成功回调（追加到输入框，调用方决定落点）
 * @param msgApi  antd message 实例（App.useApp()）
 */
export function useVoiceInput(onText: (text: string) => void, msgApi: MessageInstance) {
  const [recording, setRecording] = useState(false);
  const [cancelMode, setCancelMode] = useState(false);
  const micRef = useRef<HTMLButtonElement | null>(null);
  const recRef = useRef<RecState | null>(null);
  // 回调经 ref 中转，避免转写完成时闭包捕获过期函数
  const onTextRef = useRef(onText);
  onTextRef.current = onText;

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

  const beginRecord = async (e: ReactPointerEvent) => {
    if (recRef.current) return;
    e.preventDefault();
    // 指针捕获：触摸期间即使手指移出按钮，move/up 仍派发到本元素，
    // 避免移动端 pointerleave 误触发"提前松手"导致录不到音
    try {
      (e.currentTarget as Element).setPointerCapture(e.pointerId);
    } catch {
      /* 不支持捕获的环境降级原行为 */
    }
    const st: RecState = { rec: null, chunks: [], startY: e.clientY, cancel: false, stopped: false };
    recRef.current = st;
    setRecording(true);
    setCancelMode(false);
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      if (st.stopped) {
        // 授权期间已松手：直接清理，不识别
        stream.getTracks().forEach((t) => t.stop());
        recRef.current = null;
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
        recRef.current = null;
        setRecording(false);
        setCancelMode(false);
        if (wasCancel) {
          msgApi.info('已取消语音输入');
          return;
        }
        try {
          const wav = await blobToWav16k(new Blob(chunks, { type: rec.mimeType }));
          const text = await transcribeAudio(wav);
          if (text) onTextRef.current(text);
          else msgApi.warning('没有听清，请再试一次');
        } catch (err: any) {
          msgApi.error(err?.message || '语音识别失败');
        }
      };
      rec.start();
    } catch {
      recRef.current = null;
      setRecording(false);
      msgApi.error('无法访问麦克风，请检查浏览器权限');
    }
  };

  const moveRecord = (e: ReactPointerEvent) => {
    const st = recRef.current;
    if (!st) return;
    const cancel = st.startY - e.clientY > 48;
    if (cancel !== st.cancel) {
      st.cancel = cancel;
      setCancelMode(cancel);
    }
  };

  const endRecord = () => {
    const st = recRef.current;
    if (!st) return;
    st.stopped = true;
    if (st.rec && st.rec.state !== 'inactive') st.rec.stop();
  };

  return { recording, cancelMode, micRef, beginRecord, moveRecord, endRecord };
}
