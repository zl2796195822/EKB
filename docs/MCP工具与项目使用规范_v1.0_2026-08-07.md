# EKB MCP 工具与项目使用规范

> **文档类型**：研发流程规范  
> **版本**：v1.0（待评审）  
> **适用范围**：M0–M5 的本地研发、测试、评审和发布准备  
> **配套目录**：[`../MCP/README.md`](../MCP/README.md) · [`../MCP/registry.json`](../MCP/registry.json)

## 1. 定位和优先级

MCP 为研发人员提供浏览器自动化、GitHub 协作和 PostgreSQL 检查能力。它不属于 EKB 运行时依赖，不能替代业务 API、鉴权、审计、测试、人工审批或安全评审。

规范优先级为：安全与合规设计、已批准决策记录、Spec/API/数据模型、测试与运维文档、Backlog，最后才是 MCP 上游 README 和工具行为。发生冲突时按项目文档执行，并在任务记录中记录偏差。

## 2. 工具登记和选择标准

项目批准的工具由 `MCP/registry.json` 登记。选择依据是 GitHub 公开热度、近期维护、用途匹配、许可证可核验和可在本地受控运行。当前纳入：

| 工具 | 强制使用场景 | 默认边界 |
|---|---|---|
| `playwright-mcp` | 浏览器端到端流程、上传/问答/引用验收、响应式和视觉回归 | headless、isolated、仅 loopback，产物到 `output/playwright/` |
| `github-mcp-server` | Issue/PR/Actions/release 查询和研发协作证据 | read-only、fine-grained token/OAuth、仅授权仓库 |
| `postgres-mcp` | Schema/迁移/索引/EXPLAIN/健康度和 pgvector 检查 | restricted/read-only、本地或测试库、最小权限账号 |

没有匹配工具时使用 `none`，并说明原因；不得为了“使用 MCP”而扩大任务范围。

## 3. 任务执行流程

### 3.1 开始任务

1. 在 Backlog 中确认需求编号、依赖、`Skill refs` 和 `MCP refs`。
2. 阅读 `MCP/README.md`、对应工具上游 README、许可证和安全说明。
3. 明确 MCP 目标、输入数据分类、允许访问的主机/仓库/数据库、输出目录和人工批准点。
4. 任务计划中写下采用的工具和默认边界；没有批准前不得连接生产或真实客户数据。

### 3.2 执行和验证

- Playwright 验证必须覆盖成功、加载、空、错误、无权限和响应式状态；截图/trace 不得含真实账号、Cookie 或租户资料。
- GitHub MCP 查询优先使用只读 toolset。写操作需要任务 Owner 明确授权，并在 Issue/PR 链接、操作人、时间和结果中留证。
- Postgres MCP 先检查连接目标和账号权限，再执行 schema、迁移、EXPLAIN 或健康度检查。任何写操作必须使用隔离测试库、备份和回滚步骤。
- MCP 结果只能支持判断，代码仍须通过 API、数据库、安全和测试门禁。工具输出异常时停止自动化，转人工复核。

### 3.3 完成任务

任务记录必须包含：

```text
MCP refs: playwright-mcp / github-mcp-server / postgres-mcp / none
Snapshot: registry.json 中的 commit SHA
Scope: 主机、仓库、数据库和数据分类
Applied: 使用了哪些工具能力及其证据位置
Validation: 命令、截图、trace、查询或审查结果
Approval: 写操作、外部访问或例外的批准人和记录编号
Exceptions: 未采用项、原因和风险处置
```

## 4. 任务映射

| Backlog 任务 | MCP refs | 最低证据 |
|---|---|---|
| M0-01/M0-04 | `postgres-mcp`（若已有测试库） | schema/数据样本检查记录 |
| M1-03 | `playwright-mcp`, `postgres-mcp` | 上传流程截图、状态机和迁移检查 |
| M1-04/M1-05 | `postgres-mcp` | 索引、向量列、EXPLAIN 或健康度记录 |
| M1-06/M1-08/M1-09 | `playwright-mcp` | SSE 问答、引用、错误/无权限和响应式证据 |
| M2-01/M2-02/M2-05 | `playwright-mcp`, `postgres-mcp` | 越权回归、策略/索引检查和零泄露结果 |
| M3–M5 研发协作 | `github-mcp-server` | Issue/PR/Actions 或发布检查链接 |
| M4-01/M4-03 | `postgres-mcp`, `github-mcp-server` | 压测数据、数据库检查和变更证据 |

具体任务可增加 MCP，但不得减少安全和质量门禁。

## 5. 数据、凭据和网络边界

- 禁止输入真实租户文档、生产日志、个人 Cookie、PAT、API Key、密码、证书、数据库 URI 或未脱敏个人信息。
- 凭据只能通过密钥管理服务或进程环境注入；不得写入源码、JSON、Shell 历史、截图、trace、Issue 或 PR。
- Playwright 只访问批准的本地 URL；`allowed-origins` 不是完整安全边界，仍需后端鉴权和网络隔离。
- GitHub token 使用最小仓库范围和最短有效期；默认只读，写操作人工批准。
- Postgres 默认连接测试实例和只读账号；生产数据库连接在本项目中禁止。
- 输出统一去除敏感字段，存放在任务约定目录并设置合理保留期。

## 6. 版本和许可证门禁

新增或升级 MCP 必须记录来源 URL、下载日期、默认分支、commit SHA、stars、许可证、依赖变化和安全审查结果。禁止使用浮动版本作为交付依据。第三方源码的许可证和 NOTICE 必须保留；许可证无法确认的仓库只能列为参考，不得纳入批准工具。

升级步骤：核验上游 → 固定 SHA 下载到临时目录 → 差异/凭据/依赖扫描 → 本地最小启动 → 任务回归 → 更新 registry 和文档 → 评审批准。出现异常时回滚到上一登记 SHA，并在项目决策记录中记录原因。

## 7. 例外与审计

以下情况必须由技术负责人和安全负责人批准：访问非 loopback 地址、加载浏览器持久化登录态、GitHub 写操作、Postgres unrestricted 模式、处理未完全脱敏数据、把 MCP 暴露到共享网络或将 MCP 纳入生产运行时。

每个里程碑退出时抽查 MCP refs、快照 SHA、凭据扫描、产物位置、许可证和批准记录；缺少证据的任务不得标记完成。
