---
title: EKB 设计稿十页全栈闭环 v3 计划自审
date: 2026-08-09
status: 已批准
tier: 3
type: feature
plan_ref: ./03_plan.md
spec_ref: ./02_spec.md
matrix_ref: ./04_verification-matrix.md
owner: Sol
version: 3.0
purpose: 记录两轮文档自审、FR/NFR/AC 一致性和十路线交互覆盖
companion_docs: [./01_requirements.md, ./02_spec.md, ./03_plan.md, ./04_verification-matrix.md, ./README.md]
open_issues: []
review_stage: Sol final gate passed
approval: user authorized docs-first fullstack execution direction; Sol approved technical/security/QA document gate on 2026-08-10; implementation/browser/migration evidence remains pending. Do not claim the user reviewed final wording.
---

# EKB 设计稿十页全栈闭环 v3 计划自审

本文件记录两轮独立自审。自审对象是文档链，不代表业务代码、迁移运行或浏览器 QA 已发生。

## 第一轮：覆盖与可执行性审查

审查范围：`01_requirements.md` 的 52 FR/18 NFR、`02_spec.md` 的 API/DDL/安全/前端 mapping、`03_plan.md` 的 V3-T01–V3-T38 和 `04_verification-matrix.md`。

- FR-001–FR-052 每条都有主任务、AC-V3 验收 ID 和矩阵行；NFR-001–NFR-018 每条都有门禁和证据路径。
- 每个阶段为 5–10 个任务，并以用户可见行为作为竖切边界：身份、内容治理、助手、分析、应用、十页 cutover；没有“全数据库→全 API→全 UI”的水平阶段。
- V3-T01/V3-T07/V3-T13/V3-T19/V3-T25 分别拥有 `v3_001_identity`、`v3_002_content`、`v3_003_assistant`、`v3_004_analytics`、`v3_005_apps` 的首个 vertical slice；aggregator 只按顺序调用 immutable migration，不把 29 张表一次性写进一个 checksum。
- spec anchors、API method/path/request/response/error/permission/audit、SQL DDL、关键 FK/CHECK 或 service+verifier 补偿、索引、backfill、verify、rollback、adapter/state、security 和 rollout 均被计划任务引用。
- 现状事实已作为前置兼容/安全债进入 P1/P2/P3/P4：`/me` first/dev fallback、token fixed policy/capability trust、QA actor+tenant scope、ops days filtering、Document file size/object ref、conversation PATCH rename/archive、version changed diff、KB tags persistence、idempotency unique、SQLite quota concurrency、旧 admin user capability 和 auth session persistence。
- D18 的 role backfill 已锁定为 `OWNER→owner`、`ADMIN→admin`、`MEMBER→member`、`CUSTOMER→legacy_customer`；legacy compatibility role 只允许 `kb:read` + `qa:ask`、assigned 时 system-protected，null/unknown/unresolved tenant hard stop，`platform_role` 独立。V3-T01a/V3-T01b 仅是同一 canonical task 的串行 sub-slices；兼容路径只保留现有 POST admin user-create，不声称存在 legacy GET user list。

**第一轮结论：通过。** 发现的关键风险都已转为可执行任务、测试命令和回滚边界，没有留给最终 TN 才首次发现的安全或迁移债。

## 第二轮：合同、迁移、测试和发布一致性审查

审查重点：用户追加的 schema/auth/analytics 更正，以及跨 SQLite/PostgreSQL 的可执行性。

- `auth_sessions` 字段、索引、login/refresh/logout/password change、`GET /me/sessions`、`DELETE /me/sessions/{session_id}`、revoke-all、双会话和双主体双租户测试均在 spec、V3-T01/V3-T04 和矩阵中闭合；refresh 明确保持 JSON body `{refresh_token}`。
- Document 现状不宣称已有 `deleted_at` 或可靠 file size；content migration 用 `Document.updated_at` 生成 inferred deletion time，并以 `metadata.inferred=true`，级联删除共享 `deletion_batch_id`，父级恢复只按 batch 恢复。
- 五版本 ledger/checksum、FK/CHECK、SQLite pragma、rollback inverse order、Document idempotency unique、QA quota SQLite 并发锁/原子更新和 PG vector 异常都在 spec/plan/matrix 中有验证路径。
- QA turn get/cancel 按 actor+tenant，取消有独立 audit；既有 `qa_turns` 不被称为 replay log。若实现 Last-Event-ID replay，必须另有 replay 表/contract/test，当前 v3 只保留既有 QA-STREAM-09 设计边界。
- 既有 `PATCH /conversations/{id}` rename/archive 被保留并在 V3-T18/V3-T34 接 adapter/UI；不新增重复 rename endpoint。`GET /admin/ops/dashboard?days=1|90` 兼容路径在 V3-T20 直接修复并测试日期过滤。
- Apps run 采用 D1 决定的同步 provider call：`200 SUCCEEDED|FAILED`，UI request-pending 只表示当前请求中，`504` 可重试；没有伪造 queue/worker/durable job。
- 十页 desktop/mobile、API/DB/migration/rollback、P0/P1/P2、console 和 link gate 均有明确 stop condition；仅 provider/LLM 外部环境噪声可在全部内部门禁通过后单独记录。

**第二轮结论：通过。** 没有未闭合决策、空泛路径或占位合同；可直接进入 Sol 的文档审查，再进入按阶段的业务代码委派。

## 十路线交互覆盖附录

下表把设计稿的每个 primary control 映射到 FR、服务端 endpoint/store、canonical task 和双视口证据。以下列出的 primary action 均是核心体验，不允许以 design-only、inert click 或未实现状态结束；设计稿未覆盖的高级动作必须通过 app-v2 的 design-matching drawer/modal/popover/tab 完成。

| Route | Design primary controls and entry point | FR | Endpoint / store | Primary task | Verification and evidence |
|---|---|---|---|---|---|
| `#/dashboard` | 四个快捷动作：新建文档 note dialog、上传文档、创建知识空间、智能导入；摘要卡、日期筛选、最近访问/标签/健康度点击 | FR-029, FR-034, FR-047 | `GET /admin/dashboard/summary`; note create, existing upload/KB create; Apps `intent=import`; analytics/access stores | V3-T20, V3-T22, V3-T32 | desktop + 390x844 success/error/retry, same tenant/subject, hard reload and audit; `V3-T32-quick-actions.json` |
| `#/knowledge` | KB/folder tree create/move/rename/delete, search/filter, row menu for favorite/share/tag/trash and overview drawer | FR-008, FR-010–FR-014, FR-030, FR-036 | folders, document relations, `resource_favorites`, `resource_tags`, shares, trash services | V3-T07, V3-T09–V3-T12 | both viewports, object ACL and cross-tenant negative, hard reload, audit; `V3-T12-knowledge-pages.json` |
| `#/assistant` | project picker/member drawer, attachment picker, model/deep-thinking/web-search controls, visible stream cancel, feedback reason/comment and citation panel | FR-015–FR-019, FR-031, FR-045, FR-047 | projects/members/attachments, `/qa/ask`, SSE v2, cancel/feedback, durable `message_citations`, provider APIs | V3-T13–V3-T18 | both viewports, actor+tenant turn scope, hard-reload citations, admin provider audit; `V3-T18-assistant-browser.json` |
| `#/documents` | server filter/search/sort/cursor bar, upload/retry, batch menu, version/diff drawer, delete confirmation | FR-009, FR-010, FR-016, FR-032, FR-041 | documents list/note/upload, document_versions, batch/trash services | V3-T08, V3-T11, V3-T12 | both viewports, tenant cursor binding, hard reload and version audit; `V3-T08-documents-contract.json` |
| `#/team` | user search/list pagination, row detail/status/role menu, invite/export, role tab and permission matrix | FR-001–FR-005, FR-033, FR-040, FR-042 | tenant users/memberships, roles/permissions, live `tenants.policy_version`, admin create compatibility | V3-T01–V3-T06, V3-T35 | both viewports, dual subject/dual tenant and legacy role non-escalation, hard reload, audit; `V3-T01-identity-scope.json` |
| `#/analytics` | server 1/7/30/90-day range, trend/distribution/activity tabs, health and resource click-through, loading/empty/error/permission/retry | FR-020–FR-023, FR-034, FR-047 | dashboard/analytics aggregate and bounded `resource_access_events` | V3-T19–V3-T24 | both viewports, tenant/permission filter, 1/90-day hard reload, EXPLAIN/p95 and audit; `V3-T23-analytics-perf.json` |
| `#/apps` | six-card catalog/search/all-installed/recommended, install lifecycle drawer, credential write-once/rotate, connect, synchronous run, sync/history/retry/uninstall | FR-024–FR-028, FR-035, FR-044, FR-047 | app catalog/installations/credentials/runs and downstream sync source | V3-T25–V3-T30 | both viewports, tenant admin and secret redaction, hard reload/history, audit; `V3-T30-apps-page.json` |
| `#/recycle` | retention-days filter, trash list, restore/purge/clear confirmation, parent-deleted conflict recovery CTA | FR-014, FR-036, FR-047 | `trash_items`, deletion batches, retention/purge service | V3-T11, V3-T12, V3-T35 | both viewports, same-batch restore and `PARENT_DELETED`, hard reload/audit retention; `V3-T11-trash.json` |
| `#/profile` | profile menu navigation; profile/preferences; Security password + device sessions; API create/copy-once/revoke; notification list/read; help/feedback drawer | FR-006, FR-007, FR-033, FR-044, FR-047, FR-050–FR-052 | profile/preferences/auth_sessions/API keys/`user_notifications`/`support_feedback` | V3-T04, V3-T05, V3-T31, V3-T35 | both viewports, subject ownership and reauth, dual-session hard reload, redacted audit; `V3-T04-profile-sessions.json` |
| `#/modules` | exact nine-tile Module Map; each tile navigates through live capability/data state and truthful unauthorized/provider state | FR-037, FR-038, FR-046, FR-051 | app-v2 route manifest, capability facade and feature flags | V3-T31, V3-T36, V3-T37 | both viewports, tenant/subject capability checks, hard reload and cutover/import audit; `V3-T37-import-audit.json` |

## 生命周期与最终审查结论

- 文档 lifecycle：`01_requirements.md`、`02_spec.md`、`03_plan.md`、`04_verification-matrix.md`、`README.md` 与本审查均为 `已批准`，Sol final gate 已于 2026-08-10 通过；不声称用户审阅了最终文字。
- Implementation status：M0–M5 前端/app-v2 及既有 Team/Profile 端点接入按历史记录已完成；v3 后端闭环、五版本 migration、兼容/安全债、Vitest 配置、业务测试、浏览器 TN 和生产 cutover 仍为 pending。本 doc-only milestone 不提供代码、浏览器或迁移执行证据。
- Reviewers/roles：Sol（架构、治理、发布边界）、luna_max_worker（文档实现与静态校对）、Curie（当前仓库事实审计）、independent read-only reviewers（spec/plan/产品交互）；审查日期 `2026-08-09`；target version `v3.0`。
- Residual risks：实现阶段仍需验证现有模型与 SQLite/PostgreSQL 方言、旧 dirty worktree 的合并安全、Vitest/package 配置、provider credentials/LLM 外部依赖、Codex in-app Browser 证据、migration ledger/provenance 和真实 tenant/subject 流程；任何安全、租户、迁移、契约或业务流程失败都阻塞 cutover。
- Acceptance registry：FR-001–FR-052 对应 `AC-V3-001`–`AC-V3-052`；NFR-001–NFR-018 对应 `AC-V3-NFR-001`–`AC-V3-NFR-018`，每项均在 plan 与 matrix 具有一个 primary task 和证据目标。
