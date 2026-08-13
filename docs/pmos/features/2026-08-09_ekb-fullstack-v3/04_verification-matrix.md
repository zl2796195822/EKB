---
title: EKB 设计稿十页全栈闭环 v3 验证矩阵
date: 2026-08-09
status: 已批准
tier: 3
type: feature
requirements_ref: ./01_requirements.md
spec_ref: ./02_spec.md
plan_ref: ./03_plan.md
authoritative: true
owner: Sol
version: 3.0
purpose: 对 52 FR、18 NFR 逐项追踪自动测试、浏览器、安全/租户和证据硬门禁
companion_docs: [./01_requirements.md, ./02_spec.md, ./03_plan.md, ./03_plan_review.md, ./README.md]
open_issues: []
review_stage: Sol final gate passed
approval: user authorized docs-first fullstack execution direction; Sol approved technical/security/QA document gate on 2026-08-10; implementation/browser/migration evidence remains pending. Do not claim the user reviewed final wording.
---

# EKB 设计稿十页全栈闭环 v3 验证矩阵

本矩阵是发布前证据索引。当前状态如实记录为：M0–M5 的前端/app-v2 和既有 Team/Profile 端点接入已完成；v3 后端、兼容债修复、浏览器 TN、迁移证据和最终切换均为 pending。本文件不把文档产出误报为代码或浏览器验证。

## 1. 证据规则与硬门禁

- 每个 FR 和 NFR 都必须有对应的服务端自动测试、app-v2 contract/state 测试、浏览器行为场景、安全/权限/租户断言和证据文件；证据文件由对应任务在实现阶段写入 `evidence/`，本 milestone 只定义精确目标路径。
- 每个写操作都要证明数据库提交、审计记录和 hard reload 后的页面状态一致；拒绝、空集、超时、provider 缺失和父级冲突也必须有可恢复 UI。
- 浏览器矩阵固定覆盖十页、desktop `1440x1000`、mobile `390x844`、硬刷新、至少一个成功和一个拒绝/错误路径；最终 `P0=0`、`P1=0`、`P2=0`、console errors `0`。
- API gate：旧 `/api/v1` 路径与 response 必需字段不变；新列表使用 envelope；每个合同校验 method/path/request/response/error/permission/audit。
- DB gate：SQLite 与 PostgreSQL 都执行 `PRAGMA foreign_keys=ON`（SQLite）或等价 FK enforcement（PostgreSQL）；`v3_001_identity` 到 `v3_005_apps` 五个 checksum、索引、backfill、seed 均可重复验证。
- Migration gate：每个版本只写自己的 ledger 行；verify 列出全部版本和 checksum；rollback dry-run 按 `v3_005`→`v3_001` 逆序且默认不删除业务数据，`audit_logs` 始终保留。
- Compatibility gate：现有 `/me` dev/first-user fallback、token 固定 `policy_version=1`、token capabilities 信任、QA tenant-only turn scope、ops dashboard 未按 days 过滤、`Document` 缺可靠 file size、旧 rename adapter gap、KB tags 未持久化、文档先查后写幂等和 SQLite quota 并发风险，在相应前置阶段清零，而不是留到最终 TN 才发现。

## 2. 页面与视口覆盖

| 页面 | route | desktop 场景 | `390x844` 场景 | 全栈动作证据 | 当前状态 |
|---|---|---|---|---|---|
| Dashboard | `#/dashboard` | 四个快捷动作：新建文档 note dialog、上传文档、创建知识空间、智能导入；日期聚合、最近访问、标签/健康度跳转、权限拒绝 | 卡片折叠、四动作入口、筛选、重试、console | `evidence/V3-T20-dashboard-summary.json`, `evidence/V3-T22-access-authz.json`, `evidence/V3-T32-quick-actions.json` | pending；M0 shell 已完成 |
| Knowledge | `#/knowledge` | KB/folder/doc 搜索、移动、收藏、分享、概览 | 树折叠、批量操作、父级冲突 CTA | `evidence/V3-T07-content-migration.json`, `evidence/V3-T10-overview.json` | pending；既有 KB 已接入 |
| Assistant | `#/assistant` | 项目、附件、模型、深思、联网、SSE/cancel/feedback | composer、取消、引用滚动和重试 | `evidence/V3-T17-sse-contract.json`, `evidence/V3-T18-assistant-browser.json` | pending；既有 SSE v2 已接入 |
| Documents | `#/documents` | 服务端筛选分页、上传、版本/diff、批量、删除/恢复 | 表格横向适配、筛选抽屉、恢复冲突 | `evidence/V3-T08-documents-contract.json`, `evidence/V3-T11-trash.json` | pending；既有文档已接入 |
| Team | `#/team` | 用户/角色/邀请/状态/分配/导出 | 角色编辑、拒绝态、列表分页 | `evidence/V3-T01-identity-scope.json`, `evidence/V3-T06-team-lifecycle.json` | pending；M5 既有端点已接入 |
| Analytics | `#/analytics` | 1/7/30/90 天趋势、分布、活动、性能 | 日期选择、零数据、查询错误 | `evidence/V3-T20-dashboard-summary.json`, `evidence/V3-T23-analytics-perf.json` | pending；当前聚合缺后端 |
| Apps | `#/apps` | 六项目录、安装、配置、连接、同步、运行/卸载 | 卡片状态、凭据一次性显示、重试 | `evidence/V3-T25-app-catalog.json`, `evidence/V3-T28-app-run.json` | pending；当前为占位 |
| Recycle | `#/recycle` | 30 天列表、恢复、purge、clear | 保留天数、`PARENT_DELETED` 恢复路径 | `evidence/V3-T11-trash.json` | pending；当前能力缺后端 |
| Profile | `#/profile` | 资料、密码、偏好、API key、设备会话、通知 list/read、帮助/反馈 | 表单校验、密钥 prefix、重新认证、通知已读、反馈结果 | `evidence/V3-T04-profile-sessions.json`, `evidence/V3-T05-api-keys.json`, `evidence/V3-T31-shell.json` | pending；M5 既有端点已接入 |
| Module Map | `#/modules` | 十页入口按实时 capability 导航 | 可触达、拒绝态、硬刷新 | `evidence/V3-T31-adapter-facade.json`, `evidence/V3-T37-import-audit.json` | pending；route manifest 已完成 |

## 3. FR 逐项追踪矩阵

列说明：自动测试为实现阶段的精确测试目标；浏览器场景必须同时运行于第 2 节的两个视口；安全列必须包含主体、租户和 object-level authorization；证据路径在写入后才可标记 PASS。

| FR | AC | 里程碑/任务 | 自动测试 | 浏览器场景 | 安全/权限/租户隔离 | 证据文件 |
|---|---|---|---|---|---|
| FR-001 | AC-V3-001 | P1/V3-T01 | `test_identity_users.py::test_list_scoped` | Team 搜索/分页/刷新 | 双主体双租户，无跨租户用户 | `evidence/V3-T01-identity-scope.json` |
| FR-002 | AC-V3-002 | P1/V3-T01,V3-T06 | `test_team_lifecycle.py::test_detail_update_status` | Team 详情、suspend、恢复列表 | `team:user:manage` 和同租户 | `evidence/V3-T06-team-lifecycle.json` |
| FR-003 | AC-V3-003 | P1/V3-T06 | `test_team_lifecycle.py::test_invite_export_redaction` | 邀请后刷新、导出 | CSV 无 password/key/credential | `evidence/V3-T06-team-lifecycle.json` |
| FR-004 | AC-V3-004 | P1/V3-T03 | `test_roles.py::test_builtin_custom_lifecycle`, `test_legacy_role_backfill.py::test_customer_exact_capabilities` | 内置不可改、自定义 CRUD；legacy compatibility 标签与保护 | capability allowlist、legacy_customer 仅 `kb:read` + `qa:ask`、跨租户 404 | `evidence/V3-T03-roles.json` |
| FR-005 | AC-V3-005 | P1/V3-T02 | `test_policy_refresh.py::test_live_policy`, `test_legacy_role_backfill.py::test_unknown_role_blocks` | 分配角色后 refresh/重新进入；非法 legacy role 显示迁移阻断 | token 旧 capability 不可授权，双租户，null/unknown/unresolved tenant hard stop | `evidence/V3-T02-policy-refresh.json`; `evidence/V3-T01-identity-scope.json` |
| FR-006 | AC-V3-006 | P1/V3-T04 | `test_profile.py::test_password_revokes_sessions` | 资料/偏好保存、改密后重新认证 | 当前/第二会话均按策略失效 | `evidence/V3-T04-profile-sessions.json` |
| FR-007 | AC-V3-007 | P1/V3-T05 | `test_api_keys.py::test_prefix_hash_revoke` | 创建一次性 key、列表 prefix、撤销 | prefix lookup、恒定时间 hash、实时能力 | `evidence/V3-T05-api-keys.json` |
| FR-008 | AC-V3-008 | P2/V3-T07 | `test_folders.py::test_hierarchy_and_cycle` | 建树、移动、改名、删除 | 同 tenant+KB composite parent 校验 | `evidence/V3-T07-content-migration.json` |
| FR-009 | AC-V3-009 | P2/V3-T08 | `test_document_pagination.py::test_filter_sort_cursor` | 搜索/排序/分页/刷新 | query/cursor 绑定 tenant+subject | `evidence/V3-T08-documents-contract.json` |
| FR-010 | AC-V3-010 | P2/V3-T09 | `test_tags_favorites_batch.py::test_partial_batch` | 批量移动/标签/收藏/回收 | 每项 ACL、重试幂等 | `evidence/V3-T09-resource-actions.json` |
| FR-011 | AC-V3-011 | P2/V3-T09 | `test_tags_favorites_batch.py::test_unique_favorites` | KB/文档/会话收藏 | resource read filter、唯一约束 | `evidence/V3-T09-resource-actions.json` |
| FR-012 | AC-V3-012 | P2/V3-T10 | `test_document_sharing_overview.py::test_acl_share` | 分享、撤销、刷新 | 不可扩大调用者权限/租户 | `evidence/V3-T10-sharing.json` |
| FR-013 | AC-V3-013 | P2/V3-T10 | `test_document_sharing_overview.py::test_real_health` | 概览/health/tag 真实数字 | 仅授权文档计入 | `evidence/V3-T10-overview.json` |
| FR-014 | AC-V3-014 | P2/V3-T11 | `test_trash.py::test_retention_restore_purge` | 删除、回收站、恢复、清理 | 30 天、audit 保留、父级冲突 | `evidence/V3-T11-trash.json` |
| FR-015 | AC-V3-015 | P3/V3-T13 | `test_projects.py::test_project_conversation` | 项目 CRUD、会话归属/收藏 | project member/owner、同租户 | `evidence/V3-T13-projects.json` |
| FR-016 | AC-V3-016 | P3/V3-T14 | `test_attachments.py::test_authorized_attachment` | 既有文档/上传附件 | ACL 在检索前和引用时双检 | `evidence/V3-T14-attachments.json` |
| FR-017 | AC-V3-017 | P3/V3-T15 | `test_model_capabilities.py::test_tenant_route` | 模型选择、深思开关 | tenant routing、stale model 409 | `evidence/V3-T15-model-options.json` |
| FR-018 | AC-V3-018 | P3/V3-T16 | `test_web_search_capability.py::test_truthful_provider` | provider 配置/缺失/错误 | flag、密钥和租户 provider 隔离 | `evidence/V3-T16-web-search.json` |
| FR-019 | AC-V3-019 | P3/V3-T17 | `test_qa_v3_stream_contract.py::test_sse_v2_additive` | 问答、引用、取消、反馈 | actor+tenant turn scope，独立 cancel audit | `evidence/V3-T17-sse-contract.json` |
| FR-020 | AC-V3-020 | P4/V3-T20 | `test_dashboard_summary.py::test_aggregate_range` | Dashboard 摘要/1-90 天 | analytics capability、租户聚合 | `evidence/V3-T20-dashboard-summary.json` |
| FR-021 | AC-V3-021 | P4/V3-T21 | `test_analytics_shapes.py::test_timeseries_distribution` | 趋势、分布、活动、health | 结果按 tenant/ACL 过滤 | `evidence/V3-T21-analytics-shapes.json` |
| FR-022 | AC-V3-022 | P4/V3-T19 | `test_access_events.py::test_bounded_event` | 访问后活动刷新 | event 无内容，actor/resource 范围正确 | `evidence/V3-T19-access-events.json` |
| FR-023 | AC-V3-023 | P4/V3-T23 | `test_analytics_perf.py::test_indexed_1000000_90d` | 90 天查询与限流态 | tenant filter、EXPLAIN、p95≤500ms | `evidence/V3-T23-analytics-perf.json` |
| FR-024 | AC-V3-024 | P5/V3-T25 | `test_app_catalog.py::test_exact_six` | 目录六项逐项显示 | slug immutable、无第七项 | `evidence/V3-T25-app-catalog.json` |
| FR-025 | AC-V3-025 | P5/V3-T25,V3-T26 | `test_app_catalog.py::test_views` | 全部/已安装/推荐/搜索 | installation projection 同租户 | `evidence/V3-T25-app-catalog.json` |
| FR-026 | AC-V3-026 | P5/V3-T26,V3-T28 | `test_app_installations.py::test_lifecycle` | 安装/配置/连接/运行/卸载 | `app:manage/run`、状态转换审计 | `evidence/V3-T26-installation.json` |
| FR-027 | AC-V3-027 | P5/V3-T27 | `test_apps_credentials.py::test_fernet_redaction` | 创建凭据一次显示、后续 prefix | 专用 key、无 plaintext/签名 secret 复用 | `evidence/V3-T27-credentials.json` |
| FR-028 | AC-V3-028 | P5/V3-T29 | `test_app_sync_separation.py::test_downstream` | sync 单独运行/失败重试 | source/run 与 install 状态隔离 | `evidence/V3-T29-app-sync.json` |
| FR-029 | AC-V3-029 | P4/V3-T20,V3-T24 | `test_dashboard_pages.py::test_real_actions` | Dashboard 快捷动作和聚合 | permission state 无数据泄露 | `evidence/V3-T24-dashboard-pages.json` |
| FR-030 | AC-V3-030 | P2/V3-T12 | `test_knowledge_pages.py::test_adapter_only` | Knowledge 全核心动作 | 页面只走 adapter、资源 ACL | `evidence/V3-T12-knowledge-pages.json` |
| FR-031 | AC-V3-031 | P3/V3-T18 | `test_assistant_page.py::test_full_journey` | Assistant 完整发送流程 | 附件/模型/turn actor+tenant | `evidence/V3-T18-assistant-browser.json` |
| FR-032 | AC-V3-032 | P2/V3-T08,V3-T12 | `test_document_versions.py::test_increment_changed_file_size` | Documents 版本/diff/删除 | version unique、file_size optional 不误报 | `evidence/V3-T08-documents-contract.json` |
| FR-033 | AC-V3-033 | P1/V3-T06,V3-T04,V3-T05 | `test_team_profile_pages.py::test_adapter_only` | Team/Profile 全操作 | role/profile/key/session scope | `evidence/V3-T35-team-profile.json` |
| FR-034 | AC-V3-034 | P4/V3-T21,V3-T24 | `test_analytics_pages.py::test_date_range_states` | Analytics 1/7/30/90 天 | analytics permission、无跨 tenant | `evidence/V3-T24-dashboard-pages.json` |
| FR-035 | AC-V3-035 | P5/V3-T30,P2/V3-T11 | `test_page_capabilities.py::test_apps_recycle` | Apps/Recycle 不显示假成功 | truthful provider / trash authorization | `evidence/V3-T30-apps-page.json` |
| FR-036 | AC-V3-036 | P2/V3-T11 | `test_trash.py::test_parent_batch_restore` | 父级/子项恢复与 clear | deletion_batch 只恢复同批后代 | `evidence/V3-T11-trash.json` |
| FR-037 | AC-V3-037 | P6/V3-T31,V3-T37 | `test_adapter_facade.py::test_no_direct_fetch` | 十页导航和 hard reload | 生产页无旧 UI import/direct fetch | `evidence/V3-T31-adapter-facade.json` |
| FR-038 | AC-V3-038 | P6/V3-T37 | `test_import_audit.py::test_old_ui_unreferenced` | production cutover 回归 | 旧组件无可达引用，保留可回滚点 | `evidence/V3-T37-import-audit.json` |
| FR-039 | AC-V3-039 | P1–P5/V3-T06,V3-T18,V3-T20,V3-T29 | `test_compatibility_baseline.py::test_additive_contracts` | /me、refresh、conversation/KB/admin/sync 旧路径与新页面并行 | old required fields/auth/error semantics preserved; no tenant scope widening | `evidence/V3-T38-compatibility.json` |
| FR-040 | AC-V3-040 | P1–P5 | `test_audit_contracts.py::test_write_and_denied_audit` | 每页写入后审计可查 | DENIED 也审计，secret/content 脱敏 | `evidence/V3-T38-fullstack-tn.json` |
| FR-041 | AC-V3-041 | P2/V3-T08,V3-T09,V3-T12 | `test_pagination_contract.py::test_envelope_cursor_sort` | Documents/Knowledge list filter-sort-page and hard reload | cursor/query/page_size bound to tenant+subject, stable tie-breaker | `evidence/V3-T08-documents-contract.json` |
| FR-042 | AC-V3-042 | P1–P5 | `test_tenant_isolation.py::test_all_v3_domains` | 各页切换 tenant hard reload | users/docs/turn/apps/events 全隔离 | `evidence/V3-T38-fullstack-tn.json` |
| FR-043 | AC-V3-043 | P1–P5/V3-T01,V3-T07,V3-T13,V3-T19,V3-T25,V3-T38 | `test_migration_ledger.py::test_five_versions_verify_rollback` | 每阶段 migration verify/rollback dry-run 证据 | version checksum immutable、FK/CHECK、audit preserved | `evidence/V3-T38-migration.json` |
| FR-044 | AC-V3-044 | P1–P5 | `test_secret_regression.py::test_no_secret_outputs` | Profile/Apps/错误态 | key/password/credential 不进 response/log/audit | `evidence/V3-T38-security.json` |
| FR-045 | AC-V3-045 | P3/V3-T15,V3-T16 | `test_capability_flags.py::test_model_web_flags` | capability disabled 的真实原因 | 仅外部 provider 缺失可显示能力缺失；核心动作不冒充 | `evidence/V3-T16-web-search.json` |
| FR-046 | AC-V3-046 | P6/V3-T30–V3-T38 | `test_page_state_matrix.py::test_no_core_action_fake_disabled` | 十页核心动作可实现；provider 缺失时仅联网搜索有配置/重试路径 | no core action is replaced by unavailable/disabled; external provider failure is truthful | `evidence/V3-T38-fullstack-tn.json` |
| FR-047 | AC-V3-047 | P6/V3-T32–V3-T36 | `test_page_state_matrix.py::test_loading_empty_error_success` | 十页 loading/empty/error/success | 错误不绕过权限、不产生假成功 | `evidence/V3-T32-to-V3-T36-page-states.json` |
| FR-048 | AC-V3-048 | P6/V3-T38 | `test_visual_route_matrix.py::test_ten_routes_two_viewports` | 十页两视口对照 | console=0、P0/P1/P2=0 | `evidence/V3-T38-fullstack-tn.json` |
| FR-049 | AC-V3-049 | P6/V3-T31 | `test_shell_search.py::test_authorized_global_search` | shell 搜索提交、结果/空/错误/权限、hard reload | current subject+tenant+KB/document ACL; no result existence leak | `evidence/V3-T31-shell-search.json` |
| FR-050 | AC-V3-050 | P1/V3-T04 | `test_notifications.py::test_list_read_scoped` | Profile menu 通知列表、标记已读、hard reload | composite tenant/user scope; other user's notification is 404 | `evidence/V3-T04-notifications.json` |
| FR-051 | AC-V3-051 | P6/V3-T31 | `test_shell_navigation.py::test_profile_menu_routes` | profile menu → Profile/通知/帮助/会话控制；未授权入口 truthful | adapter-only navigation; capability state cannot become inert success | `evidence/V3-T31-shell-navigation.json` |
| FR-052 | AC-V3-052 | P6/V3-T31 | `test_support_feedback.py::test_submit_redacted` | help drawer 提交/重试/成功或错误；request id visible | tenant/user ownership; message redacted from audit/log and no business success toast | `evidence/V3-T31-help-feedback.json` |

### 3.1 AC registry: one FR, one acceptance, one primary task

This registry is the requirement-level authority. Each row has one primary task; additional collaborating tasks remain in the FR matrix above. No row is implementation or browser evidence: all evidence paths are planned outputs.

| AC | FR | Primary task | API / DB contract | New-UI control and proof | Security / isolation | Test / evidence |
|---|---|---|---|---|---|---|
| AC-V3-001 | FR-001 | V3-T01 | `GET /tenants/{tenant}/users`; tenant memberships | Team search/list/pagination | actor+tenant | `test_identity_users.py`; `V3-T01-identity-scope.json` |
| AC-V3-002 | FR-002 | V3-T06 | user detail/update/status | row menu/detail drawer | `team:user:manage` | `test_team_lifecycle.py`; `V3-T06-team-lifecycle.json` |
| AC-V3-003 | FR-003 | V3-T06 | invite/export redaction | invite dialog/export action | tenant safe columns | `test_team_lifecycle.py`; `V3-T06-team-lifecycle.json` |
| AC-V3-004 | FR-004 | V3-T03 | tenant roles/role permissions CRUD | role tab/permission matrix | built-in immutable, capability allowlist | `test_roles.py`; `V3-T03-roles.json` |
| AC-V3-005 | FR-005 | V3-T02 | atomic `tenants.policy_version` and live refresh | role assign then refresh | stale token denied | `test_policy_refresh.py`; `V3-T02-policy-refresh.json` |
| AC-V3-006 | FR-006 | V3-T04 | profile/preferences/auth_sessions/password revoke | Profile Security tab | current tenant/user sessions only | `test_profile.py`; `V3-T04-profile-sessions.json` |
| AC-V3-007 | FR-007 | V3-T05 | globally unique prefix + hash API keys | API tab create/copy-once/revoke | live membership after constant-time verify | `test_api_keys.py`; `V3-T05-api-keys.json` |
| AC-V3-008 | FR-008 | V3-T07 | folders composite parent and cycle checks | tree create/move/rename drawer | same tenant+KB parent | `test_folders.py`; `V3-T07-content-migration.json` |
| AC-V3-009 | FR-009 | V3-T08 | server filter/search/sort/cursor | document filter bar | cursor bound to tenant/subject | `test_document_pagination.py`; `V3-T08-documents-contract.json` |
| AC-V3-010 | FR-010 | V3-T09 | batch move/tag/favorite/trash result rows | batch action bar | per-item ACL/idempotency | `test_tags_favorites_batch.py`; `V3-T09-resource-actions.json` |
| AC-V3-011 | FR-011 | V3-T09 | unique `resource_favorites` | favorite controls on KB/doc/conversation | authorized resources only | `test_tags_favorites_batch.py`; `V3-T09-resource-actions.json` |
| AC-V3-012 | FR-012 | V3-T10 | document shares PUT/DELETE/list | share drawer and revoke action | ACL cannot expand | `test_document_sharing_overview.py`; `V3-T10-sharing.json` |
| AC-V3-013 | FR-013 | V3-T10 | resource tags/overview/health aggregates | overview and health tabs | authorized rows only | `test_document_sharing_overview.py`; `V3-T10-overview.json` |
| AC-V3-014 | FR-014 | V3-T11 | trash metadata, 30-day purge/audit | recycle list/purge confirmation | tenant trash capability | `test_trash.py`; `V3-T11-trash.json` |
| AC-V3-015 | FR-015 | V3-T13 | project CRUD/member/assignment relations | project drawer/picker | owner/member same tenant | `test_projects.py`; `V3-T13-projects.json` |
| AC-V3-016 | FR-016 | V3-T14 | authorized existing/upload attachment | attachment drawer | doc ACL before retrieval/citation | `test_attachments.py`; `V3-T14-attachments.json` |
| AC-V3-017 | FR-017 | V3-T15 | tenant model option/deep-thinking contract | model/deep-thinking controls | tenant routing, stale model 409 | `test_model_capabilities.py`; `V3-T15-model-options.json` |
| AC-V3-018 | FR-018 | V3-T16 | provider list/configure/rotate/disable | admin provider drawer | flag/provider credential ownership | `test_web_search_capability.py`; `V3-T16-web-search.json` |
| AC-V3-019 | FR-019 | V3-T17 | actor+tenant SSE/cancel/citations/feedback | composer stream/cancel/feedback | independent cancel audit, no replay claim | `test_qa_v3_stream_contract.py`; `V3-T17-sse-contract.json` |
| AC-V3-020 | FR-020 | V3-T20 | date-bounded aggregate and ops compatibility | dashboard date picker/summary | analytics capability/tenant filter | `test_dashboard_summary.py`; `V3-T20-dashboard-summary.json` |
| AC-V3-021 | FR-021 | V3-T21 | timeseries/distribution/activity/tag/health | charts/activity tabs | tenant+ACL query | `test_analytics_shapes.py`; `V3-T21-analytics-shapes.json` |
| AC-V3-022 | FR-022 | V3-T19 | bounded `resource_access_events` | recent access panel | actor/resource tenant relation | `test_access_events.py`; `V3-T19-access-events.json` |
| AC-V3-023 | FR-023 | V3-T23 | 1m events/tenant/90d, indexes/EXPLAIN/p95 | performance state | no cross-tenant aggregates | `test_analytics_perf.py`; `V3-T23-analytics-perf.json` |
| AC-V3-024 | FR-024 | V3-T25 | exact six immutable app seed rows | six-card catalog | slug allowlist | `test_app_catalog.py`; `V3-T25-app-catalog.json` |
| AC-V3-025 | FR-025 | V3-T25 | catalog/search/install projections | all/installed/recommended/search tabs | tenant installation scope | `test_app_catalog.py`; `V3-T25-app-catalog.json` |
| AC-V3-026 | FR-026 | V3-T26 | installation configure/connect lifecycle | lifecycle drawer | app capabilities/audit | `test_app_installations.py`; `V3-T26-installation.json` |
| AC-V3-027 | FR-027 | V3-T27 | Fernet ciphertext/key versions/redaction | write-once credential field | dedicated key, no plaintext | `test_apps_credentials.py`; `V3-T27-credentials.json` |
| AC-V3-028 | FR-028 | V3-T29 | sync source/run separate from install | sync relation/history | install state unchanged on sync failure | `test_app_sync_separation.py`; `V3-T29-app-sync.json` |
| AC-V3-029 | FR-029 | V3-T32 | dashboard summary + note/upload/KB/import endpoints | exact four quick-action controls | permission/error truthful | `test_dashboard_pages.py`; `V3-T32-quick-actions.json` |
| AC-V3-030 | FR-030 | V3-T12 | KB/folder/doc/trash adapters | tree/table/row menu | adapter ACL enforcement | `test_knowledge_pages.py`; `V3-T12-knowledge-pages.json` |
| AC-V3-031 | FR-031 | V3-T18 | assistant/project/attachment/SSE adapters | assistant composer/drawers | actor+tenant turn and ACL | `test_assistant_page.py`; `V3-T18-assistant-browser.json` |
| AC-V3-032 | FR-032 | V3-T08 | document versions unique/increment/changed/file fields | version/diff/retry drawer | tenant document history | `test_document_versions.py`; `V3-T08-documents-contract.json` |
| AC-V3-033 | FR-033 | V3-T35 | identity/profile/session/key adapters | Team/Profile tabs and menus | live role/session owner | `test_team_profile_pages.py`; `V3-T35-team-profile.json` |
| AC-V3-034 | FR-034 | V3-T24 | analytics range/shape adapters | analytics date/chart panels | analytics permission | `test_analytics_pages.py`; `V3-T24-dashboard-pages.json` |
| AC-V3-035 | FR-035 | V3-T30 | apps/trash list/lifecycle adapters | Apps/Recycle drawers | truthful provider/trash authorization | `test_page_capabilities.py`; `V3-T30-apps-page.json` |
| AC-V3-036 | FR-036 | V3-T11 | deletion batch restore/parent conflict | restore dialog/CTA | same-batch descendants only | `test_trash.py`; `V3-T11-trash.json` |
| AC-V3-037 | FR-037 | V3-T31 | app-v2 adapter facade boundary | all ten pages through adapters | no token/direct API in UI | `test_adapter_facade.py`; `V3-T31-adapter-facade.json` |
| AC-V3-038 | FR-038 | V3-T37 | production entry/import audit | final new-UI cutover | old UI unreachable; flag rollback | `test_import_audit.py`; `V3-T37-import-audit.json` |
| AC-V3-039 | FR-039 | V3-T38 | compatibility endpoints and additive schemas | old/new route parallel checks | no scope widening | `test_compatibility_baseline.py`; `V3-T38-compatibility.json` |
| AC-V3-040 | FR-040 | V3-T38 | audit writes/read trace/redaction | audit views after each action | DENIED and secret redaction | `test_audit_contracts.py`; `V3-T38-fullstack-tn.json` |
| AC-V3-041 | FR-041 | V3-T08 | exact `{items,next_cursor,page_size}` | list filters/hard reload | cursor tenant/subject binding | `test_pagination_contract.py`; `V3-T08-documents-contract.json` |
| AC-V3-042 | FR-042 | V3-T38 | tenant/actor/ACL service verifier | tenant switch hard reload | all domains negative fixtures | `test_tenant_isolation.py`; `V3-T38-fullstack-tn.json` |
| AC-V3-043 | FR-043 | V3-T38 | five immutable ledgers/checksum/rollback | migration evidence only | FK/CHECK/audit retained | `test_migration_ledger.py`; `V3-T38-migration.json` |
| AC-V3-044 | FR-044 | V3-T27 | secret response/log/audit redaction | profile/apps error states | no plaintext | `test_secret_regression.py`; `V3-T38-security.json` |
| AC-V3-045 | FR-045 | V3-T16 | server capability/feature flags | model/provider state | only provider capability may be disabled | `test_capability_flags.py`; `V3-T16-web-search.json` |
| AC-V3-046 | FR-046 | V3-T37 | core action state contracts | ten-page core actions | no fake core-action state; provider limitation remains truthful | `test_page_state_matrix.py`; `V3-T38-fullstack-tn.json` |
| AC-V3-047 | FR-047 | V3-T32 | loading/empty/error/permission/success/stale states | all ten pages/390x844 | errors do not bypass auth | `test_page_state_matrix.py`; `V3-T32-to-V3-T36-page-states.json` |
| AC-V3-048 | FR-048 | V3-T38 | visual route/overflow/console gate | desktop + 390x844 | P0/P1/P2=0 | `test_visual_route_matrix.py`; `V3-T38-fullstack-tn.json` |
| AC-V3-049 | FR-049 | V3-T31 | existing `POST /search` ACL-filtered response | shell search entry/results | no unauthorized result existence | `test_shell_search.py`; `V3-T31-shell-search.json` |
| AC-V3-050 | FR-050 | V3-T04 | `user_notifications` + GET/PATCH read | notification panel/list/read | composite tenant/user FK | `test_notifications.py`; `V3-T04-notifications.json` |
| AC-V3-051 | FR-051 | V3-T31 | shell navigation uses adapter route state | profile menu/help/session links | capability state truthful | `test_shell_navigation.py`; `V3-T31-shell-navigation.json` |
| AC-V3-052 | FR-052 | V3-T31 | `support_feedback` + `POST /help/feedback` | help drawer submit/retry | user/tenant scope and redacted audit | `test_support_feedback.py`; `V3-T31-help-feedback.json` |

## 4. NFR 逐项追踪矩阵

| NFR | exact requirement from 01 | 里程碑/主任务 | 自动测试/门禁 | 可观察行为/运行证据 | 安全/隔离断言 | 证据文件 |
|---|---|---|---|---|---|---|
| NFR-001 | 任意跨租户读写、资源 ID 猜测、失效角色 token、失效 API key 和越权附件访问必须返回统一授权错误，且不泄露资源存在性。 | V3-T01,V3-T02,V3-T05,V3-T14,V3-T17,V3-T22 | `test_tenant_isolation.py`, `test_qa_actor_tenant_scope.py`, `test_attachments.py` | 两租户猜 ID、失效 key、撤权附件均得到统一 403/404 | actor+tenant+ACL object authorization | `evidence/V3-T38-security.json` |
| NFR-002 | Bearer 与 API key 均通过服务端认证链；API key prefix 查询后使用恒定时间 hash 校验，成功后解析当前 DB 角色/能力。 | V3-T01,V3-T05 | `test_api_keys.py::test_global_prefix_lookup`, timing/hash contract | Bearer/API key hard reload 后 `/me` 能力来自 DB | prefix globally unique，验证后 membership/role live resolve | `evidence/V3-T05-api-keys.json` |
| NFR-003 | 角色或能力变更后，下一次 refresh/`GET /me` 必须反映新 `policy_version`；缓存失效不能超过一个请求生命周期。 | V3-T02 | `test_policy_refresh.py`, concurrent policy bump | Team 分配角色后 refresh/重新进入立即改变能力 | `tenants.policy_version` 原子递增、旧 token deny | `evidence/V3-T02-policy-refresh.json` |
| NFR-004 | 新列表 p95 ≤ 400ms（1 万条租户级数据、20 RPS）；分析聚合 p95 ≤ 500ms；SSE 首事件 p95 ≤ 1s（不含上游模型等待）。 | V3-T08,V3-T20,V3-T23 | pagination benchmark, analytics p95, SSE first-event benchmark | Documents/Dashboard/Assistant 显示 bounded loading and result | query scope remains tenant-bound during load | `evidence/V3-T23-performance.json` |
| NFR-005 | resource-access events 至少支持每租户 1,000,000 条且保留 90 天的索引查询；写入失败不能阻塞核心问答，但必须有降级审计事件。 | V3-T19,V3-T23 | 1,000,000-event/tenant/90-day fixture, prune, EXPLAIN | Analytics 90 天查询、retention cleanup、event writer failure metric | tenant event partition/filter and no content | `evidence/V3-T23-analytics-capacity.json` |
| NFR-006 | 写入成功响应与随后读取必须在同一事务提交后可见；批量部分失败返回逐项结果，重试不重复创建关系。 | V3-T06,V3-T09,V3-T11,V3-T13,V3-T26 | transaction visibility, batch retry/idempotency tests | write→refresh/hard reload; partial batch lists every result | atomic rows and tenant-scoped idempotency | `evidence/V3-T09-resource-actions.json` |
| NFR-007 | migration 可重复执行、前向升级可中断恢复；SQLite 和 PostgreSQL 都有可执行 verification；生产 rollback 默认采用旧代码忽略新表的兼容回退。 | V3-T01,V3-T07,V3-T13,V3-T19,V3-T25,V3-T38 | five ledger/checksum repeat, dialect verify, reverse dry-run | phase canary can flag-off and rerun without partial ledger | FK/CHECK and audit preservation | `evidence/V3-T38-migration.json` |
| NFR-008 | 生产没有 `EKB_APP_CREDENTIAL_KEY` 时禁止启用 app credentials；密钥轮换通过版本化 key id 完成，旧密文在过渡窗口可解密。 | V3-T27,V3-T28 | missing-key fail-closed, active/previous dual-decrypt and rollback tests | Apps credential update/rotate shows prefix/status only | dedicated key, bounded dual window, no signing-secret reuse | `evidence/V3-T27-credentials-rotation.json` |
| NFR-009 | 审计记录保留时间不少于业务合规策略要求，purge 业务对象不得删除审计；查询结果需带 trace/request id。 | V3-T11,V3-T17,V3-T19,V3-T38 | purge/audit retention, cancel audit, request-id contract | Recycle purge and QA cancel remain queryable with trace | audit tenant filter and secret redaction | `evidence/V3-T38-audit-retention.json` |
| NFR-010 | 旧 app-v2 M0–M5 adapters 和既有端点契约测试继续通过；新增 envelope 只用于新增 list endpoints。 | V3-T06,V3-T18,V3-T20,V3-T29,V3-T37 | compatibility contract suite for `/me`, refresh, conversations, KB/admin/sync | old pages/clients retain required fields and paths | old permission/error semantics preserved | `evidence/V3-T38-compatibility.json` |
| NFR-011 | 页面/组件/布局不导入 `src/lib/api.ts`、旧 `src/pages/**`、旧 `src/components/**` 或旧 CSS，不持有 token/hash 解析逻辑。 | V3-T31,V3-T37 | conditional import scan fails on any match | app-v2 pages reload through adapters only | no token/hash logic in UI | `evidence/V3-T37-import-audit.json` |
| NFR-012 | 十页在设计稿 desktop 视口和 390x844 视口无核心操作遮挡、不可达控件或横向滚动泄漏；表格在窄屏有明确折叠/滚动策略。 | V3-T32–V3-T36,V3-T38 | Codex in-app Browser viewport/overflow assertions | each page completes core action at both viewports | permission/error controls remain reachable | `evidence/V3-T38-responsive.json` |
| NFR-013 | 交互元素可键盘访问，图标按钮有 aria label，焦点态可见，状态不只用颜色表达，支持 `prefers-reduced-motion`。 | V3-T31–V3-T36 | accessibility assertions and keyboard route smoke | keyboard can reach forms/dialogs/retry/cancel | no hidden permission action | `evidence/V3-T38-accessibility.json` |
| NFR-014 | 每个 API 响应带 `X-Request-Id`/trace 关联；migration、应用连接、取消、权限拒绝和审计写失败可检索。 | V3-T01,V3-T17,V3-T19,V3-T27,V3-T38 | header/audit/metric lookup tests | UI error retry exposes request id | trace metadata redacted and tenant-scoped | `evidence/V3-T38-observability.json` |
| NFR-015 | LLM/联网提供商错误只作为可恢复 provider 风险处理；安全、租户、迁移、契约和业务流程测试失败不得归因于供应商而放行。 | V3-T16,V3-T17,V3-T28,V3-T38 | provider error vs security/business gate classifier | provider retry/config route; internal failure blocks | no provider bypass of policy | `evidence/V3-T38-provider-risk.json` |
| NFR-016 | 自动测试、lint、类型检查、构建、migration verify/rollback dry-run、API 契约和浏览器场景全部通过；P0/P1/P2=0，console=0。 | V3-T38 | exact TN command set and stop conditions | ten pages success/error/permission after reload | any gate nonzero blocks cutover | `evidence/V3-T38-fullstack-tn.json` |
| NFR-017 | feature flags 支持按租户/环境启停，灰度观测至少覆盖 1 个内部租户和 1 个客户租户，失败可在不丢数据情况下回退。 | V3-T01,V3-T07,V3-T13,V3-T19,V3-T25,V3-T37 | canary flag and rollback dry-run | phase-level flag-off preserves service and rows | no destructive rollback or audit loss | `evidence/V3-T37-rollout.json` |
| NFR-018 | 每条 FR/NFR 都必须在 [`04_verification-matrix.md`](./04_verification-matrix.md) 具有里程碑、自动测试、浏览器场景、安全检查和证据文件映射。 | V3-T38 | matrix completeness/link checker | document audit reports 52 FR + 18 NFR rows | no unowned requirement | `evidence/V3-T38-doc-gates.json` |

### 4.1 NFR acceptance registry

The preceding rows preserve the exact NFR text from `01_requirements.md`; this registry supplies the collision-free one-to-one acceptance IDs and primary task/evidence ownership.

| NFR | AC | Primary task | Test / observable proof | Security / evidence |
|---|---|---|---|---|
| NFR-001 | AC-V3-NFR-001 | V3-T01 | tenant isolation, actor/tenant turn and attachment negative fixtures | no resource existence leak; `evidence/V3-T38-security.json` |
| NFR-002 | AC-V3-NFR-002 | V3-T05 | global prefix lookup, constant-time hash verification, live capability test | prefix UNIQUE and live membership; `evidence/V3-T05-api-keys.json` |
| NFR-003 | AC-V3-NFR-003 | V3-T02 | concurrent policy bump and refresh/`GET /me` contract | atomic existing `tenants.policy_version`; `evidence/V3-T02-policy-refresh.json` |
| NFR-004 | AC-V3-NFR-004 | V3-T23 | list/analytics/SSE first-event p95 benchmarks | bounded tenant queries; `evidence/V3-T23-performance.json` |
| NFR-005 | AC-V3-NFR-005 | V3-T23 | 1,000,000 events per tenant/90 days, EXPLAIN, prune and writer-degrade test | tenant filter and non-blocking event writer; `evidence/V3-T23-analytics-capacity.json` |
| NFR-006 | AC-V3-NFR-006 | V3-T09 | transaction visibility and partial-batch retry/idempotency test | per-item ACL and no duplicate relation; `evidence/V3-T09-resource-actions.json` |
| NFR-007 | AC-V3-NFR-007 | V3-T38 | five immutable ledger repeat, dialect verify and reverse dry-run | FK/CHECK, audit retention and phase rollback; `evidence/V3-T38-migration.json` |
| NFR-008 | AC-V3-NFR-008 | V3-T27 | missing-key fail-closed and active/previous dual-decrypt rotation test | dedicated key and bounded window; `evidence/V3-T27-credentials-rotation.json` |
| NFR-009 | AC-V3-NFR-009 | V3-T38 | purge/audit retention, cancel audit and request-id contract | audit survives purge and redacts secrets; `evidence/V3-T38-audit-retention.json` |
| NFR-010 | AC-V3-NFR-010 | V3-T38 | `/me`, refresh, conversation, KB, admin user-create and sync compatibility suite | legacy required fields/permissions unchanged; `evidence/V3-T38-compatibility.json` |
| NFR-011 | AC-V3-NFR-011 | V3-T37 | real conditional forbidden-import scan and app-v2 adapter boundary test | no direct API/token logic in new UI; `evidence/V3-T37-import-audit.json` |
| NFR-012 | AC-V3-NFR-012 | V3-T38 | Codex in-app Browser desktop/390x844 overflow and reachability scenarios | permission/error controls remain reachable; `evidence/V3-T38-responsive.json` |
| NFR-013 | AC-V3-NFR-013 | V3-T38 | keyboard, aria, focus and reduced-motion assertions | no hidden permission action; `evidence/V3-T38-accessibility.json` |
| NFR-014 | AC-V3-NFR-014 | V3-T38 | request-id header and audit/metric retrieval tests | trace metadata tenant-scoped/redacted; `evidence/V3-T38-observability.json` |
| NFR-015 | AC-V3-NFR-015 | V3-T38 | provider-risk classifier with internal security/business gate failures | provider cannot bypass policy; `evidence/V3-T38-provider-risk.json` |
| NFR-016 | AC-V3-NFR-016 | V3-T38 | exact backend/web/migration/contract/browser TN command set | any nonzero gate blocks cutover; `evidence/V3-T38-fullstack-tn.json` |
| NFR-017 | AC-V3-NFR-017 | V3-T37 | internal/customer tenant canary flag and data-preserving rollback | no destructive rollback/audit loss; `evidence/V3-T37-rollout.json` |
| NFR-018 | AC-V3-NFR-018 | V3-T38 | requirement/AC/task/link completeness checker | every FR/NFR has owner and evidence target; `evidence/V3-T38-doc-gates.json` |

## 5. API/DB/migration/rollback gates

### 5.1 API contracts

执行 `/Users/alin/EKB/.venv/bin/pytest -q apps/api/tests/v3/test_contracts.py apps/api/tests/v3/test_tenant_isolation.py apps/api/tests/v3/test_qa_actor_tenant_scope.py apps/api/tests/v3/test_compatibility_baseline.py`。必须覆盖 response optional fields (`file_size`, `object_ref`)、`PATCH /conversations/{id}` rename/archive、body 形式的 `POST /auth/refresh`、`GET/DELETE /me/sessions`、`POST /qa/turns/{turn_id}/cancel` actor+tenant scope，以及 200 synchronous app run (`SUCCEEDED|FAILED`)。

### 5.2 Schema and migration

执行：

```bash
cd /Users/alin/EKB/apps/api
/Users/alin/EKB/.venv/bin/python -m ekb_api.migrations.v3_fullstack --database-url sqlite:///./ekb_v3_verify.db --through v3_005_apps --verify --expect-versions v3_001_identity,v3_002_content,v3_003_assistant,v3_004_analytics,v3_005_apps --explain
/Users/alin/EKB/.venv/bin/python -m ekb_api.migrations.v3_fullstack --database-url "$EKB_DATABASE_URL" --through v3_005_apps --verify --expect-versions v3_001_identity,v3_002_content,v3_003_assistant,v3_004_analytics,v3_005_apps --explain
/Users/alin/EKB/.venv/bin/python -m ekb_api.migrations.v3_fullstack --database-url sqlite:///./ekb_v3_verify.db --through v3_005_apps --rollback --dry-run --reverse --expect-versions v3_001_identity,v3_002_content,v3_003_assistant,v3_004_analytics,v3_005_apps
```

Verify 必须证明：每阶段自己的表和索引、FK/check 或 service+verifier 补偿、SQLite `PRAGMA foreign_keys=ON`、`auth_sessions` 必要列和索引、Document `file_size/object_ref` additive nullable backfill、`documents(tenant_id,idempotency_key)` 唯一规则、`Document.updated_at` inferred trash backfill、`metadata.inferred=true`、每次 cascade 的 `deletion_batch_id`、`audit_logs` 保留和五行 checksum。

### 5.3 Final TN stop conditions

任何一个 tenant-scope、actor-scope、迁移 checksum、FK/check、rollback、契约、业务流程、P0/P1/P2 或 console gate 非零，都不能切换；仅在全部内部门禁通过后，才可把纯 provider/LLM 环境错误记录为 non-blocking 风险。最终 TN 的证据文件由 `03_plan.md` 的 V3-T38 生成并回填本矩阵。
