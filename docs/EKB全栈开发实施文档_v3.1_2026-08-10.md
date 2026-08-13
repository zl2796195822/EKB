# EKB 全栈开发实施文档 v3.1

> 版本：v3.1
> 日期：2026-08-10
> 状态：执行中
> 上游 authority：[`docs/pmos/features/2026-08-09_ekb-fullstack-v3/`](./pmos/features/2026-08-09_ekb-fullstack-v3/)（requirements / spec / plan / verification-matrix）
> 被替代文档：[`EKB全前端重建实施文档_v2.0_2026-08-09.md`](./EKB全前端重建实施文档_v2.0_2026-08-09.md)（superseded-by v3）

## 0. 本文为什么存在

v2.0 把任务定义成「纯前端重建、禁止改后端」，结果是十页里有四页（个人中心、回收站、数据分析、应用中心）只能把核心动作渲染成 `disabled` / `unavailable` —— 页面看起来完成了，点下去什么也不会发生。同时 GlobalShell 布局存在跳变缺陷：点击「知识库 / AI 助手」会整页跳走，而不是像其他模块一样在右侧内容区渲染。

v3.1 撤销「禁止改后端」这条边界，改为**前后端一起做**：每个模块都必须有真实迁移、真实服务层、真实路由、真实前端，并以「真实运行的服务端 E2E 冒烟」作为完成判据。本文是 Phase 0–4 的实施与验收依据。

### 0.1 不可变边界（v3.1 生效）

- 允许新增数据库迁移、服务层、路由、前端页面；**不允许修改任何已 applied 的迁移文件**（见 §1.2 校验和锁定）。
- `app-v2` 的适配器边界继续生效：只有 `src/app-v2/adapters/**` 可以 import `src/lib/api.ts` 与 `src/types/api.ts`；页面通过 props 注入 `services`，不得直接 `fetch`，不得写内联 `style={{`。
- 核心动作**不允许**以 `disabled` / `unavailable` 作为最终交付。确属本期不做的能力（例如头像上传），必须在适配器的 capabilities 里显式声明为 `unavailable`，并在页面上说明原因，而不是静默禁用。
- 后端 Python 版本为 **3.9**：禁止 `X | None` 语法，一律用 `Optional[X]`。
- CORS 只放行 `GET / POST / PATCH / DELETE / OPTIONS`。**不得使用 PUT**，「标记已读」这类幂等更新一律用 PATCH。

## 1. 工程约束

### 1.1 目录与技术栈

| 层 | 位置 | 技术 |
| --- | --- | --- |
| 前端 | `apps/web/src/app-v2/` | React 19 + Vite 7 + TS 5.8，hash 路由 |
| 适配器 | `apps/web/src/app-v2/adapters/` | `createXAdapter(client: XApiClient)`，窄接口注入 |
| 后端路由 | `apps/api/ekb_api/routers/` | FastAPI |
| 后端服务 | `apps/api/ekb_api/services/` | 纯函数 + SQLAlchemy `text()` |
| 迁移 | `apps/api/ekb_api/migrations/` | 手写 DDL + `schema_migrations` 台账 |

API 统一前缀 **`/api/v1`**，健康检查在 **`/healthz`**（不在 `/api/v1` 下）。

### 1.2 迁移机制：校验和锁定

项目不用 Alembic。每个迁移模块自带 `VERSION`、`_DDL` 元组和 `V3_00X_CHECKSUM = sha256(DDL)`，applied 后校验和写入 `schema_migrations`。启动时 `verify_*` 会比对台账校验和与代码校验和。

**这意味着：一个迁移一旦 applied，它的 DDL 就不能再改。** 改了校验和就对不上，服务直接拒绝启动。

> **踩坑记录（2026-08-10，Phase 1）**：`services/v3_profile.py` 的 SQL 是按「我以为的表结构」写的，和 `v3_001_identity` 实际锁定的 schema 有偏差 —— `auth_sessions` 主键是 `session_id` 不是 `id`、UA 列叫 `user_agent_hash`、`user_notifications` 的类型列叫 `notification_type` 不是 `type` 且根本没有 `priority` 列、`user_preferences` 没有 `created_at` 列。
> 结果是 3 个接口 500。**修复必须落在服务层，不能改迁移**：`priority` 改为存进 `metadata` JSON 再读出来（默认 `NORMAL`），列名全部对齐迁移实际定义。
> 教训：写服务层 SQL 前，先读迁移文件里的真实 DDL，不要凭记忆。

新增迁移的落地步骤：

1. 新建 `migrations/v3_00X_<name>.py`，导出 `VERSION` / `apply_v3_00X` / `verify_v3_00X` / `rollback_v3_00X_dry_run`。
2. 在 `migrations/v3_fullstack.py` 的 CLI runner 里注册该版本（当前 runner 只认 `v3_001`，需要扩成版本列表按序执行）。
3. 应用：`.venv/bin/python -m ekb_api.migrations.v3_fullstack --database-url <url> --through v3_00X --verify`

### 1.3 测试分层与命名约定

| 层 | 位置 | 运行方式 | 作用 |
| --- | --- | --- | --- |
| 前端契约测试 | `src/app-v2/tests/v3*.test.ts` | `npx vitest run` | 适配器映射、状态机、边界审计 |
| 前端旧测试 | `src/app-v2/tests/m*.ts` 等 | `vite-node` 自跑脚本 | 历史遗留，不动 |
| 后端 E2E 冒烟 | `apps/api/scripts/smoke_v3_*.py` | `.venv/bin/python ...` | **打真实运行的服务** |

> **`vitest.config.ts` 的 `include` 只匹配 `src/app-v2/tests/v3*.test.ts`**，且必须写成 vitest 风格（`describe / it / expect`）。写成旧的 `void runXTests()` 自跑脚本风格会被判定为 "no test suite found" 而静默不执行。

> **冒烟测试不可省略。** Phase 1 的三个 500 错误，TypeScript 类型检查通过、vitest 18 项全绿，全都没发现 —— 因为它们是运行期 SQL 列名错误。只有打真实服务的 E2E 能抓到。

前端页面边界审计写进契约测试：读取页面源码，断言使用了 `services.X`、没有 `src/lib/api` 的 import、没有内联 `style={{`。

## 2. 五阶段实施计划

### Phase 0 — 布局统一（已完成）

**问题**：点击「知识库 / AI 助手」整页跳转，脱离 GlobalShell，和其他模块行为不一致。

**决策**：采用方案 B —— 统一用 GlobalShell 壳，知识库与 AI 助手作为壳内内容区渲染，不再单独占据整页。

**验收**：十个模块的切换行为一致，左侧导航与顶栏始终在位，无整页跳变。

### Phase 1 — 个人中心全栈（已完成）

**迁移**：复用 `v3_001_identity`，涉及 `user_profiles` / `user_preferences` / `user_notifications` / `api_keys` / `auth_sessions`。不新增迁移。

**服务层**：`services/v3_profile.py`

**路由**：`routers/me.py`，共 12 个端点：

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/me` | 用户 + profile + capabilities + tenants |
| PATCH | `/me/profile` | 局部更新，未提供的字段不清空 |
| GET / PATCH | `/me/preferences` | JSON 白名单键 |
| POST | `/me/password` | `old_password` / `new_password`（snake_case），改密后吊销全部 ACTIVE 会话 |
| GET | `/me/sessions` | 会话列表 |
| DELETE | `/me/sessions/{id}` | 吊销单个会话 |
| GET / POST | `/me/api-keys` | 列表 / 创建（secret 仅返回一次） |
| DELETE | `/me/api-keys/{id}` | 吊销 |
| GET | `/me/notifications` | 支持 `unread_only` 查询参数 |
| PATCH | `/me/notifications/{id}` | 标记已读（**PATCH 不是 PUT**） |

**服务层要点**：

- `auth_sessions` 主键 `session_id`，写入时必须自带 `session_id`（`uuid4().hex`）与 `expires_at`（NOT NULL，默认 7 天）；按 `jti_hash` 幂等。
- 时间统一用 `utc_now()`（来自 `ekb_api.domain`），不要用 SQLite 方言的 `datetime('now')`；SQLite 不支持 `NULLS LAST`。
- `SqlStore` **没有** `get_user_by_id`，只有 `get_user_by_email`；需要按 id 取用户请直接写 SQL。
- 通知的 `priority` 存在 `metadata` JSON 里，读出来时默认 `NORMAL`。

**auth.py 静默失败修复**：登录 / 刷新 / 登出三处对会话表的写入原本是 `except Exception: pass`，这正是「会话写入一直是坏的却没人发现」的原因。改为 `_log.warning("auth.session.write_failed", ...)` 等结构化日志 —— 仍然不阻断主流程，但必须留痕。

**前端**：`pages/ProfilePage.tsx` + `adapters/profile.ts` + `styles/profile.css`（在 `globals.css` 中于 `m6.css` 之后 import）。

`adapters/profile.ts` 的 `mapApiKey` 入参需要放宽为 `ApiKeyItem | ApiKeyCreateResponse`：后端 `POST /me/api-keys` 的响应不含 `last_used_at`，适配器自己补 `null`。

**声明为 unavailable 的能力**：`profile.avatar-upload`（本期不做对象存储）。

**验收结果**：

- `npm run build` 通过（tsc -b + vite build）
- `npx vitest run` → 18 passed（v3.identity 6 + v3.profile 12）
- `smoke_v3_profile.py` → **33 通过 / 0 失败**，覆盖全部 12 个端点，含真落库校验、局部更新不清空、secret 只返回一次、改密后旧密码被拒、未鉴权 401

### Phase 2 — 回收站全栈（已完成）

**现状**：`RecyclePage.tsx` 105 行，全页 `unavailable`，后端**完全没有** trash / recycle 路由。软删除能力本身已存在但分散：`KnowledgeBase.deleted_at`、`Document.status = DELETED`、`Conversation.deleted_at`。

**迁移 `v3_002_content`**：

- 新建 `trash_items` 统一投影表：`id` / `tenant_id` / `resource_type`(KB|DOCUMENT|CONVERSATION) / `resource_id` / `title` / `deleted_by` / `deleted_at` / `expires_at` / `restored_at` / `purged_at` / `metadata`。
- 对三类已软删数据做 backfill，保证历史软删记录在回收站可见。
- 索引：`(tenant_id, deleted_at DESC)`、`(tenant_id, resource_type)`、`(resource_type, resource_id)` 唯一。

**服务层 `services/v3_trash.py`**：list（按类型/关键词过滤 + 分页）、restore、purge、clear_all。恢复时反写原表（清 `deleted_at` / 状态改回），并写 `restored_at`。

**路由 `routers/trash.py`**：

| 方法 | 路径 |
| --- | --- |
| GET | `/api/v1/trash` |
| POST | `/api/v1/trash/{item_id}/restore` |
| DELETE | `/api/v1/trash/{item_id}` |
| DELETE | `/api/v1/trash` |

**`store.py` 改造**：三处软删除路径同步写入 `trash_items`，避免投影表与原表脱节。

**前端**：重写 `RecyclePage.tsx` + 新增 `adapters/trash.ts` + `types/trash.ts` + `styles/trash.css`（在 `globals.css` 中于 `profile.css` 之后 import），去掉全部 `unavailable`。

**实现踩坑（务必记住）**：

- 下游表的外键列名**不统一**：`chunks` / `document_versions` / `ingest_jobs` 用的是 `doc_id`（不是 `document_id`）；`messages` / `review_items` / `qa_turns` 用 `conversation_id`；`feedback` 只有 `message_id`，必须用 `messages` 子查询级联。写 `_purge_resource` 前先 `PRAGMA table_info` 核对列名，不要凭直觉。
- `documents` 表**没有** `deleted_at` 列，软删只体现为 `status = 'DELETED'`，backfill 时用 `updated_at` 当删除时间。
- `store.py` 写投影用 `_record_trash()` 包一层 try/except，失败只记 `trash.projection_failed` 日志、不阻断业务删除 —— 但绝不能写成 `except: pass`（Phase 1 的教训）。
- 后端 `GET /kb`、`GET /conversations` 返回的是**裸数组**而非 `{items: []}`；冒烟脚本要做兼容。
- 没有 `POST /conversations` 端点（会话由问答流程隐式创建），冒烟脚本改为借用一条已有会话做删除/还原并在结束时还原，保证可重复执行。
- 适配器空态判定用 `total > 0`，不能用 `items.length`：翻页越界时 items 为空但回收站并不空。

**声明为 unavailable 的能力**：`trash.auto-purge-job`（后端只写 `expires_at`，没有定时清理任务，到期项仍需手动永久删除）。

**验收结果**：

- 迁移 `v3_002_content` 应用成功，backfill 4 KB + 5 DOCUMENT + 531 CONVERSATION，重复执行幂等，rollback dry-run 通过
- `smoke_v3_trash.py` → **36 通过 / 0 失败**，覆盖「软删 → 回收站可见 → 还原 → 原位置重新可见 → 再删 → 永久删除 → 源资源 404 → 重复还原 404」，含类型筛选、关键词搜索、分页、父库未还原时文档不可还原、未鉴权 401
- `npm run build` 通过；`npx vitest run` → **27 passed**（v3.identity 6 + v3.profile 12 + v3.trash 9）
- 经 Vite 代理的真实链路复测：`GET /api/v1/trash` 返回 `counts = {KB: 6, DOCUMENT: 5, CONVERSATION: 531}`，类型筛选生效
- 后端日志 0 条 500、0 条 `trash.projection_failed`

### Phase 3 — 数据分析增强（待做）

**已知缺陷**：`store.py` 的运营看板 `days` 参数没有真正生效，需先修。

**迁移 `v3_004_analytics`**：新建 `resource_access_events`（`tenant_id` / `resource_type` / `resource_id` / `actor_id` / `action` / `occurred_at`），用于「热门知识库 / 热门文档 / 访问趋势」。

**服务层 `services/v3_analytics.py`** + `routers/admin.py` 新增端点。

**前端**：重写 `AnalyticsPage.tsx`，图表用 SVG 手绘（不引第三方图表库）。涨跌配色遵循中国习惯（涨红跌绿）。

### Phase 4 — 应用中心全栈（待做）

**迁移 `v3_005_apps`**：`app_catalog`（内置应用目录）+ `app_installations` + `app_credentials` + `app_runs`。

**服务层 `services/v3_apps.py`** + **路由 `routers/apps.py`**：list / install / uninstall / configure / run。

**前端**：重写 `AppsPage.tsx` + `adapters/apps.ts`。

## 3. 全局验收门禁

每个 Phase 收尾必须同时满足：

1. `cd apps/web && npm run build` 零错误。
2. `npx vitest run` 全绿，且该 Phase 新增了对应的 `v3.<module>.test.ts`。
3. `.venv/bin/python apps/api/scripts/smoke_v3_<module>.py http://127.0.0.1:8023/api/v1 <email> <password>` 全通过。
4. 后端日志无 500、无 `*_failed` 警告。
5. 页面边界审计通过：无 `src/lib/api` 直接 import、无内联 `style={{`、核心动作无 `disabled`。

全部 Phase 完成后：十页路由全量走查、`design-qa.md` 更新、旧 UI 无引用审计、生产构建复核。

## 4. 本地运行

```bash
# 后端（注意 --app-dir）
cd /Users/alin/EKB
.venv/bin/python -m uvicorn ekb_api.main:app --host 127.0.0.1 --port 8023 --app-dir apps/api

# 健康检查（不带 /api/v1 前缀）
curl http://127.0.0.1:8023/healthz

# 前端
cd apps/web && npm run dev
```

冒烟测试的 base_url **必须带 `/api/v1`**，否则全部 404。

## 5. 变更记录

| 日期 | 版本 | 变更 |
| --- | --- | --- |
| 2026-08-10 | v3.1 | 撤销「禁止改后端」边界，改为全栈五阶段；记录 Phase 0/1 完成与迁移校验和锁定踩坑 |
