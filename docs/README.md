# EKB 文档 authority index

本索引是 `docs/` 的 authority 入口。EKB Core Rebuild Tier 3 Spec 已于 2026-08-12 获用户最终确认并通过 Sol 最终审查，状态为 Ready for Plan；它不会把尚未执行的实现、迁移、浏览器或生产部署写成完成。历史基线正文保持不变，冲突范围通过本索引的 supersession 关系管理。

| Document | Authority scope | Version | Status | Owner | Review stage | Supersedes / superseded-by | Downstream links |
|---|---|---|---|---|---|---|---|
| [`specs/ekb-core-rebuild/00-current-state.md`](./specs/ekb-core-rebuild/00-current-state.md) | EKB Core Rebuild 00–14 文档链入口：知识库、AI Chat、Provider、附件、主题、回收站、数据库/API/测试/实施 | 0.1 | Ready for Plan | Sol | User confirmed; Sol final review passed | authoritative successor for conflicting core-rebuild scope; v3 remains historical/compatible authority outside that scope | `01-requirements` → `02-gap-analysis` → `03-architecture` → `04`–`14` |
| [`EKB后续全栈开发路线图_v4.0_2026-08-11.md`](./EKB后续全栈开发路线图_v4.0_2026-08-11.md) | v4 后续全栈阶段、门禁、复用流程和规模情景提案 | 4.0 | Sol审核通过 | Sol | Sol审核通过 | proposal; does not replace approved v3 contract or imply user approval | 开源对标评估、v3 plan/matrix |
| [`EKB开源项目对标与复用移植评估_v1.0_2026-08-11.md`](./EKB开源项目对标与复用移植评估_v1.0_2026-08-11.md) | GitHub 候选快照、许可证/白标门禁、差距和复用结论 | 1.0 | Sol审核通过 | Sol | Sol审核通过 | research proposal; no third-party code copied or user approval | v4 roadmap、v3 spec/plan |
| [`pmos/features/2026-08-09_ekb-fullstack-v3/README.md`](./pmos/features/2026-08-09_ekb-fullstack-v3/README.md) | v3 文档链入口、执行顺序和状态 | 3.0 | 已批准 | Sol | Sol final gate passed | supersedes v2 frontend-only boundary; v2 is superseded | requirements, spec, plan, matrix, review |
| [`pmos/features/2026-08-09_ekb-fullstack-v3/01_requirements.md`](./pmos/features/2026-08-09_ekb-fullstack-v3/01_requirements.md) | 52 FR、18 NFR、十页旅程和产品边界 | 3.0 | 已批准 | Sol | Sol final gate passed | supersedes conflicting v2 requirements boundary | spec, plan, matrix |
| [`pmos/features/2026-08-09_ekb-fullstack-v3/02_spec.md`](./pmos/features/2026-08-09_ekb-fullstack-v3/02_spec.md) | API、SQL、FK/CHECK、migration chain、security、adapter and rollout contract | 3.0 | 已批准 | Sol | Sol final gate passed | authoritative v3 technical successor to conflicting v2 limits | plan, matrix, baseline API/data/architecture |
| [`pmos/features/2026-08-09_ekb-fullstack-v3/03_plan.md`](./pmos/features/2026-08-09_ekb-fullstack-v3/03_plan.md) | V3-T01–V3-T38 vertical execution plan and TN | 3.0 | 已批准 | Sol | Sol final gate passed | supersedes horizontal/frontend-only execution boundary | Backlog, spec, matrix |
| [`pmos/features/2026-08-09_ekb-fullstack-v3/04_verification-matrix.md`](./pmos/features/2026-08-09_ekb-fullstack-v3/04_verification-matrix.md) | FR/NFR → AC → task → test/browser/security/evidence gates | 3.0 | 已批准 | Sol | Sol final gate passed | authoritative v3 verification successor | plan, test strategy |
| [`pmos/features/2026-08-09_ekb-fullstack-v3/03_plan_review.md`](./pmos/features/2026-08-09_ekb-fullstack-v3/03_plan_review.md) | two-round self-review and ten-route interaction appendix | 3.0 | 已批准 | Sol | Sol final gate passed | companion review for v3 chain | all feature docs |
| [`EKB全前端重建实施文档_v2.0_2026-08-09.md`](./EKB全前端重建实施文档_v2.0_2026-08-09.md) | v2 historical execution record only | 2.0 | 已替代 | Sol | Sol final gate passed | superseded-by v3; historical body preserved | v3 README |
| [`项目任务分解与Backlog_v1.0_2026-08-07.md`](./项目任务分解与Backlog_v1.0_2026-08-07.md) | historical backlog plus appended v3 task/AC governance index | 1.0 + v3 companion | 待评审 | Sol | Sol final gate passed (v3 companion) | v3 task namespace is authoritative for v3 scope | v3 plan/matrix |
| [`API接口契约_v1.0_2026-08-07.md`](./API接口契约_v1.0_2026-08-07.md) | historical API baseline plus v3 pointer/compatibility scope | 1.0 + v3 companion | 待评审 | Sol | Sol final gate passed (v3 companion) | v3 spec supersedes only conflicting v2/frontend-only API boundary | v3 spec |
| [`数据模型与数据库设计_v1.0_2026-08-07.md`](./数据模型与数据库设计_v1.0_2026-08-07.md) | historical schema baseline plus v3 schema pointer/compatibility scope | 1.0 + v3 companion | 待评审 | Sol | Sol final gate passed (v3 companion) | v3 spec supersedes only conflicting schema boundary | v3 spec |
| [`测试策略与质量门禁_v1.0_2026-08-07.md`](./测试策略与质量门禁_v1.0_2026-08-07.md) | historical test baseline plus v3 matrix/hard gates | 1.0 + v3 companion | 待评审 | Sol | Sol final gate passed (v3 companion) | v3 matrix supersedes only conflicting frontend-only gate | v3 matrix/plan |
| [`技术架构与详细设计_v1.0_2026-08-07.md`](./技术架构与详细设计_v1.0_2026-08-07.md) | historical architecture baseline plus v3 fullstack extension | 1.0 + v3 companion | 待评审 | Sol | Sol final gate passed (v3 companion) | v3 spec supersedes only conflicting frontend-only architecture boundary | v3 spec/plan |
| [`文档治理与研发交付规范_v1.0_2026-08-07.md`](./文档治理与研发交付规范_v1.0_2026-08-07.md) | historical governance plus appended v3 namespace/authority rules | 1.0 + v3 companion | 待评审 | Sol | Sol final gate passed (v3 companion) | v3 namespace rules are additive | all v3 docs and Backlog |

## Authority rules

1. 对本轮核心重构，按 `specs/ekb-core-rebuild/00-current-state.md` → `01-requirements.md` → `02-gap-analysis.md` → `03-architecture.md` → `04`–`11` 模块合同 → `12-testing-plan.md` → `13-acceptance-criteria.md` → `14-implementation-plan.md` 阅读；该文档链已获用户最终确认并进入 Ready for Plan，业务代码实施仍必须按 `14-implementation-plan.md` 的 Phase/门禁执行。
2. `EKB-CR-FR-001`–`060`、`EKB-CR-NFR-001`–`014`、`AC-CR-001`–`060`、`AC-CR-NFR-001`–`014` 和 `CR-PH0`–`PH8` 是 core-rebuild 唯一命名空间；不得与 v3 任务/验收 ID 混用。
3. Core Rebuild 仅在知识库、AI Chat、Provider、附件、Theme、Trash 和支撑基础设施的冲突范围内成为技术 successor；租户、审计、十页其他业务能力及未冲突兼容合同继续参考 v3。
4. 已应用 migration/version/checksum 是不可变事实；Core Rebuild 只允许新的 additive migration、provenance manifest 和 expand/backfill/switch/contract。
5. v3 文档仍按 `01_requirements.md` → `02_spec.md` → `03_plan.md` → `04_verification-matrix.md` 阅读；`V3-T*` 与 `AC-V3-*` 只描述 v3 历史范围，不能证明 Core Rebuild 已实现。
6. 2026-08-11 开源对标和路线图是研究背景；第三方复用必须经过固定 commit、license/NOTICE/SBOM、安全和回归门禁。

## Team directory actions local closure (2026-08-14)

`TeamPage` 的本地成员邀请与当前真实目录导出已标记 `DONE-LOCAL`：邀请使用既有 admin adapter，后端 `/admin/users` 收紧到 `team:user:manage`（兼容旧 OWNER/ADMIN，MEMBER 拒绝）；无 v3 表或租户角色缺失时保留 legacy user 创建成功，v3 表和角色完整时才在同一事务同步 `tenant_memberships` / `user_profiles` 并进入 v3 目录；导出仅使用当前服务端已加载成员，包含 RFC4180 quoting 与公式注入防护，不写密码、租户或 token。详细证据见 [`evidence/ekb-core-rebuild/local/2026-08-14-team-directory-actions.md`](./evidence/ekb-core-rebuild/local/2026-08-14-team-directory-actions.md)。

本条仅代表本地成员账户创建与 CSV 下载；不代表邮件投递、生产身份平台、角色/权限 CRUD、SCIM 或 PH0–PH8 完成。生产目标、备份、部署和回滚仍按 `[production host]` / `[REDACTED]` 记录。

## PH0 evidence status (2026-08-12)

Core Rebuild PH0 的可审查写入证据位于 [`evidence/ekb-core-rebuild/ph0/2026-08-12-baseline/`](./evidence/ekb-core-rebuild/ph0/2026-08-12-baseline/)，入口为 [`manifest.md`](./evidence/ekb-core-rebuild/ph0/2026-08-12-baseline/manifest.md)，验证记录为 [`verification.md`](./evidence/ekb-core-rebuild/ph0/2026-08-12-baseline/verification.md)。该目录只记录路径、状态、摘要、命令/来源和限制；生产只读检查当前为 `BLOCKED/NOT RUN`。本阶段仅建立未提交、未推送的 baseline 分支、部署脚本 fail-closed 安全边界和静态文档证据，不代表 PH1–PH8、运行时代码、迁移、部署、备份恢复或生产验收完成。

## PH1 foundation local evidence status (2026-08-12)

CR-PH1-T01 + CR-PH1-T03 的第一真实业务切片已在本地工作树实现，证据位于 [`evidence/ekb-core-rebuild/ph1/2026-08-12-foundation/`](./evidence/ekb-core-rebuild/ph1/2026-08-12-foundation/)，入口为 [`manifest.md`](./evidence/ekb-core-rebuild/ph1/2026-08-12-foundation/manifest.md)，验证记录为 [`verification.md`](./evidence/ekb-core-rebuild/ph1/2026-08-12-foundation/verification.md)。本地切片覆盖 v4_001/v4_002 append-only provenance/runtime migration、checksum/历史事实 fail-closed、DB-backed jobs/outbox/lease/heartbeat/retry/DLQ，以及 entrypoint migration/verify/admin/runtime config fail-closed。SQLite 与一次 disposable 本地 PostgreSQL+pgvector 执行均已验证；生产切库、生产 migration/deploy/restart 或真实 uvicorn 启动未执行，不得据此标记 PH1 总体完成。v4 runner 禁止隐式 ORM `create_all`，无 imported legacy schema 时生产路径 fail closed，测试 fixture 显式完成 bootstrap。

## Local business closure evidence (2026-08-13)

本地真实上传/对象写入/checksum complete/后台解析失败闭环、LLM-only Provider/Embedding/Vision/OCR fail-closed、附件问答引用、文档中心真实浏览器验收和回归结果记录于 [`evidence/ekb-core-rebuild/local/2026-08-13-local-business.md`](./evidence/ekb-core-rebuild/local/2026-08-13-local-business.md)。该证据明确区分本地 SQLite/开发对象存储与生产 PostgreSQL/pgvector、对象存储、队列、Worker、Scheduler、Provider 及部署前置；未满足生产前置时不得标记 PH1–PH8 完成。

## Local business closure update (2026-08-13)

本地 DONE-LOCAL 已追加附件 promotion 与主题/助手切片证据：真实 `POST /api/v1/attachments/{attachment_id}/promotions` 覆盖租户/owner/live ACL、路径冲突、回收站保留、幂等、并发，复用 `source_object_id` 创建 document/version/job，返回 `202 QUEUED` 并完成 worker 状态投影；前端助手提升入口支持，LLM-only 主题支持 `light/dark/auto` semantic tokens。验证为 backend `343 passed, 2 skipped`、web `51 passed`、typecheck/build、ruff/compileall/diff check，以及真实浏览器登录后 API `202` 与数据库 `source_object_id` 一致。生产 PostgreSQL/对象存储/队列、服务器备份部署重启和生产浏览器仍 `NOT RUN/BLOCKED`，因无运行时 `DEPLOY_HOST`/受管凭据；本地 SQLite 或 `QUEUED` 不代表生产完成。

## CR-PH4-T07 local contract closure (2026-08-13)

联网 Web Search 已从本地 QA UI、API、runtime 和配置移除；旧 `options.web_search` 请求在检索/网络调用前以 `400 FEATURE_REMOVED` fail closed。QA 仍独立保留授权 KB RAG、附件上下文和远程 LLM Provider/Model；Composer 真实浏览器状态不显示联网搜索，添加文件、远程模型和 KB 上下文仍可见。阶段记录：backend `345 passed, 2 skipped`；web `51 passed`、typecheck/build 通过。以上仅为本地证据，不代表生产部署完成。

## CR-PH3-T06 Upload Center local closure (2026-08-13)

本地 DONE-LOCAL 仅覆盖 Upload Center 的真实状态/刷新恢复切片，不代表全 PH3 或 PH0–PH8 完成。服务端 batch projection 从持久化 batch/session/item/ingest job 读取 canonical `queued`、`uploading`、`verifying`、`processing`、`indexing`、`ready`、`failed`、`cancelled`，并返回 counts、阶段/进度、attempt、job id 及脱敏 error code/message/retryable 信息；未知、越权或不属于当前租户的 batch/item fail closed。

前端以 `ekb.upload-center.batch-refs.v1` 保存 batch refs，启动/刷新后逐个重新 GET 服务端 projection，不依赖内存成功态；failed 且 retryable 的 item 调用真实 retry，processing/uploading 调用真实 cancel/abort，操作完成后重新 GET，不写假成功。浏览器实测的本地上传路径已记录真实服务端状态；本地 embedding provider 缺失时真实失败为 `EMBEDDING_UNAVAILABLE`，UI 保持失败，不把 queued/processing 冒充 ready。

验证：后端 Upload Center/ingest/boundary 定向回归 `34 passed`；前端 `52 passed`，typecheck/build 通过；`git diff --check` 与本次文档 diff secret scan 通过。生产 PostgreSQL/pgvector、对象存储、可靠队列/Worker/Scheduler、服务器备份、部署/回滚和生产浏览器均未执行，原因是当前运行时没有生产目标/受管凭据；本地 SQLite 证据不代表生产完成。

下一步本地缺口：仍需在具备受管远程 embedding/provider 与生产基础设施配置后验证成功索引、PG/pgvector、对象存储及可靠 worker/scheduler；Upload Center 目前恢复已知 batch refs，尚不是跨设备的服务端全局批次列表。全 PH3/PH4–PH8 仍按各自范围分别验收。

## Jobs Center 本地 UI 与浏览器证据（2026-08-13）

`CR-PH2-T05 / FR-058` 已有 Analytics `GovernancePanel` 追加真实「作业中心」Tab，标记为 `DONE-LOCAL`，不等同于 PH2 或 PH0–PH8 全部完成。Tab 使用当前租户作用域的 `GET /api/v1/jobs`、`GET /api/v1/jobs/cleanup` 和 `POST /api/v1/jobs/{job_id}/cancel`；显示服务端 state counts、job type、safe timestamps、lease/heartbeat availability、脱敏 error metadata、job id，以及 cleanup `total/by_state/recent`。payload、`tenant_id`、Provider 原始响应和私有地址不进入 UI/证据。仅对 `QUEUED`/`RUNNING`/`RETRY_WAIT` 提供取消，成功后重新读取服务端状态。

本地真实浏览器证据（2026-08-13）：使用现有本地开发账号登录后，从 `Analytics → 运营与治理 → 作业中心` 看到 5 条真实作业（4 `SUCCEEDED`、1 `DEAD`），其中一条文档 ingest 作业显示 `EMBEDDING_UNAVAILABLE`；cleanup projection 显示 `retention_purge` 总数 4，全部 `SUCCEEDED`；缺少 lease/heartbeat 时页面显示 unavailable。该会话过期登录阶段有 3 条较早 auth-related console errors，因此不宣称 console=0；Jobs Center 本身数据加载成功。页面显示 job id，但本文不记录完整 opaque ID、凭据、token 或私有地址。

验证：Web `57 passed in 8 files`，typecheck/build 通过，`git diff --check` 通过。后端未改动，沿用既有全量 `361 passed, 2 skipped` 结果；本次切片未重新运行后端测试。生产目标、服务器备份、部署、回滚均 `NOT RUN/BLOCKED`，统一以 `[production host]` 与 `[REDACTED]` 表示缺失项；LLM-only、无本地模型、无 mock 边界保持不变。

## Dashboard semantic-theme local closure (2026-08-13)

- DONE-LOCAL 仅覆盖 Dashboard route：`apps/web/src/app-v2/pages/DashboardPage.tsx` 的全部 31 个 page-level raw `#hex`/`rgb`/`rgba` 颜色字面量已替换为 tokens/theme 中已有的 semantic variables；未改变 backend/API/data behavior，未引入 fake data。
- 新增 `apps/web/src/app-v2/tests/v3.dashboard-theme.test.ts` source contract test，断言 DashboardPage 不包含 raw color literals。
- 本地浏览器证据：2026-08-13 认证后打开 `/#/dashboard`，真实显示 4 个 documents、3 个 knowledge spaces、trends、recent activity 和 governance proxy states。设置当前认证会话 localStorage 的 `v2.theme=dark` 后 reload，Dashboard 仍以真实数据渲染并应用主题状态。
- 当前会话累计有 13 条较早 auth-related console errors；不宣称 `console=0`、full route matrix 或 visual contrast score，且未观察到新的 Dashboard data failure。证据不记录 token、password 或 private address。
- 验证：Web `58 passed in 9 files`、typecheck/build、`git diff --check` 通过；backend 未改动，不宣称新的 backend suite。生产 target、server backup/deploy/rollback `NOT RUN/BLOCKED`，统一以 `[production host]` / `[REDACTED]` 表示。
- 本条是 Dashboard route migration 的边界记录，不宣称 `FR-001–006`、PH2 或 PH0–PH8 全部完成；LLM-only、无本地模型、无 mock 边界保持不变。详细证据见 [`evidence/ekb-core-rebuild/local/2026-08-13-local-business.md`](./evidence/ekb-core-rebuild/local/2026-08-13-local-business.md)。
## 2026-08-13 Promotion and theme local slice

- DONE-LOCAL：真实 POST /api/v1/attachments/{attachment_id}/promotions；校验 tenant/owner/live KB ACL、路径冲突/回收站保留/幂等/并发；复用 attachment source_object_id 创建 document/version/ingest job，返回 HTTP 202、状态 QUEUED，worker 投影真实状态，不把队列写成成功。
- DONE-LOCAL：助手附件“提升”入口和真实错误/状态/ID 显示；主题支持 semantic tokens、light/dark/auto、系统主题和 storage 同步、reduced-motion；LLM-only，不使用本地模型/mock。
- 验证：后端 343 passed, 2 skipped；前端 51 passed、typecheck/build 通过；ruff、compileall、git diff --check 通过；真实浏览器登录后实调 KB 与 promotion API 返回 202，数据库确认 document_version.source_object_id 与 attachment 一致。
- NOT RUN/BLOCKED：生产 PostgreSQL/pgvector、对象存储、可靠队列/Worker/Scheduler、服务器备份/部署/重启/生产浏览器；运行时 DEPLOY_HOST 和受管凭据未配置，不猜测、不输出、不落盘。生产未完成，不能标记 PH0–PH8 全部完成。

## Provider/Model linkage local closure (2026-08-13)

- DONE-LOCAL：`GET /api/v1/qa/capabilities` 与显式 Ask `options.model` 共享当前 `tenant+actor` 的活动加密 credential、启用远程 Provider/Model registry；capability/vision 信息以 DB registry 为准，不构造默认或本地模型。
- DONE-LOCAL：显式选择的 unknown/disabled/cross-tenant/no-credential model 返回 `MODEL_UNAVAILABLE`；图片 + 非 `vision=true` model 返回 `MODEL_NOT_ALLOWED`；显式选择不静默 fallback；普通无 model 请求保留 provider fallback。
- 验证：backend `349 passed, 2 skipped`；web `51 passed`、typecheck/build；定向 Provider/Model 回归 `63 passed`；范围 Ruff（忽略既有 E501 等）、compileall、`git diff --check` 通过。真实浏览器此前已确认 Profile 模型服务目录、Assistant 添加文件/模型/KB context 可见。
- 边界：未验证真实 Provider 出网或生产完成；生产 PostgreSQL/pgvector、对象存储、队列/Worker/Scheduler、服务器备份/部署/回滚和生产浏览器仍 `NOT RUN/BLOCKED`。未改 Skills、legacy backup、deploy、migration、theme 或 Web Search。

## Local Embedding boundary closure (2026-08-13)

本地 Embedding 切片使用 `services/embedding.py` 的真实 OpenAI-compatible 远程客户端；worker 绑定当前 `tenant+actor`、KB ACTIVE embedding profile 与 ACTIVE index generation。无配置或无 ACTIVE profile/generation 时以 `EMBEDDING_UNAVAILABLE` fail-closed，不写 READY。远程响应校验 `count`、dimension 和非有限值；不使用本地模型或伪向量，真实外部 Provider 未调用。

验证：Embedding 定向 `40 passed`；全量后端 `358 passed, 2 skipped`；Ruff、compileall、`git diff --check` 通过。生产 Provider 出网/凭据、PostgreSQL/pgvector、部署仍 `NOT RUN/BLOCKED`；本条不代表全 PH3/PH8 或 PH0–PH8 完成。

## CR-PH3-T06 Upload Center 跨设备批次列表收尾（2026-08-13）

本地 DONE-LOCAL 增量：新增租户作用域 `GET /api/v1/kb/uploads/batches`，服务端对 `limit` 实施 `1–100` 有界约束（默认 `30`），Upload Center 列表以服务端 batch projection 为权威来源；`localStorage` 的 `ekb.upload-center.batch-refs.v1` 仅作为 fallback/reconciliation，不作为跨设备列表真值。本条更新前一条“仅恢复已知 batch refs、尚非跨设备服务端全局列表”的历史状态。

本地浏览器证据（2026-08-13）：使用现有本地开发账号重新建立登录后，删除 `ekb.upload-center.batch-refs.v1` 并 reload `/#/documents`，真实服务端 batch 被恢复并显示，包含真实文件 `HANDOFF-2026-08-12.md`、失败阶段 `CHUNKING / 50%`、attempt `1/1`、job id 和 `EMBEDDING_UNAVAILABLE`。不记录 access/refresh token、密码、私有地址或 opaque secret。

验证：backend full pytest `361 passed, 2 skipped`；web `52 passed`；web typecheck/build 通过。生产/服务器工作因当前运行时没有生产目标（统一写作 `[production host]`）或受管凭据（统一写作 `[REDACTED]`）仍 `NOT RUN/BLOCKED`；不代表生产部署完成，也不代表全 PH3/PH8 完成。

## Current local business closure (2026-08-14)

本节是当前本地实现的 authority/evidence 索引，不是 Phase exit 证明：

- DONE-LOCAL：Jobs runtime 后端/前端展示真实 worker heartbeat、retention lease 和 scheduler runs；legacy Office 已接入隔离 `soffice` 转换并复用 OOXML parser。FR-015、CR-PH2 runtime/jobs、CR-PH3 legacy parser 仍仅标记 `DONE-LOCAL`。
- 本地证据：[`2026-08-14-jobs-runtime.md`](evidence/ekb-core-rebuild/local/2026-08-14-jobs-runtime.md)、[`2026-08-14-legacy-office.md`](evidence/ekb-core-rebuild/local/2026-08-14-legacy-office.md)。证据记录真实浏览器与 XLS→XLSX smoke 边界，不记录凭据、token 或私网地址。
- 未完成：生产 PostgreSQL/pgvector、远程 Provider/Embedding、对象存储、可靠队列/生产 Worker、服务器备份、部署和回滚仍 `NOT RUN/BLOCKED`；生产目标统一写作 `[production host]`，敏感值统一写作 `[REDACTED]`。

## Assistant conversation export local closure (2026-08-14)

- DONE-LOCAL：Assistant 导出只在 ready、非流式且存在非 transient 消息时可用；只导出当前服务端已持久化消息，流式 transient 内容不会进入 Markdown。
- Share 保持 disabled；未新增分享链接、公开访问或服务端导出存储。
- 证据见 [`evidence/ekb-core-rebuild/local/2026-08-14-conversation-export.md`](./evidence/ekb-core-rebuild/local/2026-08-14-conversation-export.md)。生产、部署、备份、回滚和 PH0–PH8 全部完成仍 `NOT RUN/BLOCKED`。
- 文档不记录密码、token、API key 或私密地址；生产统一使用 `[production host]` / `[REDACTED]`。

## Conversation rename local closure (2026-08-14)

- DONE-LOCAL：已有 `PATCH /api/v1/chat/conversations/{id}` 已被前端真实接入，覆盖 `ApiClient`、conversation adapter、受控 UI 和服务端刷新；未知客户端异常不泄漏原始 `message`。
- Share 和 Projects 仍保持 disabled。验证为定向 `2 passed`、全量 Web `12 files / 68 tests passed`、typecheck/build passed；build 保留既有 `>500KB` chunk warning。
- 证据见 [`evidence/ekb-core-rebuild/local/2026-08-14-conversation-rename.md`](./evidence/ekb-core-rebuild/local/2026-08-14-conversation-rename.md)。本地切片不代表 PH0–PH8 或生产完成；服务器、备份、部署和回滚仍 `NOT RUN/BLOCKED`，生产统一使用 `[production host]` / `[REDACTED]`。

## Conversation branches local closure (2026-08-14)

- DONE-LOCAL：真实端点为 `GET /chat/conversations/{id}/branches`、`POST /chat/conversations/{id}/active-branch`、`GET /chat/conversations/{id}/messages?branch_id=`；`ApiClient` 兼容旧数组与新 `{messages}`，adapter 完成分支映射和安全错误，`AssistantPage` 使用真实选择器并刷新消息。
- 验证：定向 `v3.conversation-branches.test.ts` 为 `4 passed`；`npm run test` 为 `13 files / 72 tests passed`；`npm run typecheck`、`npm run build`、`git diff --check` 通过。当前浏览器未认证。
- Share/Projects 保持 disabled；本地切片不能宣称 PH0-PH8 或生产完成，生产/服务器、备份、部署和回滚仍 `NOT RUN/BLOCKED`。
- 证据见 [`evidence/ekb-core-rebuild/local/2026-08-14-conversation-branches.md`](./evidence/ekb-core-rebuild/local/2026-08-14-conversation-branches.md)。不记录秘密。

## Legacy store.save_message 到 chat branch graph 本地桥接（2026-08-14）

- DONE-LOCAL：在 v4 schema 下，`store.save_message` 为 legacy QA 消息自动创建/激活 root branch，并写入 `branch_id`、`parent_message_id`、`content_hash`；无 v4 列时保持 legacy 路径。
- 验证：`bridge-check` 临时 SQLite 为 `PASS`；PH4 `32 passed`；API `28 passed`；ruff/compileall `passed`。
- 该条记录当时尚未完成；后续已由 2026-08-14 qa.py active-branch history bridge 本地收口条目完成。当前浏览器未认证。本地开发切片不宣称 PH4 或 PH0–PH8 全部完成。
- 生产/服务器（`[production host]`）、备份、部署、回滚均 `NOT RUN/BLOCKED`；不记录密码、token、API key 或私密地址。证据见 [`docs/evidence/ekb-core-rebuild/local/2026-08-14-chat-branch-bridge.md`](./evidence/ekb-core-rebuild/local/2026-08-14-chat-branch-bridge.md)。

## qa.py active branch history local closure (2026-08-14)

- DONE-LOCAL：`/api/v1/qa/ask` 多轮 history 优先按 `tenant + actor` 读取 active branch materialization，过滤 `HIDDEN` 消息和当前 turn；branch graph 或旧 schema 不可用时回退 `store.list_messages`。
- 验证：tests/test_multi_turn.py：11 passed（包含 active-branch regression）；PH4 `32 passed`；隔离临时 SQLite API `28 passed`；`compileall` passed；`git diff --check` passed；本地 `/healthz` `200`。
- 当前浏览器未认证。本地业务切片不宣称 PH0–PH8 全部完成；生产服务器（`[production host]`）、备份、部署、回滚仍 `NOT RUN/BLOCKED`。证据见 [`evidence/ekb-core-rebuild/local/2026-08-14-qa-active-branch-history.md`](./evidence/ekb-core-rebuild/local/2026-08-14-qa-active-branch-history.md)。不记录密码、token、API key 或私密地址。
