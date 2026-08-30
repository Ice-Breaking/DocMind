# DocMind 性能优化 · 第 8 轮(2026-08-23)

> 接第 7 轮(首屏加载体积专项)。本轮为 **Chat.tsx 可测试性基建 +
> 第一阶段拆分**:引入 Vitest + Testing Library 建立冒烟测试兜底,
> 随后把纯工具函数、语音输入、TTS 播报三个低纠缠域从 Chat.tsx 抽出。
> 全程 `tsc -b` + `eslint src` + `vitest run` 三重验证通过。

## 一、背景与风险控制

Chat.tsx 此前已膨胀至 ~1400 行,SSE 流处理、消息渲染、语音交互、
会话管理等全部纠缠在单文件内。直接拆分无测试兜底风险高(第 7 轮
遗留说明已明确),故本轮分两步:

1. **测试基建先行**:`vitest` + `@testing-library/react` +
   `@testing-library/user-event` + `jsdom`,配 `MemoryRouter` 的
   页面冒烟测试(登录页 + Chat 页渲染路径);
2. **只拆"叶子域"**:先抽离与其他状态耦合最小的三块,为后续
   SSE 域拆分铺路。

## 二、改动清单

### 测试基建(commit abf0d15e)

- devDependencies 新增 vitest/jsdom/testing-library 系列;
- `package.json` scripts:`test`(run 模式)/ `test:watch`;
- 冒烟用例 16 个:Login 渲染、Chat 渲染(mock 掉 api 层与
  matchMedia/scrollTo 等浏览器 API)。

### Chat.tsx 叶子域拆分(commit bc47ef71)

| 抽出模块 | 内容 | 行数 |
|---|---|---|
| `chat/utils.ts` | 时间格式化、思考链步骤归并等纯函数 | 118 |
| `chat/useVoiceInput.ts` | 豆包式按住说话/松开转写/上滑取消的录音状态机(MediaRecorder 封装) | 123 |
| `chat/useSpeech.ts` | TTS 播报状态机与音频缓存 | 68 |

- Chat.tsx 同步瘦身,并保留原有全部行为(SSE 协议、自动重连、
  ghost 消息清理等均未触碰);
- `chat/utils.test.ts`:纯函数单测 12 例(边界:空串/超长/
  多步交错)。

## 三、效果

| 指标 | 第 7 轮末 | 本轮末 |
|---|---|---|
| Chat.tsx 行数 | ~1410 | ~1211 |
| 测试用例 | 0 | 16 + 12 = 28 |
| 回归兜底 | 无 | tsc + eslint + vitest 三门禁 |

## 四、遗留说明

- Chat.tsx 剩余最大纠缠块为 **SSE 发送域**(handleSend 及其
  重试/再生成/取消)与 **气泡渲染配置**,留待第 9 轮以
  custom hook 形式提取;
- 后端 `store.py` 已同轮完成包结构拆分 + ruff 清零
  (commit c2f89442),与本轮前端拆分互不影响。
