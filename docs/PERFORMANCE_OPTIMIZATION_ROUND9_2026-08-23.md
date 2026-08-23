# DocMind 性能优化 · 第 9 轮(2026-08-23)

> 接第 8 轮(Vitest 基建 + Chat.tsx 叶子域拆分)。本轮为 Chat.tsx
> **第二阶段拆分**:将最大纠缠块——SSE 发送域与气泡渲染配置——
> 以 custom hook 形式提取,目标 Chat.tsx < 900 行,**零行为变化**。
> 全程 `tsc -b` + `eslint src` + `vitest run`(35/35)+ `vite build`
> 四重验证通过。

## 一、改动清单

### `chat/useChatStream.tsx`(新增,363 行)

聊天流的**发送域状态机**整体内聚:

- 状态:`messages` / `streaming` / `thinkingSteps` / `suggestions` /
  `lastFailedQuestion` / `failedMap`(+ref)/ `imageAttaches` /
  `uploadPct`,及配套 refs(`messagesRef` 等);
- 动作:
  - `handleSend`:SSE 消费(cache/thinking/token/step/error/final/done
    协议)、指数退避自动重连 ×2、ghost 消息清理;
  - `handleRetry`(失败原地重发)/ `handleRegenerate`(末轮再生成)/
    `handleCancel` / `abortActive`;
- **卸载时中断流**(原 Chat.tsx unmount effect 的 abort)随状态一并
  移入 hook,宿主少管一条生命周期规则。

**latest-ref bridge 解声明顺序环**:`handleSend` 内需要宿主的
`handleAuthError` / `loadSessions`,而二者又依赖 hook 的
`abortActive`(先有 hook 后有宿主闭包)。解法:hook 只接收稳定的
`streamBridge: React.RefObject<ChatStreamBridge>`,内部永远调用
`bridgeRef.current.*`;宿主每次渲染回填最新闭包。无时序竞态,
亦不引入 context 重渲染。

### `chat/useBubbleRoles.tsx`(新增,242 行)

气泡**渲染配置域**整体迁移:`renderAssistantContent` /
`renderAssistantFooter` 与完整 `bubbleRoles`(user/assistant 头像、
图片缩略图、反馈按钮组)。

### Chat.tsx 宿主侧 rewiring

- imports 精简(MarkdownContent/UserAvatar/ThoughtChain/chatStream/
  fetchSuggestions 及一批 icons 随域迁出);
- `switchSession` / `handleNewChat` 统一经 `handleCancel()` 复位;
- `exhaustive-deps`:hook 返回的 setter/ref 无法被 lint 识别为稳定
  引用,相关 useCallback deps 显式补齐(setter/ref 引用稳定,
  无行为差异)。

## 二、效果

| 指标 | 第 8 轮末 | 本轮末 |
|---|---|---|
| Chat.tsx 行数 | ~1211 | **757**(<900 达标)|
| 测试用例 | 28 | 35(新增 7 例覆盖拆分后路径)|
| 单文件职责 | 流+渲染+会话+UI | 会话编排 + UI 组装 |

产物体积不变(纯代码移动);SSE 协议、重连策略、消息清理等行为
逐项对照未变。

## 三、遗留说明

- Chat 剩余可优化点:`convItems`/`sessionGroups` 派生计算可用
  useMemo 收敛;`loadMessages`/`loadFeedback` 属数据获取域,留待
  **TanStack Query 迁移**(第 10 轮起)统一治理;
- `useBubbleRoles` 内部仍持有较多 UI 分支,后续如需可再按
  user/assistant 两角色细拆。
