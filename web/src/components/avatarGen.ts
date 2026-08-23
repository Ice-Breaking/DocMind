/**
 * DiceBear 头像生成器 —— 懒加载封装。
 *
 * @dicebear 全风格集约 600KB(压缩前),若被 AppLayout 首屏的 UserAvatar
 * 同步引入会整体进入入口 chunk。此处改为 dynamic import():首次渲染到
 * db: 头像时才拉取独立分包,就绪后生成函数驻内存复用。
 */

type GenFn = (styleId: string, seed: string) => string | null;

let genFn: GenFn | null = null;
let loading: Promise<void> | null = null;

/** 确保 dicebear 分包已加载并完成初始化(幂等,可并发调用) */
export function ensureAvatarGen(): Promise<void> {
  if (genFn) return Promise.resolve();
  loading ??= Promise.all([import('@dicebear/core'), import('@dicebear/collection')])
    .then(([{ createAvatar }, collection]) => {
      // 各风格 Options 枚举互不兼容(如 eyes: 'open'|... vs variant01|...),
      // 运行时按 id 查表取风格,只能以 any 承载异构 Style 集合
      const styles: Record<string, any> = {
        personas: collection.personas,
        adventurer: collection.adventurer,
        'big-ears': collection.bigEars,
        'notionists-neutral': collection.notionistsNeutral,
        croodles: collection.croodles,
        'big-smile': collection.bigSmile,
        bottts: collection.bottts,
      };
      genFn = (styleId, seed) => {
        const style = styles[styleId];
        if (!style) return null;
        try {
          const svg = createAvatar(style, { seed, size: 128 }).toString();
          return `data:image/svg+xml;utf8,${encodeURIComponent(svg)}`;
        } catch {
          return null;
        }
      };
    });
  return loading;
}

/**
 * avatar token 形如 db:{style}:{seed} → SVG data URI。
 * 同步接口:dicebear 分包未就绪时返回 null,由调用方在
 * ensureAvatarGen().then() 后重取。
 */
export function avatarDataUri(token: string): string | null {
  if (!token || !token.startsWith('db:')) return null;
  const parts = token.split(':');
  const seed = parts.slice(2).join(':') || 'docmind';
  return genFn ? genFn(parts[1], seed) : null;
}
