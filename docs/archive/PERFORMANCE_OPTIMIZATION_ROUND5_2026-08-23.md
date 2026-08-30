# DocMind 代码质量优化 · 第 5 轮(2026-08-23)

> 前几轮见 `PERFORMANCE_OPTIMIZATION_ROUND*.md`。本轮为**可维护性/工程化**
> 专项:拆分最大单文件、锁定依赖版本、接入静态检查——并顺带修出
> **2 个潜伏真 bug**。全部改动通过 ruff 0 错误 + pytest 96 用例验证。

## 一、拆分 store.py(1334 行 → 9 个域模块)

单文件聚集了聊天/用户/助手/评测/API Key/模型/审计等全部 SQLite 逻辑,
是仓库最大的认知负担。拆分方案(facade + 晚绑定,**零破坏性**):

```
docmind/store/
├── __init__.py    235 行  连接层(DB_PATH/_local/_SCHEMA/_conn)+ 全量再导出
├── chat.py        212 行  会话/消息/反馈/追问建议
├── users.py       327 行  认证与账号管理(pbkdf2/管理员/待审头像)
├── assistants.py  136 行  助手与知识库注册表
├── admin.py       168 行  统计/badcase/审计事件/告警
├── eval.py        117 行  评测数据集与运行记录
├── apikeys.py      80 行  API Key 签发与校验
├── ingest.py       42 行  文档摄取任务队列
└── models.py       82 行  LLM/Embedding/Rerank 模型注册表
```

兼容性关键设计:
- 外部 `from docmind import store; store.xxx(...)` 用法不变(门面再导出);
- 子模块经 `store._conn()` **晚绑定**取连接——测试
  `monkeypatch.setattr(store, "DB_PATH"/"_local")` 的语义与拆分前完全一致;
- 唯一跨模块调用(users→list_assistants)显式改为 `store.` 前缀。

## 二、依赖锁版 + 开发依赖分离

- `requirements.txt` 全部 `>=` → `==`(取自当前通过测试的 venv 实装版本),
  CI/Docker 构建从此可复现;
- 新增 `requirements-dev.txt`(pytest / pytest-timeout / ruff),
  生产镜像只装 requirements.txt——测试与 lint 工具不再进镜像;
- CI `backend-test` 改装 dev 依赖;新增独立 `lint` job。

## 三、接入 ruff 静态检查(pyproject.toml)

保守规则集(`E4/E7/E9/F`:pyflakes 全量 + pycodestyle 缺陷子集),
只抓真实缺陷不做风格强改;app.py 的"先证书/日志后 import"与 store
门面的再导出布局经 per-file-ignores 显式豁免 E402。

首轮扫描 51 项,全部处置:

### 真 bug(2 处,F821 未定义名)
| 位置 | 问题 | 后果(若触发) |
|---|---|---|
| `react_agent._force_web_step` | 使用 `extract_search_query`,但 import 在另一函数作用域 | 时效性联网兜底路径 NameError |
| `app.py /metrics` | 注册了两个同名路由,第二个引用未定义的 `_MetricsResponse` 等 | 死路由掩盖下的坏代码 |

### 死代码清除
- `vector_store.build()` 里每次启动都白跑的全目录哈希 `fingerprint = compute_fingerprint(...)`
  (缓存命中实际走 manifest 对比);
- `doc_freshness.py` 未使用变量 `threshold_days`;
- **旧一代向量缓存机制整体移除**(cache.py 的 `compute_fingerprint`/
  `save_cache`/`load_cache` 三件套,零调用方,早已被 manifest 增量机制取代,
  模块文档同步改写);
- 其余:未用导入 24 处(app.py 顶部残留的 semantic_cache/SYSTEM_PROMPT/embed
  等)、f-string 无占位符、未用异常变量 `as e`、重复 import(F811)、
  测试分号双语句(E702)等。

## 四、其他

- Dockerfile 无需改动:`COPY requirements.txt` 自动获得瘦身收益
  (pytest/ruff 已移出生产依赖);
- `.venv/bin/ruff check docmind/ tests/ scripts/ mcp_servers/` → **0 错误**;
- `pytest tests/ -q --timeout=60` → **96 passed**。

## 未纳入本轮(后续候选)

- `Chat.tsx`(1410 行)组件化拆分:无前端组件测试兜底,建议引入
  Testing Library 后单独一轮处理;
- TanStack Query 引入(请求去重/缓存)、前端 eslint 接入。
