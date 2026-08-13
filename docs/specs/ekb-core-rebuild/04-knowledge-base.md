---
title: EKB 知识库与可靠摄取规格
status: Ready for Plan
tier: 3
type: feature
authoritative: true
scope: ekb-core-rebuild
version: 0.1
date: 2026-08-12
owner: Sol
---

# EKB 知识库与可靠摄取规格

## 1. 目标与需求映射

本文件实现 [EKB-CR-FR-007 至 EKB-CR-FR-020](./01-requirements.md) 的设计合同。上传必须把“用户选择的字节”转换为可复现、可版本化、可检索的知识资产，且任何失败都可定位到具体文件和具体阶段。

## 2. 领域模型

| 实体 | 关键字段 | 约束 |
|---|---|---|
| `knowledge_bases` | tenant、name、embedding_profile_id、status | profile 变化只能走 reindex command |
| `kb_memberships` | kb、subject、role | `(kb_id, subject_type, subject_id)` 唯一 |
| `documents` | kb、relative_path、active_version_id、deleted_at | `(kb_id, normalized_relative_path)` 在未清理域唯一 |
| `document_versions` | document、version_no、source_object_id、sha256、status | `(document_id, version_no)`、`(document_id, sha256)` 唯一 |
| `source_objects` | tenant、object_key、sha256、size、mime、ref_count | object key 不含用户路径；租户内 hash 可去重 |
| `upload_batches` | creator、mode、counts、bytes、status | 服务端聚合；不是前端临时对象 |
| `upload_items` | batch、relative_path、bytes、upload_session、status | `(batch_id, client_item_id)` 唯一 |
| `ingest_jobs` | version、active_attempt_id、terminal outcome | 一个版本的逻辑摄取任务 |
| `ingest_job_attempts` | job、attempt_no、state、lease、progress、error | 同一 job 只能有一个 active attempt；历史 attempt 不可变 |
| `ingest_stages` | attempt、stage、input_hash、status、metrics、started/ended | `(attempt_id, stage, input_hash)` 唯一 |
| `chunks`（领域名 DocumentChunk） | version、ordinal、text、metadata、content_hash | 复用现有物理表并补 version FK；只属于一个 immutable version |
| `chunk_embeddings` | chunk、profile、vector、generation | 维度与 profile 一致 |
| `index_generations` | kb、profile、generation、state | build 完成后原子激活 |

`relative_path` 使用 `/` 作为逻辑分隔符；服务端执行 Unicode 规范化、移除 `.`、拒绝 `..`、绝对路径、NUL、控制字符和平台保留名。保留原始显示路径，同时以 `normalized_relative_path` 做身份判断。

## 3. 上传批次协议

### 3.1 选择与预检

浏览器在调用 API 前生成稳定 `client_item_id`，收集 `relative_path`、size、browser mime 和 lastModified。服务端创建批次时逐项返回：

- accepted/rejected；
- 规范化路径；
- 限制或格式错误；
- upload method（single/multipart）；
- 短期上传会话；
- 是否命中相同内容幂等结果。

前端必须在“开始上传”前展示选择模式、总数、总大小和逐项预检。被拒项目不计入成功数，但保留在批次报告。

### 3.2 默认限制

| 限制 | 默认值 | 配置边界 |
|---|---:|---|
| 单文件 | 100MB | tenant policy 可调低；调高需容量评审 |
| 批次文件数 | 1000 | 服务端硬校验 |
| 批次总量 | 5GB | 服务端硬校验 |
| 并发分片 | 3/浏览器 | 服务端可下发 |
| 空文件 | 拒绝 | `FILE_EMPTY` |
| 路径深度 | 32 | `PATH_TOO_DEEP` |

### 3.3 字节上传

优先使用 S3-compatible multipart 预签名。客户端只向签名限定的 object key、method、part number 和大小写入；完成时服务端校验 size、ETag/part list 和 SHA-256。不能使用预签名时，API 必须以流式块写入对象存储，禁止 `await file.read()` 读取全部内容。

断点恢复由 upload item 的 multipart upload id 和已确认 parts 支持。过期未完成会话由 scheduler abort。

## 4. 文档身份、版本和幂等

```text
scope = tenant_id + knowledge_base_id
document identity = scope + normalized_relative_path
content identity = SHA-256(source bytes)
command idempotency = scope + client_request_id
```

| 情况 | 结果 |
|---|---|
| 相同路径、相同 hash、相同/新 request id | 返回既有版本，不重复摄取 |
| 相同路径、不同 hash | 创建 `version_no+1`，旧版本保留 |
| 不同路径、相同 hash | 创建独立 Document/version，可复用 source object |
| 同文件名、不同目录 | 独立 Document |
| 活跃摄取中重复完成 | 返回相同 ingest job |
| 路径在回收站 | 默认 409；用户选择恢复后新版本或使用新路径 |

新版本只有在索引 generation 构建完成后才切换 `active_version_id`。失败版本保留失败状态和原件，不影响旧 active version。

## 5. 摄取状态机

Upload item 与 ingest attempt 是两个状态机：

```text
upload item: WAITING → UPLOADING → UPLOADED → COMPLETING → COMPLETED
                                      ↘ FAILED / ABORTED
ingest attempt: WAITING → VALIDATING → CONVERTING? → PARSING → CHUNKING
               → EMBEDDING → INDEXING → SUCCEEDED
               每个非终态可到 FAILED/CANCELLED
```

`UPLOADED` 只证明对象存储已接收字节；`COMPLETED` 证明 checksum/版本/job 已建立；只有 ingest attempt `SUCCEEDED` 才表示知识库可用。批次状态是 item/job projection，不独立推断成功。

每次状态迁移使用 compare-and-set；worker 领取 attempt 后写入 `lease_owner/lease_expires_at/heartbeat_at`。lease 到期可重领同一非终态 attempt；显式 Retry 只能在旧 attempt 进入不可变终态后创建 `attempt_no+1`，并以 partial unique index 保证一个 active attempt。stage 写入以 attempt+stage+input hash 幂等。

进度不是随机值：

- Uploading：已确认字节/总字节。
- Parsing/Chunking：已处理页、sheet、slide 或记录/总估算单位。
- Embedding：已成功 chunk/总 chunk。
- Indexing：已写入 vector/总 vector。
- 批次：按文件字节和 stage 权重聚合，同时显示 `success/failed/running/waiting` 数量。

## 6. Parser Registry

| 类型 | 处理器 | 最低元数据 |
|---|---|---|
| TXT/Markdown | encoding detection + text parser | encoding、line/heading range |
| PDF text | PDF parser | page、block、bbox（可得时） |
| scanned PDF | render + OCR | page、bbox、ocr confidence |
| DOCX | OOXML parser | paragraph/table/heading |
| XLSX | workbook parser | workbook、sheet、cell range、formula/value policy |
| PPTX | OOXML parser | slide、shape、speaker notes（允许时） |
| DOC/XLS/PPT | isolated LibreOffice → OOXML/PDF | converter version、source/target hash |
| PNG/JPEG/WebP | OCR + optional caption | dimensions、page=1、bbox/confidence |

宏禁止执行；压缩包需限制解压比、文件数和嵌套深度。转换/OCR 在资源受限 worker 中运行，临时目录按 job 隔离并在终态清理。

## 7. Chunk 与 Embedding

Embedding profile 至少包含：provider/model、dimensions、tokenizer、chunker id/version、chunk size/overlap、normalization、language policy。KB 创建时绑定 profile；profile 状态不可原地修改。

重建索引：

1. 创建新 profile/generation；
2. 对当前 active document versions 后台重算；
3. 校验数量、维度、抽样检索和失败率；
4. 原子切换 KB active generation/profile；
5. 保留旧 generation 至回滚窗口结束；
6. 创建异步清理任务。

Embedding 不可用时 job 进入可重试失败；不得将词法检索标为成功。可选 hybrid search 必须作为显式 retrieval strategy，而不是静默 fallback。

## 8. 错误合同

| 错误码 | 阶段 | 可重试 |
|---|---|---|
| `FILE_EMPTY` | validate | 否 |
| `FILE_TOO_LARGE` | preflight | 否 |
| `BATCH_LIMIT_EXCEEDED` | preflight | 否 |
| `PATH_INVALID` | preflight | 修正后 |
| `MIME_UNSUPPORTED` | validate | 否 |
| `OBJECT_CHECKSUM_MISMATCH` | upload complete | 是，重传 |
| `CONVERSION_FAILED` | convert | 按原因 |
| `PARSER_ENCRYPTED` | parse | 提供密码功能不在本轮；否 |
| `PARSER_CORRUPT` | parse | 重传后 |
| `OCR_FAILED` | parse | 是 |
| `EMBEDDING_RATE_LIMITED` | embedding | 是，退避 |
| `EMBEDDING_DIMENSION_MISMATCH` | embedding | 配置修复后 |
| `INDEX_ACTIVATION_FAILED` | indexing | 是 |
| `ACL_REVOKED` | 任意 | 否；终止 |

错误详情必须脱敏，不包含原始文件内容、密钥或上游完整响应。

## 9. 上传中心 UI

- 顶部：模式、KB、文件数、总大小、总体进度、成功/失败数、暂停新增/取消等待项。
- 文件行：路径、类型、大小、当前 stage、字节/stage 进度、用时、错误与重试。
- 支持按状态筛选、展开失败详情、复制 job/request id、仅重试失败项。
- 导航和刷新后通过 batch id 恢复；运行任务进入全局 Upload Center。
- Success 只有后端 `SUCCEEDED`；Uploaded 不显示为知识库可用。

## 10. 权限与审计

| 操作 | 最低角色 | 审计 |
|---|---|---|
| 浏览/检索 | viewer | 可选访问事件，不含正文 |
| 上传/新版本/重试 | editor | batch、document/version、hash prefix、结果 |
| 改路径/删除 | editor（按 policy） | before/after path、batch id |
| 成员/Embedding profile/reindex | owner/admin | policy/profile/generation |
| 永久清理 | owner/admin | purge job/result |

Worker 不继承用户无限权限；job 保存发起人和授权快照，同时在激活版本前检查资源仍存在且 job generation 仍有效。

## 2026-08-14 implementation evidence — FR-015 legacy conversion (DONE-LOCAL)

FR-015 legacy Office conversion is implemented locally as an isolated `soffice` conversion boundary feeding the existing OOXML parsers. The conversion process uses `shell=False`, an isolated temporary input/output/profile directory, safe basenames, and a 30-second hard timeout. Missing executable, timeout, non-zero exit, or missing/empty output fail closed as `CONVERSION_FAILED`; a target parser failure is propagated with its original stable classification and is not converted into a success claim.

This is `DONE-LOCAL` evidence only. A real local XLS → XLSX → `XlsxParser` smoke was executed; this record does not claim that DOC or PPT were successfully parsed in a real smoke, and it does not pass the production or Phase exit gates.

## 11. 验收证据

必须至少证明 TXT、PDF、10+ 文件批次和多级目录的对象、版本、Chunk、Embedding、pgvector 数量与 UI 状态一致；详细矩阵在 `12-testing-plan.md` 与 `13-acceptance-criteria.md` 中定义。
