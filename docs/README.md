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
## 2026-08-13 Promotion and theme local slice

- DONE-LOCAL：真实 POST /api/v1/attachments/{attachment_id}/promotions；校验 tenant/owner/live KB ACL、路径冲突/回收站保留/幂等/并发；复用 attachment source_object_id 创建 document/version/ingest job，返回 HTTP 202、状态 QUEUED，worker 投影真实状态，不把队列写成成功。
- DONE-LOCAL：助手附件“提升”入口和真实错误/状态/ID 显示；主题支持 semantic tokens、light/dark/auto、系统主题和 storage 同步、reduced-motion；LLM-only，不使用本地模型/mock。
- 验证：后端 343 passed, 2 skipped；前端 51 passed、typecheck/build 通过；ruff、compileall、git diff --check 通过；真实浏览器登录后实调 KB 与 promotion API 返回 202，数据库确认 document_version.source_object_id 与 attachment 一致。
- NOT RUN/BLOCKED：生产 PostgreSQL/pgvector、对象存储、可靠队列/Worker/Scheduler、服务器备份/部署/重启/生产浏览器；运行时 DEPLOY_HOST 和受管凭据未配置，不猜测、不输出、不落盘。生产未完成，不能标记 PH0–PH8 全部完成。
