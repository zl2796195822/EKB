# EKB app-v2 并行前端

app-v2 是并行的新前端边界，M2 在 M0/M1 骨架与认证边界之上完成工作台和模块地图：

- AppV2 根边界与最小 hash 路由分发；
- 十页单一 route manifest，包含页面名称、布局类别和里程碑；
- `#/dashboard` 工作台：真实会话问候、四个快捷操作、五张指标卡、趋势/分布/动态、最近访问 tabs/表格、热门标签、知识库健康度和 footer；
- `#/modules` 模块地图：`ROUTES` manifest 驱动的九个业务模块 tile 与真实 hash 跳转；
- 无工作台聚合端点的数据区域保留设计结构，并分别显示 `unavailable` 状态；
- 页面占位模块与统一 loading、empty、error、permission-denied、unavailable、ready 状态模型；
- layouts、components、adapters、styles、types 和 visual fixtures 的职责边界；
- 不依赖旧页面、旧组件、旧样式，不创建 session、token 或业务成功假数据；页面只消费 `AppV2` 注入的真实 `AuthSession`。

## app-v2 M3

M3 在并行边界内接通知识库、文档中心和知识库内语义搜索。`AppV2` 创建单一 `ApiClient`，再将知识库、文档和搜索 adapter facade 注入页面；页面、布局和组件只消费 `app-v2/types` 中的 view model，不直接接触 API 类型、token 或 `fetch`。

- `#/knowledge` 使用真实知识库、文档、成员、版本和搜索响应；创建、编辑、删除、上传、重试及成员写操作完成后重新读取服务端状态。
- `#/documents` 聚合当前主体可见知识库的真实文档；本地筛选仅作用于已加载的名称、类型和状态字段，语义搜索单独调用绑定知识库的真实 `/search`。
- 无后端端点的收藏、最近、回收站、批量删除和顶栏全局搜索明确显示为不可用，不伪造成功。
- 上传使用每次操作生成的 `Idempotency-Key`，并展示服务端返回的 `doc_id`、`job_id`、`status`、`trace_id`；API 错误保留状态码、错误码、request id、details 和 upload 元数据。
- 版本与 diff 只使用真实权限可见的响应；少于两版时明确显示不可用或空状态。

当前不会从旧 App 入口导入或渲染 AppV2。后续入口切换属于 M7，必须另行完成完整浏览器 QA 与旧 UI import 审计。

唯一独立页面清单是 routes.ts 中的十条 route entry。搜索是知识库、文档中心、AI 助手和全局入口共享的 adapter 能力，不另建独立页面。

## app-v2 M4

`#/assistant` 使用专用顶栏、左会话栏、中聊天区和右上下文栏。会话列表、消息、删除、授权知识库、SSE v2、取消、当前引用、当前 KB 搜索、文档元数据预览和回答反馈都只通过 `AppV2` 注入的 adapters 使用现有 `ApiClient`。

- 会话按真实 `updatedAt` 分为今天、昨天、过去 7 天和更早；收藏、项目、分享、重命名、导出、添加文件、联网搜索、深度思考和模型选择因无端点保持 disabled/unavailable。
- `qaStream` 保留 `askStream` 的动态 `turnId`、`messageId` 和 `conversationId` getter；`SseStreamParser` 负责旧 turn、重复/倒序 seq 和协议错误隔离，页面只展示真实可得的 seq。
- 首 token 前或部分输出后停止都会优先调用真实幂等 cancel；尚未得到 `turn_id` 时仅本地 abort，并明确显示服务端未收到 cancel。错误可把最后一次真实问题恢复到 composer，等待用户手动重试。
- 引用只来自当前 SSE citations，相关知识只来自当前授权 KB 的真实 search；文档预览只加载真实元数据，正文打开和加入引用保持 disabled。
- 只有真实 assistant `messageId` 与非空 reason 才能提交 UP/DOWN feedback；服务端失败保留可重试状态。

## app-v2 M5

`#/team` 和 `#/profile` 使用 AppV2 注入的单一 `ApiClient`、真实 `AuthSession`、KB members 以及既有 admin users/tenants 端点。

- 团队页先加载当前主体可访问的真实知识库，选择 KB 后再加载该 KB 的 member ACL；姓名、邮箱、部门和账号状态不在响应中时明确显示 `unavailable`。
- `POST /admin/users` 邀请与 KB 成员增删是两个独立流程；邀请只展示真实响应，不把邀请结果伪装成用户列表或 KB 成员。
- 角色管理和权限矩阵只读展示主体、租户、capabilities 与 policyVersion；用户列表、角色 CRUD、导出和状态管理缺少后端端点时保持 disabled/unavailable。
- 具备 `tenant:provision` 时才读取/创建真实租户；创建表单的 `owner_password` 仅用于本次请求，提交后清除且不回显。
- 个人中心只使用认证恢复时建立的 `AuthSession`；资料、安全、通知、偏好和 API key 操作均明确不可用，快捷入口只跳转十条既有 hash 路由。
## app-v2 M1

`AppV2` 是与生产入口并行的前端重建入口。M1 提供新设计系统、全局/专用壳、真实认证会话和十条规范 hash 路由的守卫；不会修改旧 `App`、`main.tsx` 或生产入口。

### 隔离预览

在 `apps/web` 目录运行 Vite 后打开 `/src/app-v2/preview.html`。预览默认从真实认证服务恢复当前浏览器会话；没有 app-v2 会话时会显示登录页，不提供预置身份或 mock 数据。

### 会话边界

认证适配器只使用 `ApiClient` 的 login、refresh、getMe 和 logout 能力。真实登录产生的会话数据暂存在当前浏览器的 app-v2 `sessionStorage` 命名空间；恢复时先 refresh，再通过 getMe 校验，失败会清理会话并回到登录页。
