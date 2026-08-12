---
title: EKB 核心重构现状基线
status: Ready for Plan
tier: 3
type: feature
authoritative: true
scope: ekb-core-rebuild
version: 0.1
date: 2026-08-12
owner: Sol
---

# EKB 核心重构现状基线

## 1. 文档目的与权威边界

本文记录开始实施前可以由源码、数据库、部署状态或既有证据确认的事实。它是本轮 EKB 核心重构的现状真值，不代表目标功能已经完成。

## PH0 状态（2026-08-12）

PH0 可审查写入阶段已在分支 `codex/ekb-core-baseline-20260812` 形成工作树与静态证据。部署脚本已移除仓库内固定生产目标和默认管理员凭据，改由运行时明确提供 `DEPLOY_HOST`、`DEPLOY_USER` 及受管凭据；缺少或占位目标、缺少管理员运行时设置、默认管理员身份或不安全密码回退都会 fail closed。部署日志不输出凭据、provider 端点或原始响应。`.gitignore` 仅补充 PH0 已确认的 transient 路径。证据索引见 [`docs/evidence/ekb-core-rebuild/ph0/2026-08-12-baseline/manifest.md`](../../evidence/ekb-core-rebuild/ph0/2026-08-12-baseline/manifest.md)，验证记录见 [`verification.md`](../../evidence/ekb-core-rebuild/ph0/2026-08-12-baseline/verification.md)。

本阶段不提交、不推送、不部署、不重启，也未声称 PH1–PH8 或生产验收完成。生产 health、迁移 ledger 与备份状态的当前只读复核因缺少安全、可审计的受管连接而记录为 `BLOCKED/NOT RUN`；不得据此推断现网状态。工作树原有用户改动保持原样，等待 Sol 审查后决定是否提交/推送。

本目录仅在“知识库、AI 助手、模型服务、附件、主题、回收站及其运行基础设施”的 core-rebuild 冲突范围内作为旧设计的技术 successor；v3 的租户、审计、其他页面和未冲突兼容合同继续有效。历史文档保留作为背景和兼容来源。产品目标见 [01-requirements.md](./01-requirements.md)，差距与优先级见 [02-gap-analysis.md](./02-gap-analysis.md)，目标架构见 [03-architecture.md](./03-architecture.md)。

## 2. 调查范围与证据口径

调查覆盖：

- 前端：`apps/web/src/app-v2`、adapter、主题样式和构建入口。
- 后端：`apps/api/ekb_api` 的 routers、services、models、store、auth、ingestion、retrieval 和 migrations。
- 数据：SQLite 现网库、PostgreSQL 容器、迁移 ledger 和 pgvector 目标。
- 运行：Docker Compose、Nginx、EKB API、对象/文件存储现状、后台任务和生产主机。
- 参考：`/Users/alin/Openclaw项目/grok-build` 的聊天状态、上下文压缩、流式事件和多模态请求设计；以及现有开源对标文档。

事实分级：

| 级别 | 含义 | 可用于验收 |
|---|---|---|
| 已验证 | 由源码、数据库查询或真实运行证据确认 | 是 |
| 部分实现 | 存在真实链路，但缺少关键状态、授权或持久化 | 否 |
| 设计存在 | 文档或 UI 存在，运行链路不完整 | 否 |
| 缺失 | 无可用实现 | 否 |

## 3. 当前技术栈

| 层 | 当前状态 | 结论 |
|---|---|---|
| Web | React 19、TypeScript、Vite、app-v2 hash routes | 保留并增量重构 |
| API | FastAPI、SQLAlchemy、模块化单体 | 保留控制面和服务边界 |
| 主数据 | 本地/现网仍存在 SQLite 路径；生产容器已具 PostgreSQL | 需要受控迁移到 PostgreSQL |
| 向量检索 | 目标为 pgvector；当前链路与生产主库未闭环 | P0 |
| 文件存储 | 上传内容未形成可靠对象存储真值 | P0 |
| 后台任务 | FastAPI `BackgroundTasks` 和进程内状态为主 | 不满足可靠摄取/清理 |
| 缓存/队列 | 生产 Compose 未包含 Redis/可靠队列 | P0 基础设施缺口 |
| Worker/Scheduler | 未形成独立可观测 worker/scheduler | P0 |
| 网关 | Nginx + Cloudflare named tunnel 配置 | 生产主机出网故障导致 tunnel 不可用 |

## 4. 知识库与上传现状

### 4.1 已有能力

- 已有知识库、文档、摄取任务、解析、Chunk、Embedding/检索相关模型或服务路径。
- 支持 TXT、Markdown、PDF、DOCX、XLSX、PPTX 的部分解析。
- UI 有单文件和批量上传入口，上传后能看到有限状态。
- 文档和知识库已经进入租户/ACL 体系的一部分。

### 4.2 真实缺口

- Upload API 以单文件 multipart 为主，读取完整文件到内存；大文件会放大 API 进程内存风险。
- 批量/目录上传由浏览器并发调用单文件接口模拟，`relative_path` 没有成为服务端身份和元数据。
- 同名文件、同路径新版本、同内容幂等没有统一约束。
- 文件原始字节未可靠持久化到对象存储；重试无法保证重新解析同一原件。
- 任务依赖进程内 BackgroundTasks，进程退出后可能留下陈旧 `RUNNING` 状态。
- UI 只有 queued/uploading/success/failed 等粗状态，没有字节进度、Parsing、Indexing、失败阶段和可操作错误。
- 前端接受旧 `.doc/.xls/.ppt`，后端解析器并不原生支持，形成虚假能力。
- 图片、扫描 PDF、OCR 和 Vision 入库链路缺失。
- “API success”尚不能证明对象、文档版本、Chunk、Embedding 和向量索引全部成功。

## 5. AI 助手现状

### 5.1 已有真实基础

- 已有 conversation/message 持久化、SSE v2、`turn_id`、递增 `seq`、heartbeat、cancel 状态和部分上下文压缩。
- app-v2 已有助手页面、模型选择、知识库选择和 Composer 入口。
- 服务端已有检索、引用事件和反馈的部分路径。

### 5.2 核心缺口

- `attachment_doc_ids` 被混入 `kb_ids`，没有独立附件资源、版本、处理状态、ACL 和生命周期。
- Turn 查询/取消路径没有完整的 `tenant_id + actor_id` 所有权条件，同租户跨主体存在风险。
- UI 未形成可靠 Markdown/代码块渲染、消息级 Retry、Regenerate 分支、活动分支和停止后部分消息语义。
- 引用未形成稳定的消息持久化记录，刷新后难以证明回答证据。
- 无文件附件实体、临时聊天索引、Vision 请求、OCR fallback 或附件清理机制。
- 长对话预算主要按纯文本估算，未统一计入附件摘要、检索证据、系统提示和图片成本。
- 代码中存在联网搜索/Tavily 方向；本轮必须从助手运行时、API 和 UI 中移除。
- grok-build 是 Rust 终端 Agent，不是可直接迁移的 Web Chat；只借鉴状态完整性、事件和压缩思想。

## 6. 模型服务现状

- 已有 `llm_providers`、`llm_models`、个人中心模型服务 UI 和部分 DB 驱动列表。
- Model sync 依赖 mock catalog，不能证明供应商真实返回或自定义模型可调用。
- OpenAI-compatible 路由可能把已选 `provider/model` 发送到错误 provider；硬编码模型名与配置可漂移。
- Provider key 的持久化、加密、轮换、脱敏和审计合同不完整。
- AI 助手与模型服务没有形成一个可信的唯一数据源；删除/禁用和当前选择 fallback 不完整。
- Provider Logo、能力标签、Vision/Embedding/Streaming 能力发现和主题适配缺失。

## 7. 主题系统现状

- app-v2 有基础 CSS 变量，但语义 token 不完整。
- dark override 分散在页面和 `profile.css` 等文件中，仍有大量原始十六进制/RGB 颜色。
- Card、Modal、Drawer、Tooltip、表格、输入、边框、hover/selected 和第三方组件没有统一状态合同。
- 当前无法通过切换 class 保证所有主要页面同时满足 Light/Dark。

## 8. 回收站与后台清理现状

- 已有 soft delete、`deleted_at`、`expires_at`、恢复和人工永久删除的部分能力。
- 前端能投影剩余保留时间，但没有可靠 scheduler 自动清理到期记录。
- 清理任务没有 lease、幂等、失败重试、死任务、对象存储清理和审计结果闭环。
- Provider/Model 不属于内容回收站；应使用 disable + audit。

## 9. 数据库与迁移现状

实际 ledger 与旧 v3 文档不一致。当前代码 manifest 的观测 append 顺序为：

`v3_001_identity → v3_002_content → v3_003_analytics → v3_004_apps → v3_005_llm → v3_005_analytics_compat → v3_006_apps_compat → v3_007_content_governance_compat`

PH0 必须从生产 `schema_migrations` 导出 version、checksum、owner、applied_at 和 deployed status，形成不可变 provenance manifest；若生产 applied_at 与代码 manifest 不同，以生产 ledger 事实为输入并阻止自动猜序。

关键约束：

- 已落库 migration 名称、SQL 和 checksum 不得改写或重排。
- 两个 `v3_005_*` 是已存在的历史事实，必须通过新的 canonical migration/ADR 补偿。
- ORM `create_all` 与 migration ownership 的边界不够严格，必须在目标架构中消除。
- API entrypoint 的迁移调用参数和异常处理存在失败后继续启动风险；上线前必须 fail closed。
- SQLite → PostgreSQL 迁移必须保留租户、用户、知识库、文档、会话、消息、模型配置和审计数据，并有回滚证据。

## 10. 生产环境现状

- 服务器已经只保留 EKB 相关项目；非 EKB 服务和数据已按用户明确授权永久删除。
- EKB API、PostgreSQL 容器和 Nginx 本机健康检查曾通过；现网业务数据仍由 SQLite 提供。
- 已建立 EKB 数据和网关配置备份，清理前后核心业务表计数一致。
- 生产主机当前无可用外网出口，DNS/HTTPS/Cloudflare tunnel 均不能建立连接；本机还存在指向无监听服务的代理环境变量。
- 在出网恢复前，不能完成真实外部 LLM、Embedding、Provider model sync 或公共域名验收。
- 主机资源约为 2 核、3.6GB 内存、30GB 磁盘，不适合在单机同时长期承载高内存解析、全部模型相关任务和不可控并发。

## 11. 兼容性与不可触碰边界

1. 保持 `/api/v1`、现有必要响应字段、SSE v2 主事件和 app-v2 route/adapter 兼容。
2. 新列表可以采用 cursor envelope；不能无迁移地改变旧 array 响应。
3. 现有脏工作树属于用户，禁止 reset、checkout 或清理。
4. 不修改 `Skills/` 参考材料。
5. 不复制 grok-build 或其他仓库的 Agent runtime、联网搜索、工具执行和专有请求字段。
6. 不把设计、API 返回 200 或 UI 状态当作实现证据。

## 12. 当前结论

EKB 具备可增量演进的控制面、UI shell、部分消息/SSE 和基础数据模型，但知识库摄取、附件、Provider 路由、RAG 证据、后台任务、主题和定时清理均未达到真实产品闭环。本轮必须先修复迁移与运行基础，再在同一控制面内补齐可靠数据流，而不是推倒重写或把第三方 RAG/Chat 项目整体嵌入。
