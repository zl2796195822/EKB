# EKB MCP 工具目录

本目录保存 EKB 研发阶段使用的 Model Context Protocol（MCP）工具源码快照和登记信息。MCP 是开发辅助能力，不属于 EKB 产品运行时依赖；生产服务不能因为 MCP 不可用而中断。

## 已批准工具

| 工具 | 本地目录 | 版本快照 | 许可证 | 适用任务 |
|---|---|---|---|---|
| Playwright MCP | `MCP/playwright-mcp` | `main` @ `4c5077651542f68525a0b51e97bab2a32abc9290`，npm `0.0.79` | Apache-2.0 | M1-03、M1-06、M1-08、M1-09 及所有浏览器验收 |
| GitHub MCP Server | `MCP/github-mcp-server` | `main` @ `1b3f89a90af6cad490384b8fdbf7fd3f057670c1` | MIT | Issue、PR、Actions、发布检查和研发协作 |
| Postgres MCP | `MCP/postgres-mcp` | `main` @ `07eb329c8c48e49640e0d1b5b35465d4d024c3ee`，Python `0.3.0` | MIT | M1-01、M1-04、M4-01、M4-03 的数据库检查 |

完整来源、stars 快照、更新时间和状态见 [`registry.json`](./registry.json)。源码目录保留上游 `LICENSE` 文件；第三方依赖的许可证以各自仓库的声明为准。

## 使用规则

1. 开始任务前先查 [Backlog](../docs/项目任务分解与Backlog_v1.0_2026-08-07.md) 的 `MCP refs`，再阅读本文件和对应工具的上游 README。
2. 默认只连接本机开发服务或脱敏测试环境。禁止把真实租户数据、生产日志、密码、API Key、PAT、Cookie、证书或数据库 URI 提交到仓库、Issue、截图和 trace。
3. Playwright 默认使用 `--headless --isolated`，只允许 `127.0.0.1`/`localhost`，浏览器产物写入 `output/playwright/`。不得加载个人浏览器 profile 或 `--storage-state`，除非安全负责人书面批准。
4. GitHub MCP 默认 `--read-only`，使用 fine-grained token 或 OAuth，仓库范围只授予 EKB 所需仓库。创建/修改 Issue、PR、Release 前必须由任务 Owner 明确批准，并在任务记录中留证。
5. Postgres MCP 默认 `--access-mode=restricted`，连接本地或专用测试库，使用最小权限账号。禁止连接生产库；禁止使用 unrestricted 模式执行写操作，除非经过数据库负责人批准并有可恢复备份。
6. MCP 输出只能作为研发证据或诊断输入，不能绕过后端鉴权、API 契约、数据模型、安全门禁或人工审批。

## 本地启动示例

以下命令用于本地开发，不会把凭据写入配置文件。不同 MCP 客户端的配置键名可能不同，请把命令映射到客户端的 stdio MCP 配置。

### Playwright MCP

```bash
node MCP/playwright-mcp/cli.js \
  --headless \
  --isolated \
  --allowed-hosts 127.0.0.1 localhost
```

Node.js 版本要求以工具 README 为准。验收截图、快照和 trace 统一放在 `output/playwright/`，并在任务记录中写明测试 URL、提交 SHA 和浏览器版本。

### GitHub MCP Server

推荐使用上游容器或由源码构建的本地二进制，并强制只读：

```bash
docker run -i --rm \
  -e GITHUB_PERSONAL_ACCESS_TOKEN \
  -e GITHUB_READ_ONLY=1 \
  ghcr.io/github/github-mcp-server
```

`GITHUB_PERSONAL_ACCESS_TOKEN` 必须由密钥管理器或进程环境注入，不能出现在 JSON 配置、Shell 历史、日志或文档中。没有 Docker 时，使用 `MCP/github-mcp-server` 的上游构建说明，并保留同样的只读和仓库范围限制。

### Postgres MCP

在本地依赖已安装时可从源码运行受限模式：

```bash
DATABASE_URI='postgresql://<user>:<password>@127.0.0.1:5432/<database>' \
  uv run --project MCP/postgres-mcp postgres-mcp --access-mode=restricted
```

示例中的 URI 只表示格式；真实值必须由密钥管理器注入。首次运行前确认数据库是本地/测试实例，并检查账号没有写权限。

## 升级、回滚和完整性

维护人每月核验一次 stars、最近提交、许可证、安全公告和依赖变化。升级必须下载到临时目录，固定到不可变 commit SHA，完成差异审查、凭据扫描和最小启动验证后再替换当前快照。回滚时恢复上一个已登记 SHA，并更新 `registry.json` 和变更记录。

当前快照通过 GitHub codeload 下载，目录不包含 `.git` 历史；`registry.json` 中的 commit URL 是完整性和追溯依据。不要用 `@latest`、浮动 Docker tag 或未登记的远程 MCP 直接替代本地快照。

## 安全检查

提交前执行：

```bash
rg -n -i --hidden --glob '!**/.git/**' \
  --glob '!MCP/**/tests/**' --glob '!MCP/**/test/**' \
  '(sk-[A-Za-z0-9]|ghp_[A-Za-z0-9]|github_pat_|api[_-]?key|password\s*=|secret\s*=|Bearer [A-Za-z0-9])' \
  MCP docs README.md
find MCP -type f \( -name '.env' -o -name '*.p12' -o -name '*.key' -o -name '*.pem' \) -print
```

当前 Playwright 快照中的 `tests/testserver/key.pem` 和 `cert.pem` 是上游测试夹具，不是 EKB 凭据；它们只能用于上游测试，不能被配置到 EKB 环境。发现其他凭据、未知二进制或网络目标异常时，立即停止使用该快照并记录到决策记录。
