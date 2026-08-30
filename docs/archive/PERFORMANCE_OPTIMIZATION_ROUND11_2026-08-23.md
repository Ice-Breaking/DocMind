# 性能优化第十一期(2026-08-23):首屏减负 + DiceBear 按风格分包

## 一、背景与定位

第十期收官后复查构建产物,发现两处「隐性浪费」(以 `vite build --sourcemap`
聚合 sourcesContent 定位构成,方法同第七期):

| 发现 | 体积 | 问题 |
|---|---|---|
| `@ant-design/x` 被 manualChunks 强制并入 vendor-antd | ~60 KB(min) | 它仅被 Chat / SessionHistory 两个**懒加载页**使用,却被塞进首屏 HTML 必引的 vendor 块——登录页白下载 |
| `import('@dicebear/collection')` barrel 打包 | **2033 KB**(min) | collection 是全量聚合出口(~30 种风格),tree-shaking 对动态导入 barrel 无效;实际只用 7 种风格 |

说明:dicebear 大块本身是 async chunk,**不阻塞首屏**;但任何用到它的页面
都要整块拉取 2MB,且与「每风格数十 KB」的真实需求严重不匹配。

## 二、改动

### 1. vite.config.ts:`@ant-design/x` 移出 vendor-antd

```diff
- 'vendor-antd': ['antd', '@ant-design/icons', '@ant-design/x'],
+ 'vendor-antd': ['antd', '@ant-design/icons'],
```

移除后 rollup 自动把它归入 Chat / SessionHistory 的共享异步分包,
进入相关页面才拉取;antd + icons 保持首屏 vendor 不变。

### 2. avatarGen.ts:collection barrel → 按风格动态导入

`collection.personas` 的本质是 `import * as personas from '@dicebear/personas'`
的命名空间对象(dicebear 官方即以命名空间直接喂 `createAvatar`)。
据此绕开 barrel,改为对 7 个具体风格包各自 `import()`:

```ts
const STYLE_LOADERS: Record<string, () => Promise<StyleModule>> = {
  personas:            () => import('@dicebear/personas'),
  adventurer:          () => import('@dicebear/adventurer'),
  'big-ears':          () => import('@dicebear/big-ears'),
  'notionists-neutral': () => import('@dicebear/notionists-neutral'),
  croodles:            () => import('@dicebear/croodles'),
  'big-smile':         () => import('@dicebear/big-smile'),
  bottts:              () => import('@dicebear/bottts'),
};
```

API 变化(调用方仅 UserAvatar 与测试):
- `ensureAvatarGen()` → `ensureStyle(styleId)`(只加载所需风格;幂等可并发,
  失败不缓存可重试,未知风格 resolve(false) 不发请求)
- 新增 `ensureAllStyles()`(AvatarPicker 全量预热 / 测试用)
- `avatarDataUri(token)` 同步签名与兜底语义完全不变(未就绪返回 null)

UserAvatar 渲染 db: 头像时只拉该头像所属风格的分包;就绪前仍以
首字母色块过渡。

## 三、效果(vite build 实测)

| 指标 | 前 | 后 | 变化 |
|---|---|---|---|
| 首屏 HTML 引用 JS(min) | entry 56 + react 160 + antd 1291 ≈ **1508 KB** | entry 57 + react 157 + antd **1231** ≈ **1445 KB** | **-63 KB(-4%)** |
| dicebear 分包 | 单块 **2033 KB** | 7 个风格块合计 **≈446 KB**(29~115 KB/块) | **-78%**,且按需加载:渲染一个头像只拉其风格一块 |
| 头像首显网络成本 | 2 MB | 30~120 KB(单风格) | **≈-94%** |
| 全站 JS 总量(dist/assets) | ~3840 KB | **2496 KB** | **-35%** |

行为不变性:tsc ✅ / eslint ✅ / vitest **36/36** ✅(avatarGen 用例重写为
ensureStyle/ensureAllStyles 契约,新增幂等缓存用例)/ build ✅。

## 四、遗留说明

- vendor-antd 仍 1231 KB(gzip ~385):antd 组件本体为首屏 UI 必需,
  进一步缩减需换轻量组件库或真 SSR,维持第七期评估结论;
- entry 中 @tanstack/query-core 约 54 KB(min):Provider 位于 App 根、
  Login 页亦受益于缓存层,收益/风险比低,暂不动;
- 若未来新增 dicebear 风格,需同步 UserAvatar.DB_STYLES 与
  avatarGen.STYLE_LOADERS 两处(测试「全部 7 种风格」会兜底提醒)。
