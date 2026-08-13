# app-v2 adapters

M0 只定义真实 API 能力的边界、输入输出类型和能力状态，不发请求、不创建会话、不生成业务假数据。

后续实现只能在 adapters 内部复用非 UI API client 或 API types。页面、布局、组件、样式和视觉 fixtures 不直接接触旧 API client，也不承载鉴权、SSE 分帧、错误映射或后端字段转换。

每个缺少后端能力的操作必须保持 unavailable 或 disabled；不能用本地数组、延时、成功 toast 或静态计数器替代真实响应。

## M4 adapters

- `conversations.ts` 映射真实会话、消息和删除结果，不构造会话或消息。
- `qaStream.ts` 只消费 `ApiClient.askStream` 的已校验回调。request raw.seq、content_delta seq 和 done.last_seq 才进入 view model；phase、citations、error 与协议诊断不伪造服务端 seq。流句柄上的 turn/message/conversation 标识保持动态读取。
- `feedback.ts` 在调用服务端前拒绝空 reason；反馈只由真实 assistant messageId 触发。
- 页面、布局和纯 UI 组件不能 import `lib/api`、`types/api` 或调用 `fetch`；所有错误、权限、取消、协议与服务端字段转换留在 adapters/页面状态边界。

## M5 adapters

- `admin.ts` 只封装既有 `POST /admin/users`、`GET /admin/tenants` 和 `POST /admin/tenants`，映射为 v2 view models/result states，并沿用 `toAdapterError` / `stateForError`。
- 邀请输入严格包含服务端所需的 `email`、`name`、`password`、`role`；租户创建严格映射既有 `TenantCreate` 全部字段。客户端不添加用户列表、资料更新、安全、通知、偏好或 API key 方法。
- `AuthSession` 由 auth adapter 的真实 `GET /me` 建立，团队与个人中心直接消费 session，不在页面再次调用 `getMe`。
- 成员数据复用 knowledge adapter 的真实 KB member service；页面和组件不接触 API client、API types 或 `fetch`。
