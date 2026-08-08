# EKB 项目任务分解与 Backlog

> **配套文档**：[开发流程与里程碑规划](./开发流程与里程碑规划_v1.0_2026-08-07.md) · [MVP 需求与验收标准](./MVP需求与验收标准_v1.0_2026-08-07.md) · [项目决策记录与开放问题](./项目决策记录与开放问题_v1.0_2026-08-07.md) · [开发 Skills 与使用规范](./开发Skills与使用规范_v1.0_2026-08-07.md) · [MCP 工具与项目使用规范](./MCP工具与项目使用规范_v1.0_2026-08-07.md) · [文档治理与研发交付规范](./文档治理与研发交付规范_v1.0_2026-08-07.md)  
> **文档版本**：v1.0（待评审）  
> **编写日期**：2026-08-07  
> **用途**：把 M0/M1/M2 的工作拆成可执行任务，供研发、测试、产品和运维排期。

---

## 当前实施状态

| 状态 | 事项 |
|---|---|
| 已完成 | `M0-00` 文档基线、文档治理规则、Skills 目录说明和链接检查 |
| 已完成第一版验证 | `M1-TRACER-BULLET` FastAPI + React 工作台骨架、认证/检索/SSE/上传链路 |
| 待评审 | M0-01 至 M0-05 的业务、数据、模型、许可证和评估口径冻结 |
| 未开始 | PostgreSQL/pgvector、真实 RAG 解析、RBAC/ACL、多租户和生产强化 |

## 1. 排期原则

1. 任务必须可独立验收，优先使用竖切片。
2. T1 必须是 tracer bullet，先证明端到端链路能走通。
3. 每个任务都要有 owner、依赖、验收标准和风险说明。
4. 不把架构争议藏进任务里；争议先进入决策记录。
5. 每个任务都要填写 `Skill refs` 和 `MCP refs`。实现前读取 `Skills/<skill>/SKILL.md` 与对应 MCP 规范，完成时回填实际使用和验证证据；不适用时填写 `none` 与理由。

## 2. M0 任务

| ID | 任务 | Owner | 依赖 | Skill refs | 验收标准 |
|---|---|---|---|---|---|
| M0-00 | 建立文档基线、Owner、评审记录和任务追踪链 | PO + 技术负责人 | 无 | `writing-guidelines` | 文档治理规范评审通过；核心文档有 Owner、状态、版本和关联任务 |
| M0-01 | 冻结首批数据源、场景和评估样本范围 | PO + 知识 Owner | 无 | `writing-guidelines` | 决策记录闭合 OQ-001/002 |
| M0-02 | 验证 RAGFlow 固定版本、许可证和替换成本 | AI/RAG 工程 | M0-01 | `security-best-practices`, `security-threat-model` | PoC 记录可复核 |
| M0-03 | 验证最小部署和模型路由 | 运维 + AI/RAG | M0-02 | `security-best-practices` | Compose 或等价 PoC 成功 |
| M0-04 | 建立脱敏评估集和指标口径 | QA | M0-01 | `writing-guidelines` | 评估手册和样本集可用 |
| M0-05 | 输出架构和开放问题决策稿 | 技术负责人 | M0-02~04 | `writing-guidelines`, `security-threat-model` | 进入 M1 设计评审 |

## 3. M1 任务

| ID | 任务 | Owner | 依赖 | Skill refs | 验收标准 |
|---|---|---|---|---|---|
| M1-01 | 初始化用户、租户、知识库、文档、chunk、会话、消息、审计模型 | 后端 | M0-05 | `security-best-practices` | 数据模型和迁移稿通过评审 |
| M1-02 | 实现登录、刷新、主体会话和最小权限 | 后端 | M1-01 | `security-best-practices`, `security-threat-model` | 认证测试通过 |
| M1-03 | 实现知识库 CRUD 和文档上传任务 | 后端 + 前端 | M1-01 | `security-best-practices`, `playwright` | 可上传并看到状态 |
| M1-04 | 实现解析、切分、索引和失败恢复 | AI/RAG + 后端 | M1-03 | `security-best-practices`, `security-threat-model` | 任务状态机可复测 |
| M1-05 | 实现检索、RRF、Rerank 和上下文构建 | AI/RAG | M1-04 | `security-best-practices` | 授权检索能返回证据 |
| M1-06 | 实现 SSE 问答、引用和无依据拒答 | 后端 + 前端 | M1-05 | `react-best-practices`, `security-best-practices`, `playwright` | 端到端问答通过 |
| M1-07 | 实现审计和基础日志脱敏 | 后端 + 安全 | M1-02~06 | `security-best-practices` | 审计抽样通过 |
| M1-08 | 完成问答页、知识库页和文档页 | 前端 | M1-06 | `react-best-practices`, `composition-patterns`, `web-design-guidelines`, `playwright` | UI 状态和引用渲染通过 |
| M1-09 | 建立 M1 评估报告和缺陷基线 | QA | M1-06~08 | `playwright`, `writing-guidelines` | 质量门禁报告完成 ✅（2026-08-07：100+30 题，准确率 95.0%，拒答率 100%） |

## 4. M2 任务

| ID | 任务 | Owner | 依赖 | Skill refs | 验收标准 |
|---|---|---|---|---|---|
| M2-01 | 落地 RBAC/ACL 和团队成员授权 | 后端 | M1-01 | `security-best-practices`, `security-threat-model` | 权限测试覆盖 |
| M2-02 | 贯穿检索、上下文、引用和缓存的授权过滤 | 后端 + AI/RAG | M2-01 | `security-best-practices`, `security-threat-model` | 零越权证据 |
| M2-03 | 客户租户入口和认证集成方案 | 安全 + 前端 | OQ-006 | `security-best-practices`, `web-design-guidelines`, `playwright` | 客户入口可用 |
| M2-04 | 出域策略、模型路由和降级 | 后端 + 运维 | OQ-004/005 | `security-best-practices`, `security-threat-model` | 策略可执行 |
| M2-05 | 越权专项、安全回归和审计补齐 | QA + 安全 | M2-01~04 | `security-best-practices`, `security-threat-model`, `playwright` | 安全评审通过 |

## 5. M3 任务

| ID | 任务 | Owner | 依赖 | Skill refs | 验收标准 |
|---|---|---|---|---|---|
| M3-01 | 运营看板 | 前端 + 后端 | M1/M2 | `react-best-practices`, `composition-patterns`, `web-design-guidelines`, `playwright` | 看板指标可追踪 |
| M3-02 | 反馈闭环与低置信度队列 | 后端 + QA | M1-06 | `security-best-practices`, `writing-guidelines` | 可审核、可回放 |
| M3-03 | LLM 超时/降级/熔断 | 后端 | M2-04 | `security-best-practices`, `security-threat-model` | 降级事件可审计 |
| M3-04 | 只读增量同步 | 后端 + AI/RAG | OQ-008 | `security-best-practices`, `security-threat-model` | 游标、幂等、重试可用 |
| M3-05 | 版本对比和知识治理 | 后端 | M3-04 | `security-best-practices`, `writing-guidelines` | 历史版本可查 |

## 6. M4/M5 任务

| ID | 任务 | Owner | 依赖 | Skill refs | 验收标准 |
|---|---|---|---|---|---|
| M4-01 | 压测和容量评估 | 运维 + QA | M1~M3 | `playwright`, `security-best-practices` | 达到冻结 NFR |
| M4-02 | 监控、告警和成本观测 | 运维 | M1~M3 | `security-best-practices` | 仪表板和告警可用 |
| M4-03 | 备份恢复演练 | 运维 + 后端 | M1~M3 | `security-best-practices`, `security-threat-model` | 恢复报告完成 |
| M5-01 | 灰度、回滚和交付清单 | 运维 + PO | M4-03 | `security-best-practices`, `writing-guidelines` | 上线检查单完成 |
| M5-02 | 培训和复盘 | PO + QA + 运维 | M5-01 | `writing-guidelines`, `playwright` | 复盘项进入下一版本 |

## 7. 风险与阻塞项

| 风险 | 影响 | 处置 |
|---|---|---|
| 数据源未定 | 无法评估入库和权限 | 先关闭 OQ-001/002 |
| 模型预算不清 | 无法冻结延迟和路由 | 先关闭 OQ-004 |
| 认证方案未定 | 客户入口无法开工 | 先关闭 OQ-006 |
| 评估口径未定 | 无法验收 | 先关闭 OQ-007 |
| 出域策略未定 | 安全和外部模型阻塞 | 先关闭 OQ-005 |

## 8. Backlog 管理规则

| 规则 | 说明 |
|---|---|
| 新需求 | 先进入决策记录，再进入 Backlog |
| 变更 | 影响 API/DB/安全边界的，必须同步多份文档 |
| 延后 | 非 M1 必需项写入 M3/M4 Backlog，不混入当前任务 |
| 完成 | 任务完成后同步状态、证据和关联文档 |
| Skill refs | 任务开始前读取本地 `SKILL.md`；完成时记录实际使用的规则、测试证据和例外理由 |
| MCP refs | 涉及浏览器验收、GitHub 协作或 PostgreSQL 检查时填写 `playwright-mcp`、`github-mcp-server` 或 `postgres-mcp`；完成时记录快照 SHA、访问范围、产物和批准/例外 |

### 8.1 MCP refs 最低映射

| 任务范围 | MCP refs | 最低证据 |
|---|---|---|
| M1-03、M1-06、M1-08、M1-09 | `playwright-mcp` | 上传/问答/引用、错误/无权限和响应式截图或 trace |
| M1-01、M1-04、M4-01、M4-03 | `postgres-mcp` | schema、迁移、索引、EXPLAIN、健康度或恢复检查 |
| M3–M5 的 Issue/PR/Actions/发布协作 | `github-mcp-server` | 只读查询或经批准的写操作链接及结果 |
| 无浏览器、GitHub 或数据库操作 | `none` | 在任务记录中说明不适用原因 |

---

_Backlog 的作用是让项目保持能交付的形状，而不是把问题藏在“以后再说”里。_
