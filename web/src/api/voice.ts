/** 语音：ASR 输入 + TTS 播报 */

/* ------------------------------------------------------------------ */
/*  语音：ASR 输入 + TTS 播报                                            */
/* ------------------------------------------------------------------ */

export interface VoiceOption {
  id: string;
  label: string;
}

export async function fetchVoices(): Promise<VoiceOption[]> {
  const r = await fetch('/api/voice/voices');
  if (r.status === 401) throw new Error('UNAUTHORIZED');
  if (!r.ok) throw new Error('HTTP ' + r.status);
  return r.json();
}

/** 语音识别：上传 16k WAV，返回识别文本 */
export async function transcribeAudio(wav: Blob): Promise<string> {
  const fd = new FormData();
  fd.append('file', wav, 'rec.wav');
  const r = await fetch('/api/voice/asr', { method: 'POST', body: fd });
  if (r.status === 401) throw new Error('UNAUTHORIZED');
  if (!r.ok) {
    const d = await r.json().catch(() => ({}));
    throw new Error(typeof d.detail === 'string' ? d.detail : 'HTTP ' + r.status);
  }
  return (await r.json()).text || '';
}

/** 语音合成：返回 MP3 Blob */
export async function synthesizeSpeech(text: string, voice: string): Promise<Blob> {
  const r = await fetch('/api/voice/tts', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ text, voice }),
  });
  if (r.status === 401) throw new Error('UNAUTHORIZED');
  if (!r.ok) {
    const d = await r.json().catch(() => ({}));
    throw new Error(typeof d.detail === 'string' ? d.detail : 'HTTP ' + r.status);
  }
  return r.blob();
}
