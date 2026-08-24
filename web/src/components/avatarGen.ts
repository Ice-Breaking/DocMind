/**
 * DiceBear 头像生成器 —— 按风格懒加载封装。
 *
 * 不经 '@dicebear/collection' barrel(barrel 会把全部 ~30 种风格打进同一
 * chunk,约 2MB);改为动态导入实际使用的 7 个具体风格包,每个风格独立
 * 分包(数十 KB 级):首次渲染某个 db: 头像只需拉取其所属风格的小分包,
 * 选择器打开时才全量预热。风格包的命名空间导出即 collection.* 的元素,
 * 可直接传给 createAvatar。同步接口在分包未就绪时返回 null,由调用方在
 * promise then 后重取(UserAvatar 以首字母色块过渡)。
 */

type StyleModule = Record<string, any>;

// 风格 id → 具体风格包动态加载器(与 UserAvatar.DB_STYLES 保持一致)
const STYLE_LOADERS: Record<string, () => Promise<StyleModule>> = {
  personas: () => import('@dicebear/personas'),
  adventurer: () => import('@dicebear/adventurer'),
  'big-ears': () => import('@dicebear/big-ears'),
  'notionists-neutral': () => import('@dicebear/notionists-neutral'),
  croodles: () => import('@dicebear/croodles'),
  'big-smile': () => import('@dicebear/big-smile'),
  bottts: () => import('@dicebear/bottts'),
};

let corePromise: Promise<StyleModule> | null = null;
let coreMod: StyleModule | null = null;
const styleCache = new Map<string, StyleModule>();
const styleLoading = new Map<string, Promise<void>>();

function loadCore(): Promise<StyleModule> {
  corePromise ??= import('@dicebear/core').then(m => {
    coreMod = m;
    return m;
  });
  return corePromise;
}

/**
 * 加载单个风格分包(幂等、可并发、失败可重试)。
 * 返回该风格最终是否可用;未知风格直接 resolve(false) 不发请求。
 */
export function ensureStyle(styleId: string): Promise<boolean> {
  if (!STYLE_LOADERS[styleId]) return Promise.resolve(false);
  if (styleCache.has(styleId)) return Promise.resolve(true);
  let p = styleLoading.get(styleId);
  if (!p) {
    p = Promise.all([loadCore(), STYLE_LOADERS[styleId]()])
      .then(([, mod]) => {
        // 各风格 Options 枚举互不兼容(eyes: 'open'|… vs variant01|…),
        // 只能以 Record<string, any> 承载异构风格命名空间
        styleCache.set(styleId, mod);
      })
      .catch(e => {
        console.warn('[avatarGen] 风格分包加载失败:', styleId, e);
        // 失败不缓存,下次访问可重试
      })
      .finally(() => {
        styleLoading.delete(styleId);
      });
    styleLoading.set(styleId, p);
  }
  return p.then(() => styleCache.has(styleId));
}

/** 全量预热所有支持的风格(AvatarPicker 打开时/测试用) */
export function ensureAllStyles(): Promise<boolean[]> {
  return Promise.all(Object.keys(STYLE_LOADERS).map(ensureStyle));
}

/**
 * avatar token 形如 db:{style}:{seed} → SVG data URI。
 * 同步接口:对应风格分包未就绪时返回 null,由调用方在
 * ensureStyle().then() 后重取。
 */
export function avatarDataUri(token: string): string | null {
  if (!token || !token.startsWith('db:') || !coreMod) return null;
  const parts = token.split(':');
  const style = styleCache.get(parts[1]);
  if (!style) return null;
  const seed = parts.slice(2).join(':') || 'docmind';
  try {
    const svg = coreMod.createAvatar(style, { seed, size: 128 }).toString();
    return `data:image/svg+xml;utf8,${encodeURIComponent(svg)}`;
  } catch {
    return null;
  }
}
