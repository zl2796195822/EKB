# EKB Core Rebuild 本地真实业务证据（2026-08-13）

## 范围

本记录只证明当前工作区在本地开发环境的真实 API、浏览器和失败闭环；不把本地 SQLite、开发对象存储或缺少外部 Provider 的结果写成生产完成。生产目标仍是 PostgreSQL/pgvector、S3-compatible object storage、可靠队列/Worker/Scheduler 和受管 LLM Provider。

## 本地运行结果

- FastAPI：`127.0.0.1:8023`，Vite：`127.0.0.1:5173`。
- 本地对象存储使用显式开发配置的磁盘适配器，上传对象写入 `.local-object-storage/`；路径越界、未知对象和过期 upload session fail closed。
- 上传协议已走真实批次：创建 batch → 预检 upload session → 上传对象 → checksum complete → 创建后台 ingest job。
- Worker 已接入真实对象读取、校验、解析、分块、远程 embedding、索引和失败状态更新；未配置远程 Embedding 时返回 `EMBEDDING_UNAVAILABLE`，不回退本地模型、不写伪向量。
- Provider/Model 运行时仅使用受管远程 LLM/Embedding 配置；本地大模型不是可选回退路径。

## 验证命令与结果

```text
cd apps/api && ../../.venv/bin/pytest -q tests ../api/tests/v3 ../api/tests/v4
153 passed, 2 skipped

cd apps/api && ../../.venv/bin/ruff check \
  ekb_api/services/storage.py ekb_api/services/ingest_worker.py \
  ekb_api/routers/kb_upload.py ekb_api/core/db.py ekb_api/embedding.py \
  ekb_api/main.py tests/v4/test_ph3_boundaries.py tests/v4/test_ph3_ingestion.py
All checks passed

cd apps/web && npm run typecheck
passed

cd apps/web && npm test -- --run
49 passed

cd apps/web && npm run build
passed; Vite only reports the existing large JavaScript chunk warning

cd apps/web && npm_config_registry=https://registry.npmjs.org npm audit --omit=dev --audit-level=high
found 0 vulnerabilities

git diff --check
passed
```

全仓库 `ruff check` 仍会报告工作区历史文件中的既有风格/测试问题；本阶段没有借机扩大修复范围。本次修改涉及的后端文件精确 lint 已通过。项目 venv 未安装 `pip-audit`。

## 真实 API 与浏览器验收

API 五步链已实际返回成功状态：登录、知识库列表、batch 创建、upload session、对象 PUT、complete；后台任务随后在无远程 Embedding 时真实失败为 `EMBEDDING_UNAVAILABLE`，数据库中的 ingest/document/upload item 均同步为 `FAILED`，没有本地向量替代。

真实 Chrome 已完成：登录 → 打开文档中心 → 选择本地文件 → 批次上传 → 文档列表刷新。文档中心显示服务端返回的真实文档行，并同时呈现 `已就绪` 与 `失败 / EMBEDDING_UNAVAILABLE` 状态。首次加载状态缺口已修复，页面不再永久停留在加载态。

## 安全检查

- upload complete、对象 PUT、document/job projection 均按租户和主体作用域校验；过期 session、错误对象键和 checksum 不匹配拒绝。
- 远程 S3-compatible presigned PUT 不携带 EKB Bearer header；本地 protected PUT 仅开发环境和显式 local root 开启。
- 生产 secret-pattern 扫描排除测试和工具目录后没有生产凭据命中；仓库内 `MCP` 测试仍有占位 token 字符串，未视为凭据。
- 本地认证 token 继续使用 session storage；项目记忆、Spec 和证据不记录密码、API key、token 或私密地址。

## 尚未执行 / 生产阻塞

- 未执行生产 PostgreSQL/pgvector 原子切库、生产对象存储、可靠队列/Worker/Scheduler、外部 LLM/Embedding 凭据配置、服务器备份/部署/回滚或生产浏览器验收。
- 这些动作需要已配置且可审计的生产基础设施、稳定出网和用户批准的部署窗口；本地缺少 Provider 凭据时保持 fail closed，不用 mock 代替。
