/**
 * 图片附件预处理：EXIF 剥离 + 等比压缩。
 *
 * - canvas 重绘剥离 EXIF（手机照片含 GPS 定位/设备信息，隐私）；
 * - 等比压缩到最长边 maxEdge（默认 2048，原图 4096 级上传慢且浪费）；
 * - 透明 PNG 垫白底后转 JPEG（JPEG 无透明通道）。
 */

/** 单张图片体积上限（与后端 _CHAT_IMAGE_MAX_BYTES 对齐） */
export const MAX_IMAGE_BYTES = 8 * 1024 * 1024;

/** 单条消息最多携带图片数 */
export const MAX_IMGS = 5;

/** 等比缩放尺寸计算（纯函数，便于单测）：只缩小不放大 */
export function computeScaledSize(
  width: number,
  height: number,
  maxEdge = 2048,
): { width: number; height: number } {
  const scale = Math.min(1, maxEdge / Math.max(width, height));
  return {
    width: Math.max(1, Math.round(width * scale)),
    height: Math.max(1, Math.round(height * scale)),
  };
}

/** 读取文件为 HTMLImageElement（经 ObjectURL，失败 reject） */
function loadImage(file: File): Promise<HTMLImageElement> {
  return new Promise((resolve, reject) => {
    const img = new window.Image();
    const url = URL.createObjectURL(file);
    img.onload = () => {
      URL.revokeObjectURL(url);   // 及时释放，避免大图 ObjectURL 滞留内存
      resolve(img);
    };
    img.onerror = () => {
      URL.revokeObjectURL(url);
      reject(new Error('图片读取失败'));
    };
    img.src = url;
  });
}

/**
 * 压缩图片文件为 JPEG data URL。
 * 环境不支持 canvas 2d 时抛错（调用方提示用户重试）。
 */
export async function compressImageFile(
  file: File,
  maxEdge = 2048,
  quality = 0.85,
): Promise<string> {
  const img = await loadImage(file);
  const { width, height } = computeScaledSize(img.width, img.height, maxEdge);
  const canvas = document.createElement('canvas');
  canvas.width = width;
  canvas.height = height;
  const ctx = canvas.getContext('2d');
  if (!ctx) throw new Error('图片处理失败');
  // 透明 PNG 垫白底（JPEG 无透明通道）
  ctx.fillStyle = '#fff';
  ctx.fillRect(0, 0, canvas.width, canvas.height);
  ctx.drawImage(img, 0, 0, canvas.width, canvas.height);
  return canvas.toDataURL('image/jpeg', quality);
}
