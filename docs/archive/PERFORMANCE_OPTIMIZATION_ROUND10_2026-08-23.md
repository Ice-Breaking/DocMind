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

## 七、第五期:管理端 CRUD 页迁移(Admin / Audit / Models / Assistants / ApiKeys / Users)

### `Admin.tsx`(176 → 173 行)

- 会话列表 → `['adminSessions']` query;401(`UNAUTHORIZED`)经
  error effect 走统一登出流程,其余错误页面内显示 + `refetch()`
  重试(替代旧 `window.location.reload()` 整页刷新);
- Drawer 消息回看 → `['adminMessages', sid]` query,
  `enabled: drawerOpen && !!activeSession`,失败静默(对齐旧行为);
  `activeTitle` state 由选中会话对象派生,少两个 state。

### `Audit.tsx`(185 → 181 行)

- 事件流水 → `['audit', { actor, action, days }]` query(过滤参数
  进 key,变化自动重拉);失败保留旧数据仅弹 toast;
- CSV 导出 → `exportMut`(一次性动作,不失效缓存),按钮 loading 接
  `isPending`;刷新 → `refetch()`。

### `Models.tsx`(303 → 302 行)

- 模型清单 → `['models']` query,失败 toast 保留旧数据;
- 五个动作全部 mutation 化:`saveMut`(create/update 同 Modal,按
  editing 分支文案)、`testMut`、`activateMut`、`deleteMut`;
  行级测试 loading 用 `testMut.variables?.id` 驱动,替掉手写
  `testing` state;
- 生效切换 / 删除 / 保存成功后统一 `invalidateQueries(['models'])`。

### `Assistants.tsx`(345 → 353 行)

- `Promise.all([fetchAssistants(), fetchKbs()])` → `['assistants']`
  + `['kbs']` 双 query,聚合 loading/error 保持「任一失败整页报错 +
  重试」语义;`['kbs']` 与 Traces / ApiKeys 共享缓存(retry off);
- 新建/编辑 → `saveMut`,删除 → `deleteMut`;成功后失效
  `['assistants']`;
- 注意:`kbs = kbsQ.data ?? []` 需包 `useMemo` 稳定引用,否则下游
  `kbNameMap` 的 useMemo 依赖每帧变化(lint 报
  exhaustive-deps)。

### `ApiKeys.tsx`(336 → 342 行)

- 密钥清单 → `['apiKeys']` query(kb 选项共享 `['kbs']`);
- 创建/吊销/轮换 → 三个 mutation;创建与轮换成功都会弹明文一次性
  展示 Modal 并重置连通性测试状态,随后失效 `['apiKeys']`;
- 明文展示 Modal 内的开放检索试调用保持原生 fetch(一次性动作,
  不进缓存层)。

### `Users.tsx`(415 → 416 行)

- 用户清单 → `['users']`,头像审核队列 → `['avatarReviews']`,
  聚合 loading;users 失败弹 toast;
- 审核通过/驳回 → `reviewMut`(同时失效两缓存,pending_avatar
  标记随列表更新);新建 → `createMut`(成功弹初始密码一次性
  Modal);重置密码 → `resetPwdMut`;管理员开关 →
  `toggleAdminMut`;删除 → `deleteMut`(modalApi 级联清理提示逐字保留)。

### 效果

| 指标 | 迁移前 | 迁移后 |
|---|---|---|
| 六页手写数据获取代码 | ~150 行 | ~60 行 useQuery/useMutation 声明 |
| 已迁移页面 | 8 页 | 14 页(+Admin/Audit/Models/Assistants/ApiKeys/Users) |

## 八、第六期:特殊交互页迁移(Eval / KnowledgeBases / Settings / RetrievalLab)

### `Settings.tsx`(261 → 247 行)

- 纯 mutation 页(无 query):上传自定义头像 → `uploadMut`
  (compressImageToAvatar 前置压缩并入 mutationFn)、保存预设头像 →
  `saveAvatarMut`,两者成功后均 `await onRefreshMe?.()` 再提示;
- 注销账号 → `deleteAccMut`,成功后关 Modal → 登出 → 跳登录页;
- 三个手写 loading state(`uploading`/`avatarSaving`/`deleting`)全部由
  `isPending` 派生,净减 14 行。

### `RetrievalLab.tsx`(208 → 214 行)

- KB 选项复用共享 `['kbs']`,链路阶段耗时 → `['stageStats']`;
  两处均为辅助数据,`retry: false` 失败静默(对齐旧
  `.catch(() => undefined)`);
- 调试检索 → `debugMut`:点击触发的一次性动作,`result`/`loading`
  两个 state 由 `debugMut.data` / `isPending` 派生,空问题校验留在
  mutate 前;行数略增因补了注释。

### `Eval.tsx`(623 → 624 行)

- 三 Tab 独立迁移:评测集 → `['evalDatasets']`(失败 error effect 弹
  toast);保存 → `saveMut`(JSON 校验在 mutationFn 内抛出,走
  `onError` 展示「样本 JSON 非法」,Modal 保持打开)、删除 →
  `deleteMut`、启动评测 → `runMut`;成功统一失效 `['evalDatasets']`;
- 运行记录 → `['evalRuns']` + 共享 `['evalDatasets']`(仅作 id→名称
  映射),聚合 loading;**旧 setInterval 手动轮询 → refetchInterval
  函数式**:存在 pending/running 任务时每 4 秒刷新,否则自动停止;
  首轮失败弹 toast、轮询期间失败静默(对齐旧 `load(silent)` 语义);
- 未命中明细展开行 → `['evalRun', runId]`,`enabled` 由 status 驱动,
  失败静默;质量监控 → `['quality', 30]`(失败红字提示不变);
- 根组件 KB 选项接入共享 `['kbs']`。

### `KnowledgeBases.tsx`(659 → 640 行,本期最复杂)

- KB 清单为本页主数据:沿用共享 key `['kbs']`,但保留「错误卡片 +
  重试按钮」UI(retry off 全局默认下首载失败即展示);
- Drawer 文档列表 → `['kbDocs', kbId]`、入库任务 →
  `['ingestTasks', kbId]`,`enabled: !!activeKb`;docs 失败 toast、
  tasks 失败静默(对齐旧行为);
- **入库任务 3s 轮询 → refetchInterval 函数式**(有 running/pending
  时轮询);「全部结束后刷新文档统计」用 `hadRunningRef` effect 在
  running→idle 翻转时失效 `['kbs']` 与 `['kbDocs']`,替掉整个
  `pollRef`/`startPolling`/卸载清理三段手写逻辑;
- 五个动作 mutation 化:`uploadMut`(Upload customRequest 经
  mutate 的 per-call 回调通知 antd onSuccess/onError)、
  `deleteDocMut`、`reindexMut`(成功后切到任务 Tab 并失效任务缓存,
  行级 loading 用 `variables?.id` 驱动)、`createMut`、`deleteKbMut`;
  上传/删除文档成功后同时失效 kbs/kbDocs/ingestTasks 三缓存;
- 文档内容搜索保持原生 fetch + 本地 loading state(一次性动作不进
  缓存层,与 ApiKeys 明文探针同模式)。

### 效果

| 指标 | 迁移前 | 迁移后 |
|---|---|---|
| 四页手写数据获取代码 | ~130 行 | ~50 行 useQuery/useMutation 声明 |
| 手写轮询(setInterval) | 2 处(RunsTab / 入库任务) | 0 处(refetchInterval 函数式) |
| 已迁移页面 | 14 页 | 18 页(+Eval/KnowledgeBases/Settings/RetrievalLab) |

## 九、第七期(收官):Chat 页迁移

SSE 流式链路保持不动(`useChatStream` 内的 messages/thinking/suggestions
等仍是本地 state——流式高频写不进查询缓存),只把**会话周边的只读数据**
迁到 react-query:

- `['sessions']`:侧栏会话列表。流结束后的 `bridge.reloadSessions`
  → `sessionsQ.refetch()`;删除会话走 `refetchSessions()` 取回新列表
  再决定「切首个/进新对话」,与旧 `loadSessions` 返回列表的语义一致;
- 首个会话自动选中改为「查询就绪后执行一次」的 effect(`didPickRef`
  防重入),空列表进入本地新对话,行为对齐旧 init;
- `['assistants']` / `['voices']`:助手选项与音色列表,静默失败;
- `['feedback', activeSid]`:反馈映射按会话键缓存,
  `enabled: sessions.some(s => s.id === activeSid)` 保证新建本地 sid 不发
  请求(对齐旧 loadFeedback 仅选中既有会话时调用);提交改 mutation,
  onSuccess 用 `setQueryData` 即时写缓存——**切回旧会话点赞状态仍在**,
  这是相对旧版(仅内存 state)的行为增强;
- 查询首轮 401 统一经 `handleAuthError` 登出;其余错误静默(对齐旧
  catch 分支);`feedbackMapRef` 同步 effect 保留(useBubbleRoles 经 ref 读);
- Chat.test.tsx 补 QueryClientProvider 包裹;「已有会话」断言改 waitFor:
  react-query 下侧栏与选中态分两次提交渲染,findAllByText 会在第一处
  出现时提前返回(与旧代码单批更新不同,属测试时机修正而非行为回归)。

### 效果(Round10 全程收官)

| 指标 | Round10 前 | 收官后 |
|---|---|---|
| 手写数据获取页面 | 19 页 | 0 页(Chat 只留 SSE 流域) |
| 已迁移页面 | 14 页 | 19 页(+Chat) |
| Chat.tsx 手写加载函数 | 4 个(loadSessions/Messages/Feedback + assistants effect) | 1 个(loadMessages,SSE 域保留) |

## 十、遗留说明

- Chat 的 `loadMessages` 与 SSE 流式域耦合,刻意保留本地 state:消息
  气泡被流式高频原地更新,进查询缓存需「缓存↔流」双向同步,收益低
  风险高;后续若做离线会话回放可再评估;
- Home/Login 无数据层需求,不需迁移;
- 各页共享 key(`['kbs']` 等)的 `staleTime` 策略可按业务节奏统一微调。

