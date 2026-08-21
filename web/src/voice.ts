/**
 * 语音能力前端工具：
 * - blobToWav16k：浏览器录音（webm/mp4）→ Web Audio 解码 → 16k 单声道 WAV
 *   （Paraformer 要求 wav/16k，客户端转码避免服务端依赖 ffmpeg）
 */

export async function blobToWav16k(blob: Blob): Promise<Blob> {
  const ab = await blob.arrayBuffer();
  const AC: typeof AudioContext =
    window.AudioContext || (window as any).webkitAudioContext;
  const ctx = new AC();
  try {
    const decoded = await ctx.decodeAudioData(ab);
    const ch = decoded.getChannelData(0);
    const sr = 16000;
    const ratio = decoded.sampleRate / sr;
    const len = Math.floor(ch.length / ratio);
    const pcm = new Int16Array(len);
    for (let i = 0; i < len; i++) {
      const s = ch[Math.floor(i * ratio)];
      pcm[i] = Math.round(Math.max(-1, Math.min(1, s)) * 32767);
    }
    const buf = new ArrayBuffer(44 + pcm.length * 2);
    const v = new DataView(buf);
    const wstr = (off: number, s: string) => {
      for (let i = 0; i < s.length; i++) v.setUint8(off + i, s.charCodeAt(i));
    };
    wstr(0, 'RIFF');
    v.setUint32(4, 36 + pcm.length * 2, true);
    wstr(8, 'WAVE');
    wstr(12, 'fmt ');
    v.setUint32(16, 16, true);
    v.setUint16(20, 1, true);
    v.setUint16(22, 1, true);
    v.setUint32(24, sr, true);
    v.setUint32(28, sr * 2, true);
    v.setUint16(32, 2, true);
    v.setUint16(34, 16, true);
    wstr(36, 'data');
    v.setUint32(40, pcm.length * 2, true);
    new Int16Array(buf, 44).set(pcm);
    return new Blob([buf], { type: 'audio/wav' });
  } finally {
    ctx.close().catch(() => undefined);
  }
}
