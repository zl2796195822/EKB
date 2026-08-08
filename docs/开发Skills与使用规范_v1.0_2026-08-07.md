# EKB 开发 Skills 与使用规范

> **文档类型**：Reference  
> **目标**：让每个研发任务使用可追溯、可核验的本地 skill，并保留实现与验证证据。  
> **适用对象**：产品、前端、后端、AI/RAG、QA、安全、运维和文档维护者。  
> **关联规范**：[MCP 工具与项目使用规范](./MCP工具与项目使用规范_v1.0_2026-08-07.md)  
> **文档版本**：v1.0（待评审）  
> **快照日期**：2026-08-07

## 摘要

本规范定义 EKB 项目如何发现、读取、使用和更新 `Skills/` 下的第三方开发参考。Skills 只影响研发方法和审查清单，不是产品运行时依赖，也不能覆盖 Spec 的范围、安全边界、API 契约、数据模型、测试策略或已批准的决策记录。

开发任务开始前，研发人员必须读取 Backlog 中列出的本地 `SKILL.md`；任务完成时必须记录实际使用的 skill、关键规则、测试证据和未采用项的理由。没有对应 skill 时填写 `none`，并说明为什么不适用。

## 1. 优先级与边界

项目规则按以下顺序生效：

1. 安全与合规设计、已批准的威胁模型和决策记录
2. 企业级知识库 Spec、MVP 需求、API 契约和数据模型
3. 测试策略、质量门禁、部署运维和数据治理方案
4. 当前任务的验收标准与 Backlog 依赖
5. 本文档和 `Skills/<skill>/SKILL.md`

Skill 与项目文档冲突时，按较高优先级执行，并在任务或决策记录中写下偏差。Skill 的建议不能授权跨租户访问、绕过后端鉴权、放宽数据出域限制、跳过测试或引入未经评估的运行时依赖。

`Skills/` 下的内容不应读取真实客户文档、生产日志、口令、API Key、证书或内部网络地址。执行其中的脚本、包安装或外部请求前，先检查脚本内容、网络目标、写入目录和依赖；测试产物只写入项目约定的输出目录。

## 2. 当前纳入的 skills

热度数据来自 2026-08-07 观察到的公开页面，使用约数，不代表质量或项目适配性。Vercel 的安装数来自 skills.sh；OpenAI curated 项目未提供同口径安装数，因此记录 GitHub 仓库热度和官方 curated 来源。所有下载均以 GitHub `main` 分支的提交快照为准，升级时必须改用不可变提交 SHA。

| Skill | 本地路径 | 来源与快照 | 热度证据 | 许可证状态 | EKB 用途 |
|---|---|---|---:|---|---|
| `react-best-practices` | `Skills/react-best-practices` | [Vercel agent-skills](https://github.com/vercel-labs/agent-skills/tree/main/skills/react-best-practices)，HEAD `7c180d9044c9ae2b442b567aad4e42a28dd5ed62`，`SKILL.md` blob `237988de4a66dd8a71d30a2c24ebe1a86b58d04e` | [skills.sh](https://skills.sh/vercel-labs/agent-skills/vercel-react-best-practices) 约 613.1K installs | `SKILL.md` 声明 MIT；上游仓库 API 未声明根级许可证，交付前需法务确认 | React 性能、数据获取、包体积、重渲染和渲染策略 |
| `web-design-guidelines` | `Skills/web-design-guidelines` | [Vercel agent-skills](https://github.com/vercel-labs/agent-skills/tree/main/skills/web-design-guidelines)，HEAD `7c180d9044c9ae2b442b567aad4e42a28dd5ed62`，`SKILL.md` blob `ceae92ab319216a68274168fba9b63b998b65997` | [skills.sh](https://skills.sh/vercel-labs/agent-skills/web-design-guidelines) 约 522.5K installs | 上游仓库 API 未声明根级许可证；只作内部审查参考，复制或再分发前需法务确认 | 可访问性、表单、焦点、动效、响应式和交互审查 |
| `composition-patterns` | `Skills/composition-patterns` | [Vercel agent-skills](https://github.com/vercel-labs/agent-skills/tree/main/skills/composition-patterns)，HEAD `7c180d9044c9ae2b442b567aad4e42a28dd5ed62`，`SKILL.md` blob `d07025bf943345bc1cf15eb923594f889ff881f0` | [skills.sh](https://skills.sh/vercel-labs/agent-skills/vercel-composition-patterns) 约 279.1K installs | `SKILL.md` 声明 MIT；上游仓库 API 未声明根级许可证，交付前需法务确认 | 复合组件、状态上提、显式变体和组件 API |
| `writing-guidelines` | `Skills/writing-guidelines` | [Vercel agent-skills](https://github.com/vercel-labs/agent-skills/tree/main/skills/writing-guidelines)，HEAD `7c180d9044c9ae2b442b567aad4e42a28dd5ed62`，`SKILL.md` blob `63facf310252a990d154bed12b3dcdd295ba771a` | [skills.sh](https://skills.sh/vercel-labs/agent-skills/writing-guidelines) 约 40.2K installs | 上游仓库 API 未声明根级许可证；只作内部文案和文档审查参考，复制或再分发前需法务确认 | 需求、错误提示、空状态、设计说明和交付文档 |
| `security-best-practices` | `Skills/security-best-practices` | [OpenAI skills](https://github.com/openai/skills/tree/main/skills/.curated/security-best-practices)，HEAD `49f948faa9258a0c61caceaf225e179651397431`，`SKILL.md` blob `45ccbd8d671c4c4cc3e7e0645a16a3f80ec50de7` | `openai/skills` GitHub 约 24.6K stars，官方 curated | Apache-2.0，目录含 `LICENSE.txt` | Python/FastAPI、TypeScript/React、输入校验、认证、日志和密钥安全 |
| `security-threat-model` | `Skills/security-threat-model` | [OpenAI skills](https://github.com/openai/skills/tree/main/skills/.curated/security-threat-model)，HEAD `49f948faa9258a0c61caceaf225e179651397431`，`SKILL.md` blob `abd3c1c95d49c4e0e0664f198f4bf168c6291aec` | `openai/skills` GitHub 约 24.6K stars，官方 curated | Apache-2.0，目录含 `LICENSE.txt` | 信任边界、资产、攻击路径和安全控制的仓库级建模 |
| `playwright` | `Skills/playwright` | [OpenAI skills](https://github.com/openai/skills/tree/main/skills/.curated/playwright)，HEAD `49f948faa9258a0c61caceaf225e179651397431`，`SKILL.md` blob `77c01afb31aa602d0b002ae2438c2b67f0335fcb` | `openai/skills` GitHub 约 24.6K stars，官方 curated | Apache-2.0，目录含 `LICENSE.txt`；含 Microsoft Playwright CLI 归属，见 `NOTICE.txt` | 真实浏览器交互、截图、响应式检查和问题复现 |

项目中原有的 `anthropics-skills`、`galaxy`、`gsap-skills-main`、`impeccable-main`、`mengto-skills`、`react-bits` 和 `taste-skill` 保留为历史参考。它们的来源、版本和许可证必须在实际采用前单独核验，不得因为目录已经存在就视为已批准依赖。

## 3. 任务到 skill 的映射

Backlog 的 `Skill refs` 是最低阅读集合。研发人员可以增加 skill，但必须说明增加原因；技能不适用时填 `none` 和理由。

| 任务类型 | 必读 skill | 最低验证 |
|---|---|---|
| React 页面、Hook、数据请求或性能 | `react-best-practices` | lint、单测、性能或包体积证据 |
| 可复用组件、共享状态或变体 API | `composition-patterns`、`react-best-practices` | 组件契约、状态边界和回归用例 |
| UI、表单、无障碍、响应式或动效 | `web-design-guidelines` | 键盘/读屏检查、响应式截图和 `prefers-reduced-motion` 验证 |
| 登录、上传、API、权限、租户隔离或外部模型调用 | `security-best-practices` | 安全用例、输入校验、审计和密钥边界证据 |
| 新增信任边界、数据出域、管理面或攻击面 | `security-threat-model`、`security-best-practices` | 更新威胁模型，验证高风险滥用路径 |
| 浏览器端到端流程或视觉检查 | `playwright` | 保存截图、快照或 trace 到 `output/playwright/` |
| 需求、API 说明、用户提示、测试报告或运维文档 | `writing-guidelines` | 术语、链接、示例、读者目标和人工复核 |

## 4. 强制执行流程

### 4.1 开始任务

1. 找到 Backlog 条目，确认需求编号、API/数据契约、风险和 `Skill refs`。
2. 从项目目录读取每个 `Skills/<skill>/SKILL.md`，再按其中的指引读取必要的 `references/` 或规则文件。
3. 在任务计划或变更说明中写下采用的规则、需要验证的行为和敏感数据边界。
4. 若 skill 建议与项目文档不同，先记录差异，再按项目优先级决定实现方案。

### 4.2 实现与审查

实现者将 skill 当作检查清单，不直接复制不明依赖、远程资源、品牌素材或生产数据。审查者检查任务是否遵守 `Skill refs`、是否补齐错误和权限状态，以及是否把 skill 误当成运行时依赖。

UI 任务在评审前重新获取 `web-design-guidelines` 指向的上游规则；上游内容只作为审查输入，不自动覆盖 EKB 的设计 token、WCAG 目标和产品术语。Playwright 的浏览器操作必须先获取最新快照，产物写入 `output/playwright/`。

### 4.3 完成任务

任务说明必须包含以下证据：

```text
Skill refs: react-best-practices, web-design-guidelines, playwright
Read: Skills/<skill>/SKILL.md 和实际使用的规则文件
Applied: 采用了哪些具体规则，以及对应的代码或文档位置
Validation: 测试命令、截图、trace、扫描或评审结论
Exceptions: 未采用的规则、项目约束和决策记录编号
```

Code Review、测试报告、发布说明和文档变更记录必须能够回到同一个任务 ID。没有证据的“已使用 skill”不算完成。

## 5. 许可证与安全检查

每次新增或升级 skill 都要保留来源 URL、ref、commit SHA、下载日期、许可证文件和差异说明。OpenAI skill 目录必须保留 `LICENSE.txt`；Playwright skill 必须保留 `NOTICE.txt`。Vercel skill 当前存在根级许可证未声明的情况，未经法务确认不得把其规则、示例或衍生文本作为客户交付物再分发。

提交前至少执行以下检查：

- 搜索私钥、云厂商密钥、密码、真实域名、服务器地址和客户数据
- 确认没有 `node_modules`、构建产物、缓存或未经批准的二进制文件
- 查看新增脚本的网络目标、写入路径、执行权限和依赖
- 检查 Markdown 链接、许可证和 NOTICE 是否存在且未断链
- 将上游变更与当前快照逐文件对比，再决定是否升级

Skills 不属于 EKB 产品运行时依赖。除非 Backlog、架构评审和许可证记录明确批准，否则不得把 skill 中的包、脚本或服务加入 `package.json`、Python 依赖、Docker 镜像或生产部署。

## 8. Skills 与 MCP 的协同

Skills 规定研发方法和审查清单，MCP 提供受控的浏览器、GitHub 和 PostgreSQL 操作能力；两者都不覆盖项目契约和安全门禁。涉及浏览器验收、GitHub 协作或数据库检查的任务，除了填写本文的 `Skill refs`，还必须按 [MCP 工具与项目使用规范](./MCP工具与项目使用规范_v1.0_2026-08-07.md) 填写 `MCP refs`。任务记录要同时给出 skill 的规则证据、MCP 的快照 SHA、访问范围和验证产物。不得把 MCP 输出当作跳过单测、权限测试、代码 Review 或人工批准的理由。

## 6. 升级与回滚

技能维护人每月检查一次上游变更，或在发现安全公告、规则错误和框架大版本升级时立即检查。升级步骤如下：

1. 从 GitHub API 或仓库页面确认目标 commit、许可证和变更范围。
2. 使用安装器指定不可变 `--ref <commit-sha>` 下载到临时目录，不覆盖当前目录。
3. 对比 `SKILL.md`、规则、脚本、LICENSE 和 NOTICE，完成敏感信息与执行权限检查。
4. 在文档中更新版本、来源、快照日期和采用理由，随后跑最小验证集。
5. 评审通过后替换本地快照；发现问题时恢复上一份目录，并在决策记录中说明原因。

示例命令仅用于维护人员核验，目标路径必须是本项目的 `Skills/`，不可改成用户全局目录：

```bash
python3 /Users/alin/.codex/skills/.system/skill-installer/scripts/install-skill-from-github.py \
  --repo vercel-labs/agent-skills \
  --path skills/react-best-practices \
  --ref <commit_sha> \
  --dest /Users/alin/EKB/Skills
```

## 7. 文档与评审门禁

未满足以下条件时，任务不得进入实现完成或里程碑退出评审：

- Backlog 有 `Skill refs`，且实现者已读取对应本地 `SKILL.md`
- 代码、UI、API、测试和文档证据与项目契约一致
- 安全、权限、出域和敏感数据检查有结果
- 许可证、来源、commit 和 NOTICE 记录完整
- 测试、浏览器截图或评审输出可复核
- 例外已经过 Owner 和批准人确认，并关联决策记录或风险项

本规范不能替代安全评审、威胁建模、代码 Review、渗透测试或正式验收。它只规定如何把第三方开发参考纳入 EKB 的可追溯研发流程。
