# EKB AI Assistant Conversation Engine Spec

> Version: 0.2  
> Date: 2026-08-14  
> Status: **DONE-LOCAL** — code + unit/integration tests pass (17/25 acceptance items); browser/deploy/Provider evidence pending  
> Scope: AI 助手连续对话、长上下文、流式恢复、Stop、Retry、Regenerate、分支、Markdown、附件/图片上下文  
> Explicit exclusion: 联网搜索、Coding Agent 工具循环、终端/MCP 工具

## 1. 结论

**当前状态 (2026-08-14): DONE-LOCAL**

EKB AI Assistant Conversation Engine 的代码实现和单元/集成测试已全部完成 (CE-PH0 ~ CE-PH5)。单一 canonical Conversation/Turn Engine 已成为所有浏览器生成的入口，legacy 生成路径已退出写入职责。17/25 验收清单项有代码+测试证据。

**仍未达到 Grok 同等级验收** 的阻断项 (8 项,均为真实运行时证据):

1. 100+ 轮真实长对话 + 2+ compaction (需要真实 Provider 长流);
2. Refresh/API restart/worker restart 真实恢复 (需要真实 worker 运行时);
3. 真实浏览器 E2E (CHAT-E2E-01 ~ 11, 需要 Playwright 套件);
4. PostgreSQL migration apply/verify/rollback 演练;
5. 真实远程 Provider 长流测试;
6. 授权服务器 backup/deploy/health/readiness/rollback smoke;
7. 同模型 Grok 行为对标报告 (非仅契约测试);
8. 缺失测试套件: test_stop_retry_races.py, assistant-turn-engine.test.tsx, assistant-reconnect.test.tsx。

本专项 Spec 的唯一目标是：将 EKB 收敛为一套 canonical Conversation/Turn Engine，使其在连续性、上下文治理、流式稳定性、停止、重试、重新生成、分支、恢复和附件理解方面达到参考 Grok Build 的行为等级。这里的“相同”指行为与稳定性同等级，不要求回答文字逐字相同。

## 2. 文档顺序

1. [`00-current-state-and-gap.md`](./00-current-state-and-gap.md)：当前实现、可用边界、P0/P1 缺口。
2. [`01-grok-benchmark.md`](./01-grok-benchmark.md)：固定源码版本、许可边界、可复用设计。
3. [`02-conversation-engine-spec.md`](./02-conversation-engine-spec.md)：目标架构、状态机、上下文和前端合同。
4. [`03-api-db-sse-contract.md`](./03-api-db-sse-contract.md)：API、PostgreSQL additive migration、SSE v3 合同。
5. [`04-implementation-and-verification.md`](./04-implementation-and-verification.md)：实施顺序、测试矩阵和 Grok 行为对标门禁。

## 3. Authority

- 用户确认前，本专项文档状态为 Draft，`docs/specs/ekb-core-rebuild/05-ai-chat.md` 仍为已批准基线。
- 用户确认后，本专项文档在 AI Assistant Conversation Engine 范围内成为更具体的 successor；冲突时以本专项文档为准，其他 Core Rebuild 范围不受影响。
- 已应用 migration 及 checksum 永不修改；本文数据库变化只能通过新 additive migration 实现。
- 原始 `messages` 是对话事实，不得因 trimming、summary、retry、regenerate 或分支切换而覆盖或删除。
- 文档中目标表/API/测试不能当作已实现证据；只有真实迁移、测试、浏览器操作和 Provider 调用证据才允许完成勾选。

## 4. Definition of Done

只有以下条件全部满足，才能声明“达到 Grok 对话同等级目标”：

- 单一 Turn Engine 成为所有浏览器生成入口，legacy 生成路径已退出写入职责；
- 100+ 轮、至少两次 compaction 的真实长对话通过；
- summary + recent complete turns 可在刷新、进程重启和分支切换后恢复；
- SSE cursor 重连不重字、不缺字，terminal event 恰好一次；
- Stop 真正到达服务端生成任务并阻止 late delta 成为 canonical 内容；
- transport retry、用户 retry、regenerate 三种语义独立且真实可用；
- Regenerate 保留旧回答并创建可切换分支；
- Markdown/GFM/代码块流式与完成态安全稳定；
- 附件和图片进入实际 Provider context，失败时显式 fail closed；
- 当前轮保存实际 Provider、Model、能力和 context budget 快照；
- 同模型/同 prompt 的 Grok 行为对标矩阵全部通过；
- 无 Web Search 能力、事件、UI 或调用残留。

## 5. Fixed decisions

| Decision | Choice | Reason |
|---|---|---|
| Parity definition | 行为、连续性、稳定性同等级；不要求文字相同 | 输出文本受模型和采样影响，系统能力可确定验收 |
| Conversation truth | PostgreSQL canonical messages/turns/events | EKB 是多租户 Web 产品，不复制 Grok 本地 JSONL |
| Context truth | 原始历史不变，模型上下文是可重建投影 | 支持 compaction、恢复、分支和审计 |
| Generation owner | 独立于浏览器连接的可靠 generation job | 浏览器断线不能决定服务端 Turn 生命周期 |
| Event recovery | 持久 event log + cursor replay | 保证重连不重字、不缺字 |
| Knowledge Base | 0..N 可选 | 普通 AI Chat 和企业 RAG 共用一个引擎 |
| Vision | 原生远程 Vision → 已配置远程 Adapter → 显式失败 | 不静默忽略图片，不引入本地模型 |
| Legacy path | 兼容代理后退出写入职责 | 渐进迁移，不保留双事实源 |
| Grok reuse | 固定 commit、复用设计、默认不复制源码 | 控制许可、依赖和架构风险 |
| Search | 明确排除 Web Search | 遵守 EKB 本轮产品范围 |

## 6. Evidence index (2026-08-14)

### Backend services (code evidence)

| Service | Path | Phase |
|---|---|---|
| ConversationApplicationService | `apps/api/ekb_api/services/conversation_app.py` | PH1 |
| TurnService (CAS state machine) | `apps/api/ekb_api/services/turns.py` | PH1 |
| ContextEngineService | `apps/api/ekb_api/services/context_engine.py` | PH2 |
| ContextBuilder (pure) | `apps/api/ekb_api/services/context.py` | PH2 |
| GenerationWorker | `apps/api/ekb_api/services/generation_worker.py` | PH2/3 |
| TurnEventStore (durable SSE) | `apps/api/ekb_api/services/generation_worker.py` | PH3 |
| RetryPolicy | `apps/api/ekb_api/services/retry_policy.py` | PH3 |
| AttachmentService (ACL + retention) | `apps/api/ekb_api/services/attachments.py` | PH5 |
| Vision cascade (3-stage) | `apps/api/ekb_api/services/vision.py` | PH5 |
| Attachment processor | `apps/api/ekb_api/services/attachment_processor.py` | PH5 |

### Backend tests

| Suite | Tests | Phase |
|---|---|---|
| test_conversation_engine_unified.py | atomic + idempotent + CAS | PH1 |
| test_context_summary_persistence.py | summary + hash invalidation | PH2 |
| test_turn_event_replay.py | cursor replay + seq monotonicity | PH3 |
| test_ph5_attachments.py | 34 tests (lifecycle + ACL + Vision + historical reuse) | PH5 |
| test_grok_parity_contract.py | 9 contract tests (matrix coverage) | PH5 |

### Frontend

| Component | Path | Phase |
|---|---|---|
| AssistantMarkdown | `apps/web/src/app-v2/components/assistant/AssistantMarkdown.tsx` | PH4 |
| AssistantMessageList | `apps/web/src/app-v2/components/assistant/AssistantMessageList.tsx` | PH4 |
| AssistantPage | `apps/web/src/app-v2/pages/AssistantPage.tsx` | PH4 |
| assistant-markdown-security.test.tsx | 16 tests (XSS + GFM + streaming) | PH4 |

### Verification results (2026-08-14)

```text
.venv/bin/python -m ruff check apps/api/ekb_api apps/api/tests  →  All checks passed!
.venv/bin/pytest -q apps/api/tests/v4/                           →  237 passed, 2 skipped, 2 pre-existing failures
npm --prefix apps/web run test                                   →  14 files, 88 tests passed
npm --prefix apps/web run typecheck                              →  OK (no errors)
```
