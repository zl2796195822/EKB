# Conversation rename local evidence (2026-08-14)

## DONE-LOCAL

- 后端已有 `PATCH /api/v1/chat/conversations/{id}` 已被前端真实接入；调用链覆盖 `ApiClient`、conversation adapter、受控 UI 操作和成功后的服务端会话刷新。
- 未知客户端异常会被归一化为安全的客户端错误信息，不向 UI 或证据泄漏原始 `message`。
- 本切片只记录真实会话重命名；Share 和 Projects 仍保持 disabled。

## 验证结果

- `npx vitest run src/app-v2/tests/v3.conversation-rename.test.ts`：`2 passed`。
- `npm run test`：`12 files / 68 tests passed`。
- `npm run typecheck`：passed。
- `npm run build`：passed；保留既有 `>500KB` chunk warning，该 warning 不影响构建通过。

## 生产边界

- 本地结果仅为 `DONE-LOCAL`，不代表 PH0–PH8 或生产完成。
- 服务器、备份、部署和回滚仍为 `NOT RUN/BLOCKED`；生产目标统一使用 `[production host]`，受管敏感值统一使用 `[REDACTED]`。
- 本文不记录密码、token、API key、私密地址或其他秘密。
