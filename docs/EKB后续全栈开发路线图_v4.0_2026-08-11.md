---
title: EKB 后续全栈开发路线图
date: 2026-08-11
status: Sol审核通过
review_status: 已审核
tier: 3
type: roadmap-proposal
owner: Sol
proposal_basis: docs/EKB开源项目对标与复用移植评估_v1.0_2026-08-11.md
---

# EKB 后续全栈开发路线图 v4.0

> 状态：Sol审核通过（已审核）。本文件是后续全栈路线提案，不是已批准实现证据、上线承诺或法律意见；不替代已批准的 v3 requirements/spec/plan/matrix，也不代表用户审阅了最终文字。

## 1. 路线图原则

- EKB 保持模块化单体：身份、租户、业务 API、RAG adapter、审计和前端先在一个可部署单元内演进；只有容量、故障域或团队边界有证据时才拆服务。
- 先修复文档/迁移/代码漂移，再扩大功能面。已应用 migration 的 checksum 永不修改；所有新 schema 通过显式、幂等、可验证的 ledger 管理。
- 每个阶段都必须穿过 migration、FastAPI service/router、app-v2 adapter/page、测试和发布门禁；核心动作不得用 `disabled`、`unavailable`、静态计数或假 toast 结束。
- GitHub 复用采用外部服务和窄 adapter 优先。任何代码移植必须固定 commit、保留来源、通过 SBOM/许可证和安全审查。
- 代码量不是交付指标。验收以真实业务能力、NFR、质量、安全、可观测性和回滚证据为准。

## 2. 当前基线与 P0 阻塞

净业务源代码约 **36,510 行**（API source+tests+web source）；`apps/` 约 47,211、全仓约 294,124 是含文档/生成物/证据的 gross 粗略 `wc`，GitHub `size` 是 KiB，不是 LOC。v3.1 已记录 Phase 0–2 完成，Phase 3 analytics 和 Phase 4 apps 待做；Team、Assistant、Knowledge、Documents 仍有核心动作占位。

当前必须先处理的漂移：v3 authority 期望 `v3_003_assistant` → `v3_004_analytics` → `v3_005_apps`，代码 runner 实际为 identity/content/`v3_003_analytics`；`init_db()` 仅自动 apply 001/002；`v3_002_content` 只有 `trash_items`；analytics 后端已出现 4 个 endpoint，但 `AnalyticsPage` 仍消费旧 admin ops dashboard 并显示 unavailable，`apps/web/src/lib/api.ts` 没有 analytics 方法，且未发现对应 `v3_003` 测试；`record_access` 使用 SQLite 专属 `INSERT OR IGNORE`，尚不能作为 PostgreSQL 生产证据；AppsPage 仍为静态占位。当前仍有 SQLite/JSON embedding、对象存储未落地、Redis 仅规划/refresh revoke 主要为进程内路径、无应用迁移/服务/路由，以及助手/团队/内容组织等缺口。任何实现前先读取各环境 `schema_migrations`，根据“未应用可重排、已应用保留并 ADR 映射”的规则行动。

## 3. 阶段总览

| 阶段 | 目标 | 主要模块 | 建议工期/团队 | 依赖 |
|---|---|---|---|---|
| P0 | 文档、迁移、代码事实统一 | authority index、schema ledger、ADR、compatibility inventory | 3-5 个工作日；Sol + 1 后端 + 1 QA | 当前工作区只读盘点、数据库访问 |
| P1 | 生产基础数据与运行时 | PostgreSQL/pgvector、S3/MinIO、Redis、session/cache、后台任务 | 2-3 周；2 后端 + 1 DevOps + 1 QA | P0 ADR、可用 PG/对象存储/Redis |
| P2 | 十页内容/助手/分析/应用闭环 | folders/tags/favorites/shares、SSE/attachments、analytics、apps、shell | 4-6 周；2 全栈 + 1 后端 + 1 QA/设计 | P1 数据与任务基础、v3 合同 |
| P3 | 企业身份与连接器平台 | OIDC/SAML/LDAP/SCIM、connector framework、ACL sync | 4-6 周；2 后端 + 1 安全 + 1 QA | P1 稳定身份/session、P2 资源 ACL |
| P4 | 平台化交付与规模治理 | workflow/plugin、SDK/CLI、Helm/K8s、灾备、压测、成本治理 | 6-10 周；3-5 人平台小队 | P1-P3 稳定 API、容量和安全证据 |

## 4. P0：文档与迁移一致性

### 4.1 目标与工作包

1. 建立非敏感 migration provenance：读取 SQLite/PG 的 `schema_migrations`，记录版本、checksum、应用时间和环境标识。
2. 对照 v3 `01_requirements`、`02_spec`、`03_plan`、`04_verification-matrix` 与 v3.1 执行记录，输出漂移 ADR：版本命名、runner 顺序、自动 apply 范围、content/analytics/apps 所有权。
3. 补齐兼容性清单：旧 `/kb`、`/conversations`、`/qa`、`/admin` 响应；新列表 envelope；app-v2 adapter facade；十页核心动作状态。
4. 形成 GitHub 依赖候选的 license inventory、SBOM 计划和固定 SHA 规则，但不引入第三方代码。

### 4.2 门禁与退出标准

- Migration：fresh DB 与现有 DB 的版本/checkout 矩阵明确；已应用 checksum 全部匹配；rollback 只 dry-run，不删除审计。
- API/UI：现有兼容合同有自动化清单；占位动作有 owner 和阶段；没有新增 endpoint 只存在于文档。
- 测试/发布：`ruff check`、既有 pytest、web build、文档链接检查通过；P0/P1/P2 security gate 未关闭前不得进入实现扩展。
- 退出：Sol 批准 ADR；每个未决漂移有“保留/重排/兼容迁移”结论和回滚说明。

## 5. P1：PostgreSQL、对象存储与运行时基础

### 5.1 模块与实施顺序

1. **PostgreSQL 原生 pgvector**：将 SQLite 方言、事务隔离、连接池和索引计划抽象为兼容 repository；生产环境 pgvector 初始化失败必须 fail closed。
2. **S3/MinIO**：原文、版本文件、解析中间产物使用 object_ref；签名 URL、大小、checksum、保留与删除策略由 EKB 控制。
3. **Redis**：session revoke/policy version、短期答案缓存和 rate/quota 计数；缓存键必须绑定 tenant、subject、ACL/policy、知识版本、模型/策略版本。
4. **session/cache**：Bearer/API key 的 live membership 解析、撤销、刷新和跨实例一致性；不信任旧 token capability。
5. **后台任务**：在模块化单体内使用受控 worker/任务表（可由 Redis 驱动），承载 ingest、embedding、purge、sync；每个任务有幂等键、重试、死信可观测状态，暂不引入新微服务。

### 5.2 门禁、风险与退出

| 门 | 必须证明 | 风险/缓解 |
|---|---|---|
| Migration | additive columns/tables、双数据库 verify、checksum、forward/rollback dry-run | 方言差异；先跑 SQLite+PG fixture，再允许灰度 |
| API | 写后读同事务可见、分页稳定、request id、错误码兼容 | 旧客户端破坏；新 path/envelope 保留旧 path |
| UI | upload/version/status/loading/error/retry 在 hard reload 后恢复 | 对象存储暂不可用；显示真实 provider 状态，不伪造成功 |
| Test | pytest、adapter contract、权限/secret regression、迁移重复执行、任务幂等 | 并发竞态；增加多租户和重试 fixture |
| Release | Compose canary、PG backup/PITR、S3 restore、Redis failover、旧代码忽略新表回退 | 数据不可逆；先演练 RPO/RTO，保留 feature flag |

**退出标准**：PG/pgvector、S3/MinIO、Redis 在测试和一套内部灰度环境可用；QA P95、Search P95、QPS 和 RPO/RTO 重新基线；无未解释的 500、越权或 secret 泄露。

## 6. P2：十页全栈闭环

### 6.1 内容治理

- 迁移/服务/API/UI 补齐 folders、tags、favorites、shares、`file_size`、`object_ref`、版本/diff、批量操作和同批次 trash restore。
- 所有资源访问同时检查 tenant、主体 capability 和对象 ACL；游标绑定 tenant/subject/query，父级删除恢复返回明确 `PARENT_DELETED`。

### 6.2 Assistant 与 RAG adapter

- 项目、成员、附件、模型 capability、联网 provider、SSE v2 cancel/citation/feedback 走 adapter facade；`qa_turns` 不冒充 replay log。
- RAGFlow 仅作为 data plane 候选；EKB 保留 prompt policy、出域、引用和审计。所有 provider 错误与内部安全/契约失败分开统计。

### 6.3 Analytics 与 Apps

- Analytics：修复 `days`，补日期范围、timeseries、distribution、access events、health、retention、EXPLAIN/p95；`record_access` 必须使用跨 SQLite/PG 的冲突语义，不保留 `INSERT OR IGNORE`。
- Apps：六个固定目录条目，install/configure/connect/run/uninstall 独立状态；凭据用专用 `EKB_APP_CREDENTIAL_KEY` 加密，响应只出现一次明文，sync 与 install 分离。

### 6.4 十页与门禁

Dashboard、Knowledge、Assistant、Documents、Team、Analytics、Apps、Recycle、Profile、Modules 均需真实 loading/empty/error/permission/success/stale 状态，desktop 与 390x844 双视口完成核心动作。页面只依赖 app-v2 adapters，不导入旧 UI/API/CSS。

### 6.5 退出标准

- v3 FR/NFR 矩阵每一项都有 API/DB/测试/浏览器/安全证据。
- `npm run build`、Vitest、pytest、migration verify/rollback dry-run、真实 E2E smoke、浏览器 console/overflow/可访问性均通过。
- 旧 UI import audit 通过后才切换入口；保留兼容 API 和 feature flag 回退。

## 7. P3：企业身份与连接器框架

- 身份：OIDC、SAML、LDAP、SCIM，统一 subject/tenant 映射、组到角色映射、policy_version、撤销和审计；先以一个 provider contract 贯穿，再增加实现。
- 连接器：定义 source、cursor、checkpoint、ACL snapshot、deletion、rate limit、credential rotation、sync result 的窄接口；Onyx community connector/indexing 只作为参考，不把其 enterprise 代码带入。
- 安全：连接器进程/任务必须有 SSRF 防护、域名 allowlist、最小权限 token、内容脱敏和租户边界；撤权后的索引新鲜度要有可量化门禁。
- 退出：至少两个数据源完成 ingest + ACL sync + revoke 回归；SSO/目录同步在双租户 fixture 中通过；连接器失败不阻塞核心问答且有降级审计。

## 8. P4：平台化交付与规模治理

- workflow/plugin：定义版本化节点、工具权限、沙箱、审批、超时、审计和回滚；Flowise 只作为外部 workflow/tool service PoC，不直接嵌入其 credential/UI。
- SDK/CLI：生成 OpenAPI/TS/Python client、tenant-aware pagination、SSE、webhook、迁移检查和本地开发命令；稳定错误码和 request id。
- Helm/K8s：仅在 P1-P3 容量证据后提供 Helm、HPA、PDB、NetworkPolicy、secret injection、灰度与回滚；Compose 继续作为 PoC/小规模入口。
- 灾备/压测/成本：PG WAL/PITR、对象存储版本/复制、Redis 恢复演练；每租户 1,000,000 access events/90 天、QPS、P95、LLM token/embedding 成本和预算告警形成证据。
- 治理：CVE/SBOM、许可证、数据留存、模型变更、评估集版本、供应商风险和升级窗口纳入发布检查单。

## 9. 复用与发布决策流程

对每个外部仓库执行固定顺序：`license inventory → SBOM → 固定 commit SHA → 最小 cherry-pick/adapter → 来源标注 → 安全审查 → 回归 → 升级策略`。

进入生产前还必须有：

- 许可证/NOTICE/白标限制得到合规负责人书面确认；GPL/AGPL/modified/custom license 不得因“源码可见”直接进入 EKB 分发物。
- 依赖锁文件、镜像 digest、CVE 扫描和 provenance 可重现；未经审查的 `main`、动态下载代码和隐式 telemetry 不得进入。
- API/DB/migration、租户隔离、ACL sync、prompt injection、SSRF、文件解析、凭据轮换、审计脱敏和性能回归全部通过。
- 组件有 owner、版本窗口、升级测试、回滚/替代方案；无法维护的第三方代码降级为“仅借鉴”。

## 10. 代码规模与合理增长

当前约 3.65 万净业务源代码；v4 目标区间 **8-12 万**，v5 **15-25 万**，平台化成熟期 **30-50 万**。这些是容量情景，不是为了 50 万行而凑行数，也不允许通过复制第三方源码、生成证据或重复页面达成。

只有以下真实需求同时增长，才可能自然接近 30-50 万净代码：连接器达到 20 个以上并有独立 ACL sync；稳定 SDK/CLI/operator；完整 API/浏览器/安全/灾备 E2E；插件和 workflow 生态；移动/桌面客户端或多部署形态；多租户运营、成本和合规治理。每个增长点必须有 FR/NFR、用户旅程、测试和运维证据。

## 11. 阶段发布节奏（建议）

| 阶段 | 建议工期 | 发布动作 |
|---|---:|---|
| P0 | 3-5 个工作日 | 只读核验、ADR、文档和 migration contract；不切流量 |
| P1 | 2-3 周 | 内部租户 canary；PG/S3/Redis/任务灰度；可 flag-off 回退 |
| P2 | 4-6 周 | 十页双视口回归后，先内部租户再客户租户；保留旧 API compatibility |
| P3 | 4-6 周 | 两类连接器和一个企业身份 provider 小流量灰度；撤权/同步回滚演练 |
| P4 | 6-10 周 | 平台化 beta；容量、灾备、成本、SBOM 和升级门禁全绿后再扩大部署 |

每阶段由 Sol 审查 diff、证据和风险后才进入下一阶段；文档“待 Sol 审核”不等于实施已批准。

## 12. 未决决策

1. `schema_migrations` 中现有环境是否已应用 `v3_003_analytics`，以及如何以 ADR 解决 authority 命名漂移。
2. PostgreSQL、S3/MinIO、Redis 的目标部署和数据保留策略，及 P1 的后台任务实现是否采用任务表 + Redis worker。
3. RAGFlow PoC 的版本、协议、ACL 注入点、数据出域和质量门禁；PoC 通过前不锁定替换方案。
4. OIDC/SAML/LDAP/SCIM 的首个 provider、合规 Owner、连接器优先级和客户试点范围。
5. 50 万行规模情景对应的真实产品需求、团队与维护预算，而不是行数承诺。

## M4-6：Grok 多轮对话移植完成项清单（里程碑）
- 完成日期：2026-08-11
- 移植来源：/Users/alin/Openclaw项目/grok-build crates/codegen/xai-chat-state
- 核心能力：
  1. 多轮上下文保留（基于 SqlStore list_messages + turn_id 过滤本轮）
  2. CJK/英文保守 token 估算器（estimate_text_tokens / estimate_messages_tokens）
  3. 三层 System Prompt 分层构造（角色身份 + 证据约束 + 思考增强）
  4. LLM 摘要式压缩 + Hard Truncate 3 轮兜底（maybe_compact_history / CompactionReport）
  5. multi_turn_enabled 灰度开关 / llm_context_window 等 5 项配置
  6. SSE v2 compaction_performed 事件
  7. Prometheus 指标：QA_COMPACTION_TRIGGERED / QA_COMPACTION_DURATION_SECONDS / QA_HISTORY_TOKENS
- 关联测试：test_llm.py ≥27，test_multi_turn.py ≥8
