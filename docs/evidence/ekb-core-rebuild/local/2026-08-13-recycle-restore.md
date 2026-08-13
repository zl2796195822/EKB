# 回收站 restore / purge 本地证据（2026-08-13）

## 证据边界

- 记录时间：2026-08-13 23:42 CST（Asia/Shanghai）。
- 范围：本地 development，API/UI 使用 `127.0.0.1`；这是 local verified，不是 production completed。
- 本证据不记录密码、token、API key、私有地址或完整 opaque ID；生产目标统一写作 `[production host]`，受管敏感值统一写作 `[REDACTED]`。

## 本地业务切片

Luna 修改了以下业务文件：

- `apps/api/ekb_api/services/v3_trash.py`
- `apps/api/tests/v4/test_ph2_trash.py`

实现并覆盖的真实 SQLite 回收站闭环：

- restore 校验租户作用域、保留期、purge 状态，并使用 CAS 防止资源状态已变化时误恢复。
- 过期条目返回 `RETENTION_EXPIRED`；`PURGING_*`、`CLAIMED`、`RETRY_WAIT` 等非可恢复状态返回 `PURGE_IN_PROGRESS`。
- purge 仅允许已过期条目，具备状态和租户/代际防护；重复删除为新的 deletion generation 和 retention window。
- 已知历史状态时恢复原状态；旧删除记录缺少 DOCUMENT 历史状态时安全 fallback 为 `READY`，这不是历史状态完整恢复。

## 验证命令与结果

以下结果来自本次本地切片验证：

```text
.venv/bin/pytest -q apps/api/tests/v4/test_ph2_trash.py
6 passed

.venv/bin/pytest -q apps/api/tests
385 passed, 2 skipped

.venv/bin/ruff check apps/api/ekb_api/services/v3_trash.py apps/api/tests/v4/test_ph2_trash.py
All checks passed!

git diff --check
passed
```

## 真实浏览器验收

在本地真实浏览器完成以下路径：

1. 打开 `http://127.0.0.1:5173/#/knowledge`，创建知识库“回收站本地验收-20260813”。
2. 删除该知识库，打开 `http://127.0.0.1:5173/#/recycle`。
3. 页面显示真实回收站条目和 30 天保留信息。
4. 点击“还原”，页面显示“已还原…”反馈。
5. 知识库重新出现在当前账号的授权列表中。

浏览器 console 存在既存 auth-related 错误，因此不声称 `console=0`；本次验收以页面真实状态和服务端结果为准。

## NOT RUN / 限制

以下内容本次没有运行，不能写成已完成：

- 生产 PostgreSQL/pgvector、生产对象存储、可靠队列、生产 Worker/Scheduler。
- 真实 Provider 凭据及外部出网验证。
- 服务器备份、部署、重启、回滚和生产浏览器验收。

现有删除入口没有为每个 DOCUMENT 保存删除前状态；当历史元数据缺失时只能安全回退为 `READY`。对象与向量的分阶段清理由其他 worker/service 负责，本切片没有越界宣称已完成。生产 NOT RUN；本地 SQLite 结果不代表生产回收站完成，也不代表 PH0–PH8 全部完成。

## 后续建议

在获得受管生产目标和凭据后，先对 PostgreSQL、对象存储、队列/Worker/Scheduler 做备份与回滚演练，再验证 purge 的 DB/object/index 分阶段一致性；同时为新删除入口持久化 DOCUMENT 删除前状态，并补充真实生产浏览器验收。
