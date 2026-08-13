---
title: EKB 设计稿十页全栈闭环 v3 需求
date: 2026-08-09
status: 已批准
tier: 3
type: feature
feature: ekb-fullstack-v3
authoritative: true
owner: Sol
version: 3.0
purpose: 定义设计稿十页真实前后端闭环的用户目标、旅程、FR/NFR 与最终交付边界
companion_docs: [./02_spec.md, ./03_plan.md, ./04_verification-matrix.md, ./README.md]
open_issues: []
review_stage: Sol final gate passed
approval: user authorized docs-first fullstack execution direction; Sol approved technical/security/QA document gate on 2026-08-10; implementation/browser/migration evidence remains pending. Do not claim the user reviewed final wording.
supersedes: ../../../EKB全前端重建实施文档_v2.0_2026-08-09.md
---

# EKB 设计稿十页全栈闭环 v3 需求

> 这是“设计稿十页真实前后端闭环”的 authoritative 需求基线。视觉真值仍为 [`设计稿/index.html`](../../../../设计稿/index.html) 及其实际渲染；本文件只定义用户可见目标与验收边界，技术合同见 [`02_spec.md`](./02_spec.md)，执行顺序见 [`03_plan.md`](./03_plan.md)。

## 1. 范围与现状

EKB 当前已有 FastAPI + SQLAlchemy 模块化单体、SQLite 开发库/PostgreSQL 生产库、Bearer 认证、租户与 KB ACL、文档入库、检索、会话、SSE v2、反馈、运营、审计、同步和备份能力。app-v2 已完成 M0–M5 的新入口骨架、十页 route manifest、认证、知识库/文档、助手、团队和个人中心的既有端点接入；M6 页面仍有真实后端能力缺口。

设计稿的文件夹、收藏、分享、回收站、用户管理、角色管理、资料/偏好/API key、助手项目/附件/模型/联网搜索、完整分析聚合和应用安装等操作不能停留在 disabled、unavailable、静态数字或本地状态。v3 把这些能力升级为可直接排期的全栈产品特性，并保留现有端点兼容性。

本需求还锁定一次只读审计得到的兼容基线：当前 `/me` 使用 store 的 first/dev user/tenant，access token 的 `policy_version` 固定为 1 且 AuthContext 信任 token capabilities；QA turn 的读取、取消、取消检查、seq 和终态更新缺少 actor+tenant 全链路作用域；`/admin/ops/dashboard` 接收 `days` 但未真正过滤日期；后端 `DocumentResponse` 没有可靠 `file_size/object_ref`，KB `tags` 尚未持久化，旧文档幂等无唯一约束且 SQLite quota 存在并发风险。身份、内容、QA、分析和迁移阶段必须先清除这些基线债，再允许阶段灰度。

## 2. 问题与用户

### 2.1 问题

企业用户可以看到一套完整的知识工作台，但当前部分核心操作只有视觉入口或真实端点的只读子集。用户无法从设计稿页面完成“管理成员并立即看到权限生效”“整理文档并从回收站恢复”“在助手中使用授权附件和项目”“查看可信的访问分析”“安装并配置企业应用”等任务，导致页面与实际能力不一致，管理员也无法审计关键写入。

### 2.2 主要用户

| 用户 | 任务上下文 | 成功结果 |
|---|---|---|
| 租户 Owner/Admin | 管理成员、角色、应用、回收站和运营指标 | 变更持久化、立即按新权限生效、可追溯 |
| 知识管理员/编辑者 | 管理 KB、文件夹、标签、分享和批量文档 | 组织结构和文档状态真实可见 |
| 普通成员/客户 | 搜索知识、使用助手、收藏和查看被授权内容 | 只看到租户及资源授权范围内的数据 |
| 审计/安全人员 | 查看访问活动、密钥与敏感操作 | 可按租户、主体、时间和资源追溯，敏感值已脱敏 |
| 平台运维人员 | 配置模型路由、联网搜索提供商和灰度开关 | 能安全启停能力，缺少部署提供商时产品如实说明 |

## 3. 目标与非目标

### 3.1 目标

- 十个设计稿页面的每个核心可见操作都能通过真实 API、数据库状态、审计记录和 app-v2 adapter 完成端到端闭环。
- 所有列表具备服务端过滤、排序、分页和权限边界；所有写操作能在刷新或重新进入页面后从服务端恢复。
- 角色、能力、密钥、应用凭据和回收站等敏感领域默认租户隔离、可审计、可回滚。
- 保持已有 `/api/v1` 端点可用；新增合同采用 additive 方式，旧客户端不因新字段或新表失败。
- 以设计稿实际渲染为视觉真值，从零实现新的 app-v2 页面，不复用旧页面、旧组件、旧布局或旧 CSS。

### 3.2 非目标

- 不引入新的微服务、消息队列或独立身份服务，因为现有 FastAPI 单体和 SQLAlchemy store 足以承载本范围；容量证据不足时继续保持模块化边界。
- 不在本范围实现多地域部署、跨租户共享资源、完整 OAuth/OIDC 身份提供商或企业级 SCIM，因为它们会改变外部身份与部署边界。
- 不把第三方应用同步本身当作安装成功；同步是已安装连接的下游运行能力。
- 不把 LLM 供应商不可用或无联网搜索提供商配置伪装成成功；联网搜索可以在部署缺少提供商时显示有原因的 disabled，但其他需求范围内的核心操作必须有实现和可验证错误路径，核心操作不能以能力缺失状态冒充完成。
- 不把旧 v2 文档中的“后端禁止修改”继续作为当前 authority；旧文档仅保留历史执行记录并标记为被本 v3 取代。

## 4. 十页用户旅程与全栈闭环

每个表格行都是用户可观察的“动作 → 服务响应 → 页面结果”闭环。服务错误、权限拒绝、空数据和加载中均为一等状态；成功必须来自真实响应或真实 SSE 终态。

### 4.1 工作台 / Dashboard (`#/dashboard`)

1. 用户进入工作台，页面根据当前租户和能力加载摘要、趋势、分布、动态、最近访问、标签和健康度；服务端按租户过滤后返回数据。
2. 用户选择日期范围或点击最近访问/标签/健康度条目；页面带上资源和时间过滤，服务端返回分页或限定集合，点击对象可跳转到已授权 KB、文档或助手上下文。
3. 用户执行设计稿保持不变的四个快捷操作：`新建文档` 打开匹配设计稿的 note/plaintext dialog 并保存文档，`上传文档` 调用既有上传闭环，`创建知识空间` 调用既有 KB create，`智能导入` 带 import intent 跳转 Apps 并完成真实 install/configure/connect/run/sync 生命周期；不替换为“开始问答”或“模块地图”。
4. 用户无审计权限时看见 permission 状态；无数据时看见空状态；接口失败时保留重试入口并不显示设计稿静态数字。

### 4.2 知识库 / Knowledge Base (`#/knowledge`)

1. 用户创建、编辑或删除 KB；服务端校验租户/能力，写入 KB 和审计，页面重新读取真实 KB。
2. 用户创建、移动、重命名或删除文件夹；服务端维护父子层级和软删除元数据，页面刷新树和当前列表。
3. 用户搜索、筛选、排序、分页文档，批量归档/恢复/打标签/移动；服务端在授权范围执行每项并返回逐项结果，失败项可重试。
4. 用户收藏 KB/文档、添加标签、分享文档或打开概览/健康度；服务端持久化关系与统计，页面展示真实计数和授权成员。
5. 用户在删除后打开回收站并恢复/永久清理；父级仍删除时恢复子级返回明确冲突，不丢失审计记录。

### 4.3 AI 助手 / AI Assistant (`#/assistant`)

1. 用户创建/重命名/归档/收藏会话，把会话加入项目或移出项目；服务端保存关系，列表按租户主体分页返回。
2. 用户从已授权文档或本次上传文档添加附件；服务端验证文档归属和读取权限，问答上下文只使用授权附件。
3. 用户选择租户允许的模型、深度思考和联网搜索；页面显示服务端 capability。没有配置联网搜索提供商时，发送该选项得到明确不可用错误，不生成假结果。
4. 用户发送问题；SSE v2 返回 `turn_id`、递增 `seq`、阶段、增量、引用、心跳和唯一终态。用户在首 token 前或部分输出后取消，服务端按主体/租户幂等处理并保留正确的可见消息/审计状态。
5. 用户提交回答反馈；服务端保存消息、引用和反馈，页面只在真实 message id 和非空理由下显示提交成功。

### 4.4 文档中心 / Document Center (`#/documents`)

1. 用户按名称、类型、状态、标签、空间和时间筛选并排序分页；过滤在服务端执行，响应包含分页游标和当前查询回显。
2. 用户上传、查看详情、查看版本/diff、重试失败文档、批量执行操作；每项写入服务端状态并在列表刷新后可见。
3. 用户删除文档后可从回收站恢复或永久清理；删除、恢复、清理的结果均有审计，清理不删除审计记录。
4. 用户没有权限或查询为空时，页面显示对应原因和下一步，不将缺失字段或静态数量当作结果。

### 4.5 团队与权限 / Team & Permissions (`#/team`)

1. 管理员按搜索、状态、角色、排序和分页查看当前租户用户列表与详情，更新角色/状态后页面重新读取数据库角色与能力。
2. 管理员邀请成员、导出授权范围内用户；邀请结果可在用户列表中出现，导出不泄露密码或密钥。
3. 管理员查看内置与自定义角色、权限并进行 CRUD；每次权限或分配变更递增租户 `policy_version`，已刷新会话获得当前能力。
4. 普通成员只能看到自己的权限摘要或明确的 permission 状态，不能通过 UI 或 API 枚举其他租户成员。

### 4.6 数据看板 / Analytics (`#/analytics`)

1. 管理员选择 1–90 天范围，页面加载真实问答量、成功/拒答/超时、文档活跃度、访问趋势、分布、活动、标签和健康度。
2. 用户点击最近访问或活动资源时，服务端再次授权并跳转；已撤销权限的资源不返回给页面。
3. 查询超出时间范围、分页上限或性能预算时得到可恢复的校验/限流错误；不会通过全表扫描或跨租户聚合掩盖问题。

### 4.7 应用中心 / App Center (`#/apps`)

1. 用户查看准确的六项目录：飞书集成/Feishu、企业微信/WeCom、GitHub、腾讯文档/Tencent Docs、数据看板 Pro/Analytics Pro、权限审计/Audit。
2. 用户按全部、已安装、推荐搜索目录；服务端返回安装状态、租户 capability 和配置状态。
3. 有权限的管理员安装、配置、连接、运行和卸载应用；每个阶段有独立状态、重试入口和审计，不把同步 run 伪装为安装。
4. 凭据只在创建/更新响应中显示一次明文秘密；后续只显示前缀和脱敏状态，服务端使用专用配置密钥加密存储。

### 4.8 回收站 / Recycle Bin (`#/recycle`)

1. 用户按 KB、文档、文件夹类型和删除时间查看当前租户回收项，服务端返回剩余保留天数和父子关系。
2. 管理员恢复或永久清理单项；恢复时父级被删除返回 `PARENT_DELETED`，永久清理只清理业务对象元数据/内容引用并保留审计。
3. 管理员清空回收站；服务端按 30 天保留规则和权限执行，返回逐项结果与失败原因。

### 4.9 个人中心 / Settings (`#/profile`)

1. 用户读取和更新资料、密码、安全设置、通知和偏好；成功后再次进入页面仍能读取服务端状态。
2. 用户创建、列出、撤销 API key；创建时显示一次完整 key，列表只显示 prefix，撤销后立即不可认证。
3. 用户在无权限、校验失败或密码不匹配时看到恢复动作，敏感字段不进入错误消息、页面 state、审计 metadata 或日志。
4. Profile menu 的个人中心入口、通知入口和帮助/反馈入口均可从新 UI shell 到达；通知列表/已读状态与帮助反馈提交都必须来自服务端。

### 4.10 模块地图 / Module Map (`#/modules`)

1. 用户从模块地图进入九个业务模块；route manifest 只驱动导航，不生成业务成功结果。
2. 每个模块进入后读取真实权限和数据状态；模块的核心操作均回到对应九页闭环。
3. 未授权或部署能力缺失时显示有原因的状态；不会在地图上冒充安装、同步、删除或恢复成功。

## 5. 功能需求（FR）

### 5.1 身份、团队与个人中心

- **FR-001**：服务端必须提供当前租户作用域内的用户列表，支持 `query`、角色、状态、排序、游标和 `page_size`，不得返回其他租户用户。
- **FR-002**：服务端必须提供用户详情、资料字段更新和 active/suspended 状态更新；详情与更新必须按租户和管理能力授权。
- **FR-003**：管理员必须能邀请用户、分配角色和导出当前租户用户；导出必须过滤口令哈希、API key、应用凭据和内部令牌。
- **FR-004**：系统必须支持每租户内置角色与自定义角色/权限 CRUD；内置角色不可修改或删除，自定义角色不能授予超出租户策略的能力。
- **FR-005**：角色、权限或用户角色分配变更必须递增 `policy_version`；刷新会话和 `GET /me` 必须从当前数据库角色解析 capabilities，不信任旧 token 声明。
- **FR-006**：用户必须能更新个人资料、修改密码、读取/更新通知设置和偏好，查看自己的持久化设备会话并撤销单个或全部会话；密码更新必须验证旧密码、按既定策略撤销会话并要求重新认证。
- **FR-007**：用户必须能创建、列出和撤销 API key；明文只在创建响应出现一次，持久化只保存 prefix、hash、状态和生命周期元数据，认证按 prefix 查询后恒定时间校验 hash 并解析实时角色能力。

### 5.2 知识库、文档与治理

- **FR-008**：系统必须支持租户隔离的文件夹层级，父级、名称、排序、移动和软删除关系必须持久化并校验环路。
- **FR-009**：知识库和文档列表必须提供服务端过滤、全文/前缀搜索、排序和游标分页；新列表使用统一分页 envelope，旧列表合同继续可用。
- **FR-010**：系统必须提供受授权的批量移动、打标签、收藏、归档/软删除等操作，返回逐项成功/失败结果并保证幂等。
- **FR-011**：KB、文档、会话必须支持通用收藏，唯一键按租户、主体、资源类型和资源 id 约束，删除资源后收藏可清理或呈现失效状态。
- **FR-012**：文档必须支持按资源 ACL 进行分享、撤销和查询；分享权限不得扩大到调用者自身无权访问的租户范围。
- **FR-013**：系统必须支持文档 tags、KB overview 和健康度计算；overview/health 数字必须来自真实存储聚合，并注明时间范围与数据新鲜度。
- **FR-014**：KB、DOCUMENT、FOLDER 必须支持软删除 metadata、审计化 trash 列表、恢复、永久 purge 和 clear；默认保留 30 天，父级删除时恢复子级返回 `PARENT_DELETED`。

### 5.3 助手、项目与流式问答

- **FR-015**：会话必须支持收藏和项目关系；项目支持租户内 CRUD、成员/负责人分配和会话归属变更。
- **FR-016**：助手必须支持添加已授权既有文档和本次上传文档作为附件；附件访问范围必须在检索和生成前验证。
- **FR-017**：模型选项必须由租户路由策略约束；深度思考是服务端声明和可审计的问答选项，不得由前端本地模拟。
- **FR-018**：联网搜索必须由显式 provider 配置和 feature flag 控制；无 provider 时返回 truthful capability disabled/`CAPABILITY_UNAVAILABLE`，不得返回伪造搜索结果。
- **FR-019**：SSE v2 必须兼容现有 `turn_id`、`seq`、envelope、heartbeat、citations、feedback、cancel 和唯一终态；turn get/cancel/is_cancelled/seq/终态都必须按 actor+tenant 作用域，取消有独立 audit；新增模型/思考/联网/附件字段必须 additive，`qa_turns` 不能被当作 replay log。

### 5.4 Dashboard 与分析

- **FR-020**：系统必须提供按租户和日期范围的真实聚合 endpoint，至少包含问答量、结果分布、文档/KB 使用和健康度；兼容的 `GET /admin/ops/dashboard?days=1|90` 也必须真正按日期过滤。
- **FR-021**：系统必须提供日期范围 timeseries、活动/最近访问、标签分布和健康度数据；每个结果携带查询范围或数据更新时间。
- **FR-022**：系统必须持久化有界的 resource-access events，事件至少含租户、主体、资源类型/id、动作、结果、时间和 trace；敏感内容不入事件。
- **FR-023**：分析查询必须按租户/权限过滤，并使用覆盖索引和日期/数量边界；目标查询 p95 ≤ 500ms（每租户 1,000,000 条事件、保留 90 天、50 RPS 的合成基准）。

### 5.5 应用目录与连接

- **FR-024**：应用目录必须且只能包含六个设计稿条目：`feishu`、`wecom`、`github`、`tencent-docs`、`analytics-pro`、`audit`，分别对应飞书集成、企业微信、GitHub、腾讯文档、数据看板 Pro、权限审计。
- **FR-025**：系统必须提供目录搜索、全部/已安装/推荐视图和租户安装状态；推荐排序可配置但不能新增设计稿外的目录条目。
- **FR-026**：有权限管理员必须能对安装进行 install、configure、connect、run、uninstall；每个状态转换有前置条件、幂等键/重复处理和审计结果。
- **FR-027**：应用凭据必须用 `EKB_APP_CREDENTIAL_KEY` 对应的专用 Fernet 密钥加密存储，绝不复用 `EKB_TOKEN_SECRET`；API 响应、日志和审计只保留 prefix/摘要。
- **FR-028**：sync source/run 是安装后的下游数据同步能力；安装不自动宣称同步成功，sync 失败不改变 installation 的连接状态。

### 5.6 页面、兼容性与最终交付

- **FR-029**：Dashboard 页必须消费真实摘要、访问、标签和健康度 adapter；所有核心快捷操作必须触发真实闭环或真实校验/权限错误。
- **FR-030**：Knowledge 页必须消费真实 KB、文件夹、文档、收藏、分享、tags、overview、health 和 trash adapter。
- **FR-031**：Assistant 页必须消费真实会话、项目、附件、模型 capability、SSE v2、cancel、citations 和 feedback adapter。
- **FR-032**：Documents 页必须消费真实服务端搜索/过滤/排序/分页、批量操作、上传、版本、diff、删除和恢复 adapter；`file_size`/`object_ref` 作为 additive optional 字段有明确 null/default/backfill，版本递增和 diff `changed` 语义有唯一约束。
- **FR-033**：Team 与 Profile 页必须消费真实用户/角色/邀请/导出、资料/密码/通知/偏好/API key adapter；不再把核心管理动作留给本地状态。
- **FR-034**：Analytics 页必须消费真实时间范围聚合、timeseries、分布、活动、访问、标签和健康度 adapter。
- **FR-035**：Apps 页必须消费六项目录和安装生命周期 adapter；Recycle 页必须消费 trash list/restore/purge/clear adapter。
- **FR-036**：Module Map 页必须只导航到十页 route manifest，并反映目标页真实权限/数据状态。
- **FR-037**：十页生产实现只能依赖 `apps/web/src/app-v2/adapters/**` facade、app-v2 view model 和在 app-v2 内新建的 design-matching stateless primitives；页面、布局和组件不得直接调用 `fetch` 或导入旧 UI/API 客户端，也不得把旧 UI 组件作为 fallback。
- **FR-038**：生产 cutover 必须在十页全栈验证和旧 UI import audit 通过后执行；只删除无引用旧 UI，保留认证、API、SSE、检索和上传非 UI 能力。
- **FR-039**：所有新 API/DB 变更必须 additive/backward-compatible；现有端点的路径、旧响应必填字段、鉴权语义和错误码不得被破坏。
- **FR-040**：所有写入、权限变化、密钥和应用凭据操作以及高价值资源读取必须形成脱敏 audit；audit 本身的查询也要审计。
- **FR-041**：所有新列表 API 必须使用 `{items,next_cursor,page_size}` envelope，并对 cursor、page_size、sort 白名单和稳定顺序做服务端校验。
- **FR-042**：所有资源访问必须同时检查 tenant、主体 capability 和对象 ACL；不得以客户端传入的 tenant id 或角色作为唯一信任来源。
- **FR-043**：必须提供显式、幂等、可验证的 v3 migration、索引创建、校验和 rollback/forward-rollback 脚本；生产 cutover 前必须有 dry-run 与恢复证据。
- **FR-044**：API key、应用 credential、密码和 token 的明文不得落库、进入普通响应、审计 metadata、日志或截图；错误信息必须使用泛化资源/权限消息。
- **FR-045**：联网搜索、应用连接和模型选项的 UI 状态必须来源于服务端 capability/部署配置；禁止用本地数组、setTimeout、假 toast 或静态计数器伪造成功。
- **FR-046**：v3 发布时，除部署确实没有 provider 的联网搜索外，需求范围内的核心动作不得以 disabled/unavailable 代替实现；缺失配置只能产生可操作的配置/重试/联系管理员路径。
- **FR-047**：所有十页必须同时覆盖 loading、empty、error、permission、success、stale/refresh 和窄屏主操作状态；成功状态必须在 hard reload 后仍可恢复。
- **FR-048**：最终 cutover 必须保留设计稿为视觉真值，并在 desktop 与 390x844 同视口完成 10/10 页面验证，P0/P1/P2、console error、横向溢出和核心流程阻塞均为 0。
- **FR-049**：全局搜索必须提供授权范围内的真实 `POST /search` 查询；服务端按当前主体、租户、KB ACL 和文档 ACL 过滤结果，页面从新 UI shell/搜索入口提交并显示加载、空、错误、权限和结果状态，不使用本地 mock。
- **FR-050**：用户必须能从 Profile menu 读取分页通知并将单条通知标记已读；通知只属于当前主体/租户，列表和已读写入在 hard reload 后保持。
- **FR-051**：新 UI shell 的 profile menu 必须导航到 Profile、通知、帮助/反馈和退出/会话控制；导航不绕过 app-v2 adapter，未授权入口显示真实权限状态而非 inert click。
- **FR-052**：用户必须能从帮助入口提交带页面、类别、描述和 request id 的反馈；服务端持久化/审计脱敏内容并返回可重试的结果，提交不冒充业务动作成功。

## 6. 非功能需求（NFR）

- **NFR-001 安全/隔离**：任意跨租户读写、资源 ID 猜测、失效角色 token、失效 API key 和越权附件访问必须返回统一授权错误，且不泄露资源存在性。
- **NFR-002 认证**：Bearer 与 API key 均通过服务端认证链；API key prefix 查询后使用恒定时间 hash 校验，成功后解析当前 DB 角色/能力。
- **NFR-003 权限新鲜度**：角色或能力变更后，下一次 refresh/`GET /me` 必须反映新 `policy_version`；缓存失效不能超过一个请求生命周期。
- **NFR-004 性能**：新列表 p95 ≤ 400ms（1 万条租户级数据、20 RPS）；分析聚合 p95 ≤ 500ms；SSE 首事件 p95 ≤ 1s（不含上游模型等待）。
- **NFR-005 容量**：resource-access events 至少支持每租户 1,000,000 条且保留 90 天的索引查询；写入失败不能阻塞核心问答，但必须有降级审计事件。
- **NFR-006 数据一致性**：写入成功响应与随后读取必须在同一事务提交后可见；批量部分失败返回逐项结果，重试不重复创建关系。
- **NFR-007 迁移**：migration 可重复执行、前向升级可中断恢复；SQLite 和 PostgreSQL 都有可执行 verification；生产 rollback 默认采用旧代码忽略新表的兼容回退。
- **NFR-008 密钥治理**：生产没有 `EKB_APP_CREDENTIAL_KEY` 时禁止启用 app credentials；密钥轮换通过版本化 key id 完成，旧密文在过渡窗口可解密。
- **NFR-009 审计**：审计记录保留时间不少于业务合规策略要求，purge 业务对象不得删除审计；查询结果需带 trace/request id。
- **NFR-010 API 兼容**：旧 app-v2 M0–M5 adapters 和既有端点契约测试继续通过；新增 envelope 只用于新增 list endpoints。
- **NFR-011 前端边界**：页面/组件/布局不导入 `src/lib/api.ts`、旧 `src/pages/**`、旧 `src/components/**` 或旧 CSS，不持有 token/hash 解析逻辑。
- **NFR-012 响应式**：十页在设计稿 desktop 视口和 390x844 视口无核心操作遮挡、不可达控件或横向滚动泄漏；表格在窄屏有明确折叠/滚动策略。
- **NFR-013 可访问性**：交互元素可键盘访问，图标按钮有 aria label，焦点态可见，状态不只用颜色表达，支持 `prefers-reduced-motion`。
- **NFR-014 可观测性**：每个 API 响应带 `X-Request-Id`/trace 关联；migration、应用连接、取消、权限拒绝和审计写失败可检索。
- **NFR-015 供应商边界**：LLM/联网提供商错误只作为可恢复 provider 风险处理；安全、租户、迁移、契约和业务流程测试失败不得归因于供应商而放行。
- **NFR-016 质量门禁**：自动测试、lint、类型检查、构建、migration verify/rollback dry-run、API 契约和浏览器场景全部通过；P0/P1/P2=0，console=0。
- **NFR-017 发布**：feature flags 支持按租户/环境启停，灰度观测至少覆盖 1 个内部租户和 1 个客户租户，失败可在不丢数据情况下回退。
- **NFR-018 证据**：每条 FR/NFR 都必须在 [`04_verification-matrix.md`](./04_verification-matrix.md) 具有里程碑、自动测试、浏览器场景、安全检查和证据文件映射。

## 7. 成功指标

| 指标 | 当前基线 | v3 目标 | 证据 |
|---|---|---|---|
| 十页核心操作真实闭环率 | M6 前存在多项缺口 | 52/52 FR 在矩阵中有 PASS 证据；核心动作不依赖 mock | API/DB/E2E/矩阵 |
| 页面后端覆盖 | M0–M5 已接入部分既有端点 | 10/10 页面只消费 adapters，所有核心可见动作均有 endpoint/state | import audit + Codex in-app Browser |
| 租户隔离 | 已有 KB/QA 隔离测试 | 新增 identity/roles/trash/apps/analytics 全部跨租户测试为 0 泄露 | pytest 安全套件 |
| 权限新鲜度 | token capability 需增强实时 DB 解析 | policy 变更后下一次 refresh/`GET /me` 获得新版本与能力 | contract test |
| 分析性能 | 无完整 access event 聚合 | 每租户 1,000,000 事件/90 天/50 RPS p95 ≤ 500ms | EXPLAIN + load report |
| 密钥暴露 | 现有 password hash；新领域未覆盖 | API key/app credential 仅创建一次明文，日志/审计/响应零明文 | secret regression |
| 流式稳定性 | SSE v2 已有基础协议 | `turn_id/seq/cancel/citations/feedback` 兼容，重复/乱序/旧流隔离全通过 | SSE contract/E2E |
| 视觉质量 | M0–M5 页面部分完成；总门禁待执行 | desktop + 390x844 10/10，P0/P1/P2=0，console=0 | screenshots/console |

## 8. 约束与固定决策

1. 保留现有 FastAPI monolith、SQLAlchemy store、SQLite(dev)/PostgreSQL(prod)、`/api/v1`、AuthContext、audit、SSE v2 和 app-v2 adapters。
2. DB/API 只 additive；新列表使用统一 pagination envelope；旧端点不随意改响应。
3. roles 为租户作用域；built-in immutable；custom roles 可 CRUD；assignment/permission change bump `policy_version`。
4. API key 使用 prefix lookup + constant-time hash verify，动态解析 live user/role capability；只在创建响应显示完整 key。
5. trash 覆盖 KB/DOCUMENT/FOLDER；child restore 在父级删除时返回 409 `PARENT_DELETED`；audit 在 purge 后保留。
6. apps install state 与 sync source/run state 分离；六个应用目录名固定。
7. 不增加 microservice/queue；密码/token signing secret 与 app credential key 分离。
8. M0–M5 记录的是前端/既有端点实际完成度，不等于 v3 全栈交付；v3 的完成以矩阵与 TN 证据为准。

## 9. 研究来源

| 来源 | 采用点 |
|---|---|
| [FastAPI Security](https://fastapi.tiangolo.com/tutorial/security/) | API key、Bearer/OAuth2 security scheme 的边界和 OpenAPI 表达。 |
| [SQLAlchemy 2.0 migration guide](https://docs.sqlalchemy.org/en/20/changelog/migration_20.html) | 保持 `Engine/Connection` 显式调用；现有 `create_all` 兼容路径上增加 immutable version chain、ledger、verify 和 rollback。 |
| [OWASP API Security](https://owasp.org/API-Security/) | object-level authorization、认证、敏感数据和租户隔离作为每个 endpoint 的硬门禁。 |
| [MDN Server-sent events](https://developer.mozilla.org/en-US/docs/Web/API/Server-sent_events) | SSE named event、连接、错误与浏览器消费的协议基础；业务 `turn_id/seq` 继续遵循 EKB SSE v2。 |
| 仓库 [`docs/API接口契约_v1.0_2026-08-07.md`](../../../API接口契约_v1.0_2026-08-07.md) | 现有 `/api/v1`、错误 envelope、SSE v1/v2、取消与兼容约束。 |
| 仓库 [`apps/web/DESIGN.md`](../../../../apps/web/DESIGN.md) | Galaxy Motion token、可访问性、响应式和新 UI 边界。 |

## 10. 需求发布门禁

- 本文件已固定为 Tier 3/type=feature；所有 52 FR、18 NFR 都有可观察结果和后续证据位置。
- 当前文档不保留开放问题；合理默认进入 [`02_spec.md`](./02_spec.md) Decision Log。
- v2 历史内容继续保留，但其“前端-only/禁止后端”边界不再是当前 authority；根文档索引已指向本目录。
