# DocMind 性能优化 · 第 4 轮(2026-08-23)

> 前三轮见 `PERFORMANCE_OPTIMIZATION_ROUND*.md`;本轮聚焦**死代码清理、
> 前端加载性能、首响应延迟、BM25 重建 CPU 开销与镜像体积**,全部改动
> 已通过 96 个单测 + 前端生产构建验证。

## 一、删除 mermaid 死代码(-3.2MB)

- **背景**:前端唯一渲染层 `MarkdownContent.tsx` 对 mermaid 代码块返回
  `null`(系统提示词也明确禁止模型输出 mermaid),旧 Gradio UI 的
  `<script src="/mermaid.min.js">` 随去 Gradio 化早已移除——3.2MB 的
  `docmind/mermaid.min.js` 成为纯死代码,却仍被 git 跟踪、打进 Docker
  镜像、并由 `/mermaid.min.js` 端点提供服务。
- **改动**:删除文件本体;移除 `app.py` 服务端点;SPA fallback 排除表
  中保留 `mermaid` 前缀(陈旧 URL 返回 404 而非误回 index.html)。

## 二、前端路由级代码分割(React.lazy)

- **背景**:`App.tsx` 静态 import 全部 21 个页面,首屏 bundle 包含
  Chat(1410 行)/KnowledgeBases/Eval 等重页面代码。
- **改动**:除登录页(首屏直达)外全部页面改为 `React.lazy` 动态导入,
  `<Routes>` 外包一层 `<Suspense>`(复用全局居中 Spin 样式)。
- **效果**:构建产物按路由自动分包——Chat 23KB / KnowledgeBases
  13.7KB / Eval 10.3KB 等,均按需拉取;配合既有 vendor(manualChunks)
  分离,首屏只下载框架 + 登录页所需代码。

## 三、术语解读步并行化(首响应延迟 -300~800ms)

- **背景**:`react_agent.ask()` 内 `_interpret_step`(一次独立 LLM 往返)
  在主链路前**串行阻塞**;而它与调用方的 embedding/缓存查询完全独立。
- **改动**:
  - 新增 `ReActAgent.start_interpret()`:`_INTERPRET_POOL`
    (max_workers=4)后台线程执行解读步;
  - `chat_stream.stream_events()` 在语义缓存查询**之前**预启动 Future,
    embedding(~150-400ms)期间解读并行完成,`ask(interpret_future=…)`
    直接收取结果;
  - 时效性分支(force_web_search)复用同一 Future,避免同问题跑两次解读;
  - 缓存命中提前返回时 `cancel()`,尽量减少命中路径的无谓消耗;
  - 兼容性:CLI/评测等未传 Future 的调用方保持原同步行为;getattr
    探测使最小 agent 替身无需实现新方法。

## 四、BM25 分词切片级缓存(增量重建 CPU 大降)

- **背景**:`HybridRetriever.build()` 每次全量重建都对全部切片跑 jieba,
  而增量重建后文本未变的切片占绝大多数(embedding 已有同思路的
  `embed_cache`,分词没有)。
- **改动**:新增 `docmind/rag/tokenize_cache.py`(text_hash → tokens JSON
  存 SQLite WAL,20 万条 LRU 上限),`build()` 接入;批内相同文本去重;
  缓存层任何故障自动降级为全量直算。测试经 conftest autouse fixture
  统一隔离到临时库。

## 五、其他

| 项 | 改动 | 收益 |
|---|---|---|
| Rerank 连接池 | `requests.post` → 模块级 `requests.Session` | 每次精排省 ~100-300ms TLS 握手 |
| Docker 镜像瘦身 | `COPY docs ./docs` → 只拷 `docs/knowledge` + `glossary.md` | UI 截图等 ~2MB 文档资产不入镜像 |
| llm.py 清理 | 删除重复的模块级 `_client` 声明 | 可维护性 |
| Git 卫生 | `.gitignore` 增补 `.pytest_cache/`、`*.tsbuildinfo`;untrack `web/tsconfig.tsbuildinfo` | 仓库干净 |

## 验证

- 后端:`pytest tests/ -q` → **96 passed**(新增 5 个分词缓存用例);
- 前端:`npm run build` 通过,分包生效(见第二节);
- mermaid:全仓 grep 无引用残留。

## 未纳入本轮(后续候选)

- `store.py`(1334 行)/`Chat.tsx`(1410 行)拆分——纯可维护性重构,风险面大,建议单独一轮;
- TanStack Query 引入(请求去重/缓存)、requirements 版本锁定(`>=`→精确版本)、ruff/eslint 接入。
