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
