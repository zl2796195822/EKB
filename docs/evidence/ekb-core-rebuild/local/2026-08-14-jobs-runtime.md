# Jobs runtime 本地证据（2026-08-14）

## 本次交付

Analytics Jobs Center 现在并行调用真实 `GET /api/v1/jobs/runtime`，并显示服务端返回的 `workers`、`leases`、`recent_runs` 与 `checked_at`。前端只映射后端白名单字段，不显示 `tenant_id`、`payload`、`counters` 或错误详情。

## 状态边界

- `status: available` 显示真实 worker 心跳、retention lease 与 scheduler run 明细。
- `status: unavailable` 原样显示 unavailable；空数组不被推断为健康或运行中。
- HTTP 错误仍按现有认证/错误状态展示，不生成本地运行时数据。

## 验证范围

- API 客户端路径与 `recent_runs_limit` 边界映射。
- adapter 字段映射、敏感字段不进入 view model、unavailable 状态保留。
- 本地 Web 测试、TypeScript 类型检查、生产构建和 `git diff --check`。

生产验收仍需在 [production host] 使用受控凭据 [REDACTED] 另行完成；本证据不宣称生产运行时可用。

## 真实浏览器验收补充

- 本地认证后打开 Analytics 作业中心，页面实际显示了真实 worker heartbeat、retention lease，以及最近一条 `SUCCEEDED` scheduler run；展示内容来自 runtime API projection，不是前端生成的运行时数据。
- API 重启前，旧进程对新 runtime endpoint 返回过 404；重启后该 endpoint 返回 200，页面随后正常显示 runtime 数据。
- 浏览器会话仍有历史 auth-related console errors；本次不宣称 `console=0`。本记录不保存完整 opaque IDs、凭据、token 或私网地址。

## 2026-08-14 stale heartbeat 修复复验

- scheduler 默认 `owner_id` 现在使用稳定进程标识；runtime projection 对 `local-scheduler` 仅展示当前 `scheduler_leases.owner_id`，历史替换 worker heartbeat 仍保留但不作为当前 worker展示。
- 测试、Ruff 与 `git diff --check` 通过；浏览器复验显示 `1 worker / 1 lease / 真实 SUCCEEDED run`。
- 生产仍未执行；生产目标使用 `[production host]`，受管敏感值使用 `[REDACTED]`，不记录凭据、token、私网地址或完整 opaque ID。
