---
title: EKB 生产 Embedding 配置与验收方案
date: 2026-08-16
status: Draft — 待用户确认后执行
scope: 生产候选环境 embedding 链路打通（上传 → READY → 检索 → 引用问答）
depends_on:
  - docs/EKB知识库与AI助手生产对标审计_v1.0_2026-08-14.md（P0 修复顺序）
  - docs/EKB知识库批量目录上传修复与开源对标_2026-08-14.md（生产前置条件）
---

# EKB 生产 Embedding 配置与验收方案

## 1. 目标与当前阻塞

生产候选环境（PostgreSQL + pgvector、对象存储、worker、公网 Nginx）已切流，登录与上传控制面可用；但**没有 Embedding Profile / 远程 embedding provider**，所有新摄取在 worker 的 embedding 阶段以 `EMBEDDING_UNAVAILABLE` fail-closed，无法产生 READY 文档、可检索向量与 AI 引用。

本方案的目标是按依赖顺序补齐配置，使「浏览器上传中文目录 → READY → search → 带引用问答」全链路在生产通过一次可审计验收。不做的事：不启用本地/回环模型（Provider 边界已拒绝回环地址），不放宽任何 fail-closed 行为，不在文档或脚本中记录凭据。

## 2. 前置条件（P0，逐项验证后才继续）

| # | 前置项 | 验证方式 | 状态 |
|---|---|---|---|
| P-1 | **部署包含 `tenant_role` 比较修复的镜像**。2026-08-16 提交 `9569baa` 引入了 `str(auth.tenant_role) in ("OWNER","ADMIN")` 判断；`str()` 对 `TenantRole` 枚举返回 `"TenantRole.OWNER"`，导致 OWNER/ADMIN 也被判定为非管理员——provider 创建全员 403、admin 读取被错误脱敏。修复（`.value` 比较）已在本地完成并通过全量回归 | 生产容器内 `POST /api/v1/llm/providers`（管理员）返回非 403；`GET /llm/providers`（管理员）响应含 `endpoint_configs` | 待部署 |
| P-2 | 容器远程出网可用（`sitecustomize.py` 代理补丁 + `--env-file` 的 `HTTPS_PROXY`） | 容器内对 provider 域名发起真实 HTTPS 请求成功（如 `GET {baseUrl}/models`） | 待验证 |
| P-3 | 管理员（OWNER）账号可用且非默认弱口令 | 公网 Origin 登录 + `/api/v1/me` 为 200 | 已达成（2026-08-15） |
| P-4 | 目标 KB 存在且管理员有 OWNER 授权 | 候选库已有 1 个 PRIVATE KB（OWNER 授权） | 已达成 |

**P-1 是硬前置**：未修复的镜像上，任何 provider/embedding 配置操作都会失败。此 bug 同时解释了此前 13 个后端测试失败（已修复，见 §6 交付证据）。

## 3. Embedding Provider 选型

约束：必须是**非回环远程 HTTP(S) OpenAI 兼容 `/v1/embeddings`**；`dimensions` 必须固定且与 Profile 一致（fingerprint 绑定，换模型 = 新 Profile + 重建索引）。

| 候选 | endpoint 示例 | 模型 / 维度 | 适用性 |
|---|---|---|---|
| 硅基流动 SiliconFlow | `https://api.siliconflow.cn/v1` | `BAAI/bge-m3`（1024）等 | 目录内预设；国内出网友好；推荐首选 |
| 阿里百炼 DashScope | OpenAI 兼容模式 | `text-embedding-v3/v4` | 目录内预设；需要确认兼容模式 endpoint |
| 智谱 AI | `https://open.bigmodel.cn/api/paas/v4` | `embedding-3` | 目录内预设 |
| OpenAI | `https://api.openai.com/v1` | `text-embedding-3-small/large` | 视服务器出网与合规而定 |
| 本机 Ollama `qwen3-embedding:4b` | `http://<非回环隧道>/v1` | 2560 维 | **仅限受控验收**（2026-08-16 已验证 OpenAI 兼容批量响应与按 index 排序）；笔记本休眠/断网即中断，不是 SLA；需加密隧道暴露为非回环地址 |

注意：**DeepSeek 不提供 embedding API**（仅 chat）。08-16 的 DeepSeek v4-flash/v4-pro 目录切换只解决 chat 侧，不能替代本方案。

选定 Provider 后记录：`provider_key`、`model_id`、`dimensions`、计费口径（不记录任何 key）。

## 4. 配置步骤（顺序执行，每步验证后再下一步）

### S1 · 创建 embedding Provider + 凭据 + 模型（管理员，经 API）

1. 管理员登录，`POST /api/v1/llm/providers`：`endpoint_configs["openai-embeddings"].baseUrl = <远程 endpoint>`，附 `api_key`（凭据以 TEAM scope 加密落库，明文不回显）。
2. `POST /api/v1/llm/providers/{id}/models` 创建 embedding 模型：`model_id`、`model_type=embedding`、`capabilities` 标注 `dimensions`，`is_enabled=true`。
3. 验证：`llm_providers/llm_models/provider_credentials` 中 embedding 相关计数 ≥ 1；`provider_credentials.ciphertext` 为密文且能被 master key 解密（同 `test_provider_secret_is_encrypted_and_redacted` 语义）；普通成员 `GET /llm/providers` 不含 `endpoint_configs`（脱敏生效）。
4. 负向：member 创建 provider 应 403（新权限契约）；回环 endpoint 应 400 `REMOTE_PROVIDER_REQUIRED`。

### S2 · 为目标 KB 绑定 Embedding Profile 与 active index generation

现状缺口：`ensure_kb_profile()`（`ekb_api/services/embedding.py`，幂等：按 fingerprint 复用 Profile、创建 ACTIVE generation、写回 KB 的 `embedding_profile_id` + `active_index_generation_id`）**没有任何 router 或 CLI 入口**，此前只能直连 DB 手工写。

按项目 ops 惯例（`repair_admin_access.py` 模式）补一个窄 CLI：

- 新增 `ekb_api/ops/embedding_profiles.py` + 入口脚本：参数显式指定 `--tenant-id --kb-id --provider-id --model-id --dimensions [--tokenizer --chunker-id --chunker-version]`；默认 dry-run 打印将写入的 profile/generation；`--apply` 才写入；跨租户/不存在 KB/维度与模型声明不符时拒绝。
- 或者（不推荐长期使用）：容器内一次性 `docker exec ... python -c "from ekb_api.services.embedding import ensure_kb_profile; ..."`，必须在受管会话中执行并留审计记录。

验证：目标 KB 的 `embedding_profile_id` 与 `active_index_generation_id` 非空；`index_generations` 有一条 `state='ACTIVE'`。

### S3 · 重试既有 FAILED 摄取

Upload Center 对 `EMBEDDING_UNAVAILABLE` 的 failed item 执行真实 retry（服务端投影为权威，不重复建版本）；worker 需有 heartbeat。验证 item/job 从 FAILED → SUCCEEDED、document 状态 READY。

### S4 · 浏览器端到端验收（生产 Origin）

按 2026-08-14 发布复核的验收序列执行：

1. 浏览器上传**中文目录**（含多级路径与至少 PDF/DOCX/MD 各一）→ 分区 batch → 对象 PUT → SHA-256 complete → CHUNKING → embedding → **READY/SUCCEEDED**。
2. `search` 对上传内容返回真实命中；`EXPLAIN`/日志确认走 pgvector 而非全量扫描（chunks 数量小时人工核对即可）。
3. QA 提问三连：有证据回答（引用指向刚上传文档）、无证据拒答、越权 KB 拒绝；回答含可回放引用。
4. 负向回归：跨租户账号不可见目标 KB；member 不能改 provider。

## 5. 回滚

- Provider 层：`PATCH /llm/providers/{id}/enable {enabled:false}`（或删除模型）→ 摄取回到 `EMBEDDING_UNAVAILABLE` fail-closed，不产生半成品索引。
- Profile 层：将 KB 的 `active_index_generation_id` 指回上一 ACTIVE generation（ops CLI 增加 `--rollback-to` 或受管 SQL；改动前先备份 KB 行）。
- 数据层：embedding 写入仅新增 chunks/向量行与 generation 记录，不破坏 source object/document；最坏情况清空 generation 相关行重跑 S2–S3。
- 全局：旧 release 与回滚点保留策略不变（2026-08-14/15 已建立）。

## 6. 本次会话已完成的配套修复（本地，未部署）

| 修复 | 内容 | 验证 |
|---|---|---|
| `tenant_role` 枚举比较 bug | `services/v3_llm.py` 8 处、`routers/llm.py` 2 处 `str(auth.tenant_role)` → `auth.tenant_role.value`；根因是 `str()` 对 str-mixin 枚举返回 `"TenantRole.OWNER"` | 全量后端 498 passed / 2 skipped / 0 failed（修复前 13 failed） |
| embedding 测试契约对齐 | `tests/v4/test_ph3_embedding_boundary.py` 改为断言「同租户 TEAM 共享可用 + 跨租户 fail-closed」，匹配 `9569baa` 的租户共享设计；`_provider_for_profile` 清理无用 `:user` 绑定并修正 docstring/错误文案 | 该文件 5/5 passed |
| 工作区卫生 | `assistant.css` 行尾空行（`git diff --check` 门禁） | diff-check 通过 |

Ruff 对改动文件的 27 个报告均为既有风格问题（与 MEMORY 08-14 记录一致），本次未新增。前端未改动（本会话 108 passed / typecheck / build 均绿）。

## 7. 完成判据（引用生产验收门槛）

只有同时满足以下条件，才可标记「知识入库终态可用」：

- [ ] P-1/P-2 前置验证通过并留证据；
- [ ] S1–S4 全部执行且每步验证记录在案（不记录凭据/私密地址）；
- [ ] 生产存在至少一条浏览器产生的 READY 文档 + 真实向量 + 带引用回答；
- [ ] 无证据拒答与越权拒绝的负向用例通过；
- [ ] MEMORY.md 与本方案状态同步更新，提交精确 diff。

在此之前，对外的口径保持「上传控制面可用 / 知识入库终态不可用」，不得把「上传按钮可点击」表述为知识库全链路完成。
