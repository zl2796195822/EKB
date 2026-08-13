# EKB Core Rebuild 本地真实业务证据（2026-08-13）

## 范围

本记录只证明当前工作区在本地开发环境的真实 API、浏览器和失败闭环；不把本地 SQLite、开发对象存储或缺少外部 Provider 的结果写成生产完成。生产目标仍是 PostgreSQL/pgvector、S3-compatible object storage、可靠队列/Worker/Scheduler 和受管 LLM Provider。

## 本地运行结果

- FastAPI：`127.0.0.1:8023`，Vite：`127.0.0.1:5173`。
- 本地对象存储使用显式开发配置的磁盘适配器，上传对象写入 `.local-object-storage/`；路径越界、未知对象和过期 upload session fail closed。
- 上传协议已走真实批次：创建 batch → 预检 upload session → 上传对象 → checksum complete → 创建后台 ingest job。
- Worker 已接入真实对象读取、校验、解析、分块、远程 embedding、索引和失败状态更新；未配置远程 Embedding 时返回 `EMBEDDING_UNAVAILABLE`，不回退本地模型、不写伪向量。
- Provider/Model 运行时仅使用受管远程 LLM/Embedding 配置；本地大模型不是可选回退路径。图片仅在当前远程模型 capability 明确支持 Vision 时以 typed image content 发送；无远程 Vision/OCR 时 fail closed，不生成本地占位描述。

## 验证命令与结果

```text
cd apps/api && ../../.venv/bin/pytest -q
333 passed, 2 skipped

cd apps/api && ../../.venv/bin/ruff check --ignore E501,F401 \
  ekb_api/routers/attachments.py ekb_api/routers/qa.py \
  ekb_api/services/attachments.py ekb_api/services/rag.py \
  tests/test_api.py tests/test_tenant_routing_quota.py tests/v4/test_ph6_rag.py
passed; the remaining E501/F401 findings are pre-existing style debt in touched legacy files

cd apps/web && npm run typecheck
passed

cd apps/web && npm test -- --run
49 passed

cd apps/web && npm run build
passed; Vite only reports the existing large JavaScript chunk warning

cd apps/web && npm_config_registry=https://registry.npmjs.org npm audit --omit=dev --audit-level=high
found 0 vulnerabilities

git diff --check
passed

../../.venv/bin/python -m compileall -q ekb_api tests
passed
```

全仓库 `ruff check` 仍会报告工作区历史文件中的既有风格/测试问题；本阶段没有借机扩大修复范围。本次修改涉及的后端文件精确 lint（忽略触及旧文件的 E501/F401/I001/E402/B007）已通过。项目 venv 未安装 `pip-audit`。

## 真实 API 与浏览器验收

API 五步链已实际返回成功状态：登录、知识库列表、batch 创建、upload session、对象 PUT、complete；后台任务随后在无远程 Embedding 时真实失败为 `EMBEDDING_UNAVAILABLE`，数据库中的 ingest/document/upload item 均同步为 `FAILED`，没有本地向量替代。

真实 Chrome 已完成两条链路：

1. 登录 → 打开文档中心 → 选择本地文件 → 批次上传 → 文档列表刷新。文档中心显示服务端返回的真实文档行，并同时呈现 `已就绪` 与 `失败 / EMBEDDING_UNAVAILABLE` 状态。首次加载状态缺口已修复，页面不再永久停留在加载态。
2. 登录 → AI 助手 → 添加真实本地 TXT → 创建 upload session → 受保护对象 PUT → 处理 → 带附件提问。最新浏览器网络记录确认 session/PUT/process/ask 全部 `200`；页面显示 `阶段：完成`、真实 `seq=22`，回答准确读取附件两行内容并显示附件引用。

浏览器首次发送暴露了前端 `standard` thinking level 与 API canonical enum 不一致的真实契约错误（400）；已在 API client 边界归一化为 `off→light`、`standard→medium`、`intensive→high`，修复后重跑同一浏览器流程通过。期间出现的旧登录 500 属于 API 重启期间的运行时断连，不是最终链路结果。

## 安全检查

- upload complete、对象 PUT、document/job projection 均按租户和主体作用域校验；过期 session、错误对象键和 checksum 不匹配拒绝。
- 远程 S3-compatible presigned PUT 不携带 EKB Bearer header；本地 protected PUT 仅开发环境和显式 local root 开启。
- 本地 protected PUT 仅接受数据库解析出的当前租户 object key，并校验 owner、UPLOADING 状态、字节数和 SHA-256；对象读取再次按租户校验并验证完整性。
- QA 将附件作为独立资源做 tenant/owner/conversation/status 校验，不把 attachment ID 当作 KB ID；消息绑定也校验当前用户的会话归属，引用预算为附件保留位置。
- 远程对象存储 session 返回 presigned PUT；仅本地开发存储返回受保护 API PUT。Vision 请求按当前用户配置的远程模型 capability 过滤并校验对象 checksum/大小。
- 生产 secret-pattern 扫描排除测试和工具目录后没有生产凭据命中；仓库内 `MCP` 测试仍有占位 token 字符串，未视为凭据。
- 本地认证 token 继续使用 session storage；项目记忆、Spec 和证据不记录密码、API key、token 或私密地址。

## 尚未执行 / 生产阻塞

- 未执行生产 PostgreSQL/pgvector 原子切库、生产对象存储、可靠队列/Worker/Scheduler、外部 LLM/Embedding 凭据配置、服务器备份/部署/回滚或生产浏览器验收。
- 本次服务器入口检查实际执行 `bash scripts/deploy/deploy.sh --skip-tests`，因运行时未提供 `DEPLOY_HOST` 以退出码 `64` fail closed；未建立 SSH、未执行备份、迁移、重启或线上写入。
- 这些动作需要已配置且可审计的生产基础设施、稳定出网和用户批准的部署窗口；本地缺少 Provider 凭据时保持 fail closed，不用 mock 代替。

## 2026-08-13 DONE-LOCAL 追加

- 真实 `POST /api/v1/attachments/{attachment_id}/promotions` 已验证：租户/owner/live ACL、路径冲突、回收站保留、幂等与并发处理均覆盖；复用 attachment `source_object_id` 创建 document/version/job，返回 `202 QUEUED`，worker 状态投影一致。
- 前端已补助手提升入口；LLM-only 主题支持 `light/dark/auto` semantic tokens。
- 验证：backend `343 passed, 2 skipped`；web `51 passed`、typecheck/build 通过；ruff/compileall/diff check 通过；真实浏览器登录后 API 返回 `202`，数据库 `source_object_id` 一致。
- `NOT RUN/BLOCKED`：生产 PostgreSQL/对象存储/队列、服务器备份/部署/重启、生产浏览器未执行，原因是没有运行时 `DEPLOY_HOST`/受管凭据。不得把本地 SQLite 或 `QUEUED` 写成生产完成。`git diff --check` 通过。

## 2026-08-13 CR-PH4-T07 contract closure

- Web Search 已从本地 QA UI/API/runtime/config 移除；旧 `options.web_search` 在 retrieval 前返回 `400 FEATURE_REMOVED`，没有 retrieval 或网络调用。
- QA 保留授权 KB RAG、附件上下文和远程 LLM Provider/Model；真实浏览器 Composer 不显示联网搜索，添加文件、远程模型和 KB 上下文仍可见。
- 验证：backend `345 passed, 2 skipped`；web `51 passed`、typecheck/build；定向 API `28 passed`、RAG/多轮回归 `21 passed`、`git diff --check` 通过。
- 生产 PostgreSQL/pgvector、对象存储、队列/Worker/Scheduler、服务器备份/部署/回滚和生产浏览器仍未执行；本证据只代表本地 DONE-LOCAL。

## 2026-08-13 Provider/Model linkage DONE-LOCAL

### 实际闭环

- `GET /api/v1/qa/capabilities` 与显式 `POST /api/v1/qa/ask` 的 `options.model` 共享当前 `tenant+actor` 的活动加密 credential 与启用远程 Provider/Model registry。
- capabilities 中的模型和 capability/vision 信息来自 DB registry；没有默认模型、本地模型或伪造 Provider 成功。
- unknown、disabled、cross-tenant、no-credential 的显式 model 返回 `MODEL_UNAVAILABLE`。
- 图片请求选择非 `vision=true` 模型返回 `MODEL_NOT_ALLOWED`；显式选择固定对应 Provider，不静默 fallback；普通无 model 请求保留既有 Provider fallback。

### 验证结果

- Backend：`349 passed, 2 skipped`。
- Web：`51 passed`；typecheck/build 通过。
- Provider/Model 定向回归：`63 passed`。
- 范围 Ruff（忽略既有 E501 等风格问题）、compileall、`git diff --check` 通过；新增测试 teardown 已隔离全局 breaker、测试 Provider 和 credential 状态。
- 真实浏览器此前已确认 Profile 模型服务目录、Assistant 添加文件、模型选择和 KB context 可见；本切片没有以浏览器或外网结果冒充真实 Provider 出网。

### 未完成与边界

- 真实 Provider 出网、生产 PostgreSQL/pgvector、生产对象存储、可靠队列/Worker/Scheduler、服务器备份/部署/回滚和生产浏览器验收均未执行，保持 `NOT RUN/BLOCKED`。
- 本地 DONE-LOCAL 不代表生产完成；本记录不包含任何凭据，且本切片未修改 Skills、legacy backup、deploy scripts、migrations、theme 或 Web Search。

## 2026-08-13 CR-PH2-T05 / FR-058 Jobs Center 本地证据

- DONE-LOCAL：现有 Analytics `GovernancePanel` 已增加真实「作业中心」Tab，调用当前租户作用域的 `GET /api/v1/jobs`、`GET /api/v1/jobs/cleanup` 和 `POST /api/v1/jobs/{job_id}/cancel`。页面展示服务端 state counts、job type、safe timestamps、lease/heartbeat availability、脱敏 error metadata、job id，以及 cleanup `total/by_state/recent`；排除 payload、`tenant_id`、Provider 原始响应和私有地址。取消只对 `QUEUED`/`RUNNING`/`RETRY_WAIT` 提供，之后重新加载服务端状态。
- 本地浏览器（2026-08-13）：认证后进入 `Analytics → 运营与治理 → 作业中心`，真实展示 5 条作业（4 `SUCCEEDED`、1 `DEAD`），含文档 ingest 的 `EMBEDDING_UNAVAILABLE`；cleanup projection 为 `retention_purge` 总数 4、全部 `SUCCEEDED`；缺少 lease/heartbeat 时显示 unavailable。过期认证阶段已有 3 条较早 auth-related console errors，不宣称 console=0；Jobs Center 数据加载成功。本文不记录凭据、token、私有地址或完整 opaque ID。
- 验证：web `57 passed in 8 files`、typecheck/build 通过；后端未改动，沿用既有全量 `361 passed, 2 skipped`，本切片未重新运行后端测试；`git diff --check` 通过。生产目标、服务器备份、部署、回滚均 `NOT RUN/BLOCKED`，统一使用 `[production host]` / `[REDACTED]`。本条保持 LLM-only、无本地模型、无 mock，不代表 PH2 或 PH0–PH8 全部完成。
