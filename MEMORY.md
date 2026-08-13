# EKB 项目记忆

> 更新时间：2026-08-13（EKB Core Rebuild Tier 3 Spec 00–14 已获用户确认并进入 Ready for Plan；历史验收账号已脱敏）
> 用途：为后续会话保留项目定位、权威文档、实施进度、验证证据与未决事项。不得记录密码、令牌或其他秘密。

## 项目定位与技术基线

- EKB 是面向企业内部员工与外部运维客户的私有化优先智能知识库：提供带引用的 AI 问答、文档管理、全文/语义检索、多租户隔离、RBAC/ACL、审计和配额。
- 前端为 React 19 + TypeScript + Vite；新界面位于 `apps/web/src/app-v2/`，遵守 adapter facade（仅 adapters 可使用 API client，页面不直接 `fetch`）。
- 后端为 Python 3.9 + FastAPI + SQLAlchemy；当前开发数据层为 SQLite，生产目标为 PostgreSQL + pgvector，配合 Redis 和 S3/MinIO 兼容对象存储。AI 目前使用 DeepSeek/OpenAI 兼容链路；RAGFlow 是待验证的可替换适配层。
- API 前缀为 `/api/v1`，健康检查为 `/healthz`。v3 迁移为手写 DDL + `schema_migrations` checksum ledger；已应用迁移不可修改，只能新增迁移并在服务层修复兼容问题。

## 文档读取顺序与权威关系

1. 先读本文件，再读 `docs/README.md`（当前 authority index）。
2. 对 v3 全栈合同，按 `docs/pmos/features/2026-08-09_ekb-fullstack-v3/README.md` 指定顺序读取：`01_requirements.md` → `02_spec.md` → `03_plan.md` → `04_verification-matrix.md`；`03_plan_review.md` 仅为审查背景，不是实现证据。
3. `docs/EKB全栈开发实施文档_v3.1_2026-08-10.md` 是较晚的实际执行与验收记录：v2 的“纯前端、禁止改后端、核心动作 disabled/unavailable”边界已被替代。修改 API、schema、安全或发布边界前，仍须回读 v3 spec/matrix 及相应 v1 基线文档。
4. 历史基线优先参考 `README.md`、`docs/企业级知识库Spec_v1.0_2026-08-07.md`、API/数据模型/架构/安全/测试文档；v1 完整复盘与技术债见 `docs/复盘报告与下一版本规划_M5-5_v1.0_2026-08-08.md`。发生冲突时遵守 `docs/文档治理与研发交付规范_v1.0_2026-08-07.md` 的权威与变更记录规则。

## 已完成进度（已记录证据，非生产上线声明）

- v1 基线：文档记录了 M0–M5 的 tracer-bullet、单租户到多租户权限、治理运营和生产强化工作。历史质量快照为 130 题评估集、M4 后首答准确率 98.0%、引用正确率/拒答率 100%、QA P95 2715ms、Search P95 37.6ms、单机 SQLite 4.5 QPS；生产签字和部分上线前门禁仍未完成。
- v3.1 Phase 0（布局统一）已完成：十个模块统一在 GlobalShell 内切换，知识库和 AI 助手不再整页跳转。
- v3.1 Phase 1（个人中心全栈）已完成：复用 `v3_001_identity`，实现 profile/preferences/password/sessions/API keys/notifications 的服务、路由、adapter 与页面；文档记录 `npm run build` 通过、Vitest 18 passed、真实服务 `smoke_v3_profile.py` 为 33/0。
- v3.1 Phase 2（回收站全栈）已完成：`v3_002_content` 的 `trash_items` 投影、backfill、恢复/永久删除和前端 adapter/page 已记录完成；文档记录 backfill 为 4 KB、5 DOCUMENT、531 CONVERSATION，`smoke_v3_trash.py` 为 36/0，Vitest 为 27 passed，构建通过。以上是已记录的阶段证据；本次会话未重新运行全套验证。
- v3.1 P0（漂移审计 & ADR）已完成：
  - 审计了 v3_001_identity / v3_002_content / v3_003_analytics / v3_004_apps 四个已 applied 迁移及其在 `schema_migrations` 中的 checksum 台账；确认 authority 的 `v3_003_assistant`→`v3_004_analytics`→`v3_005_apps` 命名与代码 `v3_003_analytics`→`v3_004_apps` 漂移，但 checksum 已锁定不可修改。
  - 审计了后端 analytics router / service / store（发现 record_access 用 SQLite 专属的 INSERT OR IGNORE，`get_ops_dashboard` 的 days 参数未过滤 feedback 和 review）。
  - 审计了前端 analytics adapter / AnalyticsPage / AccessTrendChart.tsx（发现图表无涨红跌绿约定、无峰值标记、无徽章）。
  - 决策结论（ADR-002）：**绝不修改已 applied 迁移**；新增「补偿迁移 v3_005_analytics_compat」补齐 `support_feedback` 表 + 缺失 analytic 索引；服务层代码对齐迁移实际锁定列名（`access_kind`、`occurred_at`、`occurred_day`）。
- v3.1 Phase 3（Analytics 全栈闭环）已完成并通过本次实跑验证：
  - 后端：新增 [v3_005_analytics_compat.py](file:///Users/alin/EKB/apps/api/ekb_api/migrations/v3_005_analytics_compat.py)，CHAIN 在 [v3_fullstack.py](file:///Users/alin/EKB/apps/api/ekb_api/migrations/v3_fullstack.py#L58-L63) 末尾注册；support_feedback 表（10 列 + FK + CHECK）+ `ix_support_feedback_tenant_status` + `ix_access_events_tenant_time` + `ix_access_events_resource` 全部 CREATE IF NOT EXISTS。
  - 后端兼容性修复：[v3_analytics.py record_access](file:///Users/alin/EKB/apps/api/ekb_api/services/v3_analytics.py#L150-L184) 改为按 dialect 分支：PostgreSQL → `INSERT … ON CONFLICT DO NOTHING`；SQLite → `INSERT OR IGNORE`（保留原语义 + 性能）。
  - 后端运营看板修复：[store.py get_ops_dashboard](file:///Users/alin/EKB/apps/api/ekb_api/store.py#L1685-L1709) 对 Feedback 和 ReviewItem 新增 `created_at >= since` 过滤，days 参数现在实际生效。
  - 前端 SVG 图表增强：[AccessTrendChart.tsx](file:///Users/alin/EKB/apps/web/src/app-v2/pages/analytics/AccessTrendChart.tsx) 实现涨红跌绿（Rise #E03131 / Fall #00A870 / Flat #3B82F6），加上升/下降百分比徽章、峰值圈+竖线、谷值虚线圈、线性渐变面积、图例（访问量实线/访客虚线）、<title> tooltip；渐变 id 基于 data 非加密散列避免多实例冲突。
  - 本次实跑验证证据：
    - 迁移：v3_005_analytics_compat apply=True、verify=PASS（1 table + 3 indexes）、rollback_dry_run 4 对象、blocked_reason=None；
    - 编译：py_compile 4 文件 OK；
    - 写入：record_access 对 KB/CONVERSATION 两条事件写入成功；
    - 前端：Vitest `v3.analytics.test.ts` 11/11 passed；tsc --noEmit 无错误；Vite build ✓ 4644 modules，CSS 101.92KB / JS 606.63KB（160KB gzip），built in 1.54s。

## 待完成与关键技术债

- v3.1 Phase 4（Apps 全栈闭环）已完成并通过本次实跑验证：
  - **P4-Pre 审计结论（ADR-003 思路）**：v3_004_apps 已 applied 但表未创建（apply 返回 dict，且 DDL 中 FK 引用了未建立的表/列）。严格遵守 ADR-002 原则：**绝不改已 applied checksum**。
  - **CHAIN 契约修复**：在 [v3_fullstack.py](file:///Users/alin/EKB/apps/api/ekb_api/migrations/v3_fullstack.py) 新增 `_wrap_apply_result / _wrap_verify_result / _wrap_rollback_result`，把 v3_001~v3_004 返回的 dict（status/checksum/applied/reason）统一包装为带 `version / checksum / applied / backfill_counts` 属性的 SimpleNamespace，CHAIN CLI 可穿越 v3_004 跑到 v3_005 及以后，不再抛 AttributeError: 'dict'。
  - **补偿迁移 v3_006_apps_compat**：以 `CREATE TABLE IF NOT EXISTS` 创建 02_spec.md 定义的 4 张权威表：
    - `app_catalog(slug PK, display_name, provider_name, description, category, capabilities JSON, recommended_rank, enabled, ...)` — CHECK(slug IN feishu/wecom/github/tencent-docs/analytics-pro/audit)；
    - `app_installations(id, tenant_id PK, slug, status IN (INSTALLED/CONFIGURED/CONNECTED/ERROR), installed_by, configuration JSON, last_connected_at, ..., UNIQUE(tenant_id, slug), FK(tenant_id,slug)→catalog)`；
    - `app_credentials(id PK, tenant_id, installation_id, credential_name, prefix(<=32), ciphertext, key_version, previous_ciphertext, previous_key_version, previous_expires_at, status IN (ACTIVE/REVOKED), revoked_at, UNIQUE(tenant_id, installation_id, credential_name), FK→app_installations)`；
    - `app_runs(id PK, tenant_id, installation_id, slug, run_type IN (CONNECTIVITY_CHECK/SYNC/ACTION), status IN (SUCCESS/FAILED/RUNNING), started_at, finished_at, error_message, triggered_by, UNIQUE(tenant_id, installation_id, id))`；
    - 外加 `ix_app_installations_tenant_status / ix_app_credentials_installation_status / ix_app_runs_tenant_installation_started` 三个索引；PostgreSQL 方言自动把 JSON → JSONB。
  - **种子目录**：v3_006 apply 时 upsert 6 条权威应用（飞书/企微/GitHub/腾讯文档/Analytics Pro/审计），capabilities 为飞书 `["oauth","sync_docs","audit_access"]`、企微 `["oauth","sync_docs","push"]`、GitHub `["oauth","sync_code","webhooks"]`、腾讯文档 `["oauth","sync_docs","edit"]`、Analytics Pro `["reporting","export","api"]`、Audit `["audit_logs","retention","reporting"]`。
  - **后端 Service（v3_apps.py）**：
    - `list_catalog / install_app / get_app_detail / configure_installation / connect_installation / create_credential / revoke_credential / record_run / list_runs / list_credentials`；
    - **凭据加密**：专用密钥派生 — `_fernet_key = base64.urlsafe_b64encode(sha256(EKB_MASTER_KEY).digest())`，用 `cryptography.fernet.MultiFernet([Fernet(key)])` 做密钥版本化（`ekb-apps-kms-v1`）；前缀取明文前 8 字符，DB 仅存 ciphertext（长度 ≈ 140 字节，典型 Fernet 信封），**日志/detail/list API 永远零明文泄露**；
    - **写一次语义**：`create_credential` 的返回 `CredentialCreateResult.plaintext_once` 仅在创建响应中设置一次，之后从 detail / list / runs 读取全为 None；
    - **状态机**：INSTALL(INSTALLED) → PUT configure(CONFIGURED + updated_at) → POST connect(CONNECTED + record_run CONNECTIVITY_CHECK SUCCESS)，任何空配置或未安装则走 error 分支不破坏现有状态。
  - **Router 扩展（apps.py，纯 additive）**：保留旧的 `/apps/v1` 全部端点，在 `/apps/v2` 前缀新增：`GET /{slug}`（聚合 detail=app+installation+credentials+runs）、`PUT /{slug}/configure`、`POST /{slug}/connect`、`POST /{slug}/credentials`、`POST /{slug}/credentials/{credential_id}/revoke`、`GET /{slug}/runs`；全部返回 200/201，无破坏性变动。
  - **前端**：types/api.ts 新增 `AppDetailResponse`（嵌套 `AppInstallationView | AppCredentialItem[] | AppRunItem[]`）；API client 新增 6 个 Phase 4 endpoint；adapter createAppsAdapter 暴露 `getDetail / configure / connect / addCredential / revokeCredential / listRuns`；AppsPage.tsx 新增右侧详情抽屉：目录页 → 点应用卡片滑出 Drawer，含 4 个区块：
    1. 卡片头（显示名、描述、provider、分类徽章、当前安装状态胶囊）
    2. 安装 & 配置（安装按钮 → 展开 configuration form → 保存 → 连接 → status 胶囊 INSTALLED→CONFIGURED→CONNECTED）
    3. 凭据（前缀显示、"明文只返回一次"Toast 提示、复制按钮、撤销按钮、撤销后 status→REVOKED 置灰）
    4. Runs 审计表（type/status/started_at/finished_at/triggered_by/error_message）。
  - 本次实跑验证证据：
    - 迁移：v3_006_apps_compat apply=True、verify=PASS（4 表 + 3 索引 + 6 条种子）、rollback_dry_run 13 对象、blocked_reason=None；CHAIN 现在 6 步全部跑到（v3_001→v3_002→v3_003→v3_004→v3_005→v3_006 无 dict 包装错误）；
    - 后端：py_compile 4 新文件 OK（v3_006 / v3_apps / apps_router / v3_fullstack wrapper）；Phase 4 专用 smoke `smoke_v3_apps_final.py` 8/8 passed：catalog(6)、install→INSTALLED、detail 全链路、configure→CONFIGURED、connect→CONNECTED+CONNECTIVITY_CHECK run、create_credential 前缀=sk-this- + ciphertext 140B、detail 零明文泄露、revoke→REVOKED+revoked_at、Fernet 解密往返匹配；
    - 前端：tsc --noEmit 0 error；Vitest 5 文件 49/49 passed（其中 v3.apps.test.ts 11/11，覆盖 adapter 映射 + 抽屉 UI state + 写一次明文边界 + 撤销置灰）；Vite build ✓ 4644 modules in 1.44s，CSS 101.92KB / JS 643.43KB（170KB gzip）。
- 每阶段仍需构建、Vitest、真实运行服务 E2E 冒烟、后端日志和前端 adapter 边界审计；全部完成后还需十页双视口走查、`design-qa.md` 更新、旧 UI 引用审计和生产构建复核。
- v1 遗留 P0/P1：迁移至 PostgreSQL + pgvector、接入真实 S3/MinIO、关闭 M0 开放问题与安全评审签字、补齐 Playwright E2E 截图/回归、定位 HTTP SSE 偶发拒答（G042/G077）。后续还包括权限/新鲜度/对抗集 A/B、OpenTelemetry、QPS >= 50 横向扩展、答案缓存、成本观测和 RAGFlow 适配。
- 已知能力缺口：头像上传依赖对象存储；回收站仅记录 `expires_at`，尚无自动清理任务。实现服务 SQL 前必须以已锁定迁移 DDL 为准，不能依赖记忆中的列名。

## 工作区与规模口径

- 2026-08-11 的工作区处于大量未提交改动状态（`git status --short` 约 195 项，含修改、删除与未跟踪文件）。后续任务必须在此基础上增量工作，不得 `reset`、`checkout`、清理或覆盖无关改动。
- 规模快照：`apps/` 约 47,211 行；全仓约 294,124 行。两者均为粗略 `wc` 口径，包含部分文档、生成物和证据文件，随 `.playwright-cli/`、`docs/evidence/`、`output/`、依赖或未跟踪文件而变化，不能等同于净业务源代码，也不代表已达到 50 万行目标。

## 当前任务与未决问题

- 已完成（本次）：v3.1 P0 迁移漂移审计（ADR-002）+ Phase 3 Analytics 全栈实装 + Phase 4 Apps 垂直全栈实装 + Phase 5 Dashboard & 能力缺口修复 + **Phase 5.1 Rail「最近访问/收藏夹/回收站」三点立即可用修复**（A 类：最近访问跳工作台真实区、回收站跳独立 RecyclePage（真实接线 trash adapter 全 6 功能）；C 类透明化：收藏夹/项目/分享/导出统一 phase 胶囊 + 规划中 tooltip，AssistantSidebar 收藏/项目 unavailable-card 替换为具体路线图阶段说明）。全部通过实跑验证（49/49 Vitest、tsc+build=0）。
- 进行中（下一批）：v4 P2 内容治理全栈闭环 —— favorites 垂直全栈（补偿迁移 v3_007 + service + router + adapter + 页面 star 切换 + Rail 收藏列表）、folders、tags、shares（signed share link 复用 Fernet key derivation）。这些工作包可在当前 SQLite 上实现，不阻塞于 P1 的 PG/S3/Redis 基础设施切换。
- 后续（按用户确认资源后）：P1 基础设施（PostgreSQL+pgvector、MinIO/S3 对象存储、Redis 缓存+后台任务）→ P2 Assistant+十页双视口回归 → P3 企业身份（OIDC/SAML/LDAP/SCIM + 角色 CRUD + 连接器框架）→ P4 平台化。
- 未决：第三方引入前仍需固定 commit SHA、逐文件许可证复核、SBOM、来源标注、安全审查和 API/权限/性能回归门禁；不能仅凭 GitHub star、fork 或仓库 size 直接采用项目。
- 未决：50 万行只是长期容量情景，不是验收目标；代码增长必须由真实功能、测试、文档、NFR、安全和运维需求驱动，禁止复制第三方或生成文件凑行数。

## 2026-08-11 Phase 4 Apps 垂直里程碑（本次交付）

- **ADR-003 漂移策略（思路延续 ADR-002）**：v3_004_apps 的 DDL 缺失 + 返回 dict 契约错误的已 applied checksum 一律不动；新功能走补偿迁移 v3_006_apps_compat + 工具链层 dict→SimpleNamespace 包装器。
- **CHAIN 工具链贯通**：v3_fullstack.py 三个 wrapper（apply/verify/rollback）把 v3_001~v3_004 的 dict 结果映射回 MigrationResult-like 协议，6 步 CHAIN 现在可从 idempotent init 一路跑到 v3_006，verify 全部 PASS。
- **四张权威表 + 索引 + 种子**：app_catalog 6 条、app_installations、app_credentials、app_runs + 3 个 analytic/status 索引；所有约束 CHECK 与 FK 按 02_spec.md §Apps 精确落地；PostgreSQL 方言自动 JSON→JSONB。
- **凭据安全模型**：专用 Fernet 密钥派生 + `ekb-apps-kms-v1` 版本号；DB 存前缀（前 8 字符）+ 密文（140B Fernet 信封），detail/list/list_credentials/detail_response/日志永远零明文；create 返回明文 only-once；revoke 置 REVOKED + 记录 revoked_at；撤销后密文仍可解密（用于合规导出），但 UI 状态置灰。
- **应用状态机 + run 审计**：INSTALL(INSTALLED) → configure(CONFIGURED) → connect(CONNECTED + CONNECTIVITY_CHECK run SUCCESS)；任何步骤失败不回退前一步状态，单独记录 ERROR run。
- **Router 纯 additive**：旧 `/apps/v1` 端点零改动；新增 `/apps/v2` 前缀 6 个 Phase 4 端点；detail API 一次聚合 app + installation + credentials + runs 1+N+M+K 形状，前端适配器一次请求即可渲染完整 Drawer。
- **前端 UX 细节**：AppsPage 卡片→Drawer；安装/配置/连接三段式交互；凭据创建后弹出「明文仅显示一次，请复制保存」Toast；撤销按钮带确认；Runs 表按 started_at desc 截断最近 20 条，status 用 Pill 色标（SUCCESS=绿、FAILED=红、RUNNING=蓝）。
- **交付证据实跑**：py_compile OK；smoke 8/8 passed（覆盖 catalog/install/detail/configure/connect+run/create_credential+零明文+Fernet+revoke 全链路）；tsc 0 error；Vitest 49/49（apps 11/11）；Vite build 4644 modules in 1.44s，构建产物无新引入的外部 runtime 依赖。

## 2026-08-11 Phase 3 Analytics 里程碑（本次交付）

- **ADR-002 漂移策略已定案**：绝不改 v3_001~v3_004 已 applied 的 checksum；权威 spec 与实际代码列名/版本号的漂移一律用「新增版本的补偿迁移 + 服务层代码对齐实际列」解决。
- **迁移补偿**：v3_005_analytics_compat 作为 v3_004_apps 之后的追加补偿，补齐 support_feedback（10 列 + FK 到 tenant_memberships + OPEN/CLOSED CHECK + 非空 message CHECK）、ix_support_feedback_tenant_status，以及 resource_access_events 上的两个 analytic 索引（ix_access_events_tenant_time / ix_access_events_resource）。PostgreSQL 方言自动把 JSON → JSONB。
- **跨 DB 写路径**：v3_analytics.record_access 现在按 dialect 选择 PG 的 `INSERT … ON CONFLICT DO NOTHING` 与 SQLite 的 `INSERT OR IGNORE`，保证同一个 writer 在迁移锁表的前提下，两条路线都能实现「碰撞不 double-count、无异常抛」的幂等性。
- **运营看板时间框正确**：store.get_ops_dashboard 的 Feedback 与 ReviewItem 现在都带上了 `created_at >= since`，days 参数能真实过滤窗口内的满意度、UP/DOWN、盲区 PENDING 审核计数，而不是之前返回租户全量历史。
- **Analytics 可视化规范**：AccessTrendChart.tsx 正式落实「涨红跌绿」（Rise #E03131 ▲、Fall #00A870 ▼、Flat #3B82F6 ●），带百分比徽章、峰值圈+竖线、谷值虚线圈、渐变填充、访问量实线 / 独立访客虚线的双图例；x/y 轴文字、网格与 tooltip 全部重写为 inline SVG，无外部依赖。
- **交付证据**：v3_005 apply/verify/rollback_dry_run=PASS，py_compile=OK，record_access 实写 2 事件 OK；前端 Vitest 11/11 passed，tsc 0 error，Vite build 4644 modules 在 1.54s 内通过。


## 2026-08-11 Deployment 部署里程碑（历史记录，当前状态未重新验证）

> **验收账号**：历史记录中的账号与口令已脱敏；后续只从受管运行环境获取，不在项目记忆中保存。
> **历史访问目标**：`[production host]`（HTTP 80 也开放，兼容国内运营商封 443 的客户链路）。
> **历史本地 SSH 转发验收路径**：生产主机地址和账号已替换为 `[REDACTED]`；从受管连接配置建立本地端口转发后打开 `[REDACTED]`。

### 历史目标服务器与接入信息

- 生产服务器（连接信息已脱敏）使用 CentOS 8，系统自带 Python 3.6 **太旧不能直接跑 EKB API** → 架构选型改为「前端静态 rsync + API Docker 离线镜像 + nginx 反代」。
- 证书：历史部署复用服务器已有 `[REDACTED]` 证书，HTTPS 443 当时可用；HSTS 保守配置 max-age=6 个月。
- 域名冲突处理：历史部署曾处理既有 Nginx server_name 冲突并释放目标给 EKB；具体域名和备份路径统一记为 `[REDACTED]`。

### 最终部署架构（解决 Python 3.6 + 国内网络双约束）

```
         用户浏览器 ([production host])
                 │
                 ▼
         nginx (80/443, default_server, Host=[production host] | _)
            ├── /assets/ /index.html 静态缓存 → /opt/ekb/web/current (Vite dist, rsync 上传)
            ├── /api  |  /healthz  |  /auth  |  /docs  |  /openapi.json
            └── /api/v1/* /qa/* /kb/* /me/* /admin/* /apps/*
                              │ proxy_pass (keepalive=32, 300s 读写超时)
                              ▼
                  127.0.0.1:8000  (Docker 容器 ekb-api:latest, 仅本机回环)
                              │  bind 127.0.0.1 防外部直连攻击
                              ▼
                    /opt/ekb/data (volume 挂载进容器 /data, uid=1000)
                      └── ekb.sqlite3 (SQLite 生产过渡; 未来切 PG)
```

- **API Docker 镜像**：`linux/amd64`，在本机（Mac）用 `docker buildx build --platform linux/amd64 --load` 构建 → `docker save | gzip -6` → `rsync -av --progress` 上传 → 服务器 `docker load` → `docker run` 的生产目标和 origins 均从受管运行环境注入；具体连接参数记为 `[REDACTED]`。
- **前端静态**：本机 `pnpm -C apps/web build` 产物 → rsync 到 `/opt/ekb/web/releases/<ts>` → `ln -sfn` 切 `current/`，原子切换零宕机，index.html 禁用缓存（no-cache/no-store），`assets/` 缓存 1 年（public, immutable）。
- **Nginx 组织方式**：`/etc/nginx/conf.d/ekb.conf`（80+443 两个 server block，default_server） + `/etc/nginx/ekb-api-locations.inc`（API 反代片段） + `/etc/nginx/ekb-spa-locations.inc`（SPA 静态 + 安全 deny 片段）。80 与 443 使用相同 include，避免复制粘贴漂移。

### 修复记录（本次部署中闭环解决）

| # | 现象 | 根因 | 修复 |
|---|---|---|---|
| D-1 | `docker run` 端口绑定失败：`127.0.0.1:8000 already in use` | 旧 `systemd ekb-api.service`（前版本 Python 3.8 uvicorn）仍在运行占 8000 | `systemctl stop/disable ekb-api.service` + `kill -9` 残留进程 |
| D-2 | 容器启动 uvicorn `IndexError: 4 in parents[4]` | `core/config.py` `_load_dotenv()` 硬编码向上 4 层，在 Docker 内 `/app/ekb_api/core/config.py` 只有 3 层 parents | 改为循环 `[here, *here.parents[:8]]` 搜索 `.env`，找不到直接返回（Docker 通过 `--env-file` 注入） |
| D-3 | `POST /auth/login` 返回 404 Not Found | API 真实路由前缀是 `/api/v1/auth/login`（main.py `include_router(..., prefix="/api/v1")`），之前以为是裸 `/auth` | nginx location 正则已覆盖 `/api`；登录验收端点统一用 `/api/v1/auth/login` |
| D-4 | 验收账号登录曾返回 `UNAUTHENTICATED` | 旧 systemd 服务遗留口令哈希与受管验收凭据不一致；entrypoint 首次执行时迁移还没建 users 表就报 `no such table: users` | 在受管环境幂等更新验收账号哈希；同时修复 entrypoint 迁移调用参数缺失。项目记忆不保存凭据。 |
| D-5 | 登录走 nginx 返回 `Invalid host header`，直连 127.0.0.1:8000 OK | FastAPI `TrustedHostMiddleware` `allowed_hosts` 只含 `127.0.0.1/localhost/settings.api_host`，生产 Host 被拒 | main.py 支持 `EKB_ALLOWED_HOSTS` CSV 环境变量 + 识别 `"*"` 通配；容器运行时从受管环境注入，保证容器重建不丢失 |
| D-6 | entrypoint 迁移报错 `the following arguments are required: --database-url` | entrypoint 直接调用 `migrate_main()`（空 args）触发 argparse | 改为先读 `EKB_DATABASE_URL / DATABASE_URL / get_settings().database_url` 显式传 `["--database-url", db_url]`，捕获 `SystemExit(0)` 为成功 |
| D-7 | nginx 配置 `set $web_root` 写文件时被 bash 外层双引号展开成空 → `invalid number of arguments in "set"` | heredoc 嵌在 `ssh "..."` 双引号里，`$web_root $uri $host` 都被 bash 展开 | 改为 heredoc 用单引号包 EOF 再按行号 `sed -i` 精确替换；API 与 SPA 公共片段拆成 `.inc` 文件避免复制 |

### 历史实跑验收证据（当时记录，当前未重新验证）

- **容器**：`ekb-api:latest`（ID=6d04a555eb31，488MB），状态 `Up (healthy)`，docker restart unless-stopped，memory 1G / cpu 1.0，仅 bind 127.0.0.1 防外部直接访问。
- **nginx**：`nginx -t` syntax ok + test successful；80/443 LISTEN；`/healthz-nginx` 返回 `{"ok":true,"service":"nginx","scheme":"http(s)"}`。
- **API 直连**：使用受管验收凭据调用 `/api/v1/auth/login` 曾签发有效 access token；随后访问 `/api/v1/me` 的 owner capabilities 生效。凭据和 token 不在记忆中保存。
- **HTTP 反代**：历史上曾经 Nginx 反代验证，token 长度一致，证明当时 TrustedHost 白名单生效；目标统一记为 `[production host]`。
- **HTTPS 反代**：历史上曾用受管解析方式验证证书链和 443 链路；具体目标统一记为 `[production host]`。
- **SPA 前端**：历史上曾返回完整 Vite HTML（app-v2 主题色），静态链路当时贯通；具体 URL 和 Host 统一记为 `[REDACTED]`。
- **本机 SSH 转发验收**：Mac 本机端口转发曾验证登录和 SPA HTML；验收凭据已脱敏。

### 未来每次部署的标准操作流程（scripts/deploy 自动实现）

1. `pnpm -C apps/web build` → 生成 Vite dist。
2. `docker buildx build --platform linux/amd64 --load -t ekb/ekb-api:latest -f apps/api/Dockerfile apps/api`（layer cache 命中通常 <10s）。
3. `docker save ekb/ekb-api:latest | gzip -6 > /tmp/ekb-deploy/ekb-api-amd64.tar.gz`。
4. `rsync` 通过受管 SSH 连接上传前端 dist + 镜像 tar.gz 到服务器；端口和账号统一记为 `[REDACTED]`。
5. 服务器：`nginx` 前端目录原子切 `current` symlink → 加载/切换容器 → 从受管环境确保验收账号可用；不在命令或记忆中硬编码口令。
6. 健康检查：`GET /healthz` 直连 + 经 nginx；使用受管凭据登录并访问 `/me`；`GET /` 必须是 SPA HTML。
7. 失败回滚：前端回滚 symlink 到上一个 release 目录；`docker tag` 回滚上一个 `ekb-api:previous` → `docker run` 旧镜像。

### 未决/后续

- 历史记录中的生产库曾是 SQLite `/opt/ekb/data/[REDACTED]`；按 MEMORY 中 P0 计划，下一阶段迁移到 PostgreSQL + pgvector，届时需要将容器 `DATABASE_URL` 切到受管内网 PG，`/data` 仅保留 Fernet master key、tmp、runs。
- 域名 DNS：历史记录曾要求将 `[production host]` 解析到 `[production host]`；目前不把 DNS、生产 IP 或连接目标视为当前可验证事实。
- 未接入真实对象存储，附件/头像仍在 SQLite BLOB 或本地 `/data`；后续 MinIO/S3 接入后 nginx 需新增 `/files/` presigned 路由或直接反代 S3 兼容网关。
- 镜像构建层仍能优化：目前全层 COPY 每次 174MB rsync；可拆成依赖层 + 代码层，进一步把 `docker save | gzip` 产物从 174MB 压到 ~40MB，rsync 时间从 80s → 10s 级。


## 2026-08-11 Phase 5 Dashboard & 能力缺口全量修复里程碑（本次交付）

> **用户问题触发**：用户发现工作台和多个页面存在大量「能力不可用」占位，询问是否未按文档完成所有开发。经审计，全局不可用文案分为三类（A=后端已有能力但前端未接线；B=后端未实现；C=v4 路线图规划中未开始），本里程碑按优先级顺序修复 A 类（真有功能但前端死占位）+ C 类文案透明化（明确「v4 P? · 模块：××× 规划中」与 disabled title），B 类因 spec 未定义不伪造。

### 三类缺口审计与决策（ADR-004 思路）

- **A 类 · 后端已实现 + 前端未接线 = 必修优先类**：工作台 Dashboard 所有 metric 卡片 + 趋势图 + 分布图 + 最近动态。证据：`ekb_api/routers/analytics.py` 已完整暴露 `GET /analytics/overview`、`/trend`、`/distribution`、`/activity` 4 个端点，`adapter/analytics.ts` 已封装调用，但旧版 [DashboardPage.tsx](file:///Users/alin/EKB/apps/web/src/app-v2/pages/DashboardPage.tsx) 全页硬编码字符串「能力不可用」+ 灰色占位数字。根因：Dashboard 为 Phase 5 延后项，v3 初版先完成布局骨架；与 v3.1 合同实际后端先行不符 → 本轮补上前端接线。
- **B 类 · 后端未实现（v3 spec 未定义）= 不伪造，保留明确提示**：回收站自动清理定时器、头像上传到对象存储、邮件邀请 SMTP。这些依赖外部基础设施（S3/MinIO、Redis 定时任务、邮件网关），v3.1 02_spec 未定义 → 保留 disabled 按钮 + tooltip「需要××× 基础设施」，不在本轮伪造实现。
- **C 类 · v4 路线图规划中 = 文案透明化，不用含糊的 unavailable**：角色 CRUD（v4 P1）、分享端点（v4 P2）、文件夹/共享文档/分类占比（v4 P2）、权限矩阵编辑（v4 P3）。查阅 [EKB后续全栈开发路线图_v4.0_2026-08-11.md](file:///Users/alin/EKB/docs/EKB后续全栈开发路线图_v4.0_2026-08-11.md)，这些能力在路线图 P1/P2/P3 有明确里程碑 → 本里程碑统一把 UI 上的「不可用」灰色占位、`unavailable` 字符串、empty 小标签替换为：彩色 phase 胶囊（蓝=P1、黄=P2、绿=P3，如 `<span style="badge">v4 P1 · 身份能力</span>`）+ 具体开放说明（disabled title + 不可用卡片内描述）。用户点开 tooltip 立即知道「这是规划中的 v4 功能，不会等太久」，而不是「EKB 还没做完」的失败印象。

### A 类关键交付：Dashboard 从死占位 → 实时数据驱动（3 个新图表组件 + DashboardPage 重写）

- **3 个 SVG 自绘图表组件（新增文件）**：
  1. [DashboardTrendChart.tsx](file:///Users/alin/EKB/apps/web/src/app-v2/components/dashboard/DashboardTrendChart.tsx)：访问趋势折线图（访问量蓝色实线 + 独立访客虚线），niceMax Y 轴、峰值圆点、SVG `<title>` tooltip、日期 x 轴、线性渐变填充。props 传 `points[{date,accesses,visitors}] + peak + total`，纯 SVG 无 canvas。
  2. [DashboardDistributionBar.tsx](file:///Users/alin/EKB/apps/web/src/app-v2/components/dashboard/DashboardDistributionBar.tsx)：知识库访问分布横向条形图，按 accesses 排序，max 5 条，右侧数字 + 百分比，颜色按 rank 渐变（蓝→浅蓝）。
  3. [DashboardActivityFeed.tsx](file:///Users/alin/EKB/apps/web/src/app-v2/components/dashboard/DashboardActivityFeed.tsx)：最近访问动态列表，pill 色标（DOCUMENT=蓝、KB=绿、CONVERSATION=橙）+ 图标 + 操作人 + 相对时间 + 资源名称 truncate，maxItems 默认 6。
- **DashboardPage 重写（全闭环 5 卡+3 图+日期筛选）**：
  - 顶部 5 张 MetricCard：`访问概览 / 总文档 / 活跃会话 / AI 问答 / 成员`，从 `analytics.getOverview()` 取真实 `totalAccesses/totalDocuments/activeConversations/totalAnswers/totalMembers`；每张卡片含环比数字 + 趋势徽章 + 14 天 sparkline。
  - 日期筛选：趋势图默认 14 天（Tab 7/14/30）、分布图默认 7 天（7/30）、最近动态 Tab（最近访问/文档/知识库/AI 问答）。用户点 Tab 重新 fetch 对应 days 参数。
  - 三个图表区：左上 TrendChart、左下 DistributionBar、右侧 ActivityFeed，按 v3.1 dashboard.css 网格布局。
  - 底部 RecentAccessSection：Tab 切换 4 类过滤器（文档/知识库/AI 问答），真实从 `analytics.getActivity(20)` 拉数据，表格展示名称/类型/操作人/最近访问。
- **Adapter 契约对齐**：`createAnalyticsAdapter` 的 `getOverview/getTrend/getDistribution/getActivity` 返回值形状精确匹配 Dashboard 组件 props（PageState 包装 + error state fallback 到 StatePanel）。

### C 类文案透明化（5 个页面修复，共 8 处关键变更）

| 页面 | 变更点 | 旧 UI 文案 / 状态 | 新 UI 文案 / 状态 |
|---|---|---|---|
| AppsPage | detailState 初始值 | `'unavailable'`（导致 Vitest 断言死） | `'empty'`（空抽屉 → 用户未点应用时显示「选择左侧应用」） |
| AppsPage | 应用列表「未安装」占位 | unavailable 文本标签 | 灰底 + 明确的「未安装 → 点卡片开始配置」提示 |
| KnowledgePage | 未选知识库时 documentsState/membersState | `'unavailable'`（导致 StatePanel 渲染红灰错误块） | `'empty'`（不渲染错误态，用户选 KB 后再加载） |
| KnowledgePage | 侧栏 3 个标签（文件夹/共享文档/分类占比） | `<small>当前后端未提供</small>` + unavailable | `<small>v4 P2 · 内容治理</small>` + 分类占比标注 `<small>v4 P2 · 治理指标</small>` |
| AssistantPage | 分享按钮 tooltip + disabled | 「分享未实现」 | `v4 P2 内容治理：分享端点（signed share link + ACL 快照）规划中，当前不伪造分享成功` |
| AssistantPage | 其他 2 个 disabled 按钮 | 简短「不可用」 | 每个按钮 title 标注对应 v4 阶段：引用导出、模型切换、高级指令、对话删除批量、提示词编辑器、对话导出 MD/PDF |
| TeamPage | 角色管理面板标题 + unavailable-card | `<strong>角色写入暂不可用</strong>` + `unavailable` + disabled 空 title | 2 个阶段胶囊 `v4 P1 · 身份能力` + `已授权只读` + 文案改为「角色 CRUD 暂不开放」+ `v4 P1 开放：角色类型定义、角色编辑、内置角色继承、成员能力映射` |
| TeamPage | 权限矩阵面板标题 + unavailable-card | `<strong>权限写入暂不可用</strong>` + `unavailable` | `v4 P3 · 企业身份` + `已授权只读` + 文案改为「capability 矩阵编辑、SCIM 同步、批量邀请/禁用/导出审计预计在 v4 P3 开放」 |

### 交付证据实跑

- **前端边界测试**：Vitest 5 文件 49/49 passed（identity 6 + profile 12 + trash 9 + analytics 11 + apps 11）；Vitest AppsPage tests 11/11 通过（detailState 现在 initial=empty 不渲染 `state="unavailable"`；marketplace 作为唯一 unavailable capability 的声明未改变，因此 `declares marketplace as the only unavailable capability` 仍通过）。
- **前端构建**：`tsc -b` 0 errors；`vite build` ✓ 4647 modules transformed in 1.47s；产出：`dist/index.html 0.79KB`、`index-*.css 101.92KB (16.32KB gzip)`、`index-*.js 663.38KB (175.90KB gzip)`。警告 `chunks larger than 500KB` 为可选优化（v4 代码拆分），不影响交付。
- **修复的真实类型错误**：RecentAccessSection.tsx 有 2 个 TS 错误（`JSX.Element` 命名空间 → 改为 `ReactNode` import；`readonly AnalyticsActivityItemView[]` 无法赋给 mutable array → 标注 `: readonly AnalyticsActivityItemView[]`），修复后 tsc 通过。
- **后端健康**：`GET http://127.0.0.1:8023/healthz` 返回 `{"status":"ok","pgvector":{"required":false}}`，迁移、API server、app services 正常。
- **Dashboard 4 个真实端点 shape 验证**（adapter boundary 实跑证据）：analytics router 返回的 JSON 与前端 adapter 声明完全对齐 — `overview` 含 5 个 int+rate 字段、`trend` 含 points[]+peak+total+days、`distribution` 含 items[]+totalAccesses、`activity` 含 items[]+limit+next。

### 本轮 v4 路线图对齐（为下一轮铺垫）

- 用户触发下一轮时，先读 `docs/EKB后续全栈开发路线图_v4.0_2026-08-11.md` 的 P1/P2/P3 实现顺序，不要凭记忆开发。
- v4 P1 身份能力建议起点：`roles.service` → router → adapter → TeamPage 抽屉，复用 v3 现有 `v3_identity` 迁移模式（新增补偿迁移 + 纯 additive router 端点）。
- v4 P2 内容治理：文件夹树 / 共享 ACL / signed share link 必须复用现有 Fernet key derivation（已在 Apps 凭据实现 `ekb-apps-kms-v1`，可延伸为 `ekb-share-kms-v1`），避免再造加密轮子。


## 2026-08-11 Phase 5.1 Rail「最近访问/收藏夹/回收站」三点立即修复里程碑（本次交付）

> **用户问题触发**：用户明确反馈「知识库中的最近访问、收藏夹、回收站还是显示不可用」——要求先把这三个点立即改了，再按 v4 路线图继续推进。此前 Phase 5 C 类透明化只覆盖了页面级，但遗漏了 KnowledgeSpaceRail（知识库侧栏）的三个显眼入口。用户一眼就看到「（不可用）」，触发"没开发完"印象。

### 审计结论（三点逐点分类）

| 侧栏入口 | 后端真实能力 | 前端状态 | ADR-004 分类 | 策略 |
|---|---|---|---|---|
| **回收站** | ✅ FULLY 可用：`trash_items` 表（v3_002_content）+ `v3_trash.py` + `routers/trash.py` + smoke_v3_trash 36/0；前端 trash adapter 声明 6 个方法全部 `available`，独立页面 [RecyclePage](file:///Users/alin/EKB/apps/web/src/app-v2/pages/RecyclePage.tsx)（hash 路由 `#/recycle`）真实接线列表、过滤、搜索、还原、永久删除、清空 | `<button disabled>回收站（不可用）` | **A 类 · 后端已实现前端未接线**（纯前端跳转，零后端） | `<a href="#/recycle">回收站</a>`，删除括号 |
| **最近访问** | ✅ FULLY 可用：`resource_access_events` 表（v3_003_analytics）+ `GET /analytics/activity` 端点 + `analytics.getActivity(limit)` adapter 已在 Dashboard RecentAccessSection 组件同端点渲染成功 | `<button disabled>最近访问（不可用）` | **A 类 · 后端已实现前端未接线**（复用同端点） | `<a href="#/dashboard">最近访问</a>`，title 提示「跳转到工作台最近访问区」。不做 Rail 内折叠列表（避免 100+ 行逻辑在两处复制） |
| **收藏夹** | ❌ 后端 0 代码（grep favorite/favourites 全库无 match）。v4 路线图 §6.1 明确 favorites 属于 P2 内容治理工作包，必须与 folders/tags/shares 同步（补偿迁移 + service + router + adapter + star 切换） | `<button disabled>收藏夹（不可用）` | **C 类 · v4 路线图规划中（P2 内容治理）** → 透明化 | 保留 disabled，title 改为「v4 P2 内容治理：收藏端点（跨资源 favorites 投影表 + 星标切换 API）规划中，当前不伪造本地收藏」；删除括号，加黄底 phase 徽章 `<small>v4 P2</small>`（颜色 #FEF3C7 / #92400E，与 Phase 5 约定一致） |

### 全局 C 类透明化延伸（避免"收藏"按钮在多处显示不同阶段）

Phase 5 只把 AssistantPage 头部按钮改了 tooltip，但没加 phase 胶囊；AssistantSidebar 的"收藏/项目" tab 还是含糊"API 未提供"且 unavailable-card 无具体阶段说明。本轮统一透明化，确保用户在任何页面看到的"收藏"按钮，不管是 star/heart，都显示相同的阶段与理由：

1. **[AssistantPage.tsx](file:///Users/alin/EKB/apps/web/src/app-v2/pages/AssistantPage.tsx#L518-L532) chat-actions 3 个按钮（分享/收藏/导出）**：所有 disabled 按钮都加上 `v4 P2` phase 小胶囊（黄底黑字），tooltip 与 Rail 收藏夹一致（引用 Phase 5 已更新的 title，本轮新增 phase 徽章显示）。
2. **[AssistantSidebar.tsx](file:///Users/alin/EKB/apps/web/src/app-v2/components/assistant/AssistantSidebar.tsx#L69-L93) tabs（收藏/项目）**：
   - 收藏 tab title：`"v4 P2 内容治理：收藏端点（跨资源 favorites 投影表 + 星标切换 API）规划中，当前不伪造本地收藏。"` + `<small>v4 P2</small>` 徽章。
   - 项目 tab title：`"v4 P2 内容治理：文件夹/项目桶端点（会话分组 + 拖拽）规划中，当前不伪造本地项目容器。"` + `<small>v4 P2</small>` 徽章。
   - 当 activeTab 为收藏/项目时，原 `StatePanel state="unavailable" message="收藏视图已禁用。" reason="当前 API 未提供对应端点"` → **替换为**：`message="收藏视图：规划中（v4 P2 内容治理）"` + `reason="跨资源 favorites 投影表 + 星标切换 API（toggle/list/check）完成后，此处切换为真实收藏列表；当前不伪造本地收藏容器。"`（项目同理）。

### 交付证据实跑

- **前端构建**：`tsc -b` 0 errors；`vite build` ✓ 4647 modules transformed in 1.52s；产出：`dist/index.html 0.79KB`、`index-*.css 101.92KB (16.32KB gzip)`、`index-*.js 665.12KB (176.32KB gzip)`。
- **前端边界测试**：Vitest 5 文件 49/49 passed（identity 6 + profile 12 + trash 9 + analytics 11 + apps 11）。本轮只新增/修改 3 个 React 组件（KnowledgeSpaceRail、AssistantPage、AssistantSidebar），不碰任何 page 级 unavailable state 断言；apps tests 11/11 全过（仍声明 marketplace 为唯一 unavailable，与 Phase 5 一致，无回归）。
- **Rail 手动验收（本机）**：
  - 点"最近访问" → `#/dashboard`，Dashboard 的 RecentAccessSection 显示资源类型 pill + 操作人 + 最近访问（同一 adapter 端点），跳转目标有效。
  - 点"回收站" → `#/recycle`，RecyclePage 渲染已接线的 trash adapter：type filter + keyword search + restore/purge 按钮（都不是 disabled），跳转目标真实可用。
  - "收藏夹"按钮 disabled，但 tooltip 包含具体路线图阶段，徽章显眼"v4 P2"，不再含糊"不可用"。
  - AssistantPage 头部分享/收藏/导出：鼠标悬停显示黄色 P2 胶囊 + 详细 tooltip，不再是 disabled 灰按钮无说明。
  - AssistantSidebar "收藏/项目" tab：选中时 StatePanel 明确"规划中（v4 P2 内容治理）"+ 具体功能点，不再是"已禁用"空理由。

### 与 v4 路线图的实施顺序决策

**原文档顺序**：P0（已完）→ P1（PG/S3/Redis 基础设施）→ P2（内容治理 + Assistant + 十页走查）→ P3 → P4。

**本轮决策调整**（写入计划文档 `.trae/documents/Rail三不可用修复+路线图后续开发_plan.md`）：先做 **P2 内容治理（favorites/folders/tags/shares）**，再做 **P1 基础设施切换**。理由：
1. 用户立即痛点就是收藏夹 Rail 按钮"假不可用"，先做 P2 favorites 可让按钮立即真实化，无需等待 PG/S3/Redis。
2. P2 内容治理只依赖关系表 + REST API，当前 SQLite 生产库完全可承载。
3. P1 是"基础设施切换"级工作，适合在核心功能对齐后再做，避免双轨并行的复杂性。
4. 本计划文档（`.trae/documents/`）已明确工作包 A1~A5（P2 内容治理）→ B1~B4（P1 基础设施）→ C（P2 剩余）→ D（P3）的顺序，严格落实"每轮部署验收"。

---

## 2026-08-11 Phase 6 · Batch 2 · P2 favorites 全栈闭环里程碑（本次交付）

> **触发条件**：用户明确下达「好的，立即开始 Batch 2 · P2 favorites 全栈开发」指令。此前 Phase 5.1 只把收藏夹按钮做了"透明化（v4 P2 标签+说明）"，本轮要求**后端全栈实现 + 前端真实接线 + 部署验收**。
>
> **验收标准**：
> 1. 后端迁移表 `favorites`（复合唯一键 tenant+subject+type+id）被创建并被 db.py 迁移链引用；
> 2. `/api/v1/favorites`（list/counts）、`/check`、`/toggle` 3 个端点返回真实投影；
> 3. 知识空间侧边栏（KnowledgeSpaceRail）收藏夹展开区真实渲染星标列表（KB/DOCUMENT/CONVERSATION 三类，带 type color bar）；
> 4. AssistantPage 头部 ☆ 收藏按钮真实 toggle（乐观 UI + 失败回滚 + operation notice 提示）；
> 5. AssistantSidebar 「收藏」tab 不再显示 unavailable，真实渲染 CONVERSATION 类型星标会话列表；
> 6. 部署后使用受管验收账号登录，`/api/v1/favorites` 返回 HTTP 200 且首项标题非空；
> 7. 修复 list_favorites SQL 错误（no such column: conversations.kb_id），确保 SQLite/PostgreSQL 双兼容；
> 8. 额外修复：Python 3.9 `Path.parents[:8]` 切片不兼容（config.py），保证服务器旧 Python 环境可启动。

### 交付清单（后端 → 前端 → 部署 → 验收，按实施顺序记录）

#### A · 后端迁移 + 服务 + 路由（Phase 6 新增）

| 文件 | 角色 | 核心变化 |
|------|------|----------|
| [v3_007_content_governance_compat.py](file:///Users/alin/EKB/apps/api/ekb_api/migrations/v3_007_content_governance_compat.py) | 补偿迁移 v3_007 | 新建 `favorites` 表（id / tenant_id / subject_id / resource_type ∈{KB,DOCUMENT,CONVERSATION} / resource_id / favorited_at）；`UNIQUE(tenant,subject,type,rid)` 复合约束；2 条索引（按时间/按类型）；`CHECK(resource_type IN (...))` 保证 enum 合法；遵循 ADR-002 append-only 策略，不修改任何历史迁移。 |
| [v3_fullstack.py](file:///Users/alin/EKB/apps/api/ekb_api/migrations/v3_fullstack.py) | 迁移链编排 | 注册 v3_007 为 `MigrationStep(VERSION, apply_v3_007, verify_v3_007, rollback_v3_007_dry_run)`，保证 CHAIN 是幂等的 ordered。 |
| [db.py](file:///Users/alin/EKB/apps/api/ekb_api/core/db.py#L175-L188) | init_db 迁移执行 | **修复关键遗漏**：`prepare_legacy_schema → apply_v3_001~v3_007` 的顺序执行链。此前 `init_db()` 只执行到 v3_002，导致 favorites 表从未创建（Rail 按钮不可用的根因之一）。补齐 v3_003 / v3_004 / v3_005 / v3_006 / v3_007 7 步显式导入 + 顺序调用。 |
| [config.py](file:///Users/alin/EKB/apps/api/ekb_api/core/config.py#L24-L35) | dotenv 加载兼容 | **修复 Python 3.9 兼容性**：`Path.parents[:8]` 的 slice 语法只在 Python 3.10+ 支持。改为 `itertools.islice(here.parents, 8)` 兼容 3.9/3.10/3.11，避免容器启动 `TypeError: '<' not supported between instances of 'slice' and 'int'`。 |
| [v3_content_governance.py](file:///Users/alin/EKB/apps/api/ekb_api/services/v3_content_governance.py) | favorites 服务层（核心） | 实现 3 个公共原语：`toggle_favorite(tenant, subject, type, rid)`（先 probe → INSERT / DELETE，UNIQUE 冲突时 re-read 幂等收敛）、`list_favorites(tenant, subject, type?, limit, offset)`（分页 + counts 聚合）、`check_favorite(tenant, subject, type, rid)`（composite key probe）。关键是 **`_resolve_titles_bulk` 单查询跨资源 JOIN**，避免 N+1：KB/DOCUMENT/CONVERSATION 各一条 SQL，结果组装为 `{type:id → {title, parent_id, parent_title}}` 字典。list_favorites 调用后只保留 title_map 命中的条目（硬删除/越权的投影行保留但从列表剔除）。 |
| [v3_content_governance.py L156-L173](file:///Users/alin/EKB/apps/api/ekb_api/services/v3_content_governance.py#L156-L173) | **Bugfix：删除对 conversations.kb_id 的引用** | 首次部署触发 `OperationalError: no such column: conversations.kb_id`——根因是 models.py 的 Conversation 模型只有 id/tenant_id/user_id/title，**没有 kb_id 外键**。把 conversations 子查询从 `kb_id AS parent_id, (SELECT name FROM knowledge_bases WHERE id=conversations.kb_id)` 改为 `NULL AS parent_id, NULL AS parent_title`，保证 CONVERSATION 资源在前端显示为"无父级归属"，同时 SQL 可在实际 schema 下运行。 |
| [content_governance.py](file:///Users/alin/EKB/apps/api/ekb_api/routers/content_governance.py) | REST 路由层 | 3 个纯 additive 端点（前缀 `/api/v1/favorites`）：`GET ""`（list + counts + resource_types 枚举）、`GET /check`（probe）、`POST /toggle`（body {resource_type, resource_id}）。全部通过 `get_live_auth_context` 做租户/用户隔离，禁止跨租户 favorite。无破坏性改动。 |

#### B · 前端类型 + API client + Adapter（Phase 6 新增）

| 文件 | 核心变化 |
|------|----------|
| [types/favorites.ts](file:///Users/alin/EKB/apps/web/src/app-v2/types/favorites.ts) | 新增 favorites 领域类型：`FavoritesResourceKind = 'KB' \| 'DOCUMENT' \| 'CONVERSATION'`、`FavoriteItemView`、`FavoritesListResult`（state: loading/ready/empty/error/permission-denied）、`FavoriteToggleResult`（带 favorited 新状态）。 |
| [lib/api.ts](file:///Users/alin/EKB/apps/web/src/lib/api.ts) | API client 新增 3 个方法：`listFavorites(query)`、`checkFavorite(resourceType, resourceId)`、`toggleFavorite(resourceType, resourceId)`。 |
| [adapters/favorites.ts](file:///Users/alin/EKB/apps/web/src/app-v2/adapters/favorites.ts) | 实现 `FavoritesServices` 接口：list/check/toggle 三方法全链路 try/catch → adapter failure。toggle 调用成功后返回服务端的**新状态**（不是客户端本地假设），确保和 DB 一致。 |
| [adapters/index.ts](file:///Users/alin/EKB/apps/web/src/app-v2/adapters/index.ts) | createServices 新增 `services.favorites = createFavoritesAdapter(client)`。 |

#### C · 前端 UI 接线（KnowledgeSpaceRail / AssistantPage / AssistantSidebar）

| 文件 | 变化要点 |
|------|----------|
| [KnowledgeSpaceRail.tsx](file:///Users/alin/EKB/apps/web/src/app-v2/components/knowledge/KnowledgeSpaceRail.tsx) | **Rail 收藏夹展开区真实化**：从 disabled 按钮改为可展开 `<button aria-expanded>`。点击展开区触发 `loadFavorites()`，根据 state 渲染 loading/error/empty/unavailable（权限过滤后空）/列表 5 态。每条收藏项左侧 type color bar（PDF→红/DOCX→蓝/XLSX→绿/PPTX→橙，这里会话用有机色），右侧 title + kind + favorited_at；badge 显示总数。点击 item 触发 `onOpenFavorite(type, id, parentId)` 跳转。 |
| [KnowledgePage.tsx](file:///Users/alin/EKB/apps/web/src/app-v2/pages/KnowledgePage.tsx) | 加载 favorites 数据并传给 Rail：`services.favorites.list()`（不分资源类型），统计 counts.KB / DOCUMENT / CONVERSATION 给 Rail 胶囊显示。onOpenFavorite 按资源类型跳页面：KB → #/knowledge?kb=xxx，DOCUMENT → #/documents?id=xxx&kb=yyy，CONVERSATION → #/assistant?conv=xxx。 |
| [AssistantPage.tsx](file:///Users/alin/EKB/apps/web/src/app-v2/pages/AssistantPage.tsx) | 头部 ☆ 按钮从 disabled + P2 胶囊 → **真实 toggle 交互**：① `loadFavorites(CONVERSATION)` 初次进入侧栏加载；② 切换会话时 `checkFavorite(CONVERSATION, convId)` 预填 star 状态；③ `handleToggleCurrentFavorite()`：乐观 UI（prev === null ? true : !prev）→ POST toggle → 成功：`setOperationNotice('已收藏会话 XXX，星标仅你自己可见')` → 重新 loadFavorites；失败：回滚乐观状态 + 失败提示。isStreaming / currentConvFavoriting 时按钮置 disabled（防重复提交）。 |
| [AssistantSidebar.tsx](file:///Users/alin/EKB/apps/web/src/app-v2/components/assistant/AssistantSidebar.tsx) | 「收藏」tab 从 StatePanel unavailable → **真实 CONVERSATION 收藏列表**。activeTab='favorites' 时调用 services.favorites.list({resourceType:'CONVERSATION'})，按 StatePanel 约定显示 loading/error/empty/列表。会话 item 左侧显示 fill Heart 图标（#059669 绿），title 右侧显示 favorited_at（短日期格式）。选中时 `is-selected` 高亮与最近会话一致。「项目」tab 保留 v4 P2 胶囊 + 详细 tooltip。 |

### 质量验证与回归证据

```
======== 后端 compileall + smoke ========
✓ compileall ekb_api 通过（3 新文件/3 修改文件无 SyntaxError）
✓ Python 3.9 smoke test（本地）：
    Toggle ADD (CONVERSATION): favorited=True, at=2026-08-11T06:43:08Z
    Check: favorited=True
    List:  total=1, items=1, counts={KB:0, DOCUMENT:0, CONVERSATION:1}
           first:  type=CONVERSATION, title=数据库连接池耗尽时应该先检查哪些指标？, parent_title=None
    Toggle REMOVE: favorited=False
    Final list: total=0
    → All assertions PASSED（toggle/check/list 无回归）

======== 前端 tsc + Vite build ========
✓ tsc -b 0 errors
✓ vite build ✓ 4649 modules transformed in 1.56s
    dist/index.html                   0.79 kB
    dist/assets/index-fUZvQU-I.css  101.92 kB │ gzip:  16.32 kB
    dist/assets/index-CfC2uNlI.js   672.58 kB │ gzip: 178.57 kB

======== 服务器端 API 验收（通过转发 curl） ========
✓ /healthz：{"status":"ok","pgvector":{"required":false,"available":null,"status":"not_applicable"}}
✓ `/api/v1/auth/login`（受管验收账号）：曾签发有效 access token，scope 包含 kb:read、kb:write、qa:ask 等
✓ /api/v1/favorites（HTTPS 转发）：返回 items[0].title=你好,counts.CONVERSATION=1 → 证明 favorites 表、
    v3_007 迁移、router、service、title_resolver 全链路贯通，无 kb_id 报错
✓ 验收账号幂等初始化曾完成；具体账号和口令不在项目记忆中保存

======== 浏览器 UI 验收 ========
✓ 登录页历史上曾正常加载（[REDACTED]，通过受管 SSH 端口转发访问服务器 nginx）：
    标题 "EKB 企业知识库"，邮箱/密码输入框，"登录工作台" 主面板，产品介绍 Hero
✓ Dashboard 登录后正常渲染：
    左侧 Rail（工作台/知识库/AI助手/文档中心/团队与权限/数据看板/应用中心/回收站/个人中心）
    工作台真实指标卡片（知识库文档/知识标签/团队成员/访问量/AI问答）
    resource_access_events 真实趋势图、使用分布条形图、最近动态 feed
✓ 浏览器网络请求已捕获 favorites 完整调用链：
    GET  /api/v1/favorites?limit=50&offset=0
    GET  /api/v1/favorites?resource_type=CONVERSATION
    GET  /api/v1/favorites/check?resource_type=CONVERSATION&resource_id=xxx
    POST /api/v1/favorites/toggle → 200
    GET  /api/v1/favorites?resource_type=CONVERSATION （toggle 后 reload）
    → 证明 AssistantPage 的 Star toggle 已真实触发后端写入
```

### 历史部署架构与运维细节（仅供复盘，当前未重新验证）

**架构结论**：本轮 **不使用 deploy.sh 的 venv+systemd 分支**（`/opt/ekb/api/venv` 在服务器上不存在，deploy.sh Step 4 会失败）。实际运行架构是：

```
用户浏览器 → [production host] (Nginx 80/443，SSL 证书 [REDACTED])
            ├── /opt/ekb/web/current/*.html + assets/  (SPA 静态，rsync 直推 + ln -sfn 原子切换)
            └── /api/* → 127.0.0.1:8000  (Docker 容器 ekb-api，仅绑定 loopback，避免外部直连)
Docker 容器配置（服务器 docker inspect）：
    image:  ekb/ekb-api:latest （174 MB，Python 3.11-slim）
    ports:  [REDACTED]->8000/tcp
    volumes:/opt/ekb/data:/data（SQLite / 备份）
    restart: unless-stopped，memory_limit 1g，cpu_quota 1.0（按项目记忆约束）
    env:    EKB_ALLOWED_HOSTS=[REDACTED]（ECHO 兼容性），EKB_DEV_USER_*=[REDACTED]，EKB_DATABASE_URL=[REDACTED]
Nginx 配置：
    /etc/nginx/conf.d/ekb.conf  —— 80/443 default_server，server_name [production host] _
      include /etc/nginx/ekb-api-locations.inc  (API 前缀路由)
      include /etc/nginx/ekb-spa-locations.inc  (SPA try_files)
    /etc/nginx/conf.d/ekb_high_ports.conf —— [REDACTED] 额外虚拟主机（用于端口转发无 Host 头调试）
**注意前端路径层级**：Nginx 的 `root $web_root`（即 `/opt/ekb/web/current`）期望下面直接有 index.html，
不能是 `current/dist/index.html`（否则 403）。rsync 上传时如果用 release dir 包了 dist/ 子目录，
必须在切换软链后额外 `cp -a $RELEASE_DIR/dist/. $RELEASE_DIR/` 扁平化。
```

**本轮部署快速流程（无需 Docker buildx，因为 build 拉取 docker.io 元数据会 TLS 超时）**：
1. 本地打 `tar czf ekb-api-code.tar.gz ekb_api/ ensure_admin.py` → `scp` 到 `/tmp/ekb-api-update.tar.gz`
2. 远程 `tar xzf` 后 `docker cp /tmp/ekb-api-newcode/ekb_api/. ekb-api:/app/ekb_api/`
3. `docker restart ekb-api` → wait healthz
4. 前端 dist `rsync -a web/dist/` → `[REDACTED]`
5. `ln -sfn $RELEASE /opt/ekb/web/current` → **重要**：`cp -a dist/. .` 扁平化（否则 403）
6. `nginx -s reload`
7. 从受管环境幂等初始化验收账号；禁止在脚本参数、日志或项目记忆中硬编码口令
8. 用 curl + 受管 SSH 端口转发验收（具体端口、目标和账号统一记为 `[REDACTED]`；公网 DNS/SSL 状态不在项目记忆中保存）。
```

## 2026-08-12 EKB Core Rebuild Tier 3 Spec 里程碑

- 已创建唯一的新核心重构文档链：`docs/specs/ekb-core-rebuild/00-current-state.md` 至 `14-implementation-plan.md`，共 15 份、3170 行，状态统一为 Ready for Plan；`docs/README.md` 已加入 authority/supersession 规则，用户已最终确认，Sol 最终复审无 P0/P1。
- 需求与验证命名空间：`EKB-CR-FR-001`–`060`、`EKB-CR-NFR-001`–`014`、`AC-CR-001`–`060`、`AC-CR-NFR-001`–`014`、`CR-PH0`–`PH8`。本里程碑只完成 Spec，不代表任何业务实现或验收项已通过。
- 固定架构：保留 React app-v2 + FastAPI 模块化单体控制面；目标 PostgreSQL+pgvector、S3-compatible object storage、可靠 queue/worker/scheduler；附件独立于 KB；Provider/Model 为助手唯一模型数据源；Embedding profile 不可变；Regenerate 使用回答分支；Theme 使用语义 token；内容回收站由 Scheduler 做 30 天自动清理。
- AI 范围：普通多轮对话 + 可选 0..N KB RAG + 独立文件/图片附件；支持 Strict grounded 与 Knowledge enhanced；本轮明确排除 Internet Web Search、MCP、工具/代码执行、Agent runtime 和音频。
- 数据治理：已应用 v3 migration/checksum 永不修改；新增 `v4_001`–`v4_008` 仅为规格中的目标 manifest，严格按 PH1(001–004：provenance/runtime/provider security/PostgreSQL 主库原子切换)→PH2(005 retention)→PH3(006 ingestion)→PH4(007 chat)→PH5(008 attachments) append，实际实现必须 additive 并经 apply/verify/rollback rehearsal。当前生产业务仍以 SQLite 为真值；PH1 切换前必须逐表/租户/关系对账，切换后 SQLite 只读且禁止双写。
- 服务器已只保留 EKB；清理前 EKB 数据和配置已备份并校验。当前生产主机没有稳定 DNS/HTTPS 出网，Cloudflare tunnel、外部 Provider 和 Embedding 的真实生产验收因此是硬阻塞；不得用 mock 代替或在记忆中保存任何服务器/Provider 凭据。
- 每个实施 Phase 完成后强制：更新 Spec/authority/evidence 与本记忆、提交并推送精确代码、备份和原子部署、健康+真实浏览器验证、记录回滚结果；门禁失败不切换。
- 下一步：按 `14-implementation-plan.md` 执行 PH0 工作树审计与 baseline branch 快照；每完成一个 Phase 必须同步对应 Spec/authority/evidence 与本记忆。业务代码、测试、运行配置和 migration 写入仍必须由 `luna_max_worker` 串行完成，Sol 负责拆解、架构决策、diff/证据审查和发布判断。

## 2026-08-12 CR-PH0 可审查写入阶段

- 已创建并切换未提交分支 `codex/ekb-core-baseline-20260812`；保留当前脏工作树，不执行 reset、checkout 覆盖、clean、commit、push 或生产写操作。
- 已在 `.gitignore` 仅补充 `.deploy_staging/`、`.deploy_helpers/`、`.playwright-cli/`、`.trae/`、`.pmos/settings.yaml`、`output/design-qa/`。
- 已脱敏并收紧 `scripts/deploy/` 风险脚本：生产目标和 SSH 用户改为运行时必填；可选密码只在 SSH helper 内从运行时环境注入，缺失 `sshpass` 不回退交互式输入；缺失/占位目标、默认管理员身份或缺少管理员运行时设置时 fail closed；日志和远程验证不打印凭据、代理、provider 端点或原始响应。
- PH0 证据索引：`docs/evidence/ekb-core-rebuild/ph0/2026-08-12-baseline/manifest.md`。已完成 bash/Python 语法、目标 fail-closed、secret scan（仅文件/行模式）、Markdown 链接和 FR/NFR/AC/Phase 静态交叉检查；`git diff --check` 通过。
- 生产 health、schema ledger、备份状态和浏览器只读检查未通过安全可审计的受管连接执行，证据明确记录 `BLOCKED/NOT RUN`；PH1–PH8、生产部署、备份恢复、迁移和验收仍未完成。证据入口为 `docs/evidence/ekb-core-rebuild/ph0/2026-08-12-baseline/manifest.md`，验证记录为 `.../verification.md`。Sol 需先审查 diff、证据和脚本行为，再决定是否提交/推送。

## 2026-08-12 CR-PH1 foundation local slice

- 在不改历史 v3 migration、不提交/推送/部署的前提下，完成 CR-PH1-T01 + CR-PH1-T03 第一真实业务切片：v4_001/v4_002 additive provenance/runtime migration、append-only ledger/audit、checksum/历史事实 fail-closed、DB-backed jobs/attempts/leases/heartbeats/outbox，以及 entrypoint migration/verify/admin/runtime configuration fail-closed。
- 本地验证覆盖 v4 migration lifecycle、checksum drift、immutable ledger、租户隔离幂等 enqueue、claim/heartbeat/complete/CAS、lease expiry reclaim、retry/max-attempt DEAD/DLQ/outbox 和 entrypoint no-start failure paths；相关 v3 migration regression 保持通过。证据入口为 `docs/evidence/ekb-core-rebuild/ph1/2026-08-12-foundation/manifest.md`，验证记录为 `.../verification.md`。
- SQLite v4/v3 suites 与一次 disposable 本地 PostgreSQL+pgvector apply/verify/repeat 已通过；PG 覆盖 JSONB/time bindings、租户/attempt 唯一约束、lease/CAS、双 worker single-claim、retry lifecycle 与 provenance 负向不可变保护。v4 runner 禁止隐式 ORM `create_all`，测试 fixture 显式 bootstrap，生产缺少 imported schema 时 fail closed。
- 未执行生产 DSN/生产 PostgreSQL、生产 migration/deploy/restart、真实 uvicorn、管理员初始化、备份恢复或外部 provider；精确 disposable 容器已在验证后移除，PH1 总体 exit gate 仍待 Sol 审查及后续切片。下一串行任务为 upload→parse→chunk→index→RAG。

## 2026-08-13 本地真实业务闭环

- 继续既有工作区完成本地真实上传批次、显式开发对象存储、checksum complete、后台 ingest worker 和文档中心浏览器闭环；真实 Chrome 可看到服务端文档的已就绪与失败状态。
- 统一为 LLM-only：聊天和 Embedding 只走远程受管 Provider/Model；缺少远程 Embedding 时 fail closed 为 `EMBEDDING_UNAVAILABLE`，不回退本地模型或伪向量。
- 验证：后端 `153 passed, 2 skipped`；前端 typecheck、49 tests、build 通过；官方 npm registry 生产依赖审计为 0 vulnerabilities；本次修改范围精确 ruff 与 `git diff --check` 通过。
- 证据：`docs/evidence/ekb-core-rebuild/local/2026-08-13-local-business.md`。生产 PostgreSQL/pgvector、对象存储、可靠队列/Worker/Scheduler、Provider 凭据、服务器备份部署回滚仍未执行；不得用 mock 代替，也未在此记录凭据或私密地址。
- 本次续作补齐真实附件聊天链路：浏览器可完成 session → 受保护对象 PUT → process → 带 attachment_doc_ids 的 SSE ask；附件按 tenant/owner/conversation/status 校验，独立于 KB ID，并持久化附件引用。前端 thinking level 在 API 边界归一化为 canonical `light/medium/high`，避免旧 UI 值触发 400。全量后端 `331 passed, 2 skipped`；前端 typecheck、49 tests、build、npm production audit 通过；Playwright 真实附件问答显示阶段完成、seq=10 和附件引用。生产 PostgreSQL/pgvector、对象存储、可靠队列/Worker/Scheduler、Provider 凭据、服务器备份部署回滚仍未执行。
- 又收紧图片真实性：Vision 仅使用当前用户配置且 capability 明确支持 Vision 的远程模型，发送 typed image content；无远程 Vision/OCR 时 fail closed，不写本地占位文本；远程 S3 session 返回 presigned PUT，本地开发才走受保护 PUT。回归 `333 passed, 2 skipped`，无凭据写入。
- 服务器部署入口检查因未提供运行时 `DEPLOY_HOST` 以退出码 64 fail closed；未执行 SSH、备份、迁移、重启或线上写入。最新浏览器附件问答 session/PUT/process/ask 全部 200，阶段完成、seq=22。提交 `ebc0c3e` 已推送并与远端 SHA 一致。
## 2026-08-13 Promotion and theme local slice

- DONE-LOCAL：真实 POST /api/v1/attachments/{attachment_id}/promotions；校验 tenant/owner/live KB ACL、路径冲突/回收站保留/幂等/并发；复用 attachment source_object_id 创建 document/version/ingest job，返回 HTTP 202、状态 QUEUED，worker 投影真实状态，不把队列写成成功。
- DONE-LOCAL：助手附件“提升”入口和真实错误/状态/ID 显示；主题支持 semantic tokens、light/dark/auto、系统主题和 storage 同步、reduced-motion；LLM-only，不使用本地模型/mock。
- 验证：后端 343 passed, 2 skipped；前端 51 passed、typecheck/build 通过；ruff、compileall、git diff --check 通过；真实浏览器登录后实调 KB 与 promotion API 返回 202，数据库确认 document_version.source_object_id 与 attachment 一致。
- NOT RUN/BLOCKED：生产 PostgreSQL/pgvector、对象存储、可靠队列/Worker/Scheduler、服务器备份/部署/重启/生产浏览器；运行时 DEPLOY_HOST 和受管凭据未配置，不猜测、不输出、不落盘。生产未完成，不能标记 PH0–PH8 全部完成。

## 2026-08-13 CR-PH4-T07 contract closure

- DONE-LOCAL：联网 Web Search 已从本地 QA UI/API/runtime/config 移除；旧 `options.web_search` 在检索前返回 `400 FEATURE_REMOVED`，不触发 retrieval 或外部网络调用。
- QA 继续使用授权 KB RAG、独立附件上下文和远程 LLM Provider/Model；Composer 浏览器状态不显示联网搜索，但添加文件、远程模型和 KB 上下文保持可见。
- 验证记录：backend `345 passed, 2 skipped`；web `51 passed`、typecheck/build；定向 API `28 passed`、RAG/多轮 `21 passed`、`git diff --check` 通过。生产部署、备份、回滚和生产浏览器仍未完成。

## 2026-08-13 Provider/Model linkage local closure

- DONE-LOCAL：`GET /api/v1/qa/capabilities` 与显式 Ask model 选择共享当前 `tenant+actor` 的活动加密 credential 和启用远程 Provider/Model registry；capability/vision 信息以 DB registry 为唯一来源，不构造默认模型或本地模型。
- 显式 model 的 unknown、disabled、cross-tenant、no-credential 情形统一 fail closed 为 `MODEL_UNAVAILABLE`；图片选择非 `vision=true` 模型为 `MODEL_NOT_ALLOWED`；显式选择不静默 fallback；普通无 model 请求保留既有 provider fallback。
- 验证：backend `349 passed, 2 skipped`；web `51 passed`、typecheck/build；定向 `63 passed`；范围 Ruff（忽略既有 E501 等）、compileall、`git diff --check` 通过。真实浏览器此前确认 Profile 模型服务目录、Assistant 添加文件/模型/KB context 可见。
- 未验证真实 Provider 出网或生产完成；生产 PostgreSQL/pgvector、对象存储、队列/Worker/Scheduler、服务器备份/部署/回滚和生产浏览器仍 `NOT RUN/BLOCKED`。本条不记录任何凭据，且不涉及 Skills/legacy backup/deploy/migrations/theme/Web Search。

## 2026-08-13 Upload Center DONE-LOCAL

- DONE-LOCAL：Upload Center 服务端从持久化 batch/session/item/ingest job 投影 canonical `queued`、`uploading`、`verifying`、`processing`、`indexing`、`ready`、`failed`、`cancelled`，附带 counts、阶段/进度、attempt、job id 和脱敏 error；未知或越权 batch/item fail closed。前端以 `ekb.upload-center.batch-refs.v1` 保存 batch refs，刷新后重新 GET 服务端 projection，不依赖内存状态。
- retry/cancel/abort：failed 且 retryable item 调用真实 retry，processing/uploading 调用真实 cancel/abort；操作后重新 GET，不写假成功。此前本地真实浏览器上传后显示服务端状态；本地 embedding provider 缺失时真实失败为 `EMBEDDING_UNAVAILABLE`，不伪造 ready。
- 验证：后端全量 `352 passed, 2 skipped`；Upload Center/ingest/boundary 切片 `34 passed`；前端 `52 passed`、typecheck/build 通过。
- 生产 PostgreSQL/pgvector、对象存储、可靠队列/Worker/Scheduler、服务器备份、部署/回滚和生产浏览器未执行，原因是当前运行时没有生产目标/受管凭据；本条不记录任何凭据、令牌或私密地址。此条不代表全 PH3/PH0–PH8 完成。

## 2026-08-13 本地 Embedding 切片交付记录

- DONE-LOCAL：`services/embedding.py` 使用真实 OpenAI-compatible 远程客户端；worker 绑定 `tenant+actor+KB ACTIVE profile/generation`。无配置或无 ACTIVE profile/generation 时保持 `EMBEDDING_UNAVAILABLE` fail-closed，不写 READY。
- 远程响应校验 `count`、向量 dimension 与非有限值；不使用本地模型或伪向量。真实外部 Provider 未调用。
- 验证：Embedding 定向 `40 passed`；全量后端 `358 passed, 2 skipped`；Ruff、compileall、`git diff --check` 通过。生产 Provider 出网/凭据、PostgreSQL/pgvector、部署仍 `NOT RUN/BLOCKED`；不记录凭据、token 或私密地址，不代表全 PH3/PH8 完成。

## 2026-08-13 CR-PH3-T06 Upload Center 跨设备批次列表本地收尾

- DONE-LOCAL（增量收尾）：新增租户作用域 `GET /api/v1/kb/uploads/batches`，服务端对 `limit` 实施 `1–100` 有界约束（默认 `30`）；Upload Center 列表以服务端 batch projection 为权威来源，`localStorage` 的 `ekb.upload-center.batch-refs.v1` 仅用于 fallback/reconciliation，不作为跨设备列表真值。此前“仅恢复已知 batch refs”的记录保留为历史快照。
- 浏览器证据（2026-08-13）：使用现有本地开发账号重新建立登录后，删除 `ekb.upload-center.batch-refs.v1` 并 reload `/#/documents`，真实服务端 batch 被恢复并显示：真实文件 `HANDOFF-2026-08-12.md`，失败阶段 `CHUNKING / 50%`，attempt `1/1`，显示 job id 和 `EMBEDDING_UNAVAILABLE`。不记录 access/refresh token、密码、私有地址或 opaque secret。
- 验证：backend full pytest `361 passed, 2 skipped`；web `52 passed`；web typecheck/build 通过。
- 生产/服务器工作仍 `NOT RUN/BLOCKED`：当前运行时没有生产目标（统一写作 `[production host]`）或受管凭据（统一写作 `[REDACTED]`）。不标记生产部署，不标记全 PH3 或 PH8 完成。

## 2026-08-13 CR-PH2-T05 / FR-058 Jobs Center 本地 UI 切片

- DONE-LOCAL：现有 Analytics `GovernancePanel` 增加真实「作业中心」Tab，使用当前租户作用域的 `GET /api/v1/jobs`、`GET /api/v1/jobs/cleanup` 和 `POST /api/v1/jobs/{job_id}/cancel`。页面展示服务端 state counts、job type、safe timestamps、lease/heartbeat availability、脱敏 error metadata、job id 及 cleanup `total/by_state/recent`；payload、`tenant_id`、Provider 原始响应和私有地址排除。仅对 `QUEUED`/`RUNNING`/`RETRY_WAIT` 提供取消，操作后重新加载服务端状态。
- 浏览器（2026-08-13）：现有本地开发账号认证后，`Analytics → 运营与治理 → 作业中心` 展示 5 条真实作业（4 `SUCCEEDED`、1 `DEAD`），含一条文档 ingest 作业的 `EMBEDDING_UNAVAILABLE`；cleanup projection 为 `retention_purge` 总数 4、全部 `SUCCEEDED`；缺少 lease/heartbeat 时显示 unavailable。过期认证阶段已有 3 条较早 auth-related console errors，不宣称 console=0；Jobs Center 数据加载成功。未记录凭据、token、私有地址或完整 opaque ID。
- 验证：web `57 passed in 8 files`、typecheck/build、`git diff --check` 通过；后端未改动，沿用既有全量 `361 passed, 2 skipped`，本切片未重新运行后端测试。生产目标、服务器备份、部署、回滚仍 `NOT RUN/BLOCKED`，统一使用 `[production host]` / `[REDACTED]`。保持 LLM-only、无本地模型、无 mock，不标记 PH2 或 PH0–PH8 全部完成。

## 2026-08-13 Dashboard semantic-theme local slice

- DONE-LOCAL 仅覆盖 Dashboard route：`apps/web/src/app-v2/pages/DashboardPage.tsx` 中全部 31 个 page-level raw `#hex`/`rgb`/`rgba` 颜色字面量已替换为 tokens/theme 中已有的 semantic variables；未改变 backend/API/data behavior，未引入 fake data。
- 新增 `apps/web/src/app-v2/tests/v3.dashboard-theme.test.ts` source contract test，断言 DashboardPage 不包含 raw color literals。
- 本地浏览器（2026-08-13）：认证后打开 `/#/dashboard`，真实显示 4 个 documents、3 个 knowledge spaces、trends、recent activity 和 governance proxy states；设置当前认证会话 localStorage 的 `v2.theme=dark` 后 reload，Dashboard 仍以真实数据渲染并应用主题状态。
- 当前会话累计有 13 条较早 auth-related console errors；不宣称 `console=0`、full route matrix 或 visual contrast score，未观察到新的 Dashboard data failure。记忆不记录 token、password 或 private address。
- 验证：Web `58 passed in 9 files`、typecheck/build、`git diff --check` 通过；backend 未改动，不宣称新的 backend suite。
- 生产 target、server backup/deploy/rollback `NOT RUN/BLOCKED`，原因是没有 production target/managed credentials，统一使用 `[production host]` / `[REDACTED]`。本条只记录 Dashboard route migration，不宣称 `FR-001–006`、PH2 或 PH0–PH8 全部完成；LLM-only、无本地模型、无 mock 边界保持不变。

## 2026-08-13 PH3 XLSX/PPTX parser registry local closure

- DONE-LOCAL：已在 `apps/api/ekb_api/services/parsers/registry.py` 注册真实 `XlsxParser` 与 `PptxParser`，支持 OOXML XLSX/PPTX MIME；XLSX 输出 workbook/sheet/row/cell 可检索内容与安全 provenance metadata，PPTX 输出 slide/shape/table/notes（可读时）结构。`openpyxl` 使用只读、`data_only=True`、`keep_vba=False`，不执行宏；不引入本地模型、mock 或伪成功。
- 稳定错误边界：空字节返回 `FILE_EMPTY`；损坏 OOXML 返回 `PARSER_CORRUPT` 且不泄露库异常原文；旧 `application/msword`、`application/vnd.ms-excel`、`application/vnd.ms-powerpoint` 继续 `MIME_UNSUPPORTED`，不静默转换。
- 代码与测试已在已推送 commit `5a008f17251fa8e37ee8f44980afd923055a8957`；本次仅补充文档证据，不新增提交或推送。
- 当前工作树回归：后端全量 `370 passed, 2 skipped`；office registry/PH3/旧 parser 定向 `38 passed`；前端 `58 passed`、typecheck/build 通过。全量数字是当前工作树回归结果，不声称全部由本 parser 切片单独造成。
- 浏览器边界：本地真实浏览器已验收 Documents、Assistant、Jobs Center 的真实服务端数据；本次未生成临时 XLSX/PPTX 文件，因此不声称 XLSX/PPTX 已在浏览器成功摄取。
- NOT RUN/BLOCKED：生产 PostgreSQL/pgvector、真实 Provider/Embedding 出网、生产对象存储、可靠队列/Worker/Scheduler、服务器备份、部署/回滚仍未执行。文档不记录凭据、token 或私密地址，统一使用 `[production host]` / `[REDACTED]`；本地结果不代表 PH3 或 PH0–PH8 全部完成。

## 2026-08-13 browser OOXML validation

- 后续本地真实浏览器验证：在 `Documents → 批量/目录上传 → 批量多文件` 中选择 `/tmp/ekb-parser-qa.xlsx` 和 `/tmp/ekb-parser-qa.pptx`；两份仅为临时文件，不进 Git。
- UI 扫描将文件识别为 Excel/PPT，各 1 个；真实上传完成 `2/2`；服务端分别创建了真实 batch/job/document 状态。
- worker 处理后两个条目均到达 `CHUNKING · 50%`；解析器未报 `MIME_UNSUPPORTED` 或 `PARSER_CORRUPT`。由于本地没有远程 Embedding provider/profile，两个条目的真实终态均为 `EMBEDDING_UNAVAILABLE/FAILED`；不得写成 `READY`、成功索引或远程 Provider 成功。
- 浏览器仍有历史 auth-related console errors；不宣称 `console=0`。
- 生产 PostgreSQL/pgvector、Provider 出网、对象存储、可靠队列/Worker/Scheduler、服务器备份/部署/回滚仍为 `NOT RUN/BLOCKED`。生产目标统一使用 `[production host]`，受管敏感值统一使用 `[REDACTED]`；不记录 token、password 或私密地址。

## 2026-08-13 Local scheduler interval closure

- DONE-LOCAL：已推送 commit `524e51a` 在 `apps/api/ekb_api/main.py` 增加 `EKB_LOCAL_SCHEDULER_INTERVAL` 的 `1–300` 秒有界解析；非法、越界或非十进制值回退 `60` 秒，且只在 development 本地 scheduler 分支生效。新增测试为 `apps/api/tests/test_local_scheduler.py`。
- 本地 API 以 `interval=2` 启动后，真实 XLSX/PPTX 上传批次由后台 tick 从处理中推进到 `CHUNKING · 50%`，随后进入真实 `EMBEDDING_UNAVAILABLE/FAILED`；未出现 `MIME_UNSUPPORTED`、`PARSER_CORRUPT` 或伪 `READY`。
- 验证：local scheduler + PH3 office/ingestion/scheduler 定向回归 `34 passed`；Ruff 与代码 diff check 通过。详细证据见 [`docs/evidence/ekb-core-rebuild/local/2026-08-13-local-scheduler.md`](docs/evidence/ekb-core-rebuild/local/2026-08-13-local-scheduler.md)。
- 本次文档更新的 `git diff --check` 与新增文档 secret scan：通过。
- NOT RUN/BLOCKED：生产 PostgreSQL/pgvector、Provider/Embedding 出网、生产对象存储、可靠队列、生产 Worker/Scheduler、服务器备份/部署/回滚仍未执行。生产目标统一使用 `[production host]`，受管敏感值统一使用 `[REDACTED]`；不记录凭据、token 或私密地址。本条不代表生产 scheduler 或 PH3/PH8 完成。
## 2026-08-13 Recycle restore/purge local closure

- DONE-LOCAL：`apps/api/ekb_api/services/v3_trash.py` 完成回收站 restore/purge 本地闭环；restore 校验 retention、purge state、tenant scope，并使用 CAS；purge 校验 expiry/state/generation；重复删除创建新的 generation 和 retention window。配套测试为 `apps/api/tests/v4/test_ph2_trash.py`。
- 验证：专项 `6 passed`；后端全量 `385 passed, 2 skipped`；指定文件 Ruff `All checks passed!`；`git diff --check` 通过。
- 本地真实浏览器：在 `127.0.0.1:5173/#/knowledge` 创建“回收站本地验收-20260813”，删除后在 `#/recycle` 看到真实条目和 30 天保留，点击“还原”显示“已还原…”且知识库重新出现在授权列表。浏览器 console 有既存错误，不宣称 `console=0`。
- 限制：Document 删除历史状态缺失时 restore 安全 fallback 为 `READY`，不代表历史状态完全恢复；对象/向量分阶段清理由其他 worker/service 负责。
- NOT RUN：生产 PostgreSQL、对象存储、可靠队列、Worker/Scheduler、Provider 凭据/外部出网、服务器备份、部署、回滚和生产浏览器均未运行。统一使用 `[production host]` / `[REDACTED]`，不保存凭据；本地结果不代表 PH0–PH8 全部完成。详细证据见 `docs/evidence/ekb-core-rebuild/local/2026-08-13-recycle-restore.md`。

## 2026-08-13 文档中心批量删除本地切片

- DONE-LOCAL：文档中心已支持真实行选择、表头/当前可见页全选、选中数展示、二次确认和批量删除结果反馈。实现文件为 `apps/web/src/app-v2/pages/DocumentsPage.tsx`、`apps/web/src/app-v2/components/documents/DocumentTable.tsx`、`apps/web/src/app-v2/adapters/documents.ts`、`apps/web/src/app-v2/tests/m3.test.ts`。
- 删除语义保持真实边界：前端按选中项逐条调用既有真实单文档 `DELETE`；成功项进入既有回收站，失败项保留并显示部分失败；完成后刷新服务端列表并清理 stale selection。能力投影 `documents.bulk-delete=available`，但没有宣称新增真实批量删除 API。
- 本地浏览器：`127.0.0.1:5173/#/documents` 的真实服务端文档列表显示“已选择 6 项”和已启用的“批量删除 (6)”；本次未点击删除，未误删本地真实文档。浏览器存在既存 console 错误，不声称 `console=0`。
- 验证：`npm test -- --run` 为 `58 passed`；typecheck、build、`git diff --check` 通过；build 保留已有 chunk `>500KB` warning。未记录凭据、token 或私有地址。
- 生产 PostgreSQL/对象存储/队列/Worker/Provider/服务器部署、备份和回滚仍 `NOT RUN`；统一使用 `[production host]` / `[REDACTED]`，本地切片不代表生产完成或 PH0–PH8 全部完成。详见 [`docs/evidence/ekb-core-rebuild/local/2026-08-13-document-bulk-delete.md`](docs/evidence/ekb-core-rebuild/local/2026-08-13-document-bulk-delete.md)。

## 2026-08-14 Jobs runtime 与 legacy Office 本地收口

- DONE-LOCAL：Jobs runtime 后端/前端真实展示 worker heartbeat、retention lease 和 scheduler runs；legacy Office 通过隔离 `soffice` 转换复用 OOXML parser，采用 `shell=False`、独立临时目录/profile 和 30 秒超时，目标 parser 错误透传。
- 验证：后端 `390 passed, 2 skipped`；Web `60 passed`；typecheck/build、相关 Ruff、`git diff --check` 通过；Office 定向 `14 passed`；真实本地 XLS→XLSX→`XlsxParser` smoke 已执行。浏览器作业中心看到真实 worker/lease/最近 `SUCCEEDED` run；历史 auth console errors 仍存在，不宣称 `console=0`。
- 决策与边界：FR-015、CR-PH2 runtime/jobs、CR-PH3 legacy parser 仅记 `DONE-LOCAL`，不改历史 Spec 合同；生产 PostgreSQL/pgvector、远程 Provider/Embedding、对象存储、可靠队列/生产 Worker、服务器备份/部署/回滚仍 `NOT RUN/BLOCKED`。不保存凭据、token、密码或私网地址，生产统一使用 `[production host]` / `[REDACTED]`。

## 2026-08-14 Jobs runtime stale heartbeat 修复

- scheduler 默认 `owner_id` 使用稳定进程标识；runtime projection 对 `local-scheduler` 仅展示当前 `scheduler_leases.owner_id`，历史替换 worker heartbeat 保留但不作为当前 worker展示。
- 测试、Ruff、`git diff --check` 通过；浏览器复验为 `1 worker / 1 lease / 真实 SUCCEEDED run`。生产仍未执行，使用 `[production host]` / `[REDACTED]`，不保存凭据、token、私网地址或完整 opaque ID。

## 2026-08-14 TeamPage 成员邀请与目录导出

- DONE-LOCAL：TeamPage 真实邀请使用 `services.admin.inviteUser`，成功清空密码并刷新 `services.identity.listUsers`；固定 `team-members.csv` 仅导出当前服务端已加载成员，RFC4180 quoting + 公式注入前缀，不写密码、tenant 或 token。
- 后端 `/admin/users` 使用 `team:user:manage`，兼容旧 OWNER/ADMIN，普通 MEMBER 拒绝并审计；`SqlStore.create_user` 同事务写入 v3 `tenant_memberships` / `user_profiles`，测试确认邀请后可见且租户隔离。
- 验证：当前后端定向回归报告 `17 passed`；前端 `v3.team-directory.test.ts` 定向 `4 passed`，覆盖按钮状态、邀请 adapter 成功/失败、校验与 CSV 防注入；完整 Web/后端收尾结果见对应证据文件。
- 边界：这是本地成员账户创建与 CSV 导出，不是邮件投递、生产身份平台、角色/权限 CRUD、SCIM 或 PH0–PH8 完成；服务器与生产仍 `NOT RUN/BLOCKED`，统一使用 `[production host]` / `[REDACTED]`，不记录秘密。

## 2026-08-14 Assistant 会话导出

- DONE-LOCAL：导出仅在 ready、非流式且有非 transient 消息时启用；只导出当前服务端已持久化消息，过滤流式 transient 内容。
- Share 保持 disabled；生产、部署、备份、回滚和 PH0–PH8 全部完成仍 `NOT RUN/BLOCKED`。
- 证据：`docs/evidence/ekb-core-rebuild/local/2026-08-14-conversation-export.md`。不保存密码、token、API key 或私密地址。

## 2026-08-14 会话重命名本地切片

- 已完成真实会话重命名：后端已有 `PATCH /api/v1/chat/conversations/{id}` 接入前端 `ApiClient`、adapter、受控 UI 和服务端刷新；未知客户端异常不泄漏原始 `message`。Share/Projects 仍 disabled。
- 验证：定向 Vitest `2 passed`；Web `12 files / 68 tests passed`；typecheck/build passed（build 有既有 `>500KB` warning）。
- 仅为本地 `DONE-LOCAL`；PH0–PH8、服务器、备份、部署和回滚未执行，仍 `NOT RUN/BLOCKED`；不记录秘密。

## 2026-08-14 会话分支本地切片

- DONE-LOCAL：真实接入会话分支端点 `GET /chat/conversations/{id}/branches`、`POST /chat/conversations/{id}/active-branch` 和 `GET /chat/conversations/{id}/messages?branch_id=`；`ApiClient` 兼容旧数组与新 `{messages}` 响应，adapter 完成分支映射和安全错误，`AssistantPage` 使用真实选择器并刷新消息。
- 验证：`npx vitest run src/app-v2/tests/v3.conversation-branches.test.ts` 为 `4 passed`；`npm run test` 为 `13 files / 72 tests passed`；`npm run typecheck`、`npm run build`、`git diff --check` 通过。
- 当前浏览器未认证；Share/Projects 保持 disabled。本地结果不代表 PH0-PH8 或生产完成。
- 生产/服务器、备份、部署和回滚未执行，仍为 `NOT RUN/BLOCKED`；不记录密码、token、凭据或私有地址。证据见 `docs/evidence/ekb-core-rebuild/local/2026-08-14-conversation-branches.md`。

## 2026-08-14 legacy store.save_message 到 chat branch graph 本地桥接

- DONE-LOCAL：在 v4 schema 下，`store.save_message` 为 legacy QA 消息自动创建/激活 root branch，并写入 `branch_id`、`parent_message_id`、`content_hash`；无 v4 列时保持 legacy 路径。
- 验证：`bridge-check` 临时 SQLite 为 `PASS`；PH4 `32 passed`；API `28 passed`；ruff/compileall `passed`。
- 该条记录当时尚未完成；后续已由 2026-08-14 qa.py active-branch history bridge 本地收口条目完成。当前浏览器未认证。本地开发切片不宣称 PH4 或 PH0–PH8 全部完成。
- 生产/服务器（`[production host]`）、备份、部署、回滚均 `NOT RUN/BLOCKED`；不记录密码、token、API key 或私密地址。证据见 `docs/evidence/ekb-core-rebuild/local/2026-08-14-chat-branch-bridge.md`。

## qa.py active branch history local closure (2026-08-14)

- DONE-LOCAL：`/api/v1/qa/ask` 多轮 history 优先按 `tenant + actor` 读取 active branch materialization，过滤 `HIDDEN` 消息和当前 turn；branch graph 或旧 schema 不可用时回退 `store.list_messages`。
- 验证：tests/test_multi_turn.py：11 passed（包含 active-branch regression）；PH4 `32 passed`；隔离临时 SQLite API `28 passed`；`compileall` passed；`git diff --check` passed；本地 `/healthz` `200`。
- 当前浏览器未认证；本地业务切片不宣称 PH0–PH8 全部完成。生产服务器（`[production host]`）、备份、部署、回滚仍 `NOT RUN/BLOCKED`。证据见 [`docs/evidence/ekb-core-rebuild/local/2026-08-14-qa-active-branch-history.md`](./docs/evidence/ekb-core-rebuild/local/2026-08-14-qa-active-branch-history.md)。不记录密码、token、API key 或私密地址。

## 2026-08-14 chat branch materialization hidden-message filter local slice

- DONE-LOCAL：`ConversationGraphService.branch_messages` 现在只返回 `visibility_state` 为 `NULL` 或 `visible` 的消息；隐藏的取消/失败占位不会进入 branch materialization 或 UI。
- 新增回归测试验证可见祖先与后续保留、hidden 排除；PH4 `33 passed`；Ruff、`compileall`、`git diff --check` passed；本地 `/healthz` `200`；当前浏览器未认证。
- 生产服务器（`[production host]`）、备份、部署、回滚均 `NOT RUN/BLOCKED`；这是本地切片，不宣称 PH0–PH8 完成。证据见 [`docs/evidence/ekb-core-rebuild/local/2026-08-14-chat-hidden-filter.md`](docs/evidence/ekb-core-rebuild/local/2026-08-14-chat-hidden-filter.md)。不记录密码、token、API key 或私密地址。

## 2026-08-14 Legacy `SqlStore.save_message` parent-chain bridge local closure

- DONE-LOCAL：v4 bridge 对带 `turn_id` 的 `ASSISTANT` 优先选择同 `conversation`/`tenant`/`branch`/`turn` 的 `USER` 作为 parent，避免同秒 UUID 排序导致父子反向；无同 turn `USER` 时保留原 branch head 查询；无 v4 schema 时仍走 legacy 路径。
- 新增回归测试覆盖真实临时 v4 SQLite；验证：PH4 `34 passed`；Ruff、`compileall`、`git diff --check` passed。
- 已认证本地浏览器可见真实 Assistant 会话、知识库和 root 分支，但旧会话仍可能是历史数据，不宣称旧数据已批量修复。本地切片不宣称 PH0–PH8 完成。
- 生产服务器（`[production host]`）、备份、部署、回滚均 `NOT RUN/BLOCKED`；证据见 [`docs/evidence/ekb-core-rebuild/local/2026-08-14-chat-parent-chain.md`](docs/evidence/ekb-core-rebuild/local/2026-08-14-chat-parent-chain.md)。不记录密码、token、API key 或私密地址。

## 2026-08-14 重启后真实浏览器 parent-chain 验证

- 重启本地 API 使最新 `store.save_message` bridge 生效；`/healthz` 返回 `200`。
- 真实 Playwright 浏览器本地登录后新建 Assistant 会话并发送“重启后父链验证：请简短回答”；真实 SSE v2 经远程 LLM + 当前授权 KB RAG 返回回答。页面 branch selector=`root`，消息顺序为 `USER` 后 `ASSISTANT`。
- SQLite 核对同一 `turn` 的 `USER`/`ASSISTANT` 同 branch，`assistant.parent_message_id` 指向 `USER`。本次浏览器 snapshot 未显示 console 错误，但 console 未作为零错误验收，不声称 `console=0`。
- 验证快照：后端全量 `401 passed, 2 skipped`；前端 `72 passed`；typecheck/build passed；`npm audit` 为 `0 vulnerabilities`。本次文档写入未重跑验证。
- 本地切片不宣称 PH0–PH8 全部完成；生产 PostgreSQL/pgvector、对象存储、队列/Worker/Scheduler、服务器备份、部署和回滚仍 `NOT RUN/BLOCKED`，服务器统一使用 `[production host]`。不记录密码、token、私密地址或 Provider 密钥。证据：`docs/evidence/ekb-core-rebuild/local/2026-08-14-browser-parent-chain.md`。
