import { useCallback, useEffect, useRef, useState } from 'react';
import type { MessageInstance } from 'antd/es/message/interface';
import { synthesizeSpeech } from '../../api';

export interface SpeechState {
  key: string;
  status: 'loading' | 'playing' | 'paused';
}

/**
 * TTS 播报：点击播放 / 再点暂停 / 再点续播；客户端按 `voiceId|key` 缓存音频，
 * 会话内重播零等待。自 Chat.tsx 原地迁出，状态机与事件绑定保持一致。
 *
 * @param voiceId 当前音色（切换音色后同 key 将重新合成）
 * @param msgApi  antd message 实例
 */
export function useSpeech(voiceId: string, msgApi: MessageInstance) {
  const audioCacheRef = useRef<Map<string, string>>(new Map());
  const audioRef = useRef<HTMLAudioElement | null>(null);
  const [speech, setSpeech] = useState<SpeechState | null>(null);
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

  return { speech, handleSpeak };
}
