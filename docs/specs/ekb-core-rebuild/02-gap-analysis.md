---
title: EKB Core Rebuild Gap Analysis
status: Ready for Plan
tier: 3
type: feature
authoritative: true
scope: ekb-core-rebuild
version: 0.1
date: 2026-08-12
owner: Sol
---

# EKB Core Rebuild Gap Analysis

## 1. 严重程度

- **P0**：核心功能不可用、安全/数据完整性风险或上线硬阻塞。
- **P1**：严重影响完整性、稳定性或主要体验。
- **P2**：应完善但存在可接受的短期替代。
- **P3**：优化项或后续扩展。

## 2. 开源对标原则

源码级对标参考 RAGFlow、Dify、AnythingLLM、Open WebUI、LibreChat、LobeChat、Khoj、FastGPT、MaxKB、Cherry Studio、Onyx 与 grok-build；详细研究入口和许可判断见 [EKB 开源项目对标与复用移植评估](../../EKB开源项目对标与复用移植评估_v1.0_2026-08-11.md)。采用边界如下：

- EKB 保留租户、ACL、审计、Provider 和 UI 控制面。
- 借鉴可靠摄取、会话状态、模型抽象、引用和后台任务模式，不整体移植控制面。
- RAGFlow 只可作为固定版本 adapter PoC 候选；Onyx 只考虑许可清晰的 community 边界。
- grok-build 仅借鉴消息完整性、流式事件、上下文预算和压缩报告，不迁移 Rust actor、CLI、工具、代码执行或专有 API。
- 任何代码复用必须经过固定公开 commit、逐文件许可证、NOTICE/SBOM、安全和回归门禁。

### 2.1 复用登记门禁

| 候选 | 固定公开 commit | 许可/NOTICE | 允许范围 | 禁止范围 | 采用方式 | SBOM/审批 Owner |
|---|---|---|---|---|---|---|
| RAGFlow | 未选择；选择前阻止代码复用 | Apache-2.0，需逐依赖核验 | parser/retrieval adapter PoC | control plane、auth/schema 直接嵌入 | 外部 data-plane PoC | Sol 决策；Luna Max 生成 SBOM |
| Onyx | 未选择；选择前阻止代码复用 | community MIT；`ee` 非 MIT | connector/indexing/permission-sync 思想 | `ee`、企业许可代码 | 小模块参考/重写 adapter | Sol + license review |
| AnythingLLM | 未选择；选择前阻止代码复用 | MIT，仍需依赖闭包 | workspace/provider UX 模式 | 桌面/单用户 runtime 假设 | 交互参考 | Sol |
| Dify/FastGPT/Open WebUI | 未选择 | modified/custom/branding 条款 | 架构和 UX 研究 | 产品源码、品牌限制目录 | 仅借鉴 | Sol |
| MaxKB/Khoj | 不适用 | GPL/AGPL | 隔离研究 | 并入 EKB 产品进程 | 不采用产品代码 | Sol |
| grok-build | 本地 checkout 仅作研究；公开可取 commit 未锁定 | first-party Apache-2.0；third-party/ports 各自许可 | 状态完整性、压缩、typed parts 思想 | Rust actor、CLI、Agent/tool、vendored/ports 依赖闭包 | 模式参考，不复制源码 | Sol；无需 SBOM 直至代码复用提案 |

任何 `固定公开 commit=未选择` 的行都处于 `BLOCKED_FOR_CODE_REUSE`；PH0 若没有具体复用提案，不需要为了“完成表格”引入第三方代码。

## 3. Gap Analysis

| 模块 | EKB 当前状态 | 成熟项目实现 | 当前缺失 | 严重程度 | 建议方案 |
|---|---|---|---|---|---|
| 上传入口 | 浏览器并发调用单文件 API | 批次、文件清单、预签名/分片、可恢复状态 | 真实批次资源和刷新恢复 | P0 | `upload_batches/items` + 对象存储上传会话 |
| 目录上传 | 相对路径在前端存在但服务端丢失 | 目录路径作为文档身份/metadata | 多级目录、同名、版本规则 | P0 | `KB + normalized relative_path` 唯一键 |
| 大文件 | API 完整读入内存 | 流式/分片直传对象存储 | 内存和超时风险 | P0 | 预签名 multipart 或 API 流式落盘/对象存储 |
| 上传进度 | 粗粒度状态，无字节真值 | 字节+流水线阶段+失败原因 | Parsing/Embedding/Indexing 进度 | P1 | 服务端 job progress + SSE/poll projection |
| 原文件 | 未形成 durable source truth | 不可变 object、hash、版本引用 | 重试/审计/回滚原件 | P0 | content-addressed object + ref count |
| Parser | 新格式部分支持，旧格式 UI/后端不一致 | 沙箱转换、版面/页码元数据 | legacy conversion、OCR、错误分类 | P0 | parser registry + isolated LibreOffice/OCR worker |
| Chunk/Embedding | 有路径但完成条件不闭环 | 版本化 chunker、immutable embedding profile | 维度混用、重建和索引验证 | P0 | profile binding + build/swap index |
| Vector Store | 目标 pgvector，生产主库未切 | 事务 metadata + durable vector index | 生产真值与索引一致性 | P0 | PostgreSQL+pgvector canonical store |
| Ingest tasks | BackgroundTasks/进程内 | reliable queue、lease、retry、DLQ | 重启恢复、心跳、死任务 | P0 | Redis-compatible queue + worker lease |
| Conversation | 有会话/消息基础 | thread/message/branch 持久化 | 分支、标题锁、精确快照 | P1 | normalized conversation graph |
| Streaming | 有 SSE v2/seq/cancel | 明确事件合同、唯一终态、跨实例状态 | actor scope、断线语义、部分消息 | P0 | turn state table + tenant/actor CAS |
| Stop | 部分 cancel | idempotent cancel + persisted partial | 同租户跨用户风险 | P0 | ownership verifier + `stopped` message |
| Retry/Regenerate | 不完整 | retry failed turn、regenerate branch | 历史覆盖或无法追踪 | P1 | branch table and active branch pointer |
| Markdown | 部分页面按纯文本 | 安全 Markdown、代码高亮、复制 | 代码块/表格/链接安全 | P1 | audited renderer + sanitizer |
| Context | 简化文本压缩 | token-aware summary + recent complete turns | 附件/图片/RAG 成本、压缩报告 | P1 | versioned context assembly and report |
| Chat attachments | doc ids 混入 KB ids | 独立 attachment resource + lifecycle | owner/ACL/version/status/retention | P0 | attachments + attachment_artifacts |
| Images/Vision | 无真实链路 | native multimodal + OCR fallback | capability routing、格式/尺寸校验 | P0 | capability-aware message parts |
| Attachment promotion | 缺失 | 显式导入 KB 并复用摄取管线 | 审计、版本和幂等 | P2 | promotion command referencing source object |
| Model list | DB 与 mock catalog 混合 | provider capability registry | 唯一数据源和真实同步 | P0 | providers/models canonical DB registry |
| Provider routing | OpenAI-compatible 混用风险 | native adapters + compatibility normalization | 选中 A 实际调用 B 的风险 | P0 | provider instance-bound client factory |
| Secrets | API key 保护合同不足 | envelope encryption/rotation/redaction | 明文/日志/审计风险 | P0 | credential vault abstraction + key version |
| Model fallback | 不完整或隐式 | explicit policy + disclosed route | 删除/禁用当前模型行为 | P1 | ordered fallback chain and audit |
| Provider logos | 默认图标/缺失 | licensed asset registry | 主题、尺寸、fallback | P2 | bundled approved SVG metadata registry |
| RAG selection | 有 KB 选择但附件混用 | 0..N resources, server-side ACL | 精确版本/参数快照 | P0 | turn_retrieval_scopes |
| RAG citations | 流内引用，持久化不足 | versioned citations and evidence viewer | 刷新后不可证明来源 | P0 | message_citations normalized rows |
| Strict mode | 缺少明确拒答合同 | grounded threshold/refusal | 证据不足仍回答 | P1 | retrieval gate and answer mode policy |
| KB ACL | 部分链路存在 | resource ACL at every command/query | 上传、RAG、恢复不一致 | P0 | shared authorization service |
| Theme | 变量+散落 dark overrides | semantic token/component themes | 约 439 处 raw colors、第三方残留 | P1 | token layers + lint + route matrix |
| Trash | soft delete/投影存在 | scheduler + purge workflow | 30 天不自动清理 | P0 | cleanup scheduler and idempotent purge jobs |
| Jobs Center | 缺失 | queue visibility/retry/cancel/DLQ | 运维不可诊断 | P1 | admin job projections and commands |
| Migration ledger | 版本/ownership 漂移 | immutable ordered ledger + expand/contract | 重复版本、create_all 越界 | P0 | ADR + additive canonical migrations |
| API startup | migration 失败可能被吞 | fail-closed migration job | 坏 schema 仍启动 | P0 | separate migration gate, nonzero blocks rollout |
| Main database | 生产业务仍在 SQLite | PostgreSQL canonical | 并发、向量、备份双真值 | P0 | rehearsed data migration + atomic DSN switch |
| Object storage | 未进入 Compose/生产闭环 | S3-compatible durable store | 版本、重试、清理 | P0 | S3 adapter and lifecycle/ref counting |
| Queue/Worker | 未部署 | dedicated workers/scheduler | 摄取/清理不可恢复 | P0 | reliable queue, leases, heartbeat, DLQ |
| Audit | 有基础日志 | metadata-only immutable audit | 路由/任务/清理覆盖不全 | P1 | shared audit event schema |
| Browser evidence | 构建/API 证据为主 | real journey automation + screenshots | UI 与真实后端不一致不可见 | P0 | dedicated QA tenant and browser matrix |
| Grok parity | EKB 有基础 SSE，交互不完整 | stable multi-turn/branch/attachment UX | 能力级差距 | P1 | same-model scenario comparison, not text equality |
| Deployment | 本机服务可用但出网中断 | staged atomic release/rollback | Provider、Embedding、域名无法验收 | P0 | restore managed egress before production gate |

## 4. 可关闭 Gap Registry

下表是 PH7 “P0/P1/P2=0”的权威关闭集合；上方详细表是证据说明。

| Gap ID | 范围 | Severity | Requirement / AC | Primary Phase | Owner | Status | Planned evidence |
|---|---|---|---|---|---|---|---|
| CR-GAP-001 | migration/主库/启动 fail-closed | P0 | EKB-CR-NFR-005/006；AC-CR-NFR-005/006 | PH1/PH8 | Luna Max；Sol gate | Open | migration/cutover reports |
| CR-GAP-002 | object storage/queue/worker/scheduler | P0 | EKB-CR-FR-011/057/058 | PH1 | Luna Max | Open | runtime integration/chaos |
| CR-GAP-003 | upload batch/path/version/progress | P0 | EKB-CR-FR-007–014 | PH3 | Luna Max | Open | KB-E2E-03–06 |
| CR-GAP-004 | parser/OCR/legacy conversion | P0 | EKB-CR-FR-015/016 | PH3/PH5 | Luna Max | Open | parser/ATT fixtures |
| CR-GAP-005 | embedding profile/pgvector/reindex | P0 | EKB-CR-FR-017–019 | PH3 | Luna Max | Open | KB-E2E-01/02/08 |
| CR-GAP-006 | KB ACL 全链路 | P0 | EKB-CR-FR-020/050/054；EKB-CR-NFR-001 | PH1/PH3/PH6 | Luna Max | Open | tenant/ACL negative suite |
| CR-GAP-007 | turn actor scope/SSE/Stop | P0 | EKB-CR-FR-024/025 | PH1/PH4 | Luna Max | Open | CHAT-E2E-02/03/08 |
| CR-GAP-008 | conversation branch/context/Markdown | P1 | EKB-CR-FR-021–032 | PH4 | Luna Max | Open | CHAT-E2E-01–09 |
| CR-GAP-009 | attachment/file/image/Vision | P0 | EKB-CR-FR-034–040 | PH5 | Luna Max | Open | ATT-E2E-01–08 |
| CR-GAP-010 | Provider routing/secrets/selector | P0 | EKB-CR-FR-041–049 | PH1/PH2 | Luna Max | Open | PROVIDER-E2E-01–05 |
| CR-GAP-011 | RAG modes/citations/version snapshot | P0 | EKB-CR-FR-050–054 | PH6 | Luna Max | Open | RAG scenario suite |
| CR-GAP-012 | semantic Theme/Dark Mode | P1 | EKB-CR-FR-001–006；EKB-CR-NFR-008/009 | PH2/PH7 | Luna Max | Open | THEME-E2E-01–04 |
| CR-GAP-013 | 30-day trash/Jobs Center | P0 | EKB-CR-FR-055–058 | PH1/PH2 | Luna Max | Open | TRASH-E2E-01–05 |
| CR-GAP-014 | real browser/grok comparison | P0 | EKB-CR-FR-059；EKB-CR-NFR-013 | PH7 | Sol QA gate | Open | GROK-CMP-01–06/browser manifest |
| CR-GAP-015 | production egress/atomic deployment | P0 | EKB-CR-FR-060；EKB-CR-NFR-014 | PH8 | Ops/user + Sol gate | Blocked external | production rehearsal |

## 5. 优先级结论

### 5.1 P0 先决链

1. 冻结迁移 ledger、修复 fail-closed 启动和 PostgreSQL 迁移演练。
2. 上线对象存储、可靠队列、worker、scheduler 和基础 Jobs Center。
3. 重建上传/摄取状态机和文档版本真值。
4. 修复 Turn actor scope、附件资源模型、Provider 路由和密钥保护。
5. 完成 RAG 引用/ACL、30 天清理和真实浏览器门禁。

### 5.2 P1 完整性链

会话分支、上下文摘要、模型 fallback、上传中心、Markdown、主题系统和作业可观测性在 P0 基础稳定后完成；它们不是纯 UI polish。

## 6. 不采用的方案

- 不把 RAGFlow/Dify/AnythingLLM 整体替换 EKB。
- 不继续用 BackgroundTasks 承载长任务。
- 不用前端随机进度或轮询 API 200 推断完成。
- 不在一个 vector index 混用多个 embedding 维度或模型。
- 不将聊天附件继续编码为 KB ID。
- 不为实现 Vision 随意引入完整 Agent/MCP 栈。
- 不在出网不可用时用 mock 宣称真实 Provider 验收通过。
