# DocMind 性能优化 · 第 7 轮(2026-08-23)

> 接第 6 轮(前端 ESLint/api.ts 拆分)。本轮为**首屏加载体积**专项,
> 通过 sourcemap 构成分析定位入口 chunk 膨胀根因并修复。
> 全程 `tsc -b` + `eslint` + `vite build` 三重验证通过。

## 一、问题定位:sourcemap 构成分析

懒加载路由早已就位(App.tsx React.lazy),但入口 `index` chunk 仍达
648KB(min)/ 230KB(gzip)。以 `vite build --sourcemap` 聚合
sourcesContent 定位构成,发现 **~600KB 是 @dicebear 头像库**:

| 来源 | 体积(源码) |
|---|---|
| @dicebear/adventurer 等 7 种风格 + core | ~700 KB |

根因:`UserAvatar.tsx`(被首屏 AppLayout 引用)静态
`import { adventurer, ... } from '@dicebear/collection'`,且 7 种风格
全部使用(用户可选),tree-shaking 无法摇掉 → 整库进入入口 chunk。

## 二、修复:dicebear 动态加载(dynamic import)

- 新增 `web/src/components/avatarGen.ts`:
  - `ensureAvatarGen()`:首次渲染 db: 头像时才 `import('@dicebear/core')`
    + `import('@dicebear/collection')`,生成函数驻内存复用(幂等可并发);
  - `avatarDataUri()` 保持同步签名,分包未就绪时返回 null;
- `UserAvatar.tsx`:移除 dicebear 静态导入;`DB_STYLES` 收窄为风格 id
  集合(`Record<string, true>`,AvatarPicker 仅做 `in` 归属判断,API 兼容);
  组件内 effect 等待生成就绪,**就绪前以首字母色块过渡**(避免原始 token 文本闪现);
- `vite.config.ts`:移除 `manualChunks.markdown`——react-markdown 仅被
  懒加载页面使用,强制手动分块反而使其被提升进首屏 HTML。

类型说明:各风格 Options 枚举互不兼容(adventurer 的 `eyes` 与 bottts 的
`mouth` 等),运行时按 id 查表只能以 `Record<string, any>` 承载异构
Style 集合(沿用原实现,已注释原因)。

## 三、效果

| 指标 | 优化前 | 优化后 |
|---|---|---|
| 首屏 HTML 引用的 JS | index 648 + react 157 + **markdown 154** + antd 1261 = **2220 KB** | index 27 + react 160 + antd 1291 = **1478 KB** |
| 首屏 gzip | ~737 KB | **~467 KB(-37%)** |
| dicebear(~2 MB / gzip 673) | 阻塞首屏 | 渲染到 db: 头像才拉取 |
| react-markdown(118 KB / gzip 36) | 首屏强制加载 | 访问含预览/渲染的页面时按需 |

页面级分包保持细粒度(19 个路由 chunk 各 1-24 KB),总产物体积不变
(3736 KB,只是重新分布为按需)。

## 四、遗留说明

- vendor-antd(1261 KB / gzip 405)为首屏 UI 必需(antd+icons+
  @ant-design/x),进一步缩减需换轻量组件或真 SSR 方案,不在短期计划;
- Chat.tsx(1410 行)拆分仍建议先引入 Vitest + Testing Library 建立冒烟
  测试后单独一轮处理(hooks 纠缠深,无测试兜底风险高)。
