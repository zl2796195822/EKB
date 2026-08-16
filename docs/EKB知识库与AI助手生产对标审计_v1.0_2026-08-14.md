---
title: EKB 知识库与 AI 助手生产对标审计
date: 2026-08-14
status: 已完成只读审计
scope: production knowledge-base and AI-assistant
comparison_basis: 2026-08-11 GitHub 12-repository snapshot
---

# EKB 知识库与 AI 助手生产对标审计

> 结论：部署可登录、HTTP 健康检查通过，但知识库和 AI 助手尚未达到可作为核心产品交付的生产闭环。当前最大问题不是缺少页面，而是运行时配置、授权投影、迁移和数据平面之间存在断链。

## 1. 审计范围与方法

- 审计时间：2026-08-14，生产环境只读检查。
- 检查了管理员页面、容器健康与启动日志、PostgreSQL schema/migration ledger、无敏感正文的计数和状态，以及本仓库知识库、检索、上传、问答和模型配置实现。
- 未创建知识库、未上传文件、未发送测试提问、未改动生产数据库或配置；因此对写入路径的结论只在代码、配置和现有状态可证明的范围内成立。
- 对比对象沿用 `EKB开源项目对标与复用移植评估_v1.0_2026-08-11.md` 的 12 个 GitHub 仓库快照，不把其 star、体量或 UI 当作完成标准。

## 2. 生产事实与判断

| 证据 | 观察结果 | 判断 |
|---|---|---|
| 可用性 | Web 与 `/healthz` 均返回 200；`ekb-api` 容器处于 healthy | 基础部署可达，不代表核心业务可用 |
| 管理员可见知识库 | 管理员页面显示 0 个已授权空间/文档；数据库有 1 个 PRIVATE 知识库、5 篇 READY 文档、13 个切片 | **P0 授权/种子数据断链**：现有 KB membership 不属于当前管理员；当前管理员也没有 tenant membership 投影 |
| 模型运行时 | `llm_providers`、`llm_models`、`provider_credentials`、`model_route_events`、`embedding_profiles` 均为 0；运行容器没有 LLM、embedding、对象存储、OCR/Vision、Redis 的相应配置项 | **P0 助手不可用**：QA 会返回 `LLM_PROVIDER_NOT_CONFIGURED`，而非产生真实回答 |
| 向量检索 | PostgreSQL 仅有 `plpgsql`，未启用 `pgvector`；`chunks.embedding` 是 JSON；仅有 tenant/kb/doc B-tree 索引 | **P0 检索不具备规模能力**：检索代码会拉取全部授权 chunks，在 Python 内做 cosine/BM25/RRF；没有向量 ANN 索引或数据库级相似度过滤 |
| 上传与摄取 | `source_objects`、upload batch/session/item、ingest stage、index generation、background job、worker heartbeat 均为 0；对象存储未配置 | 旧文档可被标记 READY，但不可证明原件、版本、重试、异步解析和重建索引有可靠生产闭环 |
| 对话与引用 | `conversations`、`messages`、`qa_turns` 均为 0；生产 ledger 只有 v3 到 `v3_008_content_hierarchy`，没有 v4 对话/引用迁移；`messages.citations` 列与 `message_citations` 表均不存在 | **P0 对话与引用不可验证**：当前源码的引用持久化会在 schema 不匹配时告警并降级，不能把 SSE UI 视为可追溯引用能力 |
| 启动迁移 | 容器启动日志重复出现 `TypeError: main() takes 0 positional arguments but 1 was given`；entrypoint 仍调用旧 v3 runner 方式 | **P0 部署不可靠**：容器 healthy 但不能证明重启后迁移、verify 和 schema 一致性正确执行 |

## 3. 知识库核心差距

1. **先修可见性，再谈功能**：PRIVATE KB 的唯一 membership 与当前管理员不匹配，管理员页面无可用空间。应通过一次可审计、可回滚的修复迁移/管理命令校正现有数据，并加覆盖“fresh admin + legacy KB + private ACL”的回归测试；不能靠前端显示或放宽 ACL 绕过。
2. **真实数据平面尚未落地**：当前 JSON embedding + 全量 Python 评分只适合极小语料。应启用 pgvector，建立 versioned embedding profile、HNSW/IVFFlat 方案、混合召回和 metadata/ACL pre-filter；用 `EXPLAIN ANALYZE` 与 1k/10k/100k chunk 梯度压测验收。
3. **原件与摄取链路未闭环**：生产没有 object store、upload/session/source-object 状态、worker heartbeat 或 index generation 事实。上传、解析、embedding、索引、重试、取消和删除必须由 durable object storage + job/outbox + 独立 worker 承担，不能依赖 Uvicorn `BackgroundTasks`。
4. **解析能力仍偏基础文本提取**：现有 PDF/Office/Markdown/TXT 基线具备价值，但扫描 PDF OCR、复杂布局、表格、图片、网页/邮件、增量同步和解析质量评估不足。RAGFlow 的 DeepDoc/任务模型是最有价值的外部 data-plane PoC 候选。
5. **连接器和权限同步没有生产证据**：表和接口方向存在，但当前没有 source/index/worker 数据。应优先定义 source cursor、ACL snapshot、撤权删除、断点恢复与失败隔离，并以 Onyx community 的 connector/permission-sync 作为参考，不复制 enterprise 代码。

## 4. AI 助手核心差距

1. **模型服务为零是阻塞项**：没有 provider、model 或 credential，模型选择、深度思考、附件/Vision 的 UI 与源码不能代表可用能力。先通过 AI 模型配置中心接入一个远程 chat provider 和一个 embedding provider，再将 UI capability 严格绑定实际配置。
2. **没有任何生产对话样本或 Turn**：无法证明多轮上下文、SSE 首 token、心跳、取消、重连、retry/regenerate、分支与错误恢复。现有代码设计不能替代一次可审查的生产 canary。
3. **引用的不可变性未落库**：v4 对话图、turn resource snapshot 和 message citation schema 没有在生产 migration ledger 中出现。回答必须保存“本次实际使用的 KB/doc-version/chunk/model/prompt/attachment”，并能在文档更新后回放引用，而不是只在 SSE 当前帧中展示。
4. **RAG 质量没有生产评估门禁**：历史离线数字不足以覆盖当前 PG/配置/ACL。需要租户隔离、权限撤销、无证据拒答、引用正确率、跨文档问题、表格/扫描件、长对话、附件、模型故障和 prompt injection 的版本化评估集与持续回归。
5. **模型路由、成本与降级未被验证**：fallback policy、route event 和 embedding profile 为零。生产应记录非敏感 route/cost/latency/finish-reason，并让模型、embedding、rerank、OCR 失败产生可操作的状态而非仅 toast 或通用错误。

## 5. 与 12 个项目的针对性对比

| 仓库 | EKB 已有基础 | 当前未达到的关键能力 | 建议借鉴方式 |
|---|---|---|---|
| RAGFlow | 基础解析、chunk、引用约束 | layout/table/OCR 解析、异步 data plane、可扩展 hybrid retrieval | Apache-2.0 外部服务 PoC；用 adapter 注入 ACL、引用和审计 |
| Onyx | tenant/RBAC/KB ACL 基线 | connector 生命周期、permission sync、撤权后的索引新鲜度 | 仅参考 MIT community connector/indexing 设计 |
| Dify | SSE、provider 管理代码 | provider/knowledge/workflow 的实际运行与评估闭环 | 仅借鉴分层和运营模型，不复制产品代码 |
| Flowise | Apps/工具入口方向 | 受控 workflow、tool sandbox、run audit | 作为独立 workflow service 评估，不嵌入 credential/UI |
| MaxKB | 文档与知识库基础对象 | 面向业务人员的可操作数据集、上传后状态可见性 | 仅借鉴产品流程；GPL 不进入 EKB 分发物 |
| FastGPT | 资料集与对话基础模型 | dataset processing、workflow 与评估运营 | 仅借鉴；modified license/白标限制需隔离 |
| Open WebUI | 会话页、附件/模型 UI 方向 | 已配置模型下的真实 workspace/chat/file 体验 | 仅借鉴；当前许可与 branding 限制不适合直接移植 |
| rag-template | API/RAG 最小骨架 | 最小成功路径仍未在生产打通：KB 可见、embedding、回答、引用 | 用作部署/验收的下限参照 |
| AnythingLLM | workspace、provider 管理的产品方向 | workspace 内的真实 document-to-answer 闭环 | MIT 范围内借鉴 UX 与 provider 边界 |
| 53AIHub | 平台目录与运营页面 | 多 provider/知识库运营的真实状态与成本证据 | 仅借鉴运营能力；modified license 不复制 |
| Khoj | 搜索/对话代码方向 | 可用 provider 后的搜索、记忆和实际对话数据 | AGPL 不并入产品进程；仅隔离研究 |
| Quivr | RAG domain/service 分层 | versioned corpus、citation、connector/QA service 的生产验证 | Apache-2.0 范围内参考服务边界和测试方法 |

## 6. 修复顺序与可验收结果

### P0：恢复可用的最小核心路径

1. 修复部署 entrypoint：只运行显式、兼容 CLI 契约的 migration runner，并在启动前执行 verify；失败必须使容器不进入 healthy。不可修改已应用 migration checksum。
2. 使用审计化管理命令修复管理员的 tenant/KB membership 投影，保留 PRIVATE ACL；fresh login 后管理员应看到已有知识库与 5 篇文档。
3. 配置一个远程 chat provider 与 embedding provider，凭据只经受控配置接口/密钥注入保存；`GET /qa/capabilities` 必须返回真实模型，不能返回 UI 假能力。
4. 在内部 canary 执行三条不含敏感信息的问答：有证据回答、无证据拒答、权限拒绝。三者均有引用/turn/audit 记录。

### P1：把知识库变成可靠 data plane

1. 启用 pgvector，迁移 JSON embedding，建立 ANN + tenant/KB/ACL pre-filter 索引和可重复的质量/性能基准。
2. 部署 S3/MinIO 兼容对象存储、上传 checksum/版本、outbox、ingest worker、重试/DLQ 和 worker heartbeat；上传在 hard reload 后能恢复真实状态。
3. 应用缺失的 v4 conversation/citation migrations，以 expand/backfill/switch/contract 方式落地；迁移后验证旧 API 兼容、引用回放和 rollback dry-run。

### P2：质量、连接器与助手产品化

1. RAGFlow adapter PoC：限定解析/检索 data plane，不转移 tenant、ACL、审计和引用责任；以固定版本、SBOM、许可证与评估集为门禁。
2. 扩展解析与连接器：扫描 PDF OCR、布局/表格、Web/Drive/工单、ACL sync、撤权回归、增量 cursor。
3. 建立持续评估与观测：检索 recall、citation correctness、拒答、TTFB/P95、cost、route failure、token/embedding、prompt injection、跨租户负向测试。

## 7. 生产验收门槛

只有同时满足以下条件，才能称知识库与 AI 助手“已完成”：

- 管理员和普通成员均只能看到各自被授权的 KB；迁移前遗留数据也符合该规则。
- 文件从上传到 READY 的每一步有 durable object、job、attempt、stage、可重试错误和 worker 证据。
- 检索使用真实 embedding + pgvector 索引，不在应用进程扫描全量 corpus；质量和性能阈值有版本化证据。
- 至少一个模型和 embedding provider 在生产 canary 可用，模型能力、失败状态与 UI 完全一致。
- 每个回答有可回放的 turn、引用、文档版本、授权范围和模型路由记录；无证据/越权/模型故障均 fail closed。
- 生产 `schema_migrations` 与已部署代码一致，启动 verify、备份恢复、浏览器 E2E 和安全回归通过。

本次没有复制第三方源码。RAGFlow、Onyx、AnythingLLM 和 Quivr 的复用仍须在固定 commit、逐文件许可证、SBOM、来源标注、安全审查和回归后才可进入实现。
