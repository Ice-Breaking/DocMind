# DocMind 安全文档

> 适用版本：`8fff250b` 及之后
> 最后更新：2026-08-24
> 审计状态：三轮安全处置完成，全部发现闭环；本文档所有条目均对照代码逐条核实

---

## 1. 安全概述

DocMind 采用 **FastAPI + React SPA + Docker Compose** 架构。核心原则：

- **应用层做对**：SQL 参数化、密码强哈希、XSS 默认转义、服务端独立鉴权
- **缝隙层守住**：async/sync 边界、nginx/FastAPI 双出口限速、XFF 信任链收敛、输入校验与使用的同源（SSRF pin-IP）

**当前状态**：生产就绪（内网部署）。公网部署前必须完成 [第 5 节检查清单](#5-公网部署前置检查清单)。

---

## 2. 已验证的安全措施

以下防御项均经实弹攻击复测验证，非仅代码层面声明。括号内为实现位置。

### 2.1 认证与会话

| 措施 | 实现细节 | 验证方式 |
|:---|:---|:---|
| 密码存储 | PBKDF2-SHA256 × **200,000** 迭代 + 随机 16 字节盐（存 `salt_hex$hash_hex`）+ `compare_digest` 恒时比较（`store/users.py`） | 源码审计 |
| 会话 Cookie | `dm_session`：HttpOnly + SameSite=Lax + Path=/；token 为 `secrets.token_urlsafe(32)`，服务端内存表 + **12h 滑动续期**（`web_auth.py`） | 响应头审计 + 容器重启测试 |
| 强制改密 | 首登 `must_change_pwd=true`，`require_user` 对其一律 403；改密端点自身走 `current_user` 仅验登录态（避免死锁，`da512092` 修复项） | 双守卫分野回归用例 |
| 会话吊销 | 改密成功后吊销同用户其余全部会话、保留当前（`revoke_other_sessions`，`8fff250b`） | tA/tB 双会话实弹：改密后 tB=401 |
| 防暴力破解 | **双维度**：用户名 5 次/15 分钟 + 来源 IP 20 次/15 分钟，锁定 900s；IP 取自 ContextVar（并发请求隔离）（`web_auth.py`） | 用户名 22 连发实弹 + 换源零牵连 |
| 密码强度 | 服务端强制 ≥8 位**且同时含字母和数字**（前端绕过无效） | API 边界测试 |
| 默认凭据 | 删除 admin123 回退：空库且未设 `ADMIN_PASSWORD` 直接 RuntimeError 拒启 | 空库容器启动实测 |

### 2.2 输入与文件安全

| 措施 | 实现细节 | 验证方式 |
|:---|:---|:---|
| SQL 注入防护 | 全量路由 `?` 参数化，零字符串拼接 SQL | 全量 grep + 实弹注入 |
| XSS 防护 | React 默认转义，零 `dangerouslySetInnerHTML`；富文本 react-markdown 无 rehype-raw | 前端源码审计 |
| 文档上传 | 白名单 **pdf/md/txt/docx/csv/json** + 单文件 ≤50MB + 非空校验 + 知识库容量配额（`docs_api.py _ALLOWED_EXT/_MAX_SIZE`） | 多格式上传测试 |
| 内容伪装防护 | magic bytes 签名校验（%PDF-/\x89PNG…）+ docx/xlsx 解压后体积上限（zip 炸弹防护）+ 文本 UTF-8 校验（`_validate_content`） | 单测 test_validate_* 三例 |
| 对话图片（OCR） | 扩展名白名单 png/jpg/jpeg/webp + ≤50MB（`_ocr_image`） | 上传边界测试 |
| 附件读取 | `/files/uploads/{name}` basename 防穿越 + attachments 属主校验（admin 豁免），非属主 404 不泄露存在性 | 跨用户访问测试 |
| MIME 安全 | FileResponse 显式 media_type 映射（png/jpg/jpeg/webp），未知扩展强制 `application/octet-stream` 触发下载而非内联渲染（`8fff250b`） | 探针实测 image/png |
| SSRF 防护 | http pin-IP 直连 / https 双解析一致性 / 禁重定向——校验与连接同源，消除 DNS rebinding TOCTOU（`_fetch_public`） | 云元数据/环回/302 全拦截 |

### 2.3 API 与流量控制

| 措施 | 实现细节 | 验证方式 |
|:---|:---|:---|
| nginx 限速 | zone：`api_limit` 30r/m、`login_limit` 5r/m、`pwd_change_limit` 5r/m；应用：`/api/` 与 `/open/` burst=10、`/files/` burst=20、`/login` burst=3、change-password burst=2（均 nodelay） | 50/60/120 并发实测（burst 精确达标） |
| 后端限速 | `/open/v1/*` per-key 内存滑窗 60/min（key 泄露兜底防刷爆 LLM 账单） | 120 并发直连后端 → 71×429 |
| 事件循环隔离 | 抓取移入 `anyio.to_thread` + **per-user CapacityLimiter(4)** + fail_after(25s) 排队超时→429（防单账号占满全局 40-token 线程池，`8fff250b`） | 3×并发慢抓取期间 /health 最差 5.9ms |
| Metrics 门禁 | `METRICS_TOKEN` 校验，无/错 token → 404（隐蔽拒绝，不暴露端点存在） | 四态实测 |
| 审计日志 | `audit_events(actor, action, target, detail, ip, created_at)`；记录 login / auth.password-change / kb.create·delete / apikey.* / doc.import-url，含来源 IP（`8fff250b`）。**登录失败不入审计表**，仅应用日志（含 IP） | 实弹查库验证 |

### 2.4 部署与容器安全

| 措施 | 实现细节 | 验证方式 |
|:---|:---|:---|
| 非 root 运行 | uid 10001 (`docmind`)，存量数据卷一次性 chown | `docker exec id` |
| 资源限额 | memory=4g / cpus=2.0（compose） | `docker inspect` |
| 端口暴露面 | **仅 nginx 80 对外（0.0.0.0）**；docmind 7860 / searxng 8080 均绑 `127.0.0.1` | `docker ps` 实测 |
| 网络拓扑 | compose 固定子网 `192.168.97.0/24`（底部 networks.default 显式声明，与 XFF 信任列表联动） | 重建验证 |
| 安全响应头 | XFO + nosniff + Referrer-Policy + CSP（server 级；assets location 因 add_header 继承断链规则重复声明三件套，`da512092`） | curl 根/API/assets 三处实测 |
| XFF 信任链 | uvicorn `proxy_headers=True` + `FORWARDED_ALLOW_IPS`（compose env 注入固定子网；代码默认回退 `127.0.0.1`） | 伪造 XFF 记录真实来源；网段外伪造不生效 |
| SearXNG secret | compose entrypoint 按缩进回写 `SEARXNG_SECRET`，缺失拒启；仓库仅存占位符（`da512092` 修复缩进锚定） | 重建后实测 64hex 注入 |

> ⚠️ **运维注意**：`searxng/settings.yml` 是 bind mount 且可写——容器每次启动 entrypoint 都会把真实 secret 写入该文件。提交前务必 `git restore searxng/settings.yml`。

---

## 3. 已知限制与设计选择

以下项非漏洞，是架构层面的权衡，使用前应知悉。

| 项 | 现状 | 影响半径 |
|:---|:---|:---|
| 会话无持久化 | 内存 dict，容器重启全失效（用户重登即可） | 单实例；多实例需切 Redis |
| 单进程部署 | uvicorn 单 worker——内存态会话/限速/防爆破均依赖此前提 | **扩 worker 会破坏会话与防爆破一致性**；横向扩容需先解决粘性会话 |
| SQLite 单文件 | WAL + busy_timeout=5000 + thread-local 连接 | 单机足够；高并发写需迁 PostgreSQL |
| 无 HTTPS | 明文 HTTP，依赖外部负载均衡/CDN 终结 TLS | 内网可接受；公网必须前置 HTTPS |
| SearXNG limiter 关闭 | limiter 依赖 Redis 计数，本部署未引入 Redis | 公网需补 Redis 再开启 |
| ChromaDB 进程内 | PersistentClient，无端口/REST 暴露 | 攻击面最小化，但无法独立扩缩容 |
| 数据形态 | named volume `docmind-data`（仓库内 ./data 为历史副本，非挂载源） | 备份须针对卷操作，勿误备份宿主目录 |

## 4. 漏洞披露政策

如果你发现了 DocMind 的安全问题，请通过以下方式报告：

1. **不要**在公开 Issue 中披露漏洞细节
2. 发送邮件至项目维护者（TODO：填入安全联络邮箱）
3. 提供复现步骤、影响版本、修复建议（如有）
4. 48 小时内确认，7 个工作日内给出修复计划或解释

**已知安全边界（无需重复报告）**：
- 会话 12h 滑动过期、容器重启即失效（设计选择）
- 无 HTTPS（第 3 节，公网前置项）
- 登录失败不入 `audit_events`（仅应用日志，设计选择）

---

## 5. 公网部署前置检查清单

- [ ] **HTTPS 终结**：nginx 配 443 + 证书挂载，80 强制 301
- [ ] **XFF 信任列表核对**：修改位置是 **docker-compose.yml**——`FORWARDED_ALLOW_IPS` 环境变量与底部 `networks.default.subnet` 两处必须同步。代码默认值为安全的 `127.0.0.1` 回退，无需改 app.py。⚠️ 教训：照抄 Docker 默认池 `172.16.0.0/12` 在自定义地址池环境会失配——失配表现为审计 IP 全变网关、IP 防爆破误伤全部来源
- [ ] **SearXNG Redis**：新增 Redis 容器 + `searxng/settings.yml` 开 `limiter: true`
- [ ] **防火墙**：仅放行 80/443（7860/8080 已绑回环，无需额外处理）
- [ ] **备份策略**：针对 named volume `docmind-data` 定期快照或
      `docker run --rm -v chat-1_docmind-data:/data -v $PWD:/bak alpine tar czf /bak/dm-data.tgz /data`
- [ ] **监控告警**：Prometheus 带 `METRICS_TOKEN` 拉取，或外部 APM
- [ ] **凭据轮换**：更换 `ADMIN_PASSWORD` 与 `SEARXNG_SECRET`

---

## 6. 安全审计历史

| 批次 | 日期 | 范围 | 发现与处置 | commit |
|:---|:---|:---|:---|:---|
| 第一轮 | 2026-08 | 部署层、认证链路、API 层、前端构建 | 8 项：事件循环冻结 / 默认凭据 / XFF 信任链 / 安全头 / 限速补全 / metrics 门禁 / SSRF 同源 / 部署加固（secret 注入、非 root、资源限额） | `0289cf16` |
| 第二轮 | 2026-08 | 修复回归 + 修复引入风险 + 盲区深挖 | 3 项修复引入问题：强制改密死锁(P0) / SearXNG secret 注入落空(P1) / assets 安全头丢失(P2)；另确认盲区 G/H/I/J 结论 | `da512092` |
| 第三轮 | 2026-08 | 二轮遗留 P2 清理 | 5 项：改密后会话吊销 / per-user 抓取闸门 / 审计 IP 列 / MIME 白名单 / XFF 收敛（含 1 项收敛失配当场自抓自修） | `8fff250b` |

**累计**：三个批次 16 项安全处置全部闭环；其中 4 项为修复引入问题，均在实弹复测中自抓自修。

---

## 7. 维护者安全操作手册

### 每周例行（5 分钟）

```bash
cd <项目根目录>

# 1. 容器健康（compose 项目前缀会加在容器名上，如 chat-1-docmind-1）
docker compose ps
docker compose logs --tail=50 docmind

# 2. 异常登录检测——登录成功走审计表。列名是 created_at（epoch 秒）和 actor，
#    不是 timestamp/user；镜像内无 sqlite3 CLI，用容器内 python：
docker exec chat-1-docmind-1 python -c "
import sqlite3, time
c = sqlite3.connect('/app/data/chat.db')
week = time.time() - 7*86400
for r in c.execute('SELECT created_at, actor, action, target, ip FROM audit_events'
                   ' WHERE created_at > ? ORDER BY created_at DESC LIMIT 50', (week,)):
    print(time.strftime('%F %T', time.localtime(r[0])), r[1:])"

# 3. 登录失败检测——失败不入审计表，看应用日志（每行含 user 与真实来源 ip）：
docker logs chat-1-docmind-1 --since 168h 2>&1 | grep '登录失败' | tail -20

# 4. 磁盘空间（named volume 所在分区）
df -h /var/lib/docker
```

### 变更安全相关配置时的固定动作

1. 改 compose 后 `docker compose config --quiet` 验语法
2. 涉及 XFF/子网：重建后必测「经 nginx 伪造 XFF → docker logs 里 ip= 应为伪造值」（防信任列表失配静默退化）
3. 涉及上传/MIME：探针复测 content-type
4. 提交前 `git status` 确认 `searxng/settings.yml` 已还原


