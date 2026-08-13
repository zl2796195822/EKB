# EKB Core Rebuild 本地 Scheduler 证据（2026-08-13）

## 结论

DONE-LOCAL。本文只证明 development 本地 scheduler 的可控 tick 间隔，以及真实 XLSX/PPTX 上传在本地缺少远程 Embedding 时的状态闭环；不把本地 SQLite、开发对象存储或本地 scheduler 写成生产完成。

## 代码与测试依据

- 已推送 commit `524e51a`：`apps/api/ekb_api/main.py` 新增 `EKB_LOCAL_SCHEDULER_INTERVAL` 的 `1–300` 秒有界解析；非法、越界或非十进制值回退 `60` 秒，且只在 development 本地 scheduler 分支生效。
- 新增 `apps/api/tests/test_local_scheduler.py`，覆盖 interval 解析与本地 scheduler 调度边界。
- 该切片不引入本地模型、mock、伪向量或伪成功；生产 scheduler 不由本地 interval 配置代替。

## 本地真实 API 结果

- 本地 API 以 `interval=2` 启动后，真实 XLSX/PPTX 上传批次由后台 tick 从处理中推进到 `CHUNKING · 50%`。
- 两类文件随后都进入真实 `EMBEDDING_UNAVAILABLE/FAILED` 终态；未出现 `MIME_UNSUPPORTED`、`PARSER_CORRUPT` 或伪 `READY`。
- 该结果证明 parser/ingestion/scheduler 的本地状态推进和缺少远程 Embedding 时的 fail-closed 行为，不证明远程 Provider/Embedding 出网成功或生产索引成功。

## 验证结果

- local scheduler + PH3 office/ingestion/scheduler 定向回归：`34 passed`。
- Ruff：通过。
- 代码 diff check：通过。
- 本次文档更新另行执行 `git diff --check` 与新增文档 secret scan；结果均通过。

## 生产边界

以下仍为 `NOT RUN/BLOCKED`：生产 PostgreSQL/pgvector、Provider/Embedding 出网、生产对象存储、可靠队列、生产 Worker/Scheduler、服务器备份、部署与回滚。生产目标统一写作 `[production host]`，受管敏感值统一写作 `[REDACTED]`。

本文不记录凭据、password、token、私密地址或完整 opaque secret；本地证据不代表生产 scheduler、成功索引或 PH3/PH8 完成。
