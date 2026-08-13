---
title: EKB 核心重构测试与验证计划
status: Ready for Plan
tier: 3
type: feature
authoritative: true
scope: ekb-core-rebuild
version: 0.1
date: 2026-08-12
owner: Sol
---

# EKB 核心重构测试与验证计划

## 1. 证据原则

测试通过不等于功能完成。每个核心旅程必须同时具备：

1. 自动化单元/集成/契约测试；
2. API 返回与错误路径；
3. PostgreSQL 行、约束和状态；
4. 对象存储/Chunk/Embedding/pgvector 真值（适用时）；
5. 审计/trace/job 证据；
6. 真实浏览器操作和截图/录像/console 结果；
7. 生产或 QA 环境部署验证（按 Phase）。

任一层不一致即失败。固定 `success:true`、随机进度、mock reply/model catalog 不得作为验收证据。

## 2. 测试环境

| 环境 | 用途 | 数据 |
|---|---|---|
| Unit | 纯函数、状态机、路径/token/adapter normalization | 合成 |
| Integration | API+PostgreSQL+pgvector+对象存储+Queue+Worker | 固定 QA fixtures |
| Browser QA | 完整 Web/API/Worker/Scheduler/Provider | 专用 QA tenant，非敏感 |
| Migration rehearsal | 生产备份副本 → 新 PostgreSQL | 脱敏/受控真实副本 |
| Production canary | 最终原子切换 | 1 个内部 QA tenant，再真实 tenant |

QA fixtures 必须固定 hash 和预期 locator：TXT、Markdown、文本 PDF、扫描 PDF、DOCX、XLSX、多 sheet、PPTX、PNG/JPEG/WebP、旧 Office（若 converter enabled）、空/损坏/加密/超大边界文件、10+ 多级目录。

## 3. 自动化层级

### 3.1 Backend 单元

- path normalization/traversal/reserved names；
- upload limits/idempotency/version resolution；
- ingest/turn/purge/job 状态机与非法转移；
- chunk/token budget/summary range；
- Provider adapter request/error/usage normalization；
- fallback policy 和 data egress policy；
- secret redaction；
- clock-controlled expiry。

### 3.2 Integration

- object multipart/complete/checksum/abort/resume；
- queue 至少一次、lease、heartbeat、重复投递、worker crash；
- parser/OCR/converter sandbox timeout/resource limits；
- pgvector dimensions/generation activation/reindex rollback；
- conversation/branch/message/citation/attachment 事务；
- tenant+actor+ACL negative matrix；
- Provider 使用可控 fake server 做协议失败测试，真实 Provider 另设 E2E；
- scheduler/purge/object 404/retry/dead job。

### 3.3 Frontend

- adapter contract、SSE reducer/seq/terminal/idempotency；
- Upload Center refresh recovery；
- branch switching/partial stopped message；
- model selector invalidation/fallback disclosure；
- Markdown sanitizer/code blocks；
- semantic token lint、component states、accessibility。

## 4. 知识库真实场景

| Test ID | 场景 | 必须验证 |
|---|---|---|
| KB-E2E-01 | 上传 TXT | 原件 hash、version、chunks/vector、可检索、AI 正确引用 |
| KB-E2E-02 | 上传 PDF | page locator、解析完成、AI 按页回答 |
| KB-E2E-03 | 10+ 混合文件 | 总/单文件进度、部分失败、仅失败重试 |
| KB-E2E-04 | 多级目录 | relative path、同名文件共存、刷新存在 |
| KB-E2E-05 | 同路径更新 | version+1、旧引用仍定位、新检索使用 active |
| KB-E2E-06 | 同内容重传 | 幂等、不重复 chunks/vector |
| KB-E2E-07 | worker crash | lease 恢复、无永久 RUNNING、无双 active version |
| KB-E2E-08 | profile reindex | build/swap/rollback、维度不混用 |
| KB-E2E-09 | unsupported/empty/corrupt | 稳定错误码、无假成功、原件策略正确 |
| KB-E2E-10 | ACL | viewer 不能上传；撤权内容不检索 |

## 5. AI Chat 真实场景

| Test ID | 场景 | 必须验证 |
|---|---|---|
| CHAT-E2E-01 | 连续 8+ Turn | 上下文正确、刷新/切换会话恢复 |
| CHAT-E2E-02 | Streaming | seq 单调、Markdown/代码稳定、唯一终态 |
| CHAT-E2E-03 | Stop | 首 token 前/部分输出后；partial persisted；重复 cancel 幂等 |
| CHAT-E2E-04 | Retry | Provider 暂时失败后新 attempt，不重复 user message |
| CHAT-E2E-05 | Regenerate | 旧回答保留、新 branch、active chain 正确 |
| CHAT-E2E-06 | Long context | 触发 summary、预算不超、最近完整 Turn 保留 |
| CHAT-E2E-07 | Title | 首轮异步标题、fallback、用户锁不覆盖 |
| CHAT-E2E-08 | Actor isolation | 同 tenant 用户 B 读/停 A 的 turn 失败且不泄露 |
| CHAT-E2E-09 | Disconnect | 重连查询已持久状态，不宣称 SSE replay |
| CHAT-E2E-10 | No web search | UI/API/runtime 无联网搜索；旧字段明确拒绝 |

## 6. 附件/Vision 场景

| Test ID | 场景 | 必须验证 |
|---|---|---|
| ATT-E2E-01 | PDF/DOCX/TXT/MD/XLSX | 模型回答使用文件内固定事实 |
| ATT-E2E-02 | 大文件 | 使用 attachment retrieval，不把全文塞入 context |
| ATT-E2E-03 | Vision model 图片 | actual route 支持 Vision，回答识别固定图像事实 |
| ATT-E2E-04 | 非 Vision model | OCR/caption fallback 披露并可回答 |
| ATT-E2E-05 | 扫描 PDF | OCR locator 和回答证据 |
| ATT-E2E-06 | 生命周期 | 刷新存在、软删/过期/撤权后不能使用 |
| ATT-E2E-07 | 跨用户 ID | 统一授权错误，不泄露文件名 |
| ATT-E2E-08 | Promotion | 目标 KB 真实 version/index，非 UI 标签 |

## 7. Provider 场景

| Test ID | 路由/角色/前置 | 操作 | 断言与证据 |
|---|---|---|---|
| PROVIDER-E2E-01 | Profile；personal owner/team admin；真实凭据 | 创建→刷新→同步/手工模型→进入 Assistant→发送 | selector 唯一数据源、真实流式、actual route/usage 匹配、DB/audit、Light/Dark 截图、console/network=0 |
| PROVIDER-E2E-02 | Profile+Assistant；当前模型已选 | 禁用/删除模型、制造 transient error | selector 同步移除；显式 fallback 或阻止；route event/reason；中途失败不拼模型 |
| PROVIDER-E2E-03 | Profile；admin | 创建/轮换 credential，扫描响应/日志/审计/storage | 无明文，key version 变化，旧 key 受控窗口，错误 credential 可恢复 |
| PROVIDER-E2E-04 | 两用户/两租户；personal/team providers | 枚举 ID、使用 personal provider 发送受限 Team KB | 404 anti-enumeration、egress deny/audit、历史 route 仅脱敏可见 |
| PROVIDER-E2E-05 | Vision/非 Vision/unknown capability 模型 | 上传图片并发送 | capability 路由正确；unknown fail closed；Vision 不兼容不静默 fallback |

OpenAI、Anthropic、Gemini 和每个启用的 compatible profile 至少通过 PROVIDER-E2E-01；错误 base URL/SSRF host、mock catalog production gate 纳入相应测试。若生产无出网，该 Provider 保持 Blocked，不改用 mock 通过。

## 8. RAG 场景

- 0 KB 普通对话；1 KB；多个 KB 配额/去重。
- Strict 有证据回答、有证据不足拒答。
- Enhanced 分离引用知识和常识。
- citation 刷新、branch、文档新版本后仍指向生成时版本。
- 删除、撤权、重建中、profile 不兼容的 chunk 不进入新 Turn。
- Retrieval preview 与实际 Turn 使用相同授权/策略版本。

## 9. Theme/Accessibility 浏览器矩阵

| Test ID | 路由/角色/视口 | 场景 | 断言与证据 |
|---|---|---|---|
| THEME-E2E-01 | 全部主要路由；member/admin；desktop+390x844 | Light/Dark default/loading/empty/error/permission | computed semantic token、截图、残留=0、overflow=0、console/network=0 |
| THEME-E2E-02 | Knowledge/Assistant/Profile/Trash | Modal/Drawer/Dropdown/Tooltip/Table/Input hover/focus/selected/disabled | portal 继承主题、边框/文字/overlay 可读、双主题截图 |
| THEME-E2E-03 | 任意登录/未登录页 | light/dark/system 切换、刷新、系统主题改变 | 无首屏闪烁、偏好优先级正确、布局不跳动 |
| THEME-E2E-04 | 全路由核心操作 | keyboard、focus、aria、reduced-motion、状态非仅颜色 | WCAG AA 报告、键盘录像/截图、自动 a11y=0 P0/P1/P2 |

视觉差异必须人工审查，禁止无条件更新 golden。

## 10. Trash/Jobs 场景

| Test ID | 路由/角色/前置 | 操作 | 断言与证据 |
|---|---|---|---|
| TRASH-E2E-01 | Knowledge/Assistant/Trash；资源 owner/admin | 删除文档/KB/会话/附件→刷新→恢复 | deleted/expires/generation、页面消失/恢复、DB/audit、双主题桌面/移动 |
| TRASH-E2E-02 | 父 KB 已删、软删路径保留、purge 已 claim | restore/restore_parents；尝试同路径新建 | `PARENT_DELETED`/`PATH_RESERVED_BY_TRASH`/`PURGE_IN_PROGRESS` 精确 shape，无数据损坏 |
| TRASH-E2E-03 | 注入时钟 | 29d23h59m 与 30d 边界运行 scheduler | 到期前不 purge；到期后自动 enqueue/purge；audit 保留 |
| TRASH-E2E-04 | 双 scheduler/重复队列/worker crash/object 404 | run/retry/reconcile | fencing/lease 生效、幂等、暂时失败 retry、超限 dead、ref/vector 正确 |
| TRASH-E2E-05 | Admin Jobs Center；混合 job states | filter/detail/retry/cancel/heartbeat/cleanup report | API/DB/job timeline 一致、敏感正文不显示、console/network=0 |

Provider/Model disable 流程另测，必须证明未进入内容 Trash。

## 11. Migration/回滚

每次 rehearsal：

1. 验证历史 ledger/checksum 不变；
2. 新库 apply 两次（第二次 no-op）并 verify；
3. 导入 SQLite，逐表/租户/关系/抽样 hash 核对；
4. 执行约束/孤儿/pgvector dimension 检查；
5. 启动旧兼容代码和新代码 smoke；
6. 模拟 migration failure，证明 API/worker 不启动；
7. 执行 rollback/forward-fix 演练和数据差异报告。

## 12. 真实浏览器与 grok-build 对比

| Test ID | 能力 | EKB Pass 条件 |
|---|---|---|
| GROK-CMP-01 | 8+ Turn 连续上下文 | 固定事实追问全部沿活动分支正确，刷新后不丢失 |
| GROK-CMP-02 | Streaming/Markdown/Code | 首事件、增量、终态稳定；表格/代码可读可复制；console=0 |
| GROK-CMP-03 | Stop/Retry/Regenerate | 部分消息持久、失败可重试、旧回答保留且新分支可继续 |
| GROK-CMP-04 | 会话持久化/切换 | 标题、历史、active branch 和选择的默认 KB 恢复 |
| GROK-CMP-05 | 文件附件 | 同一 PDF/DOCX/XLSX 固定事实可回答，引用/处理方式正确 |
| GROK-CMP-06 | 图片与 fallback | Vision 模型识别固定图像事实；非 Vision 明确 OCR/caption fallback |

使用相同或尽量相同模型、prompt、连续会话、附件和图片。六项全部通过才满足“同等级别”验收；比较 EKB 自身可观察合同和错误恢复，不比较逐字输出。grok-build 的 Agent、工具、终端、MCP 和联网能力明确不计入矩阵。

浏览器测试必须由可控 QA tenant 执行，记录页面、操作、预期、实际、request/turn/job id、截图和 console/network error。curl/单测/构建不能替代。

## 13. 建议检查命令合同

实际命令在实施计划每个任务中锁定；最终门禁至少包含：

```bash
pytest -q apps/api/tests
npm --prefix apps/web run test -- --run
npm --prefix apps/web run typecheck
npm --prefix apps/web run build
```

当前 `apps/web/package.json` 没有 lint script，因此 lint 不得以不存在的命令冒充通过；PH0/PH2 必须新增可执行主题/raw-color/import lint script 后把它纳入门禁，并锁定现有 `package-lock.json`，不擅自迁移到 pnpm。

另需 migration verifier、PostgreSQL integration、object/queue/worker integration、Playwright/应用内真实浏览器套件和 secret/link/import scans。任何缺失命令不能以“无测试”方式通过。

## 14. 证据目录约定

每阶段写入 `docs/evidence/ekb-core-rebuild/<phase>/<run-id>/`：manifest、环境版本（无 secret）、测试报告、migration diff、DB/storage/vector counts、browser scenarios/screenshots、known failures、deployment/rollback result。证据文件本身不得包含凭据或消息/附件敏感正文。
