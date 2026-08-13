# 真实浏览器 parent-chain 验证（本地）

日期：2026-08-14

## 状态

- `DONE-LOCAL`：为使最新的 `store.save_message` bridge 生效，先重启本地 API；重启后 `/healthz` 返回 HTTP `200`。
- 使用真实 Playwright 浏览器完成本地登录，新建 Assistant 会话，并发送：`重启后父链验证：请简短回答`。
- 请求走真实 SSE v2；页面收到远程 LLM 结合当前授权 KB RAG 返回的回答。此处不将本地结果写成生产 Provider 或生产完成。
- 页面显示 branch selector=`root`，消息顺序为 `USER` 后 `ASSISTANT`。

## SQLite 核对

- 核对同一 `turn` 的持久化记录：`USER` 与 `ASSISTANT` 属于同一 branch。
- `ASSISTANT.parent_message_id` 指向同一 turn 的 `USER` 消息。
- 证据不记录完整消息 ID、token、password、私密地址或 Provider 密钥。

## 验证快照

本次仅写入文档，未在本任务中重跑验证；记录的验收快照为：

- 后端全量：`401 passed, 2 skipped`。
- 前端：`72 passed`。
- typecheck/build：passed。
- `npm audit`：`0 vulnerabilities`。

浏览器本次 snapshot 未显示 console 错误；浏览器 console 未作为“零错误”验收条件，因此不声称 `console=0`。

## 边界

- 本地切片不宣称 PH0–PH8 全部完成。
- 生产 PostgreSQL/pgvector、对象存储、队列/Worker/Scheduler、服务器备份、部署和回滚仍为 `NOT RUN/BLOCKED`。
- 生产服务器统一写作 `[production host]`；不记录密码、token、私密地址或 Provider 密钥。
