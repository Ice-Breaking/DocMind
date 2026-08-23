# DocMind 性能优化 · 第 10 轮(2026-08-23)

> 接第 9 轮(Chat.tsx SSE 域提取)。本轮开启 **TanStack Query 数据层
> 迁移**:第一期落地基建(QueryClientProvider + 全局默认值)并以
> Dashboard 页试点,替换手写 fetch 三件套(useEffect + cancelled 标记 +
> loading/error state)。全程 `tsc -b` + `eslint src` + `vitest run`
> (35/35)+ `vite build` 四重验证通过。

## 一、背景

全站 20+ 页面各自手写同构的数据获取模式:

```ts
const [data, setData] = useState(null);
const [loading, setLoading] = useState(true);
const [error, setError] = useState(null);
useEffect(() => {
  let cancelled = false;
  (async () => { try { ... } catch { ... } finally { ... } })();
  return () => { cancelled = true; };
}, []);
```

问题:样板代码重复、StrictMode 双挂载双请求、无缓存去重、
失效/刷新逻辑各页自洽难维护。引入 @tanstack/react-query(v5)
统一治理。

## 二、本期改动

### 基建(`web/src/main.tsx`)

- 新增依赖 `@tanstack/react-query`;`QueryClientProvider` 挂载于
  ConfigProvider 外层(仅数据层,不影响 antd 主题上下文);
- **全局默认值刻意保守,与既有手写行为逐项对齐(零行为变化)**:

| 默认值 | 取值 | 对齐旧行为 |
|---|---|---|
| staleTime | 0(默认) | 每次进入页面重新请求 |
| refetchOnWindowFocus | false | 旧实现无窗口聚焦重拉 |
| retry | false | 旧实现失败即展示错误态 |

### 试点:`Dashboard.tsx`

- 手写 effect 三件套(约 30 行)→ `useQuery({ queryKey: ['dashboard'],
  queryFn: fetchDashboard })`,加载/错误态由 `isPending`/`error` 托管;
- 附带收益:StrictMode 双挂载不再双请求(query 去重);错误信息
  直接复用 Error 实例消息,文案不变。

## 三、效果

| 指标 | 第 9 轮末 | 本轮末 |
|---|---|---|
| Dashboard 数据获取代码 | ~30 行手写 effect | 4 行 useQuery |
| StrictMode 挂载请求数 | 2(双发) | 1(去重) |
| 首屏 entry chunk(min) | 27 KB | 56 KB(+TanStack Query 运行时,gzip +~11 KB)|

行为验证:进入页面即拉取、失败展示原错误文案与重试按钮,
均与改造前一致;35 个既有测试全绿。

## 四、遗留说明(迁移路线)

- 第二期起按域逐页迁移(SessionHistory/Usage 等列表页优先),
  mutation 类操作(增删改)用 `useMutation` + `invalidateQueries`
  替换手写 reload;
- 测试基建补 `createTestQueryClient` 辅助,供包裹被测页面
  (当前试点页无直接渲染测试,暂不需要);
- Chat 页的 loadSessions/loadMessages 属会话编排(与 SSE 流耦合),
  放在迁移末期单独处理。
