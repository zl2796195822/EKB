# EKB 全前端重建实施文档 v2.0

> **v3 successor notice（已替代，2026-08-10）：** 本文保留 v2 的完整正文与历史执行记录；当前十页真实前后端闭环以 [`docs/pmos/features/2026-08-09_ekb-fullstack-v3/README.md`](./pmos/features/2026-08-09_ekb-fullstack-v3/README.md) 及其 requirements/spec/plan/matrix 为 authority。`superseded-by`：v3；reason：v3 supersedes frontend-only、禁止后端修改和将核心动作以 `disabled/unavailable` 作为最终交付的边界。除本 authority banner/status 外，不改写下方历史执行文字。

> 版本：v2.0
> 日期：2026-08-09
> 状态：已替代（superseded-by v3）
> 视觉真值：[`/Users/alin/EKB/设计稿/index.html`](../设计稿/index.html) 及其实际渲染结果
> 替代文档：[`前端视觉改版实施文档_v1.0_2026-08-09.md`](./前端视觉改版实施文档_v1.0_2026-08-09.md)

## 1. 决策摘要与不可变边界

本版本把 EKB 前端定义为一次“从零重建”，不是旧组件换皮、旧 JSX 搬迁或旧 CSS 重命名。旧 `QAPage`、`KbPage`、`SearchPage`、`OpsPage`、旧 `DashboardPage`、旧 UI 组件、旧 `styles.css` / `styles.css.bak` 和旧布局全部废弃，不作为新前端的重建基础。视觉与交互只以 `设计稿/index.html` 的实际渲染为真值；旧 React 页面只用于读取已有业务能力和做最终无引用审计。

允许复用的只有非 UI 能力：

- 后端 API 契约、现有端点的请求/响应类型和错误码约定；
- 认证、刷新、退出、`me`、租户/主体授权和会话生命周期能力；
- SSE v1/v2 解析、`turn_id` / `seq` 隔离、取消和流状态能力；
- 知识库、文档上传/删除/重试、版本与 diff、成员 ACL、检索、反馈和管理端点；
- 与上述能力对应的非 UI `types`。复用类型不等于复用页面、组件、class、布局或样式。

### 保留的非 UI 后端能力层

`apps/web/src/lib/api.ts` 与 `apps/web/src/types/api.ts` 明确属于保留的“非 UI 后端能力层”：前者是请求、鉴权刷新、SSE 和错误响应的客户端边界，后者是现有 API 契约的前端类型；它们不是旧 UI，不因 M7 删除旧页面、组件或 CSS 而删除。当前 `ApiClient` 对 M3-M6 所需的部分既有端点覆盖不完整，这属于客户端能力缺口，不是后端端点或 API 契约缺口。M3 可以仅为已存在契约补齐最小方法/类型，包括 KB 详情/编辑、文档详情/重试、KB members 列表/添加/删除，以及上传 `202` 响应和既有错误 `code` / `request_id` / `details` 元数据的类型化暴露；M5/M6 也可以仅为服务端已存在但 `ApiClient` 尚未覆盖的 admin 能力补齐最小 client methods/types。所有这些扩展都不得新增请求路径、改变请求/响应字段、状态码、鉴权/权限语义或修改服务端。

M5 的 client 扩展严格限于当前已存在的 `POST /admin/users`、`GET /admin/tenants`、`POST /admin/tenants`；`GET /me` 已存在，只复用现有方法/类型，不重复扩展。`POST /admin/users` 只对应既有 `UserInvite` 字段 `email`、`name`、`password`、`role`，以及既有响应字段 `id`、`email`、`name`、`role`、`tenant_id`；租户创建只对应既有 `TenantCreate` 字段 `name`、`owner_email`、`owner_name`、`owner_password`、`model_routing_key`、`egress_policy`、`quota_daily_qa`、`quota_storage_docs`、`quota_storage_bytes_per_file`，并复用既有 `TenantResponse` 字段 `id`、`name`、`role`、`model_routing_key`、`egress_policy`、`quota_daily_qa`、`quota_storage_docs`、`quota_storage_bytes_per_file`。不得借此增加用户列表、角色 CRUD、资料更新、安全/通知/偏好或 API key 端点。

M6 的 client 扩展严格限于当前已存在的 `GET /admin/ops/dashboard?days=`、`GET /admin/audit`、`GET /admin/reviews`、`GET /admin/reviews/{item_id}`、`PATCH /admin/reviews/{item_id}`、`GET/POST /admin/sync/sources`、`POST /admin/sync/sources/{source_id}/run` 和 `POST /admin/backup` 的最小封装/类型，复用当前 router/schema 的字段和查询参数，不虚构 endpoint。它们是治理/运营能力，不等于应用安装、连接、卸载，也不等于回收站列表、恢复、永久删除或清空；页面/组件/布局只呈现设计稿已有结构，任何完整契约覆盖都必须留在 app-v2 adapters，且不得新增脱离设计稿的运营导航。

在生产 app-v2 中，只有 `src/app-v2/adapters/**` 可以消费 `src/lib/api.ts` 和 `src/types/api.ts`；页面、组件、布局、styles、fixtures 均不得直接 import 这两个文件，也不得直接调用 `fetch`。`adapters` 向页面暴露 v2 view model 和状态，不把 API 客户端边界泄漏到 UI。

实施顺序固定为：在新目录并行构建 → 新目录完成十页与关键流程验收 → 切换 `App` 入口 → 对生产 import 做全量审计 → 仅删除无引用旧 UI。切换前不得删除旧文件，切换后也不得为了“清理”而删除仍被引用的能力代码。

以下事项在本任务中明确禁止：

- 不修改后端、数据库、API 路径、认证策略、权限策略或数据模型来迎合设计稿；
- 不把设计稿中的 mock 数字、mock 会话、mock 成功 toast 或静态文档当作真实业务结果；
- 不把旧页面包一层新壳后宣称完成重建；
- 不用 `git reset`、`git checkout` 或其他覆盖操作处理工作区脏改动。

## 2. 视觉真值与十页页面地图

### 2.1 真值来源

唯一视觉真值是 `设计稿/index.html` 及浏览器实际渲染。基线检查以设计稿页面中的 section header、专用顶栏、导航层级、卡片/表格/表单/模块 tile、信息密度、间距、状态色、响应式折叠和交互状态为准，不以旧 `apps/web/src` 的 DOM 或 CSS 为准。

设计稿实际渲染包含以下十个页面：

| 编号 | 页面 | 规范路由 | 渲染基线要点 |
|---:|---|---|---|
| 1 | 工作台 / Dashboard | `#/dashboard` | 设计稿现有完整首页：全局侧栏、顶栏、问候与快捷操作、指标卡、趋势/分布、动态、最近访问、标签和健康度 |
| 2 | 知识库 / Knowledge Base | `#/knowledge` | 专用顶栏 + 左知识空间导航 + 中间文件表 + 右侧知识库概览 |
| 3 | AI 助手 / AI Assistant | `#/assistant` | 专用顶栏 + 左会话列表 + 中间聊天/引用答案/composer + 右知识库上下文/文档预览 |
| 4 | 文档中心 / Document Center | `#/documents` | section header、筛选/上传操作、搜索与类型/空间筛选、文档表格、分页 |
| 5 | 团队与权限 / Team & Permissions | `#/team` | section header、导出/邀请、成员管理/角色管理/权限矩阵 tabs、成员表格和权限状态 |
| 6 | 数据看板 / Analytics | `#/analytics` | section header、日期范围控件、指标 cards、访问趋势图、内容使用分布 |
| 7 | 应用中心 / App Center | `#/apps` | section header、应用搜索、全部应用/已安装/推荐 tabs、应用 module tiles/cards |
| 8 | 回收站 / Recycle Bin | `#/recycle` | section header、保留期提示、清空操作、删除项表格、还原/永久删除操作 |
| 9 | 个人中心 / Settings | `#/profile` | section header、个人资料 card、个人信息/账户安全/通知/偏好/API 管理 tabs、表单、快捷入口 |
| 10 | 模块地图 / Module Map | `#/modules` | section header、返回工作台、九个业务模块 tiles；模块地图本身也是可访问页面 |

设计稿中“工作台”导航和模块地图展示的九个业务模块共同组成上述十页；不能把模块地图误当作弹窗，也不能把个人中心降级为用户下拉菜单。`#/search` 不列入 v2 的独立页面：旧 `SearchPage` 废弃，但 `POST /api/v1/search` 能力继续作为知识库、文档中心、AI 助手引用和全局搜索入口的 adapter 能力。

### 2.2 精确页面蓝图

#### 工作台 `#/dashboard`

按设计稿现有完整首页实现，不从旧 `DashboardPage` 复制 JSX。保留并逐项重建：

- 全局侧栏、顶栏、搜索入口、通知/帮助/设置/用户入口和模块地图入口；
- 问候区和四个快捷操作入口；
- 五张指标卡；
- 知识库访问趋势、知识库使用分布和最近动态；
- 最近访问表格、热门标签、知识库健康度及底部信息。

所有指标、图表、最近动态和健康度都必须有真实 API 来源或明确的 loading/empty/unavailable 状态。没有相应后端端点时只保留设计稿的结构和状态占位，不显示看似真实的 mock 成功数据。

#### 知识库 `#/knowledge`

严格采用设计稿的三段式内容关系：

- 专用顶栏：当前页面标题、全局搜索、通知、帮助、设置、用户入口；
- 左知识空间导航：全部空间、各知识空间、我创建的、与我共享、收藏夹、回收站；
- 中间文件表：页面标题/说明、上传知识、筛选/排序/列表或网格视图、文件表、分页；
- 右知识库概览：文档总数、文件夹、共享文档、成员数、最近更新/热门文档、分类占比。

无文件夹、收藏夹、共享视图或回收站列表端点时，左侧入口必须显示 `unavailable` 或 disabled 说明；不得用静态数量伪装可用。文件表的上传、删除、失败重试、版本和 diff 只调用已授权的真实 API。

#### AI 助手 `#/assistant`

严格采用设计稿的三栏工作区，不引入旧 `ChatPanel`、`ConversationHistory` 或旧 `QAPage`：

- 专用顶栏：知识空间/页面标题、全局搜索、通知、帮助、设置、用户入口；
- 左会话栏：新建会话、会话搜索、最近/收藏/项目 tabs、按日期分组的会话列表、当前知识空间；
- 中聊天区：会话标题和操作、用户消息、AI 回答、引用列表、流式阶段、composer、发送和取消；
- 右上下文栏：知识库状态、已用文档、相关知识、当前引用文档、上下文筛选、文档预览、打开原文/加入引用。

问答必须走真实 conversations/messages 与 SSE adapter；`turn_id`、`seq`、旧流隔离、心跳、错误、拒答、超时、取消、引用和反馈都要有可见状态。设计稿的“联网搜索、深度思考、模型选择、composer 添加文件”在没有对应后端能力时保持 disabled，并说明原因。

#### 文档中心 `#/documents`

保持设计稿 section header（`KNOWLEDGE BASE / WORKSPACE`、标题、说明）和 header 操作区；主体严格按“搜索与筛选工具栏 → 文档表格 → 分页”布局。表格字段以设计稿为准：选择、名称、所属空间、类型、更新时间、更新者、状态、操作。上传、详情、删除、失败重试、版本与 diff 使用真实 API；批量删除因当前没有批量端点，默认 disabled，不得伪造一次请求成功。搜索、类型、空间、状态等无后端过滤条件时只允许对已加载真实结果做明确标注的前端过滤，否则显示 disabled。

#### 团队与权限 `#/team`

保持设计稿 section header、导出/邀请操作、三组 tabs（成员管理、角色管理、权限矩阵）、成员搜索/角色/状态筛选和成员表格。成员表按设计稿显示成员、部门、角色、状态、加入时间、操作；权限不足、成员列表端点缺失或角色矩阵端点缺失时，表格/对应 tab 必须显示无权限或 unavailable 状态。不能把 `POST /admin/users` 的“邀请用户”误认为“用户列表”能力，也不能伪造角色编辑成功。

#### 数据看板 `#/analytics`

保持设计稿 section header、说明、日期范围控件、四张指标 cards、访问趋势和内容使用分布。`/admin/ops/dashboard?days=` 是当前唯一的运营看板数据源；如果主体没有审计读取能力或响应不包含某个设计字段，该 card/chart 显示 permission/unavailable，而不是设计稿 mock 数字。审计、低置信度 review、同步和备份能力只在设计稿已有入口或后续已批准的模块入口中启用，不新增脱离视觉真值的导航结构。

#### 应用中心 `#/apps`

保持 section header、应用搜索、全部应用/已安装/推荐 tabs 和六个应用 module tiles/cards 的结构。当前没有应用安装、连接、卸载或应用目录端点；所有安装按钮及状态操作都显示 disabled/unavailable，并注明“当前后端未提供应用中心端点”。`/admin/sync/sources*` 是同步源能力，不等价于应用安装，不得用同步成功伪造安装成功。

#### 回收站 `#/recycle`

保持 section header、30 天保留提示、清空回收站、删除项表格和还原/永久删除操作。当前后端只有知识库/文档软删除，没有回收站列表、还原、永久删除或清空端点，因此整个数据区必须明确显示 unavailable/disabled；软删除只能在知识库/文档页面按真实 DELETE 端点执行，不能在回收站页面编造结果。

#### 个人中心 / 设置 `#/profile`

保持 section header、资料 card、五个 tabs（个人信息、账户与安全、通知设置、偏好设置、API 管理）、表单和快捷入口。`GET /me` 只用于真实填充主体、租户、能力和策略信息；当前没有资料更新、密码/安全设置、通知、偏好或 API key 端点，因此编辑与保存按钮必须 disabled/unavailable，退出登录只能调用真实 logout。快捷入口只做路由跳转，不在个人中心生成新业务数据。

#### 模块地图 `#/modules`

保持设计稿的 `MODULE MAP / 01 - 09` section header、说明、返回工作台按钮和九个模块 tiles。tile 可以跳转到十页中的对应路由；tile 内的描述和编号是视觉占位，不是后端结果。对应页面或能力未授权时，跳转后应呈现权限/禁用状态，不应在模块地图上报“安装、同步、删除成功”。

## 3. 后端能力矩阵与页面映射

### 3.1 当前后端能力基线

端点前缀统一为 `/api/v1`，鉴权使用 `Authorization: Bearer <access_token>`，写操作遵守契约中的权限、幂等、错误码和审计要求。以下状态按当前 `apps/api/ekb_api/routers/` 与 `docs/API接口契约_v1.0_2026-08-07.md` 对照；新前端不得为了补视觉功能修改这些端点。

| 能力 | 当前端点/范围 | 当前状态 | v2 使用规则 |
|---|---|---|---|
| auth/login-refresh-logout | `POST /auth/login`、`POST /auth/refresh`、`POST /auth/logout` | 已存在 | 登录页和全局会话守卫使用；不保留旧 `DEV_MOCK_SESSION`，过期后清理 v2 内存态并回到登录 |
| me | `GET /me` | 已存在 | 全局用户/租户/能力、团队和个人中心只读数据来源 |
| KB CRUD | `GET/POST/PATCH/DELETE /kb`、`GET/PATCH/DELETE /kb/{kb_id}` | 已存在 | 知识库页真实读写；按钮按权限显示 loading/error/permission |
| 文档上传/删除/重试 | `GET/POST/DELETE /kb/{kb_id}/docs*`、`POST .../{doc_id}/retry` | 已存在 | 知识库与文档中心使用；上传使用 multipart 和 `Idempotency-Key`；不显示假进度/假成功 |
| 文档版本/diff | `GET /admin/kb/{kb_id}/docs/{doc_id}/versions`、`GET .../diff` | 已存在，需管理员/读取能力 | 文档详情/版本面板真实加载；无权限显示 permission 状态 |
| KB 成员 | `GET/POST/DELETE /kb/{kb_id}/members*` | 已存在，需 KB 管理能力 | 知识库与团队权限使用；成员角色来自服务端 |
| conversations/messages/delete | `GET /conversations`、`GET .../{conversation_id}/messages`、`PATCH .../{conversation_id}`、`DELETE .../{conversation_id}` | 已存在 | AI 助手左会话栏使用；软删除/归档结果以响应为准 |
| qa ask SSE/cancel | `POST /qa/ask`、`POST /qa/turns/{turn_id}/cancel` | 已存在，支持 v1/v2 协商 | 只使用 v2 adapter；严格校验 `turn_id`/`seq`，取消按主体/租户授权且幂等 |
| search | `POST /search` | 已存在 | 知识库、文档中心、AI 助手引用和全局搜索使用；不再建立旧 SearchPage |
| feedback | `POST /qa/messages/{message_id}/feedback`；管理读取/标注 `GET/PATCH /qa/messages/feedback*` | 已存在 | AI 回答反馈真实提交；管理视图无权限时 disabled |
| admin audit | `GET /admin/audit` | 已存在，需审计权限 | 仅在批准的运营入口使用；不向普通成员泄露租户或审计数据 |
| admin tenants | `GET/POST /admin/tenants` | 已存在，平台管理员 | 团队/租户管理能力；个人租户切换以登录与 `me` 返回为准 |
| admin users | `POST /admin/users` | 仅邀请端点已存在 | 团队“邀请成员”可用；用户列表/编辑/停用端点不存在，相关控件 disabled |
| admin ops dashboard | `GET /admin/ops/dashboard?days=1..90` | 已存在，需审计读取能力 | 数据看板主数据源；字段不足时显示 unavailable |
| admin reviews | `GET/PATCH /admin/reviews*` | 已存在，需审计读取能力 | 只在设计稿已有入口/批准入口中接入低置信度审核；不添加 mock 审核结果 |
| admin sync | `POST/GET /admin/sync/sources`、`POST /admin/sync/sources/{source_id}/run` | 已存在，MVP 记录同步游标 | 作为同步源能力；不冒充应用安装；同步内容和状态以响应为准 |
| admin backup | `POST /admin/backup` | 已存在，平台管理员 | 只在批准的运营入口触发；二次确认、loading、失败和审计结果必须可见 |

### 3.2 十页使用矩阵

| 设计页 | 真实使用能力 | 必须标为 unavailable/disabled 的设计操作 |
|---|---|---|
| 工作台 | `me`；有权限时可读 `admin ops dashboard` | 无通用 dashboard 聚合、动态、健康度、标签统计端点时，统计卡/图表/动态/健康度为 API unavailable；智能导入无端点则 disabled |
| 知识库 | KB CRUD、文档上传/删除/重试、版本/diff、KB 成员、search | 文件夹 CRUD、收藏/共享/回收站列表、批量动作无端点则 disabled；不伪造右侧概览数值 |
| AI 助手 | conversations/messages/delete、qa SSE/cancel、search、feedback、KB/文档引用 | 联网搜索、深度思考、模型选择、composer 文件上传无对应端点则 disabled；引用只能来自授权 SSE/search |
| 文档中心 | 文档列表/详情/上传/删除/重试、版本/diff、search | 批量删除无批量端点则 disabled；服务端不支持的过滤字段不能伪造请求结果 |
| 团队与权限 | `me`、KB 成员、`admin/users` 邀请、必要时 `admin/tenants` | 用户列表、角色 CRUD、权限矩阵写入、停用/重置密码无端点则 disabled；邀请成功必须以真实响应为准 |
| 数据看板 | `admin/ops/dashboard`；有批准入口时 audit/reviews/sync/backup | 非 ops 响应字段、任意粒度日期、图表明细无端点则 unavailable；不使用设计稿静态数字 |
| 应用中心 | 当前无应用目录能力；同步源只能由 admin sync adapter 处理 | 安装/连接/卸载/推荐目录/已安装状态全部 disabled；不得把 sync run 当安装成功 |
| 回收站 | 当前没有回收站端点；删除动作仍归属 KB/文档页 | 回收列表、还原、永久删除、清空全部 disabled/unavailable |
| 个人中心 | `me`、logout、登录/刷新会话 | 资料编辑、密码/安全、通知、偏好、API 管理和保存无端点则 disabled；不得把 localStorage 伪装成持久化成功 |
| 模块地图 | 路由跳转和能力/权限状态读取 | 模块描述中的未实现业务操作只做视觉说明；不能显示假安装、同步或删除成功 |

## 4. 新前端架构与依赖边界

### 4.1 并行目录

新前端建议使用以下独立目录（如实现时采用等价目录，必须在 M0 记录明确映射，不得回到旧目录）：

```text
apps/web/src/app-v2/
├── AppV2.tsx                 # 新入口组件，M7 前不接管旧 App
├── routes.ts                 # 十页 route id、守卫和 404/unavailable 状态
├── pages/                    # Dashboard/Knowledge/Assistant/.../ModuleMap
├── components/               # 纯 UI：table/card/tab/form/state/citation 等
├── layouts/                  # GlobalShell、DedicatedHeader、三栏工作区等
├── adapters/                 # 唯一可访问 lib/api 的业务适配层
│   ├── auth.ts
│   ├── knowledge.ts
│   ├── documents.ts
│   ├── conversations.ts
│   ├── qaStream.ts
│   ├── search.ts
│   └── admin.ts
├── styles/
│   ├── tokens.css
│   ├── globals.css
│   └── components.css
├── types/                    # v2 view model；可引用非 UI API types
└── fixtures/visual/          # 仅视觉占位，生产构建不得作为业务结果来源
```

### 4.2 强制依赖规则

1. 在 app-v2 生产代码中，只有 `src/app-v2/adapters/**` 可以 import `src/lib/api.ts` 和 `src/types/api.ts`；页面、组件、布局、styles 和 fixtures 不得直接 import `lib/api`、`types/api` 或调用 `fetch`。`src/lib/api.ts` 与 `src/types/api.ts` 是保留的非 UI 后端能力层，不属于旧 UI。
2. `adapters` 可以复用 `src/types/api.ts` 和 API 契约类型，但不得 import `src/pages/**`、`src/components/**`、`src/styles.css`、`src/styles.css.bak` 或旧布局。
3. `pages` 只依赖 v2 layouts/components、v2 view model、v2 adapters facade；页面不得知道 fetch、Bearer header、SSE 分帧或后端错误映射细节。
4. `components` 保持纯组合，业务数据和状态由页面/adapter 注入；每个可交互控件必须有 loading、empty、error、permission 或 disabled 语义。
5. `styles/tokens` 是新视觉系统唯一 token 来源；不得通过旧 class 兼容、旧 CSS import 或全局覆盖实现“换皮”。
6. 新路由覆盖十页；旧 `useHashPage` 不作为新路由器。M7 切换入口前，新目录可以与旧 `App` 并存但不得生产可见地混用。

### 4.3 会话与安全数据边界

- 新前端只从真实 login/refresh/me 获取主体和租户，禁止迁移旧 `DEV_MOCK_SESSION`、`dev-mock-token`、静态用户或静态租户。
- 切换到 v2 时清理旧 session 命名空间，不能因为旧 localStorage 恢复 mock 身份；真实 Token 的存储/刷新按项目安全与认证契约执行，不在 v2 新增旁路。
- SSE adapter 必须保留 v2 envelope、`turn_id`、`seq`、heartbeat、done 唯一性、取消和旧流隔离；页面只接收已授权、已校验的 view model。
- 上传必须显示真实 `202 Accepted`、任务状态和失败原因；检索、引用、版本、反馈和审计数据必须绑定当前租户与主体授权。

## 5. 数据策略与状态模型

### 5.1 真实数据优先

设计稿中的 mock 数字、姓名、文档名、趋势点和应用名只用于视觉占位，不能作为生产状态。实现顺序是 API adapter → loading skeleton → 真实响应映射 → empty/error/permission/disabled；任何没有端点的操作都不得用 `setTimeout`、本地数组、假 toast 或静态计数器冒充成功。

允许 `fixtures/visual/` 存放同视口视觉对照所需的脱敏结构样本，但必须满足：只由显式视觉检查入口加载；不创建 Token/session；不参与生产业务提交；生产构建不能依赖其作为真实数据；最终 QA 同时覆盖 API 可用和 API 不可用两种状态。

### 5.2 每个数据区的最低状态

| 状态 | 统一要求 |
|---|---|
| loading | 显示与设计稿密度一致的 skeleton/进度，不突然跳布局；上传和 SSE 显示阶段 |
| empty | 说明“没有数据”的范围、原因和下一步真实可用动作；没有动作时显示 unavailable |
| error | 显示用户可理解错误、request/trace id（若可见）和重试；不吞掉权限或上游错误 |
| permission | 明确“当前主体无权访问”，不泄露资源是否存在；隐藏或禁用写操作 |
| disabled/unavailable | 标注缺少后端端点或能力，不触发假请求，不显示成功 toast |
| success | 只在真实响应或真实 SSE 终态后呈现，写操作必须刷新真实数据或显示服务端状态 |

## 6. M0-M7 实施里程碑

每个里程碑独立验收；没有达到退出条件不得把工作标记为完成。允许文件范围是该里程碑可写入的最小边界，未列文件不得“顺手修改”。

### 执行台账/里程碑状态

以下台账严格区分已经验证的证据与延后/风险项；未执行的浏览器 QA 不记为通过。router diff SHA 按 `git diff --no-ext-diff --binary -- <paths> | shasum -a 256` 计算，并在后续里程碑完成时重跑后逐字或哈希比对。

| 里程碑 | 状态 | 已验证证据 | 延后/风险/未决项 |
|---|---|---|---|
| M0 | 已完成 | 视觉基线、十页路由与 app-v2 边界已记录；构建、静态扫描、真实浏览器关键流程已通过 | 无未决的 M0 阻塞项 |
| M1 | 已完成 | 设计系统、全局壳、登录/刷新/退出和会话守卫已完成；构建、静态扫描、真实浏览器关键流程已通过 | 无未决的 M1 阻塞项 |
| M2 | 已完成 | 工作台、模块地图和对应路由已完成；构建、静态扫描、真实浏览器关键流程已通过 | 无未决的 M2 阻塞项 |
| M3 | 已完成 | 知识库、文档中心及既有 KB/文档 client 能力已完成；构建、静态扫描、真实浏览器关键流程已通过 | 无未决的 M3 阻塞项 |
| M4 | 已完成代码与契约门禁 | M4 代码、build、M4/M3 contract test 已通过；完成时记录的相关 router diff SHA：`86c5edd16810ce9b58bf0d1878786b69c340594313269eb6779b49c70616ba37` | 桌面/窄屏浏览器 QA 延后到 M7 总门禁，未写成已通过。后端 pytest 结果为 `34 passed / 12 failed`；失败属于既有 v1/v2/fixture/provider 环境问题，前端不阻塞，未改后端 |
| M5-M7 | pending | 暂无完成证据 | M5 开始前 admin+me router diff SHA 已记录为 `760919aeb88df56411aa75053150451436180e2f342e58fec52588ecb1f1600e`；M5/M6 完成后必须与起始 SHA 逐字/哈希比对，M7 浏览器总门禁仍待执行 |

### M0：视觉基线、路由清单、新架构骨架

**目标**：冻结设计稿十页、路由 ID、差异分级、依赖边界和 app-v2 空骨架。

**允许文件范围**：`apps/web/src/app-v2/**`（只建骨架、route manifest、状态类型、空 adapter facade）；`docs/design-qa.md` 或等价记录文件（如项目已有约定）；不改 `src/App.tsx`、旧 pages/components/styles、后端或配置。

**功能验收**：十个规范路由均有 route manifest；每个路由有 loading/empty/error/permission/unavailable 状态占位；app-v2 不 import 旧页面、组件、样式；adapter 边界可由静态扫描证明。

**视觉验收**：记录 `设计稿/index.html` 实际渲染的十页标题、section header、专用布局、tabs/cards/tables/forms/module tiles 和至少一个 desktop/窄屏视口基线；不改设计稿。

**检查命令**：

```bash
cd /Users/alin/EKB/apps/web && npm run build
cd /Users/alin/EKB && rg -n "dashboard|knowledge|assistant|documents|team|analytics|apps|recycle|profile|modules" apps/web/src/app-v2
cd /Users/alin/EKB && rg -n "src/(pages|components|styles\.css)|lib/api" apps/web/src/app-v2
```

**退出条件**：route manifest 与十页清单一一对应；新目录可独立编译或有明确的骨架编译检查；依赖扫描没有旧 UI import；设计基线证据已记录。

### M1：设计系统、全局壳、登录

**目标**：实现新 tokens、全局侧栏/顶栏、专用顶栏基础、登录/刷新/退出和路由守卫。

**允许文件范围**：`apps/web/src/app-v2/styles/**`、`layouts/**`、`components/ui/**`、`pages/LoginPage.tsx`、`adapters/auth.ts`、`routes.ts`、v2 types/tests；不得接管 `App.tsx`，不得 import 旧 CSS/组件。

**功能验收**：真实 login 成功进入 v2；refresh 续期、logout 清理会话、过期回到登录；无 Token 不能访问业务页；无 `DEV_MOCK_SESSION`、静态 Token 或旧 session 恢复行为。

**视觉验收**：新 tokens 与设计稿色板、字号、圆角、阴影、focus、响应式断点一致；全局壳只出现一套；专用顶栏与页面内容边界正确。

**检查命令**：

```bash
cd /Users/alin/EKB/apps/web && npm run build
cd /Users/alin/EKB && rg -n "DEV_MOCK_SESSION|dev-mock-token|mock-refresh|src/styles\.css|src/components|src/pages" apps/web/src/app-v2
```

**退出条件**：登录/刷新/退出的 API、错误和权限测试通过；未登录/过期/无权限状态可见；v2 壳在设计稿同视口无 P0/P1/P2。

### M2：工作台、模块地图

**目标**：从零实现完整工作台和模块地图十页中的两页，不复用旧 `DashboardPage` 或旧布局。

**允许文件范围**：`apps/web/src/app-v2/pages/DashboardPage.tsx`、`ModuleMapPage.tsx`、相关 v2 components/layouts/styles、`adapters/me.ts` 和经批准的 dashboard adapter facade；不改旧 page/component/style。

**功能验收**：`#/dashboard` 与 `#/modules` 可访问；快捷操作仅调用已有真实 KB/doc 路由；模块 tile 可跳转十页；无 dashboard 聚合端点的数据区显示 unavailable/loading/empty，不显示 mock 成功结果。

**视觉验收**：工作台逐项对应问候、快捷操作、五卡、趋势、分布、动态、最近访问、热门标签、健康度和 footer；模块地图逐项对应 `MODULE MAP / 01 - 09` 与九个 tile；同视口不改变 section 顺序或布局层级。

**检查命令**：

```bash
cd /Users/alin/EKB/apps/web && npm run build
cd /Users/alin/EKB && rg -n "DashboardPage|ModuleMap|MODULE MAP|知识库模块分布" apps/web/src/app-v2
```

**退出条件**：两页路由、权限状态和视觉对照证据齐全；API 缺口已在页面上显式呈现；旧 `DashboardPage` 无生产 import。

### M3：知识库、文档中心及 KB/文档 API

**目标**：实现设计稿知识库三栏关系和文档中心表格，接通 KB CRUD、文档上传/删除/重试、版本/diff、成员和 search 能力。

**允许文件范围**：`apps/web/src/app-v2/pages/KnowledgePage.tsx`、`DocumentsPage.tsx`、相关 v2 layouts/components、`adapters/knowledge.ts`、`adapters/documents.ts`、`adapters/search.ts`、`apps/web/src/lib/api.ts`、`apps/web/src/types/api.ts`、对应测试；不改后端和旧 `KbPage`/`SearchPage`/`DocVersionPanel`。

其中 `lib/api.ts` 与 `types/api.ts` 只能作为保留的非 UI 后端能力层按需扩展，且仅限 M3 已存在契约所需的最小范围：补齐 `GET/PATCH /kb/{kb_id}`、`GET /kb/{kb_id}/docs/{doc_id}`、`POST /kb/{kb_id}/docs/{doc_id}/retry`、`GET/POST /kb/{kb_id}/members` 和 `DELETE /kb/{kb_id}/members/{user_id}` 的客户端方法/类型；让上传客户端携带既有 `Idempotency-Key` 并返回服务端已有的 `doc_id`、`job_id`、`status`、`trace_id`，保留既有错误响应的 `code`、`message`、`request_id`、`details` 及上传错误元数据。不得借此增加后端端点、改请求路径或服务端契约，也不得让页面、组件、布局直接 import `lib/api` 或调用 `fetch`。

**功能验收**：真实 KB 列表/创建/详情/编辑/删除；真实文档列表/上传/状态/删除/失败重试；版本列表和 diff 只在权限允许时加载；KB 成员管理按服务端响应；搜索结果不泄露未授权 KB；上传使用幂等键并正确处理 `202/409/413/415/429`。

**视觉验收**：知识库为专用顶栏 + 左知识空间导航 + 中文件表 + 右概览；文档中心为 section header + 搜索/筛选/操作 + 表格 + 分页；空库、空文档、无结果、处理中、失败、无权限、缺端点状态均符合设计系统。

**检查命令**：

```bash
cd /Users/alin/EKB/apps/web && npm run build
cd /Users/alin/EKB/apps/api && pytest -q tests/test_api.py tests/test_tenant_isolation.py tests/test_security_regression.py
cd /Users/alin/EKB && rg -n "fetch\(|lib/api|KbPage|SearchPage|styles\.css" apps/web/src/app-v2
cd /Users/alin/EKB && rg -n "lib/api|types/api|getKnowledgeBase|updateKnowledgeBase|getDocument|retryDocument|listKbMembers|addKbMember|removeKbMember|uploadDocument" apps/web/src/app-v2/adapters
cd /Users/alin/EKB && ! rg -n "lib/api|fetch\(" apps/web/src/app-v2/pages apps/web/src/app-v2/components apps/web/src/app-v2/layouts
cd /Users/alin/EKB && git diff -- apps/api/ekb_api/routers/kb.py
```

前两条新增静态证据必须能证明：M3 新增的 `lib/api` 方法由 app-v2 adapters 消费，页面/组件/布局没有 `lib/api` 或 `fetch`；最后一条应在 M3 开始前记录 `apps/api/ekb_api/routers/kb.py` 的既有 `git diff`，M3 完成后重跑并逐字比对，证明没有新增 router 改动；干净 checkout 时该命令应无输出。若方法采用等价命名，检查命令必须同步改为实际方法名并保留同等证据强度。

**退出条件**：KB/文档关键流程和负向权限/失败用例通过；版本/diff 有证据；十页中知识库与文档中心同视口无 P0/P1/P2；无后端支持的 folder/favorite/recycle/bulk 操作全部明确 disabled；非 UI API 客户端的新增方法/类型仅覆盖本节既有端点，并由 adapters 消费。

### M4：AI 助手、会话、SSE、引用、反馈

**目标**：从零实现 AI 助手三栏工作区，接通会话、消息、SSE v2、取消、引用、搜索和反馈。

**允许文件范围**：`apps/web/src/app-v2/pages/AssistantPage.tsx`、相关 v2 chat/citation/composer components/layouts、`adapters/conversations.ts`、`adapters/qaStream.ts`、`adapters/search.ts`、`adapters/feedback.ts`、对应测试；不改旧 `QAPage`、`ChatPanel`、`ConversationHistory` 或旧 CSS。

**功能验收**：会话列表/消息加载/新会话/删除或归档；真实 SSE v2 的 `request → retrieval_* → generation_started → content_delta/citations → done`；重复/倒序/旧 `turn_id` 事件被丢弃并记录协议错误；首 token 前和部分输出后 cancel 都能收敛；错误、拒答、超时、heartbeat、引用和反馈真实可见且不伪造。

**视觉验收**：左会话、中聊天/引用答案/composer、右知识库上下文/文档预览严格符合设计稿；流式阶段、发送中、停止、重试、空会话、无知识库、无引用和无权限不改变核心三栏层级。

**检查命令**：

```bash
cd /Users/alin/EKB/apps/web && npm run build
cd /Users/alin/EKB/apps/api && pytest -q tests/test_api.py tests/test_fault_injection.py tests/test_tenant_isolation.py
cd /Users/alin/EKB && rg -n "turn_id|seq|heartbeat|cancel|citation|feedback" apps/web/src/app-v2
```

**退出条件**：SSE 契约、取消、旧流隔离、引用授权和反馈测试证据齐全；AI 助手同视口无 P0/P1/P2；无联网搜索/深度思考/模型选择等端点的控件保持 disabled。

### M5：团队权限、个人中心及 me/members/admin

**目标**：实现团队与权限、个人中心两页，使用 `me`、KB members、admin users invite、admin tenants，并覆盖权限状态；如 `ApiClient` 尚未覆盖，只为这些既有端点补齐最小 client methods/types，不改变服务端 API 契约。

**允许文件范围**：`apps/web/src/app-v2/pages/TeamPage.tsx`、`ProfilePage.tsx`、相关 v2 components/forms、`adapters/me.ts`、`adapters/members.ts`、`adapters/admin.ts`、`apps/web/src/lib/api.ts`、`apps/web/src/types/api.ts`、对应测试；不改后端、旧 UI 和旧会话存储实现。

M5 对 `lib/api.ts` / `types/api.ts` 的增量只能是当前服务端已存在但 `ApiClient` 尚未覆盖的最小 client methods/types：`POST /admin/users` 使用既有 `UserInvite` 的 `email`、`name`、`password`、`role` 字段并返回既有 `UserInviteResponse` 的 `id`、`email`、`name`、`role`、`tenant_id`；`GET /admin/tenants` 返回既有 `TenantResponse` 列表；`POST /admin/tenants` 使用既有 `TenantCreate` 的 `name`、`owner_email`、`owner_name`、`owner_password`、`model_routing_key`、`egress_policy`、`quota_daily_qa`、`quota_storage_docs`、`quota_storage_bytes_per_file` 字段并返回既有 `TenantResponse` 的 `id`、`name`、`role`、`model_routing_key`、`egress_policy`、`quota_daily_qa`、`quota_storage_docs`、`quota_storage_bytes_per_file`。`GET /me` 已存在且只复用，不重复增加 client 方法/类型；不得增加用户列表、角色 CRUD、资料更新、安全/通知/偏好或 API key 端点。页面、组件、布局不得直接 import `lib/api`、`types/api` 或调用 `fetch`，只能由 app-v2 adapters 消费这些扩展。

**功能验收**：真实主体/租户/能力显示；有权限时列出和增删 KB 成员；邀请成员调用既有 `POST /admin/users` 并按真实响应展示；平台管理员按既有 `GET/POST /admin/tenants` 读取/创建租户；`GET /me` 不重复扩展；角色、权限矩阵、用户列表、资料/安全/通知/偏好/API 管理缺少端点时显式 disabled；越权响应不泄露资源；上述 client 扩展不新增或改变任何服务端契约。

**视觉验收**：团队页 section header、导出/邀请、三 tabs、搜索/筛选、成员表格完整；个人中心资料 card、五 tabs、表单和快捷入口完整；禁用控件保留设计稿布局但有清晰原因。

**检查命令**：

```bash
cd /Users/alin/EKB/apps/web && npm run build
cd /Users/alin/EKB/apps/api && pytest -q tests/test_api.py tests/test_tenant_isolation.py tests/test_security_regression.py
cd /Users/alin/EKB && rg -n "me|members|admin/users|admin/tenants|permission|disabled|unavailable" apps/web/src/app-v2
cd /Users/alin/EKB && rg -n "lib/api|types/api" apps/web/src/app-v2/adapters
cd /Users/alin/EKB && ! rg -n "lib/api|types/api|fetch\(" apps/web/src/app-v2/pages apps/web/src/app-v2/components apps/web/src/app-v2/layouts
cd /Users/alin/EKB && git diff --no-ext-diff --binary -- apps/api/ekb_api/routers/admin.py apps/api/ekb_api/routers/me.py | shasum -a 256
cd /Users/alin/EKB && test "$(git diff --no-ext-diff --binary -- apps/api/ekb_api/routers/admin.py apps/api/ekb_api/routers/me.py | shasum -a 256 | awk '{print $1}')" = "760919aeb88df56411aa75053150451436180e2f342e58fec52588ecb1f1600e"
```

静态检查必须证明 M5 新增的 `lib/api` 方法/`types/api` 只由 app-v2 adapters 消费，页面/组件/布局没有 `lib/api`、`types/api` 或 `fetch`；router diff 必须在 M5 开始前记录为 `760919aeb88df56411aa75053150451436180e2f342e58fec52588ecb1f1600e`，完成后重跑并逐字或哈希比对。若方法采用等价命名，adapter 检查必须同步改为实际方法名并保留同等证据强度。

**退出条件**：主体、租户、成员和邀请关键流程通过；无权限、无端点和失败状态通过；团队与个人中心同视口无 P0/P1/P2；非 UI client 新增方法/类型仅覆盖本节列出的现有 `admin/users`、`admin/tenants` 能力并由 adapters 消费；`GET /me` 不重复扩展；router diff 与 M5 起始 SHA 一致；未新增或改变后端 API 契约。

### M6：数据看板、应用中心、回收站及 ops/sync/audit 能力

**目标**：实现剩余三页并接入已有 ops、audit、reviews、sync、backup adapter；如 `ApiClient` 尚未覆盖，只为列明的既有治理/运营端点补齐最小 client methods/types，不为应用中心/回收站虚构缺失端点或改变服务端 API 契约。

**允许文件范围**：`apps/web/src/app-v2/pages/AnalyticsPage.tsx`、`AppsPage.tsx`、`RecyclePage.tsx`、相关 v2 cards/tables/module tiles、`adapters/admin.ts`、`apps/web/src/lib/api.ts`、`apps/web/src/types/api.ts`、对应测试；不改后端和旧 OpsPage/CSS。

M6 对 `lib/api.ts` / `types/api.ts` 的增量只能是当前服务端已存在端点的最小封装/类型：`GET /admin/ops/dashboard?days=`、`GET /admin/audit`、`GET /admin/reviews`、`GET /admin/reviews/{item_id}`、`PATCH /admin/reviews/{item_id}`、`GET/POST /admin/sync/sources`、`POST /admin/sync/sources/{source_id}/run` 和 `POST /admin/backup`，复用当前 router/schema 的字段、查询参数、状态码和权限语义。它们是治理/运营能力，不等于应用安装、连接、卸载或应用目录，也不等于回收站恢复、永久删除或清空；页面层只能呈现设计稿已有结构，adapter 可以完整覆盖这些现有契约，但不得新增脱离设计稿的运营导航。

**功能验收**：有权限时按 `days=1..90` 真实加载 `/admin/ops/dashboard?days=`；`/admin/audit`、`/admin/reviews*`、`/admin/sync/sources*` 和 `/admin/backup` 按既有权限、字段、状态码和错误响应完成 adapter 鉴权与 loading/error/audit 状态，backup 有二次确认；应用安装/连接与回收站还原/永久删除/清空全部明确 unavailable/disabled；同步不显示为应用安装，治理/运营能力不显示为回收站恢复；上述 client 扩展不新增或改变任何服务端契约。

**视觉验收**：数据看板保持日期控件、四卡、趋势和分布；应用中心保持搜索、tabs、应用 tiles；回收站保持保留期提示、清空、表格、还原/永久删除；不得加不在设计稿中的侧栏或运营导航。

**检查命令**：

```bash
cd /Users/alin/EKB/apps/web && npm run build
cd /Users/alin/EKB/apps/api && pytest -q tests/test_ops_governance.py tests/test_backup.py tests/test_tenant_isolation.py
cd /Users/alin/EKB && rg -n "ops/dashboard|admin/audit|admin/reviews|admin/sync|admin/backup|unavailable|disabled" apps/web/src/app-v2
cd /Users/alin/EKB && rg -n "lib/api|types/api" apps/web/src/app-v2/adapters
cd /Users/alin/EKB && ! rg -n "lib/api|types/api|fetch\(" apps/web/src/app-v2/pages apps/web/src/app-v2/components apps/web/src/app-v2/layouts
cd /Users/alin/EKB && git diff --no-ext-diff --binary -- apps/api/ekb_api/routers/admin.py apps/api/ekb_api/routers/me.py | shasum -a 256
cd /Users/alin/EKB && test "$(git diff --no-ext-diff --binary -- apps/api/ekb_api/routers/admin.py apps/api/ekb_api/routers/me.py | shasum -a 256 | awk '{print $1}')" = "760919aeb88df56411aa75053150451436180e2f342e58fec52588ecb1f1600e"
```

静态检查必须证明 M6 新增的 `lib/api` 方法/`types/api` 只由 app-v2 adapters 消费，页面/组件/布局没有 `lib/api`、`types/api` 或 `fetch`；router diff 必须以 M5 开始时记录的 `760919aeb88df56411aa75053150451436180e2f342e58fec52588ecb1f1600e` 为基线，在 M6 开始前和完成后重跑并逐字或哈希比对。若方法采用等价命名，adapter 检查必须同步改为实际方法名并保留同等证据强度。

**退出条件**：数据看板授权/字段缺口处理正确；运营能力有可复核 adapter 和负向证据；非 UI client 新增方法/类型仅覆盖本节列出的现有治理/运营端点并由 adapters 消费；应用中心/回收站没有假成功，且治理/运营能力没有被解释为应用安装或回收站恢复；页面只保留设计稿已有导航结构；三页同视口无 P0/P1/P2；router diff 与基线 SHA 一致；未新增或改变后端 API 契约。

### M7：切换 App、删除旧 UI/组件/CSS、完整 QA

**目标**：完成十页入口切换、旧 UI 无引用审计、生产构建、浏览器回归和视觉 QA。

**允许文件范围**：`apps/web/src/App.tsx`、`apps/web/src/main.tsx`、必要的 v2 route/entry 文件；经 import 审计确认无引用后，才允许删除旧 `src/pages/**`、旧 `src/components/**`、旧 `src/styles.css` / `styles.css.bak` 中的 UI 文件。允许更新 `design-qa.md` 和本重建文档的执行证据；不得删除 API/types/认证/SSE/上传/检索非 UI 能力。

**功能验收**：生产入口只渲染 v2；十个路由可访问；登录/刷新/退出、KB/文档、会话/SSE/cancel/引用/反馈、me/members/invite、ops/admin 能力按权限通过；缺失端点均是 disabled/unavailable；旧 UI 删除前后的 import 审计均有证据。

**视觉验收**：十页逐一在设计稿相同视口对照；P0/P1/P2 均为 0；加载、空、错误、权限、禁用、窄屏主操作不遮挡；最终 `design-qa.md` 写明 `Final result: PASSED`。

**检查命令**：

```bash
cd /Users/alin/EKB/apps/web && npm run build
cd /Users/alin/EKB && git diff --check -- docs
cd /Users/alin/EKB && rg -n "from ['\"].*src/(pages|components)|from ['\"].*styles\.css|import ['\"].*styles\.css" apps/web/src --glob '*.{ts,tsx,css}'
cd /Users/alin/EKB && rg -n "DEV_MOCK_SESSION|dev-mock-token|mock-refresh|ekb\.session" apps/web/src/app-v2 apps/web/src/App.tsx apps/web/src/main.tsx
cd /Users/alin/EKB && rg -n "Final result: PASSED|P0|P1|P2|dashboard|knowledge|assistant|documents|team|analytics|apps|recycle|profile|modules" design-qa.md
```

**退出条件**：满足第 7 节全部量化验收；浏览器控制台无 React runtime error；旧 UI 页面/组件/style 无生产 import；`design-qa.md` final result passed；切换和删除均有可回滚证据。

## 7. 总体验收与证据清单

只有以下全部满足，v2 才可交付：

| 量化门槛 | 通过定义 | 必须保留的证据 |
|---|---|---|
| 10/10 路由可访问 | `#/dashboard`、`#/knowledge`、`#/assistant`、`#/documents`、`#/team`、`#/analytics`、`#/apps`、`#/recycle`、`#/profile`、`#/modules` 均能打开并有页面根节点 | 浏览器快照/截图、路由清单 |
| 10/10 视觉对照 | 十页与 `设计稿/index.html` 同视口对照，P0/P1/P2 均为 0 | `design-qa.md`、桌面和窄屏截图 |
| 关键后端流程 | 现有后端支持的登录、刷新、退出、KB/文档、版本/diff、成员、会话消息删除、SSE/cancel、search、feedback、me、admin 能力按权限通过 | API/E2E/契约测试输出、失败/权限用例 |
| 构建与 diff | `npm run build` 通过；在 Git checkout 可用时 `git diff --check -- docs` 通过 | 命令输出和环境说明 |
| 浏览器稳定性 | 十页回归无 React runtime error；控制台无新增 error | Playwright console 日志 |
| 视觉 QA 结论 | `design-qa.md` 存在且包含 `Final result: PASSED` | 文件路径和最终结论 |
| 旧 UI 清理 | 旧页面、旧组件、旧 style 没有生产 import；删除仅发生在 import 审计之后 | `rg` 审计输出、切换前后 diff |

浏览器检查建议使用本地 HTTP 服务，不使用 `file://`：

```bash
cd /Users/alin/EKB/apps/web && npm run build
cd /Users/alin/EKB && python3 -m http.server 4173 --directory apps/web/dist
```

随后用 Playwright 依次打开十个 hash 路由，在设计稿相同 desktop/窄屏视口记录 snapshot、截图和 console；检查每页根 heading、section header、关键操作、disabled/unavailable 状态和横向溢出。任何 console React runtime error、P0/P1/P2 视觉缺陷或假成功都阻塞发布。

## 8. 风险、回滚与变更纪律

| 风险 | 防护与回滚 |
|---|---|
| 工作区存在用户未提交改动 | 只在 `src/app-v2/**` 并行构建；切换前后查看精确 diff；不使用 `git reset`/`git checkout` 覆盖任何改动 |
| API 缺口 | 现有端点但客户端未覆盖时，M3、M5、M6 只能在各自允许范围内扩展保留的 `lib/api.ts` / `types/api.ts`，且只能由 app-v2 adapters 消费；M5 仅限 `admin/users`、`admin/tenants`，M6 仅限 `ops/dashboard`、`audit`、`reviews`、`sync/sources`、`backup`；其他能力在矩阵标记 unavailable/disabled；这些最小 client 扩展不新增或改变服务端 API 契约，不改后端迎合设计稿；如必须新增端点，先回 Sol 做架构/API 决策并更新契约 |
| M5/M6 client 范围或语义漂移 | 通过 M5/M6 开始/完成的 admin+me router diff SHA 做逐字/哈希比对；页面、组件、布局禁止直接 import `lib/api`、`types/api` 或调用 `fetch`；同步源只表示治理/运营同步，不能冒充应用安装，治理/运营也不能冒充回收站恢复；发现漂移即停止该里程碑并回报 Sol |
| 设计稿是静态数据 | 只把 mock 用作视觉占位；生产模块优先真实 API；无端点显示真实缺口状态 |
| 旧 UI 删除误伤共享能力 | M7 先做 import 审计，再按文件删除；保留 `lib/api`、types、认证、SSE、上传、检索和测试；发现引用立即停止删除 |
| 切换入口导致回归 | M0-M6 保持旧入口可回退；M7 先切换、跑完整 QA、再删除旧 UI；回滚只允许把入口恢复到切换前 commit/工作区状态，不能覆盖其他用户改动 |
| 会话或权限泄露 | 不迁移 mock session；切换清理旧 session 命名空间；所有 adapter 绑定当前 Token、tenant、subject；SSE 只接受当前 turn |
| 视觉验收被“能跑”掩盖 | 以同视口截图和 `design-qa.md` 为硬门禁；P0/P1/P2 任一未清零不得进入删除旧 UI 阶段 |

## 9. 正式目标文本

以下是一段可直接传给 `create_goal` 的单一、可测量 objective；不设置 token budget：

```text
在 /Users/alin/EKB 内按《EKB 全前端重建实施文档 v2.0》完成一次不复用旧 UI 的 EKB 前端从零重建：M0 冻结设计稿 index.html 实际渲染的十页、十个路由和 app-v2 架构边界；M1 完成新设计系统、全局壳、登录/刷新/退出和无 mock session 的会话守卫；M2 完成工作台与模块地图；M3 完成知识库与文档中心并接通真实 KB CRUD、文档上传/删除/重试、版本/diff、成员和 search，同时仅按既有端点契约最小扩展保留的非 UI `apps/web/src/lib/api.ts` 与 `apps/web/src/types/api.ts`，由 app-v2 adapters 消费；M4 完成 AI 助手并接通真实 conversations/messages、SSE v2、turn_id/seq/引用/cancel/feedback；M5 完成团队与权限、个人中心并接通既有 `GET /me`、members、`POST /admin/users`、`GET/POST /admin/tenants`，仅允许为这些已存在端点补齐最小 client methods/types，`GET /me` 不重复扩展，且不改变服务端 API 契约；M6 完成数据看板、应用中心、回收站并接通既有 `ops/dashboard`、`audit`、`reviews`、`sync/sources`、`backup`，仅允许为这些已存在治理/运营端点补齐最小 client methods/types，页面只呈现设计稿已有结构，不把治理/运营能力解释为应用安装或回收站恢复，且不改变服务端 API 契约；所有无后端端点的设计操作明确呈现 unavailable/disabled；M7 在完整浏览器 QA 后切换 App 入口，先通过 import 审计再删除无引用旧 QAPage/KbPage/SearchPage/OpsPage、旧 UI 组件、旧 CSS 和旧布局。最终必须提供可复核证据：10/10 路由可访问，10/10 与设计稿同视口对照 P0/P1/P2 均为 0，现有后端支持的关键流程和权限/错误/SSE 负向用例通过，`cd apps/web && npm run build` 通过，Git checkout 可用时 `git diff --check -- docs` 通过，十页浏览器控制台无 React runtime error，`design-qa.md` 包含 `Final result: PASSED`，并由 `rg` 证明旧 UI 页面/组件/style 无生产 import；全程不得修改后端、数据库、API 契约或覆盖工作区已有改动；M3、M5、M6 的 `lib/api.ts` / `types/api.ts` 扩展仅补齐对应的既有端点能力，由 app-v2 adapters 消费，不改变任何服务端端点或 API 契约。
```
