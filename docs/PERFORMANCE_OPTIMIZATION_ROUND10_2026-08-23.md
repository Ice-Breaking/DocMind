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

## 四、第二期:列表页批量迁移(SessionHistory / Queries)

同轮完成两个典型列表页的等价迁移,并补测试辅助:

### `SessionHistory.tsx`(294 → 269 行)

- 初始 `Promise.all` 手写 effect → `['sessions']` 与
  `['assistants']` 两条独立 query(并行度不变,且各自缓存、
  跨页复用——Chat 页后续接入时直接命中);
- Drawer 消息加载:`openSession` 内手写 fetch/清空/loading 态
  (~15 行)→ `useQuery(['messages', sid], { enabled })`,开关与
  换会话自动启停;**有意的行为改进**:再次打开同一会话先显示
  缓存再静默刷新(staleTime 0),替代原先"清空 + Spin",数据
  终值一致;
- `messages` 以 useMemo 收敛稳定引用(handleLocate/bubbleItems
  的 deps 不再每渲染变化)。

### `Queries.tsx`(141 行)

- 过滤参数(user/q/days)进 queryKey,参数变化自动重拉,
  对齐旧 `load()` 语义;失败保留上次数据仅弹 toast(effect 监听
  error,文案不变);刷新按钮 → `refetch()`。

### 测试辅助(`src/test/queryTestUtils.tsx`,新增)

- `createTestQueryClient()`:关 retry 避免用例等待退避;
- `withQueryClient(ui, client?)`:包裹被测 UI,支持预置缓存。

### 效果

| 指标 | 迁移前 | 迁移后 |
|---|---|---|
| 两页手写数据获取代码 | ~75 行 | ~25 行 useQuery 声明 |
| SessionHistory 打开 Drawer | 清空+Spin 重拉 | 缓存直显+静默刷新 |
| 请求去重 | 无 | StrictMode/重复 key 自动去重 |

## 五、第三期:mutation 页迁移(Backups / Alerts)

引入 `useMutation` + `invalidateQueries` 范式:

### `Backups.tsx`(145 → 140 行)

- 列表 → `['backups']` query;「立即备份」→ `createMut`
  (onSuccess toast + `invalidateQueries(['backups'])` 自动重拉,
  等价旧「toast + await load()」);按钮 loading 态接
  `createMut.isPending`,手写 creating 布尔删除。

### `Alerts.tsx`(280 → 275 行)

- `load()` 的 Promise.all → `['alerts']` + `['sla', 7]` 两条独立
  query(并行不变、各自缓存);三个操作 handler(evaluate/ack/
  resolve)收敛为三个 useMutation:成功 toast 文案逐字保留,
  成功后失效对应 query(evaluate/resolve 连带 SLA 统计);
- 注意点:`fetchAlerts(status?, limit?)` 这类带可选参的 API
  不能直接当 queryFn(context 会占位首参),需箭头函数包裹。

### 测试辅助(`src/test/queryTestUtils.tsx`,第二期已建)

- `createTestQueryClient()` / `withQueryClient()` 备用。

### 效果

| 指标 | 迁移前 | 迁移后 |
|---|---|---|
| 两页数据/操作代码 | ~120 行 | ~55 行声明 |
| 操作后刷新 | 手写 await load()(全量 setState) | invalidateQueries 定向失效 |
| 已迁移页面 | Dashboard/SessionHistory/Queries | +Backups/Alerts(共 5 页) |

## 六、第四期:只读/轻 mutation 页迁移(Usage / Badcases / Traces)

### `Usage.tsx`(345 → 342 行)

- `Promise.all` 三连发 → `['usage', days]` / `['topQueries', days]` /
  `['adminOverview']` 三条独立 query(days 进 key,切换自动重拉);
  聚合 loading/error 保持旧「任一失败整页报错」语义;
- `fetchTopQueries` 返回 `{items,total}` 包装对象,data 取 `.items`。

### `Badcases.tsx`(245 → 241 行)

- 列表 → `['badcases']` query(失败页面内报错 + 重试按钮 →
  `refetch()`,不走 toast,对齐旧 UI);状态流转 Modal →
  `updateMut`,成功 toast + 关弹窗 + 失效列表;确认按钮接
  `isPending` 防重复提交(新增的小改进)。

### `Traces.tsx`(231 → 228 行)

- 分页 + 多过滤参数进 queryKey(dayjs 对象先格式化为字符串再入
  key,保证引用稳定);KB 过滤选项 → `['kbs']` query(retry off,
  失败静默降级空选项),后续 KnowledgeBases 页可共享该缓存。

### 效果

| 指标 | 迁移前 | 迁移后 |
|---|---|---|
| 三页手写数据获取代码 | ~110 行 | ~45 行 useQuery 声明 |
| 已迁移页面 | 5 页 | 8 页(+Usage/Badcases/Traces) |

## 七、遗留说明(迁移路线)

- 第五期候选:Models / Audit / Admin / Assistants / ApiKeys /
  Users 等管理端 CRUD 页(useMutation + invalidateQueries);
- Eval / KnowledgeBases / Settings / RetrievalLab 体量大或交互特殊,
  单独评估;Home/Login 无数据层需求;
- Chat 页的 loadSessions/loadMessages 属会话编排(与 SSE 流耦合),
  放在迁移末期单独处理。

