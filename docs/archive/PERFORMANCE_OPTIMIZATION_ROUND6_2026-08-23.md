# DocMind 代码质量优化 · 第 6 轮(2026-08-23)

> 接第 5 轮(后端 store 拆分/依赖锁版/ruff)。本轮为**前端工程化**专项:
> 接入 ESLint 并清零、拆分 1400 行的 `api.ts`。全程 `tsc -b` +
> `eslint` + `vite build` 三重验证通过。

## 一、接入 ESLint(eslint 9 flat config)

新增 `web/eslint.config.js`,规则取向与后端 ruff 一致:**只抓真缺陷,
不做风格强改**:

| 规则 | 级别 | 说明 |
|---|---|---|
| `react-hooks/rules-of-hooks` | error | Hook 调用合法性,违例即 bug |
| `react-hooks/exhaustive-deps` | error | 依赖完整性,防 stale closure |
| `@typescript-eslint/no-unused-vars` | error | 允许 `_` 前缀占位 |
| js.recommended + ts recommended | error | 基础正确性 |
| `no-explicit-any` / 空 catch 等 | off | 项目现状噪音规则,渐进收紧 |

配套:`package.json` 增加 `"lint": "eslint src"`;
CI `frontend-build` job 在 build 前增加 lint 步骤。

**首轮扫描仅 4 处**(全部在 `DocumentPreviewModal.tsx`),已修:
1. `useEffect` 缺 `loadPreview` 依赖(exhaustive-deps)。该函数每次渲染
   重建引用,**直接加依赖会无限循环**——正确修法:`useCallback([kbId,
   filename])` 包裹后再入依赖数组;
2. 三处 react-markdown 组件回调解构丢弃 `node` 改为 `node: _node`
   (惯用写法,防未知 DOM 属性透传告警)。

## 二、拆分 api.ts(1400 行 → 12 个域模块)

单文件聚集了认证/聊天/后台/知识库/评测等全部接口封装。与后端
`store.py` 拆分同模式(**门面再导出**,页面 `from '../api'` 导入路径零变化),
按既有分区注释机械切分(AST 行范围脚本 `/tmp/split_api.py`):

```
web/src/api/
├── index.ts        门面:export * ×12(保持导入路径不变)
├── core.ts         60 行  身份/登录/登出/改密
├── chat.ts        226 行  SSE 流协议 + 会话/消息/反馈/建议(+chatStreamXHR)
├── admin.ts       248 行  后台统计/Badcase/traces/质量/审计/备份
├── assistants.ts   69 行  智能体 CRUD
├── kbs.ts         132 行  知识库/文档/入库任务
├── evals.ts       176 行  评测数据集与运行 + 检索调优实验室
├── users.ts       187 行  用户管理 + 账号头像审核
├── models.ts       77 行  模型注册表          ├── usage.ts    59 行
├── alerts.ts       64 行  告警与 SLA           ├── apikeys.ts  54 行
└── voice.ts        44 行  ASR/TTS
```

拆分中发现并修正一处归位错误:原文件把 `fetchSla`(SLA 查询)误放在
"语音"分区尾部,已归位 `alerts.ts`;唯一跨域类型依赖
(`DashboardStats.recent_sessions: Session[]`)以
`import type { Session } from './chat'` 显式声明。

验证:`tsc -b` 一次通过;`eslint src` 0 错误;`vite build` 成功
(index 648KB / vendor-antd 1291KB,gzip 230KB+405KB)。

## 未纳入本轮(后续候选)

- **Chat.tsx(1410 行)拆分**:单组件 ~25 个 useState、`handleSend`
  单回调约 208 行,hooks 相互纠缠,无前端组件测试兜底时强行提取
  hooks 有闭包时序风险。建议先引入 Vitest + Testing Library 建立冒烟
  测试,再单独一轮做 hooks/子组件提取;
- **路由级懒加载**(React.lazy):当前 vendor-antd 1.29MB 已 manualChunks
  分包,进一步减首屏需按页面 code-split,涉及路由结构改动需回归验证;
- TanStack Query 引入(请求去重/缓存)。
