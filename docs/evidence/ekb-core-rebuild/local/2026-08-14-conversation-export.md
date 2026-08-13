# Conversation export local evidence (2026-08-14)

## DONE-LOCAL

- `canExportConversation` 仅在 `state === 'ready'`、未流式且存在至少一条非 transient 消息时启用导出。
- Markdown 导出只包含当前服务端已持久化的会话消息；流式 transient 消息被过滤，不写入导出文件。
- 当前分享能力保持 disabled；本切片不新增分享链接、公开访问或服务端导出存储。

## 验证与边界

- 定向 Vitest 覆盖 ready/loading、空消息、全 transient、流式中的导出状态，并覆盖 transient 消息过滤。
- 本地结果仅为 `DONE-LOCAL`。生产目标、部署、备份、回滚以及 PH0–PH8 全部完成仍为 `NOT RUN/BLOCKED`。
- 本文不记录密码、token、API key、私密地址或其他秘密；生产目标统一使用 `[production host]`，敏感值统一使用 `[REDACTED]`。
