---
title: EKB 设计稿十页全栈闭环 v3 实施计划
date: 2026-08-09
status: 已批准
tier: 3
type: feature
feature: ekb-fullstack-v3
spec_ref: ./02_spec.md
requirements_ref: ./01_requirements.md
execution_mode: subagent-driven
writer_policy: Sol 串行调度一个 luna_max_worker；不并发写同一工作区
commit_cadence: per-task
contract_version: 3
owner: Sol
version: 3.0
purpose: 按用户可见行为编排六阶段、38 个全栈竖切任务、TDD、回滚和最终 TN
companion_docs: [./01_requirements.md, ./02_spec.md, ./04_verification-matrix.md, ./03_plan_review.md, ./README.md]
open_issues: []
review_stage: Sol final gate passed
approval: user authorized docs-first fullstack execution direction; Sol approved technical/security/QA document gate on 2026-08-10; implementation/browser/migration evidence remains pending. Do not claim the user reviewed final wording.
---

# EKB 设计稿十页全栈闭环 v3 实施计划

## 1. 计划结论与状态

本计划把 v3 拆成 6 个可独立部署的全栈竖切阶段；每个阶段 5–10 个任务，每个任务都穿过所需的 schema/migration、FastAPI service/router、app-v2 adapter/page 和测试层。执行 mode 记录为 `subagent-driven`，但本项目写入规则固定为 Sol 串行调度一个 luna writer，避免两个 writer 同时改工作区。

M0–M5 的前端/app-v2 进度如历史文档所记：M0 路由/骨架、M1 认证壳、M2 工作台/模块地图、M3 KB/文档、M4 助手/SSE、M5 Team/Profile 对既有端点接入已完成相应记录；这些不等于 v3 全栈完成。v3 的 identity user list/roles/profile/API key、folders/tags/shares/favorites/trash、assistant projects/attachments/model/web-search、analytics access events、apps lifecycle 等均为 pending。

**Done when:** 52 FR 与 18 NFR 均有 PASS 证据；6 个可部署阶段的 backend/frontend/security 套件、migration verify/rollback dry-run、`/Users/alin/EKB/.venv/bin/ruff check`、`npm run build` 全部退出 0；10/10 页面在 desktop 与 390x844 完成硬刷新场景，P0/P1/P2=0、console errors=0，核心写入可从服务端恢复。

**Done-when walkthrough:** 先以 `/Users/alin/EKB/.venv/bin/python -m ekb_api.migrations.v3_fullstack --through v3_005_apps --verify --expect-versions v3_001_identity,v3_002_content,v3_003_assistant,v3_004_analytics,v3_005_apps` 证明五个 immutable version（`v3_001_identity` 到 `v3_005_apps`）各自 checksum、29 张 v3-owned 表、至少 26 个索引、六条 app catalog seed 和 backfill 完整，再以每阶段的 `/Users/alin/EKB/.venv/bin/pytest` 与 adapter contract 证明真实 request/response/error/permission/audit；TN 用 Codex in-app Browser 在十个 hash 路由逐一执行创建/更新/删除/恢复/问答/安装流程并 hard reload，截图、网络响应、console 和矩阵证据互相引用，最后 import audit 通过才切换 App 入口并清理无引用旧 UI。

## 2. 执行顺序

```text
P1 身份/策略/密钥 ──> P2 知识治理/回收 ──> P3 助手扩展
          │                   │                   │
          └──────────────> P4 分析 ────────────────┤
                                                   v
                         P5 应用目录/连接 ──> P6 十页 cutover/QA
```

每个阶段完成后执行一次该阶段完整 verify、更新 [`04_verification-matrix.md`](./04_verification-matrix.md) 证据列并由 Sol 审查 diff；阶段内 tasks 只能由同一个 writer 串行实现。外部 LLM/provider 错误只能在安全、租户、迁移、契约和业务流程证据全部通过后记录为 non-blocking 风险。

## 3. 计划 Decision Log

| # | 决定 | 选项 | 理由 |
|---|---|---|---|
| P-D1 | 先做身份实时授权与 API key tracer bullet | 先做 UI / 先做数据库 / 一个真实 user list→refresh→页面切片 | user list + policy refresh 同时验证新增表、AuthContext、API、adapter 和权限风险，最能证明全栈架构。 |
| P-D2 | 每个任务同一提交包含契约测试和 UI adapter | 分层提交 / 只在 TN 测试 / 竖切提交 | 让每个阶段可部署、可回滚且有真实行为证据，避免“数据库完成但用户不可用”。 |
| P-D3 | v3 list 新 path 使用 envelope，旧 path 保留 | 改旧 list / 双格式协商 / 新 path | 旧 app-v2 和外部调用方不破坏，前端可按 flag 切换。 |
| P-D4 | provider、credential 和 sync 独立状态机 | 合并 installation status / 只保留 sync / 新微服务 | 保护秘密与状态语义；sync 失败不冒充安装失败或成功。 |
| P-D5 | migration 先 shadow verify，再分域灰度 | 一次性全开 / 先 UI / 先 schema 后长期停滞 | 每个 immutable version 在自己的首个 vertical slice 应用并验证；identity→knowledge→assistant→analytics→apps 顺序最小化交叉风险。 |
| P-D6 | cutover 最后做并保留可回退边界 | 立即替换 App / 并行永久保留 / 验证后切换再清理 | 设计稿要求新 UI；生产回滚只依赖 feature flag/API compatibility 到最后新 UI slice，只有 import audit 后才清理无引用 legacy UI。 |

## 4. Code Study Notes

### Patterns to follow

- `apps/api/ekb_api/core/db.py:init_db`：当前 `create_all` 后显式兼容 migration；v3 使用相同启动边界，五个 `apps/api/ekb_api/migrations/v3_00N_*.py` 各自托管表/seed/backfill，`apps/api/ekb_api/migrations/v3_fullstack.py` 仅作顺序 aggregator/CLI。
- `apps/api/ekb_api/core/auth.py:get_auth_context`：统一认证入口；v3 在此处接入 live membership 与 API key，不由 router 各自认证。
- `apps/api/ekb_api/core/audit.py:redact_metadata` 与 `apps/api/ekb_api/routers/kb.py:_audit`：所有敏感写入通过统一脱敏与 trace。
- `apps/api/ekb_api/store.py`：router→store/domain 的既有数据访问风格；新服务保持 tenant-first query。
- `apps/web/src/app-v2/AppV2.tsx`：单一 ApiClient 注入 adapter facade；新页面不得绕过该边界。
- `apps/web/src/app-v2/adapters/index.ts`：`AdapterResult`、`stateForError` 和 `stateForData` 的状态映射。
- `apps/web/src/app-v2/pages/AssistantPage.tsx`、`apps/web/src/app-v2/adapters/qaStream.ts`：SSE v2 动态 turn/message getter、cancel、引用和 protocol error 处理。

### Existing code to reuse

- `apps/api/ekb_api/models.py`：旧表 ORM 与新 v3 表的同一 Base。
- `apps/api/ekb_api/schemas.py`：现有 Pydantic contract；新 schema additive。
- `apps/api/ekb_api/routers/admin.py`：audit/ops/review/sync/backup 的权限与响应 precedent。
- `apps/web/src/app-v2/types/*.ts`：v2 view-model 类型、state 和 service 注入形状。
- `apps/web/src/app-v2/components/V3StatePanel.tsx`：loading/empty/error/permission 与仅限外部 provider 的 truthful-disabled 视觉状态，由 v3 新 UI 自行创建；核心动作不能走此状态结束。

### Constraints discovered

- 当前 root 使用 `create_all` + 手工兼容列迁移，没有现成 Alembic authority；必须先落 v3 ledger/verify/rollback。
- 既有 `/kb`、`/conversations`、`/qa` 和 `/admin` 端点是外部兼容面，不能改旧列表响应为 envelope。
- `AuthContext` 当前 token 带 capabilities；v3 必须把 token claim 降为 advisory，授权读取 live DB role/policy。
- app-v2 当前 `apps/web/src/app-v2/pages/AnalyticsPage.tsx`、`apps/web/src/app-v2/pages/AppsPage.tsx`、`apps/web/src/app-v2/pages/RecyclePage.tsx` 有占位/缺口状态；它们是 pending 实现而非已完成证据。
- `apps/web/package.json` 当前只有 `npm run build`（`tsc -b && vite build`），没有独立测试 runner、test/lint script 或浏览器自动化依赖；V3-T01 的 bounded tooling prerequisite 必须先落地 Vitest/config/scripts，之后任务只调用已配置 runner。

### Stack signals

- Python: `apps/api/pyproject.toml`、FastAPI、SQLAlchemy、/Users/alin/EKB/.venv/bin/pytest、/Users/alin/EKB/.venv/bin/ruff；backend 命令固定使用 `/Users/alin/EKB/.venv/bin/python`、`/Users/alin/EKB/.venv/bin/pytest`、`/Users/alin/EKB/.venv/bin/ruff` 或显式激活的等价 venv，不声称存在 backend typechecker。
- TypeScript/React: `apps/web/package.json`、`apps/web/tsconfig*.json`、Vite、React 19；lockfile `apps/web/package-lock.json` → npm。
- Runtime: `docker-compose.prod.yml`、`apps/api/Dockerfile`、`apps/web/Dockerfile`；dev SQLite, prod PostgreSQL。
- No new queue/service signal; no task may introduce a microservice or queue without a new Sol decision.

## 5. Prerequisites

1. Sol 已审查 [`01_requirements.md`](./01_requirements.md) 和 [`02_spec.md`](./02_spec.md)，确认 v3 authoritative 链接无冲突。
2. `EKB_ENV=test` 可运行现有 backend tests；SQLite 临时库可创建，PostgreSQL CI/环境可提供验证 URL。
3. 前端 `cd apps/web && npm ci` 是实现前置；当前没有可执行 v3 frontend test runner，V3-T01 先添加已批准的 Vitest devDependency/lock entry、`apps/web/vitest.config.ts`、`npm test`、`npm run typecheck` 和 adapter fixture，之后才执行 frontend contract tests。
4. production secret 只通过部署 secret store 注入：`EKB_TOKEN_SECRET`、`EKB_APP_CREDENTIAL_KEY`；计划和测试输出不得打印值。
5. 设计稿通过本地 HTTP 服务提供，验证使用 `设计稿/index.html` 实际渲染，不使用旧 UI 作为视觉实现来源。
6. 浏览器视觉 QA 使用用户选择的 Codex in-app Browser 工作流和证据捕获；任何浏览器 CLI 都不是当前 gate，V3-T32–V3-T38 不得调用未配置的 CLI，除非 Sol 后续批准并先添加 package/config/fixture。
7. backend tests 使用临时 SQLite 数据库，web build 会写入 `apps/web/dist/build-info`；任务证据必须记录这些写入型验证副作用，并只清理任务拥有的临时输出。

### 5.1 前置兼容/安全债清单

这些不是 V3-T38 才首次发现的质量问题，必须由前置阶段完成并作为 phase gate：

| 事实 | 前置任务 | 必须证明的修复 |
|---|---|---|
| `/me` 使用 store first/dev user/tenant；token policy 固定 1 且信任 token capabilities | V3-T01–V3-T02 | live `auth_sessions` + membership/role，双主体双租户 `/me`/refresh 测试 |
| QA turn get/cancel/is_cancelled/seq/终态仅 tenant 或 turn_id scope | V3-T17 | actor+tenant 查询/更新、独立 cancel audit、stale actor/tenant deny；不把 `qa_turns` 当 replay log |
| `/admin/ops/dashboard?days` 未过滤日期 | V3-T20 | 兼容端点 1 天、90 天及边界日期测试，真实 bounded aggregate |
| Document 无可靠 file size/object ref；KB tags 未持久化 | V3-T07–V3-T10 | nullable additive columns/response/backfill，resource_tags 写入 KB/document tags |
| 文档 idempotency 先查后写；version 不可靠递增；diff 缺 changed | V3-T07–V3-T08 | partial unique index、并发冲突测试、version unique/transaction increment、changed contract |
| SQLite create_all/ALTER 无 ledger/rollback；PG pgvector 异常被吞 | V3-T01/V3-T07/V3-T13/V3-T19/V3-T25 | 五个版本 checksum/verify/reverse dry-run；PG 初始化 fail closed |
| SQLite QA quota 并发可能重复计数 | V3-T17 | 原子 update/transaction lock、并发 fixture 和 quota regression |
| `POST /admin/users` 仅 `kb:write` | V3-T06 | 精确 `team:user:manage` capability，新路由使用新权限，旧端点保留兼容映射 |

## 6. 阶段与任务

> 每个 code task 固定采用 TDD：先写失败测试并运行确认 FAIL，再写最小实现并运行确认 PASS，再执行 lint/build 和提交。任务正文给出任务专属测试、文件、命令和行为断言；任何任务都不以“只创建表/只加 endpoint/只画页面”作为完成形态，纯 migration/配置例外会显式标注 `Slice shape`。

**Shape exceptions are bounded and explicit:** V3-T01/V3-T07/V3-T13/V3-T19/V3-T25 include a migration/schema provenance step because each is the first user-visible vertical slice for its version; V3-T23/V3-T38 are verification/performance/doc gates; V3-T31 is adapter infrastructure; V3-T37 is the reversible cutover/import-audit exception. Each exception has a downstream page, API contract or evidence boundary in the same phase and cannot be accepted as an isolated horizontal layer.

### Phase 1：身份、实时策略与密钥（V3-T01–V3-T06）

阶段目标：管理员能在一个租户内列出/更新一个用户，刷新后看到实时 role/capability；用户能安全管理 profile 和 API key。完成后可独立灰度。

#### V3-T01：用户列表 tracer bullet（当前 pending）

**Goal:** 从 tenant-scoped user list 请求到 Team 页面渲染一条真实用户记录，证明 migration→service→API→adapter→page 全链路。

**Spec refs:** FR-001, FR-002, FR-033, FR-037, FR-039, FR-042; §5.2, §6.2, §7.1, §9.2

**Depends on:** none；**Slice shape:** vertical tracer bullet；**Idempotent:** yes；**TDD:** yes — new-feature

**Files:** Create `apps/api/ekb_api/migrations/v3_001_identity.py`, `apps/api/ekb_api/migrations/v3_fullstack.py` (aggregator/CLI only), `apps/api/ekb_api/services/v3_identity.py`, `apps/api/ekb_api/routers/identity_v3.py`, `apps/api/tests/v3/test_identity_users.py`, `apps/api/tests/v3/test_identity_subject_tenant.py`, `apps/api/tests/v3/test_legacy_role_backfill.py`, `apps/api/tests/v3/test_migration_v3_001.py`, `apps/web/src/app-v2/adapters/v3Identity.ts`, `apps/web/src/app-v2/types/v3Identity.ts`, `apps/web/src/app-v2/tests/v3.identity.test.ts`, `apps/web/vitest.config.ts`; Modify `apps/api/ekb_api/main.py`, `apps/api/ekb_api/core/auth.py`, `apps/api/ekb_api/routers/me.py`, `apps/web/src/app-v2/AppV2.tsx`, `apps/web/src/app-v2/pages/TeamPage.tsx`, `apps/web/src/app-v2/types/services.ts`, `apps/web/src/app-v2/types/index.ts`, `apps/web/src/app-v2/adapters/index.ts`, `apps/web/src/lib/api.ts`, `apps/web/package.json`, `apps/web/package-lock.json`.

**Execution note:** 为 dirty-worktree 安全，Sol 将本 canonical task 串行执行为两个 sub-slices：`V3-T01a` 先完成 backend migration/live subject/user list 与 backfill verify，随后 `V3-T01b` 完成 Vitest 配置及 adapter/Team page；这不是两个 Backlog task，不改变 V3-T01–V3-T38 的任务总数。`V3-T01a` 只创建/回填 `auth_sessions` 表及 `/me`/user-list 所需 live membership，不提前实现完整 login/refresh/logout/password-change session lifecycle；完整 lifecycle 属于 V3-T02/V3-T04。

**Steps:**

1. Add failing /Users/alin/EKB/.venv/bin/pytest fixture with two tenants and three users; `GET /api/v1/tenants/{tenant}/users?page_size=20` must return only the current tenant and `items/next_cursor/page_size`.
2. Run `cd /Users/alin/EKB/apps/api && EKB_ENV=test /Users/alin/EKB/.venv/bin/pytest -q tests/v3/test_identity_users.py`; expected `FAIL: v3 user list route is not registered`.
3. Before applying `v3_001_identity`, add the explicit legacy/v3 metadata allowlist guard around `init_db`: `Base.metadata.create_all` must not create any v3-owned name. In V3-T01a, add only `v3_001_identity` tables/backfill hook (roles, membership, profile/preferences, API keys and the `auth_sessions` schema), its ledger/checksum, live scoped store/service, schemas and router; the backfill must implement only `OWNER→owner`, `ADMIN→admin`, `MEMBER→member`, `CUSTOMER→legacy_customer`, exact `kb:read` + `qa:ask`, and fail on null/unknown role or unresolved tenant. Do not implement login/refresh/logout/password session lifecycle here, and do not create or edit `v3_002`–`v3_005`.
4. Re-run identity /Users/alin/EKB/.venv/bin/pytest, legacy-role non-escalation tests, fresh/upgraded metadata provenance fixture and migration verify; expected `v3_001_identity status=PASS`, legacy compatibility role is labeled and protected while assigned, and no legacy fallback. Required production pgvector verification fails closed with visible health/audit signal; explicit non-required dev/test fallback remains observable.
5. In `V3-T01b`, add the approved Vitest dependency/config and `npm test`/`npm run typecheck` scripts, extend the listed adapter/type extension points, and wire `apps/web/src/app-v2/pages/TeamPage.tsx` only through adapter data; that page must not import `apps/web/src/lib/api.ts` or call `fetch`. Run frontend tests and `npm run build`; expected `PASS` and successful build. Record temporary SQLite and `apps/web/dist/build-info` writes as verification side effects.
6. Commit `feat(v3-t01): add tenant scoped user list tracer bullet` after Sol diff review.

**Inline verification:** cross-tenant fixture asserts tenant B id and email never occur in tenant A body; `rg -n "src/lib/api|fetch\(" apps/web/src/app-v2/pages/TeamPage.tsx` exits 1.

**Risk/Rollback:** stale existing user rows, live `me` fallback, or role escalation; block migration if tenant/role cannot be resolved, if `CUSTOMER` would gain any capability beyond `kb:read` + `qa:ask`, or if checksum mutation is attempted. Role backfill rollback is dry-run first: assigned `legacy_customer` is retained and protected, unassigned v3-only seed rows may be removed only with explicit data-loss approval; flag-off retains v3 tables and the existing POST user path. Full session lifecycle rollback remains owned by V3-T02/V3-T04.

#### V3-T02：实时角色与 policy_version 刷新（当前 pending）

**Goal:** 修改用户角色后，下一次 refresh/`GET /me` 返回 live role、capabilities 和递增 policy version。

**Spec refs:** FR-004, FR-005, FR-039, FR-042; §3 D3, §6.2, §7.1

**Depends on:** V3-T01；**Slice shape:** vertical；**Idempotent:** yes；**TDD:** yes — new-feature

**Files:** Create `apps/api/ekb_api/services/v3_policy.py`, `apps/api/tests/v3/test_policy_refresh.py`, `apps/web/src/app-v2/adapters/v3IdentityPolicy.ts`, `apps/web/src/app-v2/tests/v3.policy.test.ts`; Modify `apps/api/ekb_api/core/auth.py`, `apps/api/ekb_api/core/authorization.py`, `apps/api/ekb_api/routers/auth.py`, `apps/api/ekb_api/routers/me.py`, `apps/web/src/app-v2/adapters/auth.ts`, `apps/web/src/app-v2/pages/TeamPage.tsx`.

**Steps:**

1. Test creates member token at policy 1, assigns auditor role, then calls refresh and `/me`; expected FAIL because capability remains old.
2. Run `EKB_ENV=test /Users/alin/EKB/.venv/bin/pytest -q tests/v3/test_policy_refresh.py`; expected `FAIL: expected policy_version=2`.
3. Implement DB role resolution/cache key invalidation; refresh resolves live membership and `/me` maps live response without changing old required fields. Update `tenants.policy_version` and the role change in one atomic transaction; add the concurrent assignment test proving increments are not lost.
4. Run /Users/alin/EKB/.venv/bin/pytest plus `cd /Users/alin/EKB/apps/web && npm run build`; expected `1 passed`, build 0.
5. Commit `feat(v3-t02): refresh live tenant capabilities`.

**Inline verification:** assert old token cannot write after policy change; assert tenant B policy never appears; browser refresh shows changed role without re-login.

**Risk/Rollback:** policy cache race; disable v3 role writes and use existing role path while preserving new tables.

#### V3-T03：内置/自定义角色管理（当前 pending）

**Goal:** Team role tab can list built-ins, create/update/delete custom roles, refuses immutable/assigned/system-protected roles, and labels `legacy_customer` as `Customer (legacy compatibility)` without changing its exact capabilities.

**Spec refs:** FR-004, FR-005, FR-033, FR-040; §5.2, §6.2

**Depends on:** V3-T02；**Idempotent:** yes；**TDD:** yes — new-feature

**Files:** Create `apps/api/tests/v3/test_roles.py`, `apps/web/src/app-v2/adapters/v3Roles.ts`, `apps/web/src/app-v2/tests/v3.roles.test.ts`; Modify `apps/api/ekb_api/services/v3_identity.py`, `apps/api/ekb_api/routers/identity_v3.py`, `apps/web/src/app-v2/types/v3Identity.ts`, `apps/web/src/app-v2/pages/TeamPage.tsx`.

**Steps:** write failing tests for built-in immutability, `legacy_customer` exact `kb:read` + `qa:ask` permissions, system-protected assigned-role deletion, custom create/update/delete and policy bump; run `EKB_ENV=test /Users/alin/EKB/.venv/bin/pytest -q tests/v3/test_roles.py tests/v3/test_legacy_role_backfill.py` expecting `FAIL`; implement service/router/adapter/UI; rerun expecting `PASS`; run `npm run build` expecting 0; commit `feat(v3-t03): add tenant role management`.

**Inline verification:** response permissions are exact allowlisted strings; `legacy_customer` shows the compatibility label, cannot be deleted while assigned, and never exposes audit capability; deleting an assigned role returns 409 and leaves assignment intact; UI never sends a built-in/system-protected delete.

**Risk/Rollback:** capability escalation; reject unknown capabilities against registry and roll back only custom role rows via flag-off.

#### V3-T04：profile、password、notification/preferences（当前 pending）

**Goal:** Profile page edits persisted profile/preferences, lists/revokes device sessions, and changes password with explicit reauthentication semantics.

**Spec refs:** FR-006, FR-033, FR-044, FR-047; §5.2, §6.2, §7.2, §9.2

**Depends on:** V3-T02；**Idempotent:** profile/preferences yes, password no — retry requires current password and no duplicate audit；**TDD:** yes — new-feature

**Files:** Create `apps/api/ekb_api/routers/profile_v3.py`, `apps/api/ekb_api/services/v3_profile.py`, `apps/api/ekb_api/services/v3_sessions.py`, `apps/api/tests/v3/test_profile.py`, `apps/api/tests/v3/test_auth_sessions.py`, `apps/web/src/app-v2/adapters/v3Profile.ts`, `apps/web/src/app-v2/adapters/v3Sessions.ts`, `apps/web/src/app-v2/tests/v3.profile.test.ts`, `apps/web/src/app-v2/tests/v3.sessions.test.ts`; Modify `apps/web/src/app-v2/pages/ProfilePage.tsx`, `apps/web/src/app-v2/AppV2.tsx`.

**Steps:** failing tests assert profile reload, session list/revoke ownership, two active sessions, wrong-password 401, password values absent from response/audit, and password change revoking all existing sessions including the current request's session; run `EKB_ENV=test /Users/alin/EKB/.venv/bin/pytest -q tests/v3/test_profile.py tests/v3/test_auth_sessions.py` expecting `FAIL`; implement `GET /me/sessions`, `DELETE /me/sessions/{session_id}`, `revoke_all=true`, JSON-body refresh session updates and profile UI controls; rerun expecting `5 passed`; run `npm run build`; commit `feat(v3-t04): persist profile and session security`.

**Inline verification:** inspect audit JSON and assert no password/secret keys or values; browser hard reload keeps updated display name and preference.

**Risk/Rollback:** session invalidation could strand user; keep password hash update and session revocation transactional, return `session_reauth_required=true`, retain existing JSON-body logout/refresh compatibility, and flag-off only the new session UI if the security tests fail.

#### V3-T05：API key create/list/revoke/auth（当前 pending）

**Goal:** User can create one API key, see it once, authenticate with it, list prefix only and revoke it.

**Spec refs:** FR-007, FR-033, FR-040, FR-044; §3 D4, §6.1, §6.2, §7.2

**Depends on:** V3-T02；**Idempotent:** create no, recovery is revoke orphan by idempotency key; list/revoke yes；**TDD:** yes — new-feature

**Files:** Create `apps/api/ekb_api/services/v3_api_keys.py`, `apps/api/tests/v3/test_api_keys.py`, `apps/web/src/app-v2/adapters/v3ApiKeys.ts`, `apps/web/src/app-v2/tests/v3.api-keys.test.ts`; Modify `apps/api/ekb_api/core/auth.py`, `apps/api/ekb_api/routers/profile_v3.py`, `apps/web/src/app-v2/pages/ProfilePage.tsx`.

**Steps:** failing tests create key, assert one-time secret, globally unique prefix collision retry, Bearer/ApiKey auth without tenant header, revoke rejection and constant-time verification path; run `EKB_ENV=test /Users/alin/EKB/.venv/bin/pytest -q tests/v3/test_api_keys.py` expecting `FAIL`; implement prefix/hash/live role flow; rerun expecting `5 passed`; run secret scan and build; commit `feat(v3-t05): add globally scoped hashed api key lifecycle`.

**Inline verification:** `rg -n "secret_hash|prefix|ApiKey"` may show code but response assertions prove only prefix after create; test database contains hash not full key.

**Risk/Rollback:** auth parser ambiguity; keep JWT Bearer branch unchanged and disable only API-key creation flag if regression occurs.

#### V3-T06：邀请、状态、角色分配和导出闭环（当前 pending）

**Goal:** Team page invitation and export actions update user list and return safe, tenant-scoped data.

**Spec refs:** FR-001–FR-005, FR-033, FR-040, FR-042, FR-044; §6.2, §7.1

**Depends on:** V3-T01–V3-T05；**Idempotent:** invite via email+tenant idempotency key; role/status updates yes；**TDD:** yes — new-feature

**Files:** Create `apps/api/tests/v3/test_team_lifecycle.py`, `apps/web/src/app-v2/adapters/v3Team.ts`, `apps/web/src/app-v2/tests/v3.team.test.ts`; Modify `apps/api/ekb_api/routers/identity_v3.py`, `apps/api/ekb_api/services/v3_identity.py`, `apps/web/src/app-v2/pages/TeamPage.tsx`, `apps/web/src/app-v2/types/v3Identity.ts`.

**Steps:** failing test invites user, asserts list visibility, status suspend, role assignment policy bump and export safe columns; run `EKB_ENV=test /Users/alin/EKB/.venv/bin/pytest -q tests/v3/test_team_lifecycle.py` expecting `FAIL`; implement adapter/forms; rerun expecting `4 passed`; run `npm run build`; commit `feat(v3-t06): complete team lifecycle`.

**Inline verification:** export contains `id,email,display_name,role,status` and not `password_hash,secret_hash,ciphertext`; browser sees invited user only after server reload.

**Risk/Rollback:** duplicate email semantics; return 409 and leave existing user unchanged, then flag-off v3 invite if needed.

### Phase 2：知识库组织、批量、分享、收藏与回收站（V3-T07–V3-T12）

阶段目标：Knowledge/Documents/Recycle 三页可以真实整理、授权、删除和恢复资源；完成后可独立灰度。

#### V3-T07：folders hierarchy vertical slice（当前 pending）

**Goal:** Knowledge page can create/list/move one folder and render hierarchy from server.

**Spec refs:** FR-008, FR-030, FR-042; §5.2 DDL, §6.3 folder contracts, §10 cycle edge case

**Depends on:** V3-T01；**Slice shape:** first content vertical slice owns `v3_002_content` only；**Idempotent:** create with Idempotency-Key；**TDD:** yes — new-feature

**Files:** Create `apps/api/ekb_api/migrations/v3_002_content.py`, `apps/api/ekb_api/services/v3_folders.py`, `apps/api/ekb_api/routers/knowledge_v3.py`, `apps/api/tests/v3/test_folders.py`, `apps/api/tests/v3/test_migration_v3_002.py`, `apps/web/src/app-v2/adapters/v3Knowledge.ts`, `apps/web/src/app-v2/components/knowledge/FolderTree.tsx`, `apps/web/src/app-v2/tests/v3.folders.test.ts`; Modify `apps/web/src/app-v2/pages/KnowledgePage.tsx`, `apps/api/ekb_api/main.py`.

**Steps:** write failing tests for create/list/cycle and a migration test asserting only `v3_002_content` objects, additive nullable `documents.file_size/object_ref`, inferred deletion backfill and idempotency/version preflight; run `EKB_ENV=test /Users/alin/EKB/.venv/bin/pytest -q tests/v3/test_folders.py tests/v3/test_migration_v3_002.py` expecting `FAIL`; implement this version's migration/transaction/ancestor guard/adapter/tree; rerun expecting `5 passed`; build; commit `feat(v3-t07): add content migration and folder hierarchy`.

**Inline verification:** cycle attempt leaves parent unchanged; tenant B folder is absent; browser refresh keeps folder.

**Risk/Rollback:** recursive query cost or duplicate legacy idempotency rows; cap depth at 32, block preflight rather than merge, keep `v3_002_content` checksum immutable, and flag-off folder write while preserving existing docs.

#### V3-T08：server-side documents filter/search/sort/pagination（当前 pending）

**Goal:** Documents page uses the new paginated endpoint and server query state, while content governance guarantees optional file metadata, version increments and changed diff semantics.

**Spec refs:** FR-009, FR-032, FR-041, NFR-004; §6.3, §5.4

**Depends on:** V3-T07；**Idempotent:** yes；**TDD:** yes — new-feature

**Files:** Create `apps/api/tests/v3/test_document_pagination.py`, `apps/api/tests/v3/test_document_versions.py`, `apps/web/src/app-v2/adapters/v3Documents.ts`, `apps/web/src/app-v2/tests/v3.documents.test.ts`; Modify `apps/api/ekb_api/services/v3_documents.py`, `apps/api/ekb_api/routers/knowledge_v3.py`, `apps/api/ekb_api/schemas.py`, `apps/web/src/app-v2/pages/DocumentsPage.tsx`, `apps/web/src/app-v2/components/documents/DocumentTable.tsx`.

**Steps:** failing fixtures assert filter/sort/cursor no duplicates and envelope, `file_size/object_ref` optional null mapping, concurrent ingest version increment under unique `(tenant_id,doc_id,version)`, and `changed[]` alongside legacy diff arrays; run `EKB_ENV=test /Users/alin/EKB/.venv/bin/pytest -q tests/v3/test_document_pagination.py tests/v3/test_document_versions.py` expecting `FAIL`; implement indexed query, version transaction and adapter; rerun expecting `7 passed`; run build and EXPLAIN verify; commit `feat(v3-t08): add paginated document and version contracts`.

**Inline verification:** same cursor/query returns stable order; query with page_size 101 returns 400; browser hard reload restores query and selected KB.

**Risk/Rollback:** old list response or diff client break; preserve existing arrays and add `changed`/optional metadata, use new endpoint only under flag, keep `/kb/{id}/docs` untouched, and never mutate an applied `v3_002_content` checksum.

#### V3-T09：tags、favorites、batch operations（当前 pending）

**Goal:** user can tag/favorite KB/document/conversation and batch move/tag/trash with per-item result.

**Spec refs:** FR-010, FR-011, FR-013, FR-030, FR-032; §5.2 D5, §6.3

**Depends on:** V3-T07–V3-T08；**Idempotent:** favorite/batch operations yes；**TDD:** yes — new-feature

**Files:** Create `apps/api/ekb_api/services/v3_resources.py`, `apps/api/tests/v3/test_tags_favorites_batch.py`, `apps/web/src/app-v2/adapters/v3ResourceActions.ts`, `apps/web/src/app-v2/tests/v3.resources.test.ts`; Modify `apps/api/ekb_api/routers/knowledge_v3.py`, `apps/web/src/app-v2/pages/KnowledgePage.tsx`, `apps/web/src/app-v2/pages/DocumentsPage.tsx`, `apps/web/src/app-v2/pages/AssistantPage.tsx`.

**Steps:** failing tests assert unique favorites, authorization-filtered lists, mixed batch result and retry idempotence; run `EKB_ENV=test /Users/alin/EKB/.venv/bin/pytest -q tests/v3/test_tags_favorites_batch.py` expecting `FAIL`; implement service/router/adapter; rerun expecting `4 passed`; build; commit `feat(v3-t09): add tags favorites and batch actions`.

**Inline verification:** cross-tenant resource id returns safe 404; duplicate favorite does not create two rows; UI refreshes from response.

**Risk/Rollback:** batch partial semantics; preserve per-item result contract and disable only affected action if tests fail.

#### V3-T10：document sharing and overview/health（当前 pending）

**Goal:** admins share a document and view real overview/health numbers.

**Spec refs:** FR-012, FR-013, FR-030, FR-040, FR-042; §6.3 shares/overview, §7.1

**Depends on:** V3-T08–V3-T09；**Idempotent:** share PUT yes；**TDD:** yes — new-feature

**Files:** Create `apps/api/ekb_api/services/v3_sharing.py`, `apps/api/tests/v3/test_document_sharing_overview.py`, `apps/web/src/app-v2/adapters/v3Sharing.ts`, `apps/web/src/app-v2/components/knowledge/ShareDialog.tsx`, `apps/web/src/app-v2/tests/v3.sharing.test.ts`; Modify `apps/web/src/app-v2/pages/KnowledgePage.tsx`, `apps/web/src/app-v2/components/documents/DocumentTable.tsx`.

**Steps:** failing tests grant/revoke share, enforce subject ACL and assert overview counts from rows; run /Users/alin/EKB/.venv/bin/pytest expecting `FAIL`; implement; rerun expecting `3 passed`; build; commit `feat(v3-t10): add document sharing and health overview`.

**Inline verification:** user without `kb:share` gets 403; overview count changes only after committed share/tag/doc rows; audit metadata excludes content.

**Risk/Rollback:** expensive aggregate; use bounded 90-day/recent queries and cached `generated_at`, keep old KB overview untouched under flag.

#### V3-T11：trash list/restore/purge/clear（当前 pending）

**Goal:** Recycle page completes 30-day retention, restore conflict, purge and clear with audit retention.

**Spec refs:** FR-014, FR-035, FR-036, FR-040, FR-047; §4.3, §5.2, §6.3, §10

**Depends on:** V3-T07–V3-T10；**Idempotent:** restore/purge/clear keyed and state checked；**TDD:** yes — new-feature

**Files:** Create `apps/api/ekb_api/services/v3_trash.py`, `apps/api/ekb_api/routers/trash_v3.py`, `apps/api/tests/v3/test_trash.py`, `apps/web/src/app-v2/adapters/v3Trash.ts`, `apps/web/src/app-v2/components/knowledge/TrashPanel.tsx`, `apps/web/src/app-v2/tests/v3.trash.test.ts`; Modify `apps/web/src/app-v2/pages/RecyclePage.tsx`, `apps/web/src/app-v2/pages/KnowledgePage.tsx`, `apps/web/src/app-v2/pages/DocumentsPage.tsx`.

**Steps:** failing tests delete KB/document/folder, list, restore child with deleted parent (409), purge and query audit retained; run /Users/alin/EKB/.venv/bin/pytest expecting `FAIL`; implement; rerun expecting `5 passed`; build; commit `feat(v3-t11): add audited recycle lifecycle`.

**Inline verification:** expired row is clearable; active row cannot be accidentally purged without confirm; browser displays `PARENT_DELETED` recovery CTA.

**Risk/Rollback:** restore cascade inconsistency; transactionally restore only valid descendants and flag-off purge while preserving trash rows.

#### V3-T12：Knowledge/Documents/Recycle integration slice（当前 pending）

**Goal:** three pages consume only v3 adapters for all core actions and survive hard reload.

**Spec refs:** FR-029–FR-032, FR-035, FR-037, FR-047–FR-048; §9.2, §11.4

**Depends on:** V3-T07–V3-T11；**Idempotent:** yes；**TDD:** yes — new-feature

**Files:** Modify `apps/web/src/app-v2/AppV2.tsx`, `apps/web/src/app-v2/pages/KnowledgePage.tsx`, `apps/web/src/app-v2/pages/DocumentsPage.tsx`, `apps/web/src/app-v2/pages/RecyclePage.tsx`, `apps/web/src/app-v2/adapters/index.ts`, `apps/web/src/app-v2/types/index.ts`; Create `apps/web/src/app-v2/tests/v3.knowledge-pages.test.ts`, `apps/web/src/app-v2/fixtures/visual/v3-knowledge.ts`.

**Steps:** add failing adapter-only/import/state tests and an in-app Browser route spec; run `cd /Users/alin/EKB/apps/web && npm run build` expecting test assertions FAIL; wire real adapters and query URL state; rerun build and the approved in-app Browser workflow expecting PASS; commit `feat(v3-t12): connect knowledge governance pages`.

**Inline verification:** `! rg -n "src/lib/api|fetch\(" apps/web/src/app-v2/pages apps/web/src/app-v2/components apps/web/src/app-v2/layouts`; 1440x1000 and 390x844 screenshots contain no core disabled action.

**Risk/Rollback:** UI cutover before backend flag; keep route-level v3 flag and the last new-UI slice/API compatibility boundary, never an old UI restoration.

### Phase 3：助手项目、附件、模型与 web search（V3-T13–V3-T18）

阶段目标：Assistant 的项目/附件/模型/深度思考和联网搜索与 SSE v2 形成真实、可审计的发送流程。

#### V3-T13：projects 与 conversation favorites（当前 pending）

**Goal:** Assistant side rail creates/assigns/favorites projects and persists conversation membership.

**Spec refs:** FR-011, FR-015, FR-031, FR-037; §6.4, §9.2

**Depends on:** V3-T09, V3-T02；**Slice shape:** first assistant vertical slice owns `v3_003_assistant` only；**Idempotent:** project create keyed；**TDD:** yes — new-feature

**Files:** Create `apps/api/ekb_api/migrations/v3_003_assistant.py`, `apps/api/ekb_api/services/v3_projects.py`, `apps/api/ekb_api/routers/assistant_v3.py`, `apps/api/tests/v3/test_projects.py`, `apps/api/tests/v3/test_project_members.py`, `apps/api/tests/v3/test_migration_v3_003.py`, `apps/api/tests/v3/test_message_citations.py`, `apps/web/src/app-v2/adapters/v3Assistant.ts`, `apps/web/src/app-v2/components/assistant/ProjectPicker.tsx`, `apps/web/src/app-v2/components/assistant/ProjectMembersDialog.tsx`, `apps/web/src/app-v2/tests/v3.projects.test.ts`; Modify `apps/web/src/app-v2/pages/AssistantPage.tsx`.

**Steps:** failing tests create/update/delete/assign project, list/add/update-role/remove project members, favorite conversation and list only authorized rows; migration test asserts only `v3_003_assistant` objects including `message_citations` and no mutation of `v3_001`/`v3_002` checksum; run /Users/alin/EKB/.venv/bin/pytest expecting `FAIL`; implement this version plus service/router/adapter/member dialog; rerun expecting `8 passed`; build; commit `feat(v3-t13): add assistant migration projects and members`.

**Inline verification:** deleting project removes relation not conversation; unauthorized project assignment returns 403; hard reload shows assignment.

**Risk/Rollback:** relation cleanup; transactional delete and flag-off project writes.

#### V3-T14：authorized attachments（当前 pending）

**Goal:** user attaches an existing authorized document or uploads a document to the current conversation and sees server state.

**Spec refs:** FR-016, FR-031, FR-042, FR-044; §6.4 attachments, §7.1

**Depends on:** V3-T08, V3-T11, V3-T13；**Idempotent:** attachment add keyed；**TDD:** yes — new-feature

**Files:** Create `apps/api/ekb_api/services/v3_attachments.py`, `apps/api/tests/v3/test_attachments.py`, `apps/web/src/app-v2/adapters/v3Attachments.ts`, `apps/web/src/app-v2/components/assistant/AttachmentList.tsx`, `apps/web/src/app-v2/tests/v3.attachments.test.ts`; Modify `apps/api/ekb_api/routers/assistant_v3.py`, `apps/web/src/app-v2/pages/AssistantPage.tsx`.

**Steps:** failing tests attach authorized doc, reject cross-tenant/removed ACL, upload size/type errors; run /Users/alin/EKB/.venv/bin/pytest expecting `FAIL`; implement; rerun expecting `4 passed`; build; commit `feat(v3-t14): add authorized assistant attachments`.

**Inline verification:** attachment list has no raw credentials/content; revoked doc is omitted before citation; browser shows retryable 413/403 message.

**Risk/Rollback:** upload duplication; reuse existing idempotency and old upload path; flag-off attachment option only.

#### V3-T15：tenant model options and deep thinking（当前 pending）

**Goal:** Assistant exposes only tenant-routed models and sends auditable deep-thinking option.

**Spec refs:** FR-017, FR-031, FR-045; §6.4, §8

**Depends on:** V3-T02, V3-T13；**Idempotent:** model seed/upsert；**TDD:** yes — new-feature

**Files:** Create `apps/api/ekb_api/services/v3_model_options.py`, `apps/api/tests/v3/test_model_capabilities.py`, `apps/web/src/app-v2/adapters/v3AssistantCapabilities.ts`, `apps/web/src/app-v2/tests/v3.model-options.test.ts`; Modify `apps/api/ekb_api/routers/qa.py`, `apps/api/ekb_api/schemas.py`, `apps/web/src/app-v2/pages/AssistantPage.tsx`, `apps/web/src/app-v2/adapters/qaStream.ts`.

**Steps:** failing tests assert allowed model, stale/forbidden model 409, deep-thinking payload and redacted audit; run /Users/alin/EKB/.venv/bin/pytest expecting `FAIL`; implement additive schemas and adapter; rerun expecting `3 passed`; build; commit `feat(v3-t15): add tenant model capabilities`.

**Inline verification:** UI cannot choose model absent from server capabilities; request body has boolean only, no secret/provider key.

**Risk/Rollback:** upstream provider route mismatch; keep existing default route and reject only non-allowed selection.

#### V3-T16：configured web search provider（当前 pending）

**Goal:** Web search is a truthful capability with explicit provider config/flag and no fake result.

**Spec refs:** FR-018, FR-045, FR-046, NFR-015; §3 D8/D11, §6.4, §7.2, §8

**Depends on:** V3-T15, migration；**Idempotent:** provider configure upsert；**TDD:** yes — new-feature

**Files:** Create `apps/api/ekb_api/services/v3_web_search.py`, `apps/api/ekb_api/routers/assistant_v3.py`, `apps/api/tests/v3/test_web_search_capability.py`, `apps/api/tests/v3/test_web_search_provider_lifecycle.py`, `apps/web/src/app-v2/adapters/v3AssistantCapabilities.ts`, `apps/web/src/app-v2/components/assistant/WebSearchProviderSettings.tsx`, `apps/web/src/app-v2/tests/v3.web-search.test.ts`; Modify `apps/api/ekb_api/core/config.py`, `apps/api/ekb_api/core/audit.py`, `apps/web/src/app-v2/pages/AssistantPage.tsx`.

**Steps:** failing tests cover flag off, provider absent 409, provider list/configure/disable/rotate, owner/tenant permission, active/previous key-version redaction and provider error; run /Users/alin/EKB/.venv/bin/pytest expecting `FAIL`; implement Fernet config and explicit provider adapter; rerun expecting `8 passed`; run secret regression and build; commit `feat(v3-t16): add truthful web search provider lifecycle`.

**Inline verification:** provider test double returns a real marked result only when configured; no result path can return fixture/static data; production without key fails closed.

**Risk/Rollback:** external provider outage; keep `EKB_WEB_SEARCH_ENABLED=false`, record non-blocking provider incident only after internal tests pass.

#### V3-T17：SSE v2 additive ask extension（当前 pending）

**Goal:** Assistant sends project/attachment/model/deep/web options while preserving turn/seq/cancel/citations/feedback.

**Spec refs:** FR-019, FR-031, FR-045, FR-047; §4.4, §6.4, §10 cancel race

**Depends on:** V3-T14–V3-T16；**Idempotent:** ask uses existing turn semantics；**TDD:** yes — new-feature

**Files:** Create `apps/api/tests/v3/test_qa_v3_stream_contract.py`, `apps/api/tests/v3/test_qa_actor_tenant_scope.py`, `apps/api/tests/v3/test_quota_sqlite_concurrency.py`, `apps/web/src/app-v2/tests/v3.qa-stream.test.ts`; Modify `apps/api/ekb_api/schemas.py`, `apps/api/ekb_api/routers/qa.py`, `apps/api/ekb_api/store.py`, `apps/web/src/lib/api.ts`, `apps/web/src/app-v2/adapters/qaStream.ts`, `apps/web/src/app-v2/pages/AssistantPage.tsx`.

**Steps:** write red tests for request optional fields, duplicate/out-of-order/stale turn, actor+tenant protected get/cancel/is_cancelled/seq/terminal updates, independent cancel audit, pre-token/partial cancel, durable `message_citations` write/read and hard reload; add SQLite concurrent quota fixture proving atomic count/no double acceptance; run `EKB_ENV=test /Users/alin/EKB/.venv/bin/pytest -q tests/v3/test_qa_v3_stream_contract.py tests/v3/test_qa_actor_tenant_scope.py tests/v3/test_quota_sqlite_concurrency.py` expecting `FAIL`; implement additive fields and parser assertions. Do not add Last-Event-ID replay storage in this slice; if a later implementation proposes it, it requires a separate replay table/contract/test; rerun expecting `11 passed`; run `EKB_ENV=test /Users/alin/EKB/.venv/bin/pytest -q tests/test_fault_injection.py` and build; commit `feat(v3-t17): extend sse v2 actor-scoped assistant contract`.

**Inline verification:** old stream fixture still passes; every visible citation is from server payload; cancel audit contains turn id, actor and tenant with no question secret; a different actor in the same tenant and any actor in another tenant cannot read/cancel/update the turn.

**Risk/Rollback:** provider-specific error; retain `stream_version=2` and flag-off optional controls, never fall back to fake answer.

#### V3-T18：Assistant page full closure（当前 pending）

**Goal:** Assistant page replaces disabled core controls with real project/attachment/model/deep/web/search/feedback states and maps rename/archive to the existing `PATCH /conversations/{id}` contract.

**Spec refs:** FR-015–FR-019, FR-031, FR-037, FR-047–FR-048; §9.2, §11.4

**Depends on:** V3-T13–V3-T17；**Idempotent:** UI actions use backend idempotency；**TDD:** yes — new-feature

**Files:** Modify `apps/web/src/app-v2/pages/AssistantPage.tsx`, `apps/web/src/app-v2/components/assistant/*.tsx`, `apps/web/src/app-v2/adapters/index.ts`, `apps/web/src/app-v2/types/*.ts`; Create `apps/web/src/app-v2/tests/v3.assistant-page.test.ts`.

**Steps:** red route spec asserts project assignment, attachment, stream option, cancel, feedback, rename and archive flow; run `npm run build` plus browser test expecting FAIL; wire state machines and refresh using existing `PATCH /conversations/{id}` (no duplicate rename endpoint); rerun build/browser expecting PASS; commit `feat(v3-t18): complete assistant fullstack journey`.

**Inline verification:** pre-provider web search copy says configuration required, not success; 390x844 composer/cancel remains reachable; console 0.

**Risk/Rollback:** dense three-column responsive regression; keep layout flag and route-level rollback.

### Phase 4：Dashboard 聚合与访问分析（V3-T19–V3-T24）

阶段目标：Dashboard/Analytics 读取真实 bounded access events and aggregate，且租户/权限/性能门禁可证实。

#### V3-T19：resource-access event persistence（当前 pending）

**Goal:** high-value resource reads write bounded tenant events without blocking non-critical responses.

**Spec refs:** FR-022, FR-040, FR-042, NFR-005, NFR-014; §5.2/5.4, §7.3

**Depends on:** V3-T02；**Slice shape:** first analytics vertical slice owns `v3_004_analytics` only, then hooks service reads；**Idempotent:** event id/trace dedupe；**TDD:** yes — new-feature

**Files:** Create `apps/api/ekb_api/migrations/v3_004_analytics.py`, `apps/api/ekb_api/services/v3_access_events.py`, `apps/api/tests/v3/test_access_events.py`, `apps/api/tests/v3/test_migration_v3_004.py`, `apps/api/scripts/prune_v3_access_events.py`; Modify `apps/api/ekb_api/store.py`, `apps/api/ekb_api/routers/kb.py`, `apps/api/ekb_api/routers/qa.py`, `apps/api/ekb_api/routers/admin.py`, `apps/api/ekb_api/core/metrics.py`.

**Steps:** red tests assert tenant/resource/action/result fields, no content, retention prune and non-critical failure metric; migration test asserts only `v3_004_analytics` table/indexes and prior checksums unchanged; run /Users/alin/EKB/.venv/bin/pytest expecting `FAIL`; implement this version, event writer and hooks; rerun expecting `5 passed`; run /Users/alin/EKB/.venv/bin/ruff; commit `feat(v3-t19): add analytics migration and bounded access events`.

**Inline verification:** event write failure does not suppress authorized document read but increments metric; audit-sensitive write still fails closed.

**Risk/Rollback:** event volume; cap writes to configured actions, prune by indexed date and flag-off only event hook.

#### V3-T20：dashboard summary aggregate（当前 pending）

**Goal:** Dashboard gets one date-bounded aggregate response with real metrics and health signals.

**Spec refs:** FR-020, FR-029, FR-034, NFR-004; §6.5, §5.4

**Depends on:** V3-T19；**Idempotent:** read；**TDD:** yes — new-feature

**Files:** Create `apps/api/ekb_api/services/v3_analytics.py`, `apps/api/ekb_api/routers/analytics_v3.py`, `apps/api/tests/v3/test_dashboard_summary.py`, `apps/api/tests/v3/test_ops_dashboard_days.py`, `apps/web/src/app-v2/adapters/v3Analytics.ts`, `apps/web/src/app-v2/tests/v3.analytics-summary.test.ts`; Modify `apps/api/ekb_api/routers/admin.py`, `apps/api/ekb_api/store.py`, `apps/api/ekb_api/main.py`, `apps/web/src/app-v2/pages/DashboardPage.tsx`.

**Steps:** red fixture asserts metrics derive from seeded QA/access/doc rows and 90-day cap; compatibility fixture calls existing `GET /admin/ops/dashboard?days=1` and `days=90` with boundary/outside rows and expects true date filtering; run /Users/alin/EKB/.venv/bin/pytest expecting `FAIL`; implement bounded aggregate and route existing ops dashboard through the corrected date filter without changing its required response fields; rerun expecting `5 passed`; build; commit `feat(v3-t20): add dashboard summary and days compatibility`.

**Inline verification:** no design稿 static numbers appear when no rows; 403 body has safe error; response carries `generated_at` and requested range.

**Risk/Rollback:** aggregate joins or old ops shape drift; use transaction snapshot and indexes, retain old response fields, flag-off only v3 aggregate reads after the 1/90-day compatibility suite passes.

#### V3-T21：timeseries/distribution/activity（当前 pending）

**Goal:** Analytics page renders date-range timeseries, distribution and recent activity from real rows.

**Spec refs:** FR-020, FR-021, FR-034, FR-041; §6.5, §9.2

**Depends on:** V3-T20；**Idempotent:** read；**TDD:** yes — new-feature

**Files:** Modify `apps/api/ekb_api/services/v3_analytics.py`, `apps/api/ekb_api/routers/analytics_v3.py`; Create `apps/api/tests/v3/test_analytics_shapes.py`, `apps/web/src/app-v2/components/analytics/*.tsx`, `apps/web/src/app-v2/tests/v3.analytics-shapes.test.ts`; Modify `apps/web/src/app-v2/pages/AnalyticsPage.tsx`.

**Steps:** red tests seed sparse days and assert zero-filled time buckets, safe distribution keys and cursor activity; run /Users/alin/EKB/.venv/bin/pytest expecting `FAIL`; implement; rerun expecting `4 passed`; build; commit `feat(v3-t21): add analytics trend and activity views`.

**Inline verification:** activity rows are tenant-filtered; range over 90 days returns 400; sparse range does not invent non-zero data.

**Risk/Rollback:** privacy leakage in labels; mask actor and limit resource labels by permission.

#### V3-T22：recent access/tags/health interactions（当前 pending）

**Goal:** Dashboard click-throughs and health/tag panels use server-authorized resources.

**Spec refs:** FR-021, FR-029, FR-034, FR-042; §6.5, §9.2

**Depends on:** V3-T20–V3-T21, V3-T10；**Idempotent:** read；**TDD:** yes — new-feature

**Files:** Create `apps/api/tests/v3/test_recent_access_authorization.py`, `apps/web/src/app-v2/components/dashboard/AccessActivityPanel.tsx`, `apps/web/src/app-v2/tests/v3.dashboard-links.test.ts`; Modify `apps/web/src/app-v2/pages/DashboardPage.tsx`, `apps/web/src/app-v2/pages/AnalyticsPage.tsx`, `apps/web/src/app-v2/adapters/v3Analytics.ts`.

**Steps:** red test revokes resource ACL after event and asserts link returns no resource; run /Users/alin/EKB/.venv/bin/pytest expecting `FAIL`; implement reauthorization and adapter state; rerun expecting `2 passed`; build/browser; commit `feat(v3-t22): authorize dashboard resource links`.

**Inline verification:** clicking a stale event produces recoverable permission message; no raw event id is treated as resource authority.

**Risk/Rollback:** stale event UX; retain event but hide link and display current permission state.

#### V3-T23：analytics performance/security gate（当前 pending）

**Goal:** prove indexed bounded queries, tenant isolation and p95 threshold before page rollout.

**Spec refs:** FR-023, FR-040, FR-042, NFR-001, NFR-004, NFR-005; §5.4, §11.1

**Depends on:** V3-T19–V3-T22；**Slice shape:** verification vertical; **Idempotent:** yes；**TDD:** yes — new-feature

**Files:** Create `apps/api/tests/v3/test_analytics_perf.py`, `apps/api/scripts/benchmark_v3_analytics.py`, `docs/pmos/features/2026-08-09_ekb-fullstack-v3/evidence/` during implementation only.

**Steps:** add red benchmark assertions for 1,000,000 events per tenant/90d/50 RPS and EXPLAIN index use; run `EKB_ENV=test /Users/alin/EKB/.venv/bin/pytest -q tests/v3/test_analytics_perf.py` expecting FAIL; tune query/index only within spec; rerun expecting `p95_ms<=500`; run retention cleanup and security suite; commit `test(v3-t23): gate analytics capacity and performance`.

**Inline verification:** report includes dataset size, p50/p95, query plan and cross-tenant attempt count; no unbounded full scan passes.

**Risk/Rollback:** environment variance; record hardware/DB version and treat provider noise separately, but never waive tenant failure.

#### V3-T24：Dashboard/Analytics page closure（当前 pending）

**Goal:** both pages consume only analytics adapters and meet design-source responsive states.

**Spec refs:** FR-020–FR-023, FR-029, FR-034, FR-037, FR-047–FR-048; §9.2, §11.4

**Depends on:** V3-T20–V3-T23；**Idempotent:** UI reads；**TDD:** yes — new-feature

**Files:** Modify `apps/web/src/app-v2/pages/DashboardPage.tsx`, `apps/web/src/app-v2/pages/AnalyticsPage.tsx`, `apps/web/src/app-v2/components/dashboard/*`; Create `apps/web/src/app-v2/tests/v3.dashboard-pages.test.ts`.

**Steps:** red browser/adapter tests assert no mock metric, permission/empty/error/retry and date query; run `npm run build` expecting FAIL; wire real states; rerun build/browser expecting PASS; commit `feat(v3-t24): complete dashboard analytics pages`.

**Inline verification:** desktop/mobile screenshots and console log saved; all charts show server `generated_at`; no core metric uses local fixture.

**Risk/Rollback:** API response latency; keep new app-v2 loading primitive and actionable retry state during flag rollout; never use an old dashboard fallback.

### Phase 5：应用目录、凭据、连接与同步语义（V3-T25–V3-T30）

阶段目标：Apps 页真实显示六项目录，管理员可安装/配置/连接/运行/卸载，并能区分 sync downstream 状态。

#### V3-T25：exact six app catalog（当前 pending）

**Goal:** App Center lists exactly six immutable slugs with all/installed/recommended server views.

**Spec refs:** FR-024, FR-025, FR-035, FR-045; §5.2, §6.6

**Depends on:** V3-T02；**Slice shape:** first apps vertical slice owns `v3_005_apps` only；**Idempotent:** seed upsert；**TDD:** yes — new-feature

**Files:** Create `apps/api/ekb_api/migrations/v3_005_apps.py`, `apps/api/ekb_api/services/v3_apps.py`, `apps/api/ekb_api/routers/apps_v3.py`, `apps/api/tests/v3/test_app_catalog.py`, `apps/api/tests/v3/test_migration_v3_005.py`, `apps/web/src/app-v2/adapters/v3Apps.ts`, `apps/web/src/app-v2/tests/v3.apps-catalog.test.ts`; Modify `apps/web/src/app-v2/pages/AppsPage.tsx`, `apps/web/src/app-v2/AppV2.tsx`, `apps/api/ekb_api/main.py`.

**Steps:** red test asserts exact six immutable rows with slug/display_name/provider/category/capabilities/recommended_rank and no seventh catalog row; migration test asserts only `v3_005_apps` objects and unchanged prior checksums; run /Users/alin/EKB/.venv/bin/pytest expecting FAIL; seed/catalog/search/install projection; rerun expecting `5 passed`; build; commit `feat(v3-t25): add immutable app migration and catalog`.

**Inline verification:** catalog response slugs match fixed set in order-independent assertion; UI search only filters server results.

**Risk/Rollback:** design copy drift; map fixed slug/display names from design asset evidence and keep catalog flag-off.

#### V3-T26：installation/configure lifecycle（当前 pending）

**Goal:** admin installs/configures an app and sees persistent redacted status.

**Spec refs:** FR-025, FR-026, FR-040, FR-045; §6.6, §8

**Depends on:** V3-T25；**Idempotent:** install/config via key; **TDD:** yes — new-feature

**Files:** Create `apps/api/tests/v3/test_app_installations.py`, `apps/web/src/app-v2/components/apps/InstallDialog.tsx`, `apps/web/src/app-v2/tests/v3.app-install.test.ts`; Modify `apps/api/ekb_api/services/v3_apps.py`, `apps/api/ekb_api/routers/apps_v3.py`, `apps/web/src/app-v2/pages/AppsPage.tsx`, `apps/web/src/app-v2/adapters/v3Apps.ts`.

**Steps:** red test install duplicate 409/idempotent, configure readback redacted and permission denied; run /Users/alin/EKB/.venv/bin/pytest expecting FAIL; implement; rerun expecting `3 passed`; build/browser; commit `feat(v3-t26): add app installation lifecycle`.

**Inline verification:** refreshing Apps keeps `INSTALLED`; response never contains secret/ciphertext; non-admin cannot install.

**Risk/Rollback:** duplicate install; unique tenant/slug constraint and idempotency key.

#### V3-T27：Fernet credential storage/redaction（当前 pending）

**Goal:** credential create/list/revoke encrypts at rest with dedicated key and exposes only prefix.

**Spec refs:** FR-027, FR-044, NFR-008; §3 D8, §6.6, §7.2, §8

**Depends on:** V3-T26；**Idempotent:** credential upsert; **TDD:** yes — new-feature

**Files:** Create `apps/api/ekb_api/services/v3_credentials.py`, `apps/api/tests/v3/test_apps_credentials.py`, `apps/api/tests/v3/test_credential_rotation.py`, `apps/api/tests/v3/test_secret_regression.py`; Modify `apps/api/ekb_api/core/config.py`, `apps/api/ekb_api/core/audit.py`, `apps/api/ekb_api/routers/apps_v3.py`, `apps/api/pyproject.toml` only if already approved cryptography dependency is absent.

**Steps:** red tests assert production missing key fails closed, ciphertext differs from plaintext, response/audit/log redaction, active→previous rotation, bounded dual-decrypt, expired previous rejection and rollback after failed provider update; run /Users/alin/EKB/.venv/bin/pytest expecting FAIL; implement Fernet/key version; rerun expecting `9 passed`; run /Users/alin/EKB/.venv/bin/ruff; commit `feat(v3-t27): encrypt and rotate app credentials at rest`.

**Inline verification:** test scans response, DB row, audit payload and captured log for secret; no signing token secret reuse.

**Risk/Rollback:** dependency/key rotation; if dependency approval is absent, stop at Sol decision before code, not substitute plaintext; flag-off credential writes.

#### V3-T28：connect/run/uninstall state machine（当前 pending）

**Goal:** app connection and run lifecycle has explicit statuses, retry/error, audit and uninstall safety.

**Spec refs:** FR-026, FR-027, FR-028, FR-040; §4.4, §6.6, §10

**Depends on:** V3-T26–V3-T27；**Idempotent:** connect/run key and state checks；**TDD:** yes — new-feature

**Files:** Create `apps/api/ekb_api/services/v3_app_runs.py`, `apps/api/tests/v3/test_app_run_state.py`, `apps/web/src/app-v2/components/apps/ConnectionStatus.tsx`, `apps/web/src/app-v2/components/apps/RunHistory.tsx`, `apps/web/src/app-v2/tests/v3.app-runs.test.ts`; Modify `apps/api/ekb_api/routers/apps_v3.py`, `apps/web/src/app-v2/pages/AppsPage.tsx`.

**Steps:** red tests cover connect success/provider error, synchronous provider run with final `SUCCEEDED|FAILED`, retryable 504, uninstall active-run conflict and credential revoke; run the configured backend test command expecting FAIL; implement the synchronous provider boundary and state transitions without durable `QUEUED|RUNNING` rows or a worker; rerun expecting `5 passed`; build/browser; commit `feat(v3-t28): add app connect run uninstall states`.

**Inline verification:** sync status is displayed separately; retry does not create duplicate run; audit has no credential.

**Risk/Rollback:** provider timeout; return 504 with a safe retry key, keep installation state stable on failed run, and never invent durable queued/running state.

#### V3-T29：sync downstream separation（当前 pending）

**Goal:** explicit sync source/run can be attached to an installed app without changing install semantics.

**Spec refs:** FR-028, FR-035, FR-040; §6.6, §10 app run edge case

**Depends on:** V3-T28, existing admin sync；**Idempotent:** source/run existing semantics；**TDD:** yes — new-feature

**Files:** Create `apps/api/tests/v3/test_app_sync_separation.py`, `apps/web/src/app-v2/tests/v3.app-sync.test.ts`; Modify `apps/api/ekb_api/routers/admin.py`, `apps/api/ekb_api/services/v3_app_runs.py`, `apps/web/src/app-v2/adapters/v3Apps.ts`, `apps/web/src/app-v2/pages/AppsPage.tsx`.

**Steps:** red test installs app, runs without sync, then explicit source run; assert installation remains connected when sync fails; run /Users/alin/EKB/.venv/bin/pytest expecting FAIL; implement relation/status; rerun expecting `3 passed`; build; commit `feat(v3-t29): separate app install from sync runs`.

**Inline verification:** UI labels install/connect/run/sync independently; no sync success changes installation from `INSTALLED` to `CONNECTED` without connect.

**Risk/Rollback:** existing sync endpoint response drift; preserve existing schema and add relation only additively.

#### V3-T30：Apps page closure（当前 pending）

**Goal:** Apps page full design flow uses v3 adapters, exact six cards, redacted credential and real states.

**Spec refs:** FR-024–FR-028, FR-035, FR-037, FR-047–FR-048; §9.2, §11.4

**Depends on:** V3-T25–V3-T29；**Idempotent:** UI retries server-keyed；**TDD:** yes — new-feature

**Files:** Modify `apps/web/src/app-v2/pages/AppsPage.tsx`, `apps/web/src/app-v2/components/apps/*`, `apps/web/src/app-v2/adapters/index.ts`, `apps/web/src/app-v2/types/index.ts`; Create `apps/web/src/app-v2/tests/v3.apps-page.test.ts`.

**Steps:** red browser spec asserts all/installed/recommended/search/install/config/connect/run/uninstall and provider error; run build/browser expecting FAIL; wire real states; rerun expecting PASS; commit `feat(v3-t30): complete app center journey`.

**Inline verification:** only six app labels from design稿 appear; credential input clears after submit; 390x844 run status and retry remain accessible.

**Risk/Rollback:** visual catalog density; keep CSS within v2 design tokens and flag-off page if API unavailable.

### Phase 6：十页统一 adapter cutover、审计与最终 QA（V3-T31–V3-T38）

阶段目标：十页所有 core journeys 都走 adapters，旧 v2 “缺后端”占位被真实闭环替换，完成生产入口切换和清理。

#### V3-T31：统一 v3 adapter facade/state contract（当前 pending）

**Goal:** AppV2 injects typed v3 services and pages share consistent loading/error/permission/refresh semantics.

**Spec refs:** FR-037, FR-047, FR-049–FR-052, NFR-010–NFR-013; §6.3, §9

**Depends on:** V3-T06, V3-T12, V3-T18, V3-T24, V3-T30；**Idempotent:** yes；**TDD:** yes — new-feature

**Files:** Create `apps/web/src/app-v2/types/v3Services.ts`, `apps/web/src/app-v2/adapters/v3Index.ts`, `apps/web/src/app-v2/adapters/v3Shell.ts`, `apps/web/src/app-v2/components/V3StatePanel.tsx`, `apps/web/src/app-v2/components/ShellMenu.tsx`, `apps/web/src/app-v2/tests/v3.facade.test.ts`, `apps/web/src/app-v2/tests/v3.shell.test.ts`; Modify `apps/web/src/app-v2/AppV2.tsx`, `apps/web/src/app-v2/adapters/index.ts`, `apps/web/src/app-v2/types/index.ts`.

**Steps:** red type/adapter tests assert every service returns state/error/refresh and no page import; shell tests cover global authorized search, profile menu navigation, notification list/read and help/feedback submission; Dashboard quick actions are exactly `新建文档` (note dialog/create), `上传文档` (existing upload), `创建知识空间` (existing KB create), and `智能导入` (Apps `intent=import`), never alternate placeholders; run the configured `npm test` expecting FAIL; implement facade, new app-v2 stateless primitives and service injection; rerun `npm test`, `npm run typecheck`, build and static scan expecting PASS; commit `refactor(v3-t31): unify app-v2 v3 facade and shell actions`.

**Inline verification:** `rg -n "src/lib/api|fetch\(" apps/web/src/app-v2/pages apps/web/src/app-v2/components apps/web/src/app-v2/layouts` exits 1; all service methods have error mapping.

**Risk/Rollback:** type widening breaks M0–M5; keep old service fields and add optional v3 fields. Rollback is feature-flag/API compatibility to the last new-UI slice; never restore old pages/layouts/components/CSS.

#### V3-T32：Dashboard/Analytics final route states（当前 pending）

**Goal:** dashboard and analytics route tests cover loading/empty/error/permission/success/hard reload.

**Spec refs:** FR-029, FR-034, FR-047–FR-048; §9.2, §11.4

**Depends on:** V3-T24, V3-T31；**Idempotent:** read；**TDD:** yes — new-feature

**Files:** Modify `apps/web/src/app-v2/pages/DashboardPage.tsx`, `apps/web/src/app-v2/pages/AnalyticsPage.tsx`, `apps/web/src/app-v2/components/dashboard/`; Create `apps/web/src/app-v2/tests/v3.dashboard.e2e.ts`.

**Steps:** red Codex in-app Browser scenarios run on a test tenant expecting missing aggregate and missing quick-action server responses; execute the in-app Browser workflow at both viewports expecting FAIL; implement design-matching quick-action dialog, server date range, loading/empty/error/permission/retry and resource click-through; rerun the in-app Browser scenarios expecting PASS; commit `test(v3-t32): gate dashboard analytics routes and quick actions`.

**Inline verification:** output records 1440x1000 and 390x844 screenshots and console count 0.

**Risk/Rollback:** chart library/runtime error; use only new app-v2 stateless primitives and return an actionable API error state. Rollback is the last new-UI slice/feature flag, never an old component or layout.

#### V3-T33：Knowledge/Documents/Recycle final route states（当前 pending）

**Goal:** three governance routes cover folder, document, batch, share, tag, favorite and trash journeys.

**Spec refs:** FR-030, FR-032, FR-035, FR-047–FR-048; §9.2, §11.4

**Depends on:** V3-T12, V3-T31；**Idempotent:** UI commands keyed；**TDD:** yes — new-feature

**Files:** Modify `apps/web/src/app-v2/pages/KnowledgePage.tsx`, `apps/web/src/app-v2/pages/DocumentsPage.tsx`, `apps/web/src/app-v2/pages/RecyclePage.tsx`, `apps/web/src/app-v2/components/`; Create `apps/web/src/app-v2/tests/v3.knowledge.e2e.ts`.

**Steps:** write red in-app Browser scenarios for design-matching tree menus, row drawers, batch move/tag/favorite/trash, share/revoke, version/diff/retry, retention confirmation and parent conflict recovery; execute the approved in-app Browser workflow expecting FAIL; wire all adapters and reload; rerun expecting PASS; commit `test(v3-t33): gate knowledge governance routes`.

**Inline verification:** no core control is permanently disabled after its API flag is on; 409 conflict is visible and recoverable.

**Risk/Rollback:** long table interactions on mobile; use explicit responsive table mode and preserve action menu.

#### V3-T34：Assistant final route states（当前 pending）

**Goal:** assistant route browser scenario covers project, attachment, capability, stream, cancel, citation and feedback.

**Spec refs:** FR-031, FR-047–FR-048; §6.4, §9.2, §11.4

**Depends on:** V3-T18, V3-T31；**Idempotent:** stream/cancel existing；**TDD:** yes — new-feature

**Files:** Modify `apps/web/src/app-v2/pages/AssistantPage.tsx`, `apps/web/src/app-v2/components/assistant/`; Create `apps/web/src/app-v2/tests/v3.assistant.e2e.ts`.

**Steps:** red in-app Browser scenario uses controlled SSE and real API fixtures, expecting no project/attachment state; execute the approved in-app Browser workflow expecting FAIL; implement visible stream cancel, feedback reason/comment, citation hard reload and admin-only provider drawer; rerun expecting PASS; commit `test(v3-t34): gate assistant route`.

**Inline verification:** stale turn event is absent from DOM and protocol error is diagnostic; cancel endpoint result appears; citations link to authorized docs only.

**Risk/Rollback:** model/provider error; provider failure is recorded separately only after internal SSE contract passes.

#### V3-T35：Team/Profile final route states（当前 pending）

**Goal:** Team/Profile use identity v3 endpoints and prove policy/key/profile persistence.

**Spec refs:** FR-001–FR-007, FR-033, FR-044, FR-047–FR-048; §6.2, §9.2, §11.4

**Depends on:** V3-T06, V3-T31；**Idempotent:** key create/revoke semantics tested；**TDD:** yes — new-feature

**Files:** Modify `apps/web/src/app-v2/pages/TeamPage.tsx`, `apps/web/src/app-v2/pages/ProfilePage.tsx`, `apps/web/src/app-v2/components/`; Create `apps/web/src/app-v2/tests/v3.identity.e2e.ts`.

**Steps:** red in-app Browser scenario covers row-menu/detail-drawer invite/list/status/role, permission matrix, policy, profile menu, profile/password/preferences, device sessions, notifications and API key copy-once/revoke; execute the approved in-app Browser workflow expecting FAIL; wire; rerun expecting PASS; commit `test(v3-t35): gate identity profile routes`.

**Inline verification:** complete key appears once only; reload lists prefix; old session sees refreshed policy state.

**Risk/Rollback:** sensitive screenshots; evidence masks secret and uses synthetic tenant.

#### V3-T36：Apps/Module Map final route states（当前 pending）

**Goal:** Apps and Module Map match exact route/catalog mapping and no business action leaks into route manifest.

**Spec refs:** FR-024–FR-028, FR-035–FR-037, FR-048; §9.2, §11.4

**Depends on:** V3-T30–V3-T31；**Idempotent:** route read；**TDD:** yes — new-feature

**Files:** Modify `apps/web/src/app-v2/pages/AppsPage.tsx`, `apps/web/src/app-v2/pages/ModuleMapPage.tsx`, `apps/web/src/app-v2/routes.ts`, `apps/web/src/app-v2/components/apps/module-map/`; Create `apps/web/src/app-v2/tests/v3.apps-module.e2e.ts`.

**Steps:** red in-app Browser route/catalog test asserts 10 routes, exact nine module tiles, six app slugs, live capability/data states, profile/help navigation and state-only module navigation; execute the approved in-app Browser workflow expecting FAIL; implement; rerun expecting PASS; commit `test(v3-t36): gate apps module map and shell routes`.

**Inline verification:** `ROUTES.length === 10`, module links exclude self, catalog set exact; console 0 at both viewports.

**Risk/Rollback:** route regression; retain manifest-compatible route resolver and switch flag off.

#### V3-T37：production App cutover and old UI import audit（当前 pending）

**Goal:** switch production entry to AppV2, prove no old UI imports, then remove only unreferenced old UI files.

**Spec refs:** FR-038, FR-039, FR-048, NFR-010–NFR-012; §3 D13, §12

**Depends on:** V3-T32–V3-T36；**Slice shape:** cutover/config vertical exception with full browser proof；**Idempotent:** entry switch reversible；**TDD:** yes — new-feature

**Files:** Modify `apps/web/src/App.tsx`, `apps/web/src/main.tsx`; Create `apps/web/src/app-v2/tests/v3.cutover.e2e.ts`, `docs/pmos/features/2026-08-09_ekb-fullstack-v3/evidence/import-audit.txt`; Delete only after audit: unreferenced `apps/web/src/pages/**`, `apps/web/src/components/**`, old CSS/layout files.

**Steps:** write red import audit and production route in-app Browser test; run `npm run build` and the approved in-app Browser workflow expecting old entry/import failures; switch entry; rerun expected build/browser PASS; run the conditional import audit and only then delete files; rerun full build; commit `feat(v3-t37): cut over production to app-v2`.

**Inline verification:** `rg -n "from ['\"].*src/(pages|components)|styles\.css" apps/web/src` returns no production import; full route list loads after hard reload; rollback is a revert of entry-only commit, never workspace reset.

**Risk/Rollback:** shared non-UI import mistaken as old UI; stop deletion on any reference. Rollback is a reviewed entry revert plus feature flags to the last new-UI slice, never restoration of old pages/layouts/components/CSS.

#### V3-T38：Final TN evidence and matrix closure（当前 pending）

**Goal:** run all code, security, migration, browser, visual and docs gates and publish evidence.

**Spec refs:** all FR-001–FR-052 and NFR-001–NFR-018; §11–§12

**Depends on:** V3-T37；**Idempotent:** verification only；**TDD:** yes — new-feature verification

**Files:** Create implementation evidence under `docs/pmos/features/2026-08-09_ekb-fullstack-v3/evidence/`, update `04_verification-matrix.md` only after command output is captured; do not modify Skills or unrelated docs.

**Steps:** run backend tests/lint/migration verify/rollback dry-run; run frontend build/type/contract; run the Codex in-app Browser ten-route workflow at 1440x1000 and 390x844 with forced error/permission paths; run link/import/placeholder scans; expected all gates PASS; update evidence references and commit `test(v3-t38): publish fullstack verification evidence`.

**Inline verification:** final report contains exact commands, exit codes, test counts, screenshot paths, console count, P0/P1/P2 count, migration checksum and rollback statement.

**Risk/Rollback:** external model provider only; mark non-blocking only if all internal gates PASS and document provider configuration/test limitation. Any security/tenant/migration/contract/business failure blocks.

## 7. Final TN

### TN：Fullstack v3 verification and handoff

**Files:** no business code; evidence and matrix only.

```bash
cd /Users/alin/EKB/apps/api
EKB_ENV=test /Users/alin/EKB/.venv/bin/pytest -q tests tests/v3
/Users/alin/EKB/.venv/bin/ruff check ekb_api tests
/Users/alin/EKB/.venv/bin/python -m ekb_api.migrations.v3_fullstack --database-url sqlite:///./ekb_v3_verify.db --through v3_005_apps --verify --expect-versions v3_001_identity,v3_002_content,v3_003_assistant,v3_004_analytics,v3_005_apps
/Users/alin/EKB/.venv/bin/python -m ekb_api.migrations.v3_fullstack --database-url sqlite:///./ekb_v3_verify.db --through v3_005_apps --verify --expect-versions v3_001_identity,v3_002_content,v3_003_assistant,v3_004_analytics,v3_005_apps --explain
/Users/alin/EKB/.venv/bin/python -m ekb_api.migrations.v3_fullstack --database-url sqlite:///./ekb_v3_verify.db --through v3_005_apps --rollback --dry-run --reverse --expect-versions v3_001_identity,v3_002_content,v3_003_assistant,v3_004_analytics,v3_005_apps

cd /Users/alin/EKB/apps/web
npm run build
npm run typecheck

cd /Users/alin/EKB
git diff --check -- .pmos docs
test -f docs/pmos/features/2026-08-09_ekb-fullstack-v3/04_verification-matrix.md
placeholder_pattern='T''BD|TO''DO|以''后再''做|核心动作.*未实现'
if rg -n "$placeholder_pattern" docs/pmos/features/2026-08-09_ekb-fullstack-v3; then
  echo "forbidden placeholder found"
  exit 1
fi
if rg -n "src/lib/api|fetch\(" apps/web/src/app-v2/pages apps/web/src/app-v2/components apps/web/src/app-v2/layouts; then
  echo "forbidden direct API/import found"
  exit 1
else
  echo "adapter-boundary=PASS"
fi
```

Expected: all exit 0; backend tests include existing regressions plus all v3 tests with `0 failed`; frontend build/type checks pass; migration says `status=PASS`; diff check clean; no forbidden placeholders or direct UI fetch; browser TN is recorded in the matrix with 10/10 routes, both viewports, P0/P1/P2=0 and console=0.

**TN rollback:** if any gate fails, do not complete cutover or remove any UI files; disable v3 flags, retain new tables/rows, preserve audit, and return the failed task to its phase with evidence. If production is already cut over, reviewed entry revert plus flag-off to the last new-UI slice is the first action; no old UI restoration, `git reset --hard`, `git checkout`, destructive DB down or workspace overwrite.

## 8. Risks

| # | Risk | Likelihood | Impact | Severity | Mitigation | Mitigation in |
|---|---|---|---|---|---|---|
| R1 | Existing users lack resolvable tenant membership during backfill | M | H | Medium | fail verification and require explicit mapping; never guess tenant | V3-T01, V3-T38 |
| R2 | Live policy resolution changes old auth behavior | M | H | Medium | compatibility tests, token claims advisory only under flag, old fields unchanged | V3-T02, V3-T06 |
| R3 | New list envelope breaks old clients | L | H | Medium | new paths only; old endpoints retained and contract-tested | V3-T08, V3-T38 |
| R4 | Trash restore cascade loses or exposes child | M | H | Medium | transactional parent check, PARENT_DELETED 409, cross-tenant tests | V3-T11, V3-T33 |
| R5 | Access events grow beyond budget | M | M | Medium | indexed bounded writes, 90-day prune, benchmark and metrics | V3-T19, V3-T23 |
| R6 | Credential implementation leaks plaintext | L | H | Medium | dedicated Fernet key, secret regression scans response/DB/audit/log | V3-T27, V3-T38 |
| R7 | Provider/web search failure is mistaken for product failure | H | L | Low | explicit capability state; classify non-blocking only after internal gates | V3-T16, V3-T18, V3-T38 |
| R8 | App install and sync state become conflated | M | H | Medium | separate tables/state machines and contract test | V3-T28, V3-T29 |
| R9 | V2 UI cutover deletes shared non-UI capability | M | H | Medium | import audit before deletion; entry-only rollback | V3-T37 |
| R10 | Mobile visual fixes hide broken interaction | M | M | Medium | same-flow desktop/mobile Codex in-app Browser and console capture | V3-T32–V3-T38 |
| R11 | SQLite migration passes while PostgreSQL differs | M | H | Medium | dialect verification and EXPLAIN on both supported stores | V3-T01, V3-T23, V3-T38 |
| R12 | Concurrent workspace writer causes lost changes | L | H | Medium | Sol serially dispatches one luna writer; diff-name-only audit each task | all phases |

## 9. Rollback and recovery commands

- Feature incident: set `EKB_V3_*_ENABLED=false`; confirm old `/kb`, `/conversations`, `/qa`, `/admin` contracts and app-v2 compatibility tests pass.
- Migration pre-production: run the aggregator rollback command with `--dry-run --reverse`; execute only with explicit data-loss approval and zero installation/trash rows; verify `audit_logs` remains.
- Production: deploy prior compatible code, leave additive v3 tables, preserve audit and keys; do not remove data that old code does not know.
- Frontend cutover: reviewed revert of `apps/web/src/App.tsx`/`main.tsx` entry change; never reset unrelated dirty files.
- Provider incident: disable only web-search/app-provider flag, keep core QA/KB/team/trash functionality available; classify as non-blocking only when all internal tests remain green.

## 10. FR → task index

| FR | Primary tasks |
|---|---|
| FR-001–FR-007 | V3-T01–V3-T06, V3-T35 |
| FR-008–FR-014 | V3-T07–V3-T12, V3-T33 |
| FR-015–FR-019 | V3-T13–V3-T18, V3-T34 |
| FR-020–FR-023 | V3-T19–V3-T24, V3-T32 |
| FR-024–FR-028 | V3-T25–V3-T30, V3-T36 |
| FR-029–FR-036 | V3-T12, V3-T18, V3-T24, V3-T30–V3-T36 |
| FR-037–FR-039 | V3-T01, V3-T12, V3-T18, V3-T24, V3-T30–V3-T37 |
| FR-040–FR-044 | V3-T02, V3-T05, V3-T10–V3-T11, V3-T16, V3-T19, V3-T23, V3-T27–V3-T29, V3-T35, V3-T38 |
| FR-045–FR-048 | V3-T15–V3-T18, V3-T24, V3-T30–V3-T38 |
| FR-049–FR-052 | V3-T04, V3-T31, V3-T36 |
| NFR-001–NFR-003 | V3-T01–V3-T06, V3-T10–V3-T11, V3-T23, V3-T35 |
| NFR-004–NFR-007 | V3-T08, V3-T19–V3-T23, V3-T38 |
| NFR-008–NFR-010 | V3-T05, V3-T16, V3-T27, V3-T37–V3-T38 |
| NFR-011–NFR-013 | V3-T12, V3-T18, V3-T24, V3-T30–V3-T37 |
| NFR-014–NFR-018 | V3-T16, V3-T19, V3-T23, V3-T27, V3-T37–V3-T38 |

## 10.1 Canonical FR acceptance ownership

The matrix is the detailed API/DB/UI/security/evidence authority. This plan registry makes the one-to-one acceptance ID and primary task explicit for every FR; V3-T31/V3-T36 absorb shell work without creating extra tasks.

| Acceptance | FR | Primary task | Detailed proof |
|---|---|---|---|
| AC-V3-001 | FR-001 | V3-T01 | See 04 matrix row and API/DB/UI/security/evidence fields |
| AC-V3-002 | FR-002 | V3-T06 | See 04 matrix row and API/DB/UI/security/evidence fields |
| AC-V3-003 | FR-003 | V3-T06 | See 04 matrix row and API/DB/UI/security/evidence fields |
| AC-V3-004 | FR-004 | V3-T03 | See 04 matrix row and API/DB/UI/security/evidence fields |
| AC-V3-005 | FR-005 | V3-T02 | See 04 matrix row and API/DB/UI/security/evidence fields |
| AC-V3-006 | FR-006 | V3-T04 | See 04 matrix row and API/DB/UI/security/evidence fields |
| AC-V3-007 | FR-007 | V3-T05 | See 04 matrix row and API/DB/UI/security/evidence fields |
| AC-V3-008 | FR-008 | V3-T07 | See 04 matrix row and API/DB/UI/security/evidence fields |
| AC-V3-009 | FR-009 | V3-T08 | See 04 matrix row and API/DB/UI/security/evidence fields |
| AC-V3-010 | FR-010 | V3-T09 | See 04 matrix row and API/DB/UI/security/evidence fields |
| AC-V3-011 | FR-011 | V3-T09 | See 04 matrix row and API/DB/UI/security/evidence fields |
| AC-V3-012 | FR-012 | V3-T10 | See 04 matrix row and API/DB/UI/security/evidence fields |
| AC-V3-013 | FR-013 | V3-T10 | See 04 matrix row and API/DB/UI/security/evidence fields |
| AC-V3-014 | FR-014 | V3-T11 | See 04 matrix row and API/DB/UI/security/evidence fields |
| AC-V3-015 | FR-015 | V3-T13 | See 04 matrix row and API/DB/UI/security/evidence fields |
| AC-V3-016 | FR-016 | V3-T14 | See 04 matrix row and API/DB/UI/security/evidence fields |
| AC-V3-017 | FR-017 | V3-T15 | See 04 matrix row and API/DB/UI/security/evidence fields |
| AC-V3-018 | FR-018 | V3-T16 | See 04 matrix row and API/DB/UI/security/evidence fields |
| AC-V3-019 | FR-019 | V3-T17 | See 04 matrix row and API/DB/UI/security/evidence fields |
| AC-V3-020 | FR-020 | V3-T20 | See 04 matrix row and API/DB/UI/security/evidence fields |
| AC-V3-021 | FR-021 | V3-T21 | See 04 matrix row and API/DB/UI/security/evidence fields |
| AC-V3-022 | FR-022 | V3-T19 | See 04 matrix row and API/DB/UI/security/evidence fields |
| AC-V3-023 | FR-023 | V3-T23 | See 04 matrix row and API/DB/UI/security/evidence fields |
| AC-V3-024 | FR-024 | V3-T25 | See 04 matrix row and API/DB/UI/security/evidence fields |
| AC-V3-025 | FR-025 | V3-T25 | See 04 matrix row and API/DB/UI/security/evidence fields |
| AC-V3-026 | FR-026 | V3-T26 | See 04 matrix row and API/DB/UI/security/evidence fields |
| AC-V3-027 | FR-027 | V3-T27 | See 04 matrix row and API/DB/UI/security/evidence fields |
| AC-V3-028 | FR-028 | V3-T29 | See 04 matrix row and API/DB/UI/security/evidence fields |
| AC-V3-029 | FR-029 | V3-T32 | See 04 matrix row and API/DB/UI/security/evidence fields |
| AC-V3-030 | FR-030 | V3-T12 | See 04 matrix row and API/DB/UI/security/evidence fields |
| AC-V3-031 | FR-031 | V3-T18 | See 04 matrix row and API/DB/UI/security/evidence fields |
| AC-V3-032 | FR-032 | V3-T08 | See 04 matrix row and API/DB/UI/security/evidence fields |
| AC-V3-033 | FR-033 | V3-T35 | See 04 matrix row and API/DB/UI/security/evidence fields |
| AC-V3-034 | FR-034 | V3-T24 | See 04 matrix row and API/DB/UI/security/evidence fields |
| AC-V3-035 | FR-035 | V3-T30 | See 04 matrix row and API/DB/UI/security/evidence fields |
| AC-V3-036 | FR-036 | V3-T11 | See 04 matrix row and API/DB/UI/security/evidence fields |
| AC-V3-037 | FR-037 | V3-T31 | See 04 matrix row and API/DB/UI/security/evidence fields |
| AC-V3-038 | FR-038 | V3-T37 | See 04 matrix row and API/DB/UI/security/evidence fields |
| AC-V3-039 | FR-039 | V3-T38 | See 04 matrix row and API/DB/UI/security/evidence fields |
| AC-V3-040 | FR-040 | V3-T38 | See 04 matrix row and API/DB/UI/security/evidence fields |
| AC-V3-041 | FR-041 | V3-T08 | See 04 matrix row and API/DB/UI/security/evidence fields |
| AC-V3-042 | FR-042 | V3-T38 | See 04 matrix row and API/DB/UI/security/evidence fields |
| AC-V3-043 | FR-043 | V3-T38 | See 04 matrix row and API/DB/UI/security/evidence fields |
| AC-V3-044 | FR-044 | V3-T27 | See 04 matrix row and API/DB/UI/security/evidence fields |
| AC-V3-045 | FR-045 | V3-T16 | See 04 matrix row and API/DB/UI/security/evidence fields |
| AC-V3-046 | FR-046 | V3-T37 | See 04 matrix row and API/DB/UI/security/evidence fields |
| AC-V3-047 | FR-047 | V3-T32 | See 04 matrix row and API/DB/UI/security/evidence fields |
| AC-V3-048 | FR-048 | V3-T38 | See 04 matrix row and API/DB/UI/security/evidence fields |
| AC-V3-049 | FR-049 | V3-T31 | See 04 matrix row and API/DB/UI/security/evidence fields |
| AC-V3-050 | FR-050 | V3-T04 | See 04 matrix row and API/DB/UI/security/evidence fields |
| AC-V3-051 | FR-051 | V3-T31 | See 04 matrix row and API/DB/UI/security/evidence fields |
| AC-V3-052 | FR-052 | V3-T31 | See 04 matrix row and API/DB/UI/security/evidence fields |

### 10.2 Canonical NFR acceptance ownership

Each exact NFR row in `01_requirements.md` has one collision-free acceptance ID, one primary task and a detailed proof row in `04_verification-matrix.md`.

| Acceptance | NFR | Primary task | Detailed proof |
|---|---|---|---|
| AC-V3-NFR-001 | NFR-001 | V3-T01 | tenant/actor/object authorization and no-existence-leak fixtures |
| AC-V3-NFR-002 | NFR-002 | V3-T05 | globally unique prefix, constant-time hash and live capability contract |
| AC-V3-NFR-003 | NFR-003 | V3-T02 | atomic policy version bump and stale-token enforcement |
| AC-V3-NFR-004 | NFR-004 | V3-T23 | list/analytics/SSE p95 benchmark evidence |
| AC-V3-NFR-005 | NFR-005 | V3-T23 | 1,000,000-event/tenant/90-day retention, EXPLAIN and prune |
| AC-V3-NFR-006 | NFR-006 | V3-T09 | transaction visibility, partial batch and idempotency retry |
| AC-V3-NFR-007 | NFR-007 | V3-T38 | five immutable ledger/checksum/dialect/rollback gates |
| AC-V3-NFR-008 | NFR-008 | V3-T27 | missing-key fail-closed and dual-decrypt rotation |
| AC-V3-NFR-009 | NFR-009 | V3-T38 | purge/audit retention, cancel audit and request-id trace |
| AC-V3-NFR-010 | NFR-010 | V3-T38 | `/me`, refresh, conversation, KB, admin user-create and sync compatibility |
| AC-V3-NFR-011 | NFR-011 | V3-T37 | conditional adapter-boundary import scan |
| AC-V3-NFR-012 | NFR-012 | V3-T38 | Codex in-app Browser desktop/mobile reachability and overflow |
| AC-V3-NFR-013 | NFR-013 | V3-T38 | keyboard, aria, focus and reduced-motion checks |
| AC-V3-NFR-014 | NFR-014 | V3-T38 | X-Request-Id and audit/metric retrieval |
| AC-V3-NFR-015 | NFR-015 | V3-T38 | provider-risk classifier cannot bypass internal gates |
| AC-V3-NFR-016 | NFR-016 | V3-T38 | backend/web/migration/contract/browser final TN |
| AC-V3-NFR-017 | NFR-017 | V3-T37 | tenant canary flags and data-preserving rollback |
| AC-V3-NFR-018 | NFR-018 | V3-T38 | FR/NFR/AC/task/link completeness audit |

## 11. Execution handoff

Sol must dispatch tasks serially with the following context each time:

```text
Role: luna_max_worker
Goal: <one task from V3-T01–V3-T38>
Scope: exact Files list in that task
Do not change: Skills, unrelated dirty files, architecture/API decisions outside the cited spec
Acceptance: task-specific Steps and Inline verification
Checks: exact commands in task plus phase gates
Report: Status / Changed / Checks / Risks / Handoff
```

Before any business-code milestone, Sol performs document audit: cross-links, FR/NFR coverage, API/SQL/migration/rollback consistency, no unresolved placeholders, and current-vs-pending status. After each worker result, Sol reviews the exact diff and does not accept an unverified “should work” claim.
