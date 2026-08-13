---
title: EKB 核心重构需求规格
status: Ready for Plan
tier: 3
type: feature
authoritative: true
scope: ekb-core-rebuild
version: 0.1
date: 2026-08-12
owner: Sol
---

# EKB 核心重构需求规格

## 1. 产品目标

EKB 必须成为可供团队真实使用的“知识库 + AI 助手”系统：资料能够被可靠上传、版本化、解析、索引、检索、恢复和清理；AI 能够持续对话、使用附件和图片、选择真实模型，并在授权范围内使用一个或多个知识库回答。

功能优先级为：真实可用 > 架构合理 > 用户体验 > UI 美观。现状依据见 [00-current-state.md](./00-current-state.md)，目标架构见 [03-architecture.md](./03-architecture.md)。

## 2. 角色

| 角色 | 主要能力 |
|---|---|
| Tenant Owner/Admin | 团队 Provider、模型、默认 Embedding、ACL、Jobs、回收站和审计管理 |
| KB Owner/Admin | 管理指定知识库、成员、文档、版本、Embedding profile 和重建索引 |
| Editor | 上传、更新、整理和删除被授权 KB 的内容 |
| Viewer | 搜索、阅读并在助手中使用被授权 KB |
| 普通用户 | 管理个人会话、附件和个人 Provider |
| 运维/审计 | 查看任务、部署、失败、路由元数据和清理结果，不默认读取消息正文 |

## 3. 术语

| 术语 | 定义 |
|---|---|
| Source object | 对象存储中的不可变原始文件对象 |
| Document | KB 中由 `knowledge_base_id + relative_path` 标识的逻辑文档 |
| Document version | 一次不可变上传及其解析/索引产物 |
| Ingest job | 上传、解析、分块、Embedding、索引状态机实例 |
| Attachment | 属于用户/会话/消息的文件或图片资源，不等同于 KB |
| Conversation branch | 同一用户消息下的一个或多个 Assistant 回答分支 |
| Embedding profile | Provider、模型、维度、分块策略和版本的不可变组合 |
| Strict grounded | 只基于检索证据回答；证据不足时拒答 |
| Knowledge enhanced | 优先引用 KB，允许补充模型常识并明确分隔 |

## 4. 范围

### 4.1 本轮范围

- 统一 Light/Dark Theme System。
- 可靠的单文件、批量和目录上传，真实进度、解析、索引、重试和版本管理。
- Conversation/Message/Branch/Attachment 持久化、Streaming、Stop、Retry、Regenerate 和上下文治理。
- 文件理解、图片理解、OCR/Vision fallback。
- Provider/Model 管理与助手选择器统一。
- 0..N KB RAG、两种回答模式、引用持久化和 ACL。
- 内容回收站 30 天闭环、可靠 Scheduler/Worker 和 Jobs Center。
- PostgreSQL+pgvector、对象存储、可靠队列和可回滚迁移。
- 自动化、浏览器、生产部署和 grok-build 能力级对比验收。

### 4.2 明确不在范围

- 联网搜索及搜索引用。
- MCP、工具调用、代码执行、终端、Agent 编排和音频。
- 复制 grok-build Rust actor/CLI UI 或第三方项目控制面。
- 多地域、超大规模微服务拆分和跨租户公开共享。
- 未经许可审查直接复制第三方源码。

## 5. 功能需求

### 5.1 主题系统

- **EKB-CR-FR-001**：系统必须以语义 token 定义 background、surface、elevated、border、text、hover、active、selected、input、overlay、shadow 和状态色。
- **EKB-CR-FR-002**：Light/Dark 必须由单一主题状态驱动并持久化；刷新后保持，首次可跟随系统偏好。
- **EKB-CR-FR-003**：Card、Modal、Drawer、Tooltip、Dropdown、Table、Input、Sidebar 及第三方组件必须消费语义 token，不保留页面级主题分叉。
- **EKB-CR-FR-004**：所有交互状态必须在两种主题下保持可读、可辨和键盘焦点可见。
- **EKB-CR-FR-005**：新增代码不得引入未经允许的原始颜色；历史颜色需进入可追踪迁移清单。
- **EKB-CR-FR-006**：主题验收必须覆盖全部主要路由、弹层和错误/空/加载状态。

### 5.2 知识库、上传与索引

- **EKB-CR-FR-007**：用户必须能选择单文件、多文件或目录，并在确认前看到模式、数量、总大小、类型和相对路径。
- **EKB-CR-FR-008**：目录上传必须保留多级 `relative_path`；逻辑文档身份为 `knowledge_base_id + relative_path`。
- **EKB-CR-FR-009**：同一路径的新内容必须创建新版本；同内容重复请求必须幂等；不同目录同名文件必须共存。
- **EKB-CR-FR-010**：默认限制为 100MB/文件、1000 文件/批、5GB/批；服务端必须再次校验并返回逐项错误。
- **EKB-CR-FR-011**：上传必须直达或分片写入对象存储，API 不得把大文件完整常驻内存。
- **EKB-CR-FR-012**：批次和单文件必须显示 Waiting、Uploading、Uploaded、Parsing、Chunking、Embedding、Indexing、Success、Failed、Cancelled 状态与真实进度。
- **EKB-CR-FR-013**：失败必须包含稳定错误码、失败阶段、可读原因、request/job id 和可重试性。
- **EKB-CR-FR-014**：用户离开页面或刷新后，上传中心必须从服务端恢复批次、文件和进度。
- **EKB-CR-FR-015**：解析必须支持 PDF、TXT、Markdown、DOCX、XLSX、PPTX；旧 Office 格式经隔离的 LibreOffice 转换，不把转换成功等同解析成功。
- **EKB-CR-FR-016**：图片与扫描 PDF 必须经 OCR/版面提取生成可定位文本，并保留页码、区域和来源元数据。
- **EKB-CR-FR-017**：成功只在 source object、document version、chunks、embeddings、vector index 和数据库事务全部满足完成条件后产生。
- **EKB-CR-FR-018**：重试必须复用已持久化 source object；不可重试失败必须明确要求重新选择文件。
- **EKB-CR-FR-019**：每个 KB 必须绑定不可变 Embedding profile；更换模型/维度必须显式全量重建，不得静默混用或降级为词法检索。
- **EKB-CR-FR-020**：KB 成员权限至少包含 owner/admin、editor、viewer，并在上传、列表、检索、RAG、恢复和清理链路统一执行。

### 5.3 AI 对话

- **EKB-CR-FR-021**：用户必须能新建、切换、重命名、归档/删除会话并在刷新后恢复。
- **EKB-CR-FR-022**：每个会话可以保存默认的 0..N 个知识库；每次 Turn 必须快照实际 KB 版本、检索参数和模型路由。
- **EKB-CR-FR-023**：用户消息、Assistant 消息、停止后的部分消息、错误消息和分支必须持久化。
- **EKB-CR-FR-024**：响应必须流式返回，事件含 turn id、单调 seq、阶段、delta、citation、usage、heartbeat 和唯一终态。
- **EKB-CR-FR-025**：Stop 必须幂等、校验 tenant+actor 所有权，并把已生成内容保存为 `stopped` 部分消息。
- **EKB-CR-FR-026**：Retry 用于重新发送失败的用户 Turn；Regenerate 为同一用户消息创建新的 Assistant 分支，旧分支仍可访问。
- **EKB-CR-FR-027**：会话必须保存活动分支；切换分支后后续上下文只能沿活动链构建。
- **EKB-CR-FR-028**：第一轮成功完成后异步生成标题；失败时使用首个问题；用户编辑标题后自动标题不得覆盖。
- **EKB-CR-FR-029**：助手必须正确渲染 Markdown、表格、列表、引用和代码块，并安全处理 HTML/链接。
- **EKB-CR-FR-030**：上下文构建必须有 Token Budget，计入系统提示、历史、摘要、附件、图片、RAG 证据和输出预留。
- **EKB-CR-FR-031**：长会话必须采用保留最近完整 Turn + 有来源范围的摘要 + 有界截断兜底，不得产生孤立消息或破坏分支。
- **EKB-CR-FR-032**：模型/Provider 失败与内部授权、迁移或数据错误必须区分；不得以假回答降级。
- **EKB-CR-FR-033**：联网搜索控件、运行路径、配置和搜索引用必须从本轮助手范围移除。

### 5.4 附件与图片

- **EKB-CR-FR-034**：聊天附件必须有独立 `attachment_id`、owner、tenant、source object、mime、size、hash、状态和保留期。
- **EKB-CR-FR-035**：PDF、DOCX、TXT、Markdown、XLSX 和图片附件必须被实际读取，不能只显示文件名。
- **EKB-CR-FR-036**：小型文本附件可在预算内直接内联；大型附件必须形成持久解析产物和会话级临时检索索引。
- **EKB-CR-FR-037**：原生支持 Vision 的模型优先接收合规图片；不支持时使用 OCR/描述 fallback，并向用户披露处理方式。
- **EKB-CR-FR-038**：PNG、JPG、JPEG、WebP 必须支持；尺寸、像素和格式必须服务端校验。
- **EKB-CR-FR-039**：附件必须绑定消息并随会话恢复；撤权、软删、过期或处理未完成的附件不得进入上下文。
- **EKB-CR-FR-040**：用户可以显式把附件提升到有权限的 KB；提升必须走正常版本、解析、索引和审计流程。

### 5.5 Provider 与模型

- **EKB-CR-FR-041**：系统必须原生支持 OpenAI、Anthropic、Gemini，并提供受控 OpenAI-compatible adapter。
- **EKB-CR-FR-042**：兼容层必须覆盖系统配置允许的 DeepSeek、Groq、xAI、OpenRouter、Ollama 和 Azure OpenAI 差异，不假设能力完全相同。
- **EKB-CR-FR-043**：Provider 分个人私有和管理员团队两类；团队模型受租户数据外发策略约束。
- **EKB-CR-FR-044**：模型服务必须是助手模型列表唯一数据源，只有 enabled 且 capability/credential 有效的模型可选。
- **EKB-CR-FR-045**：删除/禁用当前模型时必须按显式 fallback chain 切换或阻止发送，不能静默换模型。
- **EKB-CR-FR-046**：每次 Turn 必须保存请求和实际使用的 provider/model、fallback 原因、token、延迟和状态；UI 必须披露实际路由。
- **EKB-CR-FR-047**：Provider 密钥必须加密存储、响应脱敏、支持轮换，并禁止进入日志、审计 metadata、浏览器存储和项目记忆。
- **EKB-CR-FR-048**：模型能力必须表达 chat、streaming、vision、embedding、context window 和文件限制；未知能力默认 fail closed。
- **EKB-CR-FR-049**：Provider 列表必须使用合法来源的真实 Logo，并有统一尺寸、主题适配、加载和 fallback。

### 5.6 RAG

- **EKB-CR-FR-050**：每个 Turn 可选择 0..N 个有权限的 KB；服务端必须忽略客户端伪造的资源范围并重新授权。
- **EKB-CR-FR-051**：Strict grounded 模式证据不足必须拒答；Knowledge enhanced 模式必须明确区分知识库证据和模型常识。
- **EKB-CR-FR-052**：检索结果必须携带 document/version/chunk 以及适用的 page、sheet、paragraph、path 元数据。
- **EKB-CR-FR-053**：消息引用必须持久化，刷新、切换分支和审计时仍可定位到生成时的版本快照。
- **EKB-CR-FR-054**：被删除、撤权、重建中或 embedding profile 不兼容的内容不得进入新 Turn 检索。

### 5.7 回收站、任务与部署

- **EKB-CR-FR-055**：KB、文档、文档版本、会话和附件进入 30 天回收站；Provider/Model 采用 disable+audit。
- **EKB-CR-FR-056**：删除必须原子写入 `deleted_at` 和 `expires_at=deleted_at+30 days`；30 天内可恢复。
- **EKB-CR-FR-057**：Scheduler 必须自动领取到期清理任务，幂等永久删除业务内容/对象/索引并保留审计。
- **EKB-CR-FR-058**：Jobs Center 必须展示队列、运行、失败、dead、重试、取消、worker heartbeat 和清理结果。
- **EKB-CR-FR-059**：开发、测试和验收必须使用真实服务链路；禁止固定 success、随机进度、写死模型和假回复。
- **EKB-CR-FR-060**：每个实施阶段必须在门禁通过后备份、迁移演练、原子发布、健康检查和浏览器验证；失败不得切换生产。

## 6. 非功能需求

- **EKB-CR-NFR-001 安全隔离**：所有资源读写必须同时约束 tenant、actor 和资源 ACL；越权响应不得泄露资源存在性。
- **EKB-CR-NFR-002 可靠性**：摄取、清理和模型调用状态转移必须幂等；进程重启后任务可恢复，不能永远停留 RUNNING。
- **EKB-CR-NFR-003 性能**：目标负载为约 200 用户、30 并发对话、2TB 原始资料；普通列表 p95≤400ms，SSE 内部首事件 p95≤1s，不含上游模型等待。
- **EKB-CR-NFR-004 可扩展性**：API、worker、scheduler、storage 和 vector 边界可独立扩容，但首期保持模块化单体控制面。
- **EKB-CR-NFR-005 数据完整性**：成功响应后立即读取必须可见；跨数据库、对象和索引的最终一致性必须由 job 状态和补偿任务表达。
- **EKB-CR-NFR-006 可迁移性**：迁移可重复验证、可中断恢复、可回滚到旧代码读取兼容 schema；不修改既有 checksum。
- **EKB-CR-NFR-007 可观测性**：所有 API、job、turn、provider call 和 cleanup 带 request/trace id、结构化状态、耗时和无敏感内容的错误。
- **EKB-CR-NFR-008 可访问性**：键盘可达、焦点可见、状态不只靠颜色、图标有名称、支持 reduced motion。
- **EKB-CR-NFR-009 主题质量**：Light/Dark 关键文字和交互对比度满足 WCAG AA；主要页面无明显主题残留。
- **EKB-CR-NFR-010 兼容性**：保留现有 `/api/v1`、app-v2 route/adapter 和 SSE v2 必要字段，破坏性变化只能经版本化端点。
- **EKB-CR-NFR-011 隐私**：消息正文只在受保护业务表；审计只存元数据、hash、路由、token、延迟和状态。
- **EKB-CR-NFR-012 供应链**：新增依赖必须记录目的、许可证、固定版本、SBOM 和替代方案；不整体复制第三方控制面。
- **EKB-CR-NFR-013 浏览器证据**：代码门禁不能替代真实浏览器；所有核心旅程必须在 QA tenant 执行并保存证据。
- **EKB-CR-NFR-014 发布**：生产原子切换目标中断≤5分钟；回滚不得丢失已提交业务数据和审计。

## 7. 固定产品决策

1. 对话默认支持普通模式；KB 与附件均为可选且彼此独立。
2. 会话保存默认 KB 集合，Turn 保存不可变实际快照。
3. Regenerate 使用分支，不覆盖历史 Assistant 消息。
4. Embedding profile 不可变；变化必须显式重建。
5. 模型 fallback 只能走管理员/用户可见的显式链。
6. 审计默认不保存消息正文。
7. 研究/Spec 阶段更新文档与记忆；实施阶段门禁通过才部署。

## 8. 外部前置条件

| 条件 | Owner | 不满足时行为 |
|---|---|---|
| 生产主机恢复稳定 DNS/HTTPS 出网或提供受管代理 | 云平台/用户 | 阻止真实 Provider、Embedding、Tunnel 验收和生产切换 |
| 提供各目标 Provider 的测试凭据 | 用户/管理员 | 对应 Provider 保持未验收，不使用 mock 代替 |
| 配置生产对象存储、队列和加密密钥 | 运维 | 摄取/附件/清理阶段不得切换 |

## 9. 完成定义

只有 [13-acceptance-criteria.md](./13-acceptance-criteria.md)（计划文档）中的条目有真实自动化、数据库/存储/向量证据和浏览器证据时才能完成；目前本文件状态为 Draft，不能据此勾选任何实现项。
