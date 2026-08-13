---
title: EKB 核心重构验收标准与追踪矩阵
status: Ready for Plan
tier: 3
type: feature
authoritative: true
scope: ekb-core-rebuild
version: 0.1
date: 2026-08-12
owner: Sol
---

# EKB 核心重构验收标准与追踪矩阵

## 1. 门禁规则

每条需求必须有唯一 AC、主要自动化证据、浏览器/运行证据和实施 Phase。只有证据实际产生且复核通过后才可把 AC 标为 Passed；文档、代码、API 200、构建成功或截图中的 UI 单独都不足以通过。

状态：`Not Started → Implemented → Verified → Passed`；发现回归时可退回。当前全部为 `Not Started`。

## 2. 功能需求追踪

| Requirement | Acceptance ID | 可观察完成条件 | 自动化/数据证据 | 浏览器/运行证据 | Phase |
|---|---|---|---|---|---|
| EKB-CR-FR-001 | AC-CR-001 | 语义 token 覆盖定义类别 | THEME-UNIT-01、raw-color scan | THEME-BR-01 token inspection | PH2 |
| EKB-CR-FR-002 | AC-CR-002 | light/dark/system 切换和刷新保持 | preference tests | THEME-BR-02 reload/system | PH2 |
| EKB-CR-FR-003 | AC-CR-003 | 所列共享/第三方组件仅消费 token | component/import scan | THEME-BR-03 overlays | PH2 |
| EKB-CR-FR-004 | AC-CR-004 | 两主题所有交互状态可辨/可聚焦 | a11y/contrast tests | THEME-BR-04 keyboard | PH2 |
| EKB-CR-FR-005 | AC-CR-005 | 新 raw color 为零、legacy 有 allowlist | lint gate | evidence allowlist review | PH2 |
| EKB-CR-FR-006 | AC-CR-006 | 主要路由和状态矩阵无浅色残留 | route visual tests | desktop/mobile screenshots | PH7 |
| EKB-CR-FR-007 | AC-CR-007 | 预检显示模式/数量/大小/类型/路径 | upload reducer tests | KB-E2E-03/04 | PH3 |
| EKB-CR-FR-008 | AC-CR-008 | 多级路径服务端保存并作为身份 | path/DB unique tests | KB-E2E-04 | PH3 |
| EKB-CR-FR-009 | AC-CR-009 | 同路径版本、同内容幂等、异目录共存 | KB-E2E-05/06 + DB counts | document version browser | PH3 |
| EKB-CR-FR-010 | AC-CR-010 | 100MB/1000/5GB 服务端逐项限制 | boundary tests | upload preflight errors | PH3 |
| EKB-CR-FR-011 | AC-CR-011 | 大文件不完整驻留 API 内存、对象校验 | streaming/multipart integration | network/object evidence | PH1/PH3 |
| EKB-CR-FR-012 | AC-CR-012 | 字节和各摄取 stage 真实进度 | stage projection tests | KB-E2E-03 | PH3 |
| EKB-CR-FR-013 | AC-CR-013 | 失败显示 code/stage/reason/id/retryability | error contract tests | KB-E2E-09 | PH3 |
| EKB-CR-FR-014 | AC-CR-014 | 导航/刷新恢复批次与进度 | persistence/UI tests | browser reload during job | PH3 |
| EKB-CR-FR-015 | AC-CR-015 | 指定文档格式和隔离旧格式转换 | parser fixture suite | upload format matrix | PH3 |
| EKB-CR-FR-016 | AC-CR-016 | 图片/扫描 PDF OCR 带 locator | OCR integration | KB/ATT scanned PDF | PH5 |
| EKB-CR-FR-017 | AC-CR-017 | 仅对象/版本/chunk/vector 全成才 Success | failure injection/DB assertions | KB-E2E-01/02 | PH3 |
| EKB-CR-FR-018 | AC-CR-018 | 重试复用 source object 且 attempt+1 | worker retry tests | failed-only retry | PH3 |
| EKB-CR-FR-019 | AC-CR-019 | profile immutable，reindex build/swap/rollback | KB-E2E-08 | admin reindex flow | PH3 |
| EKB-CR-FR-020 | AC-CR-020 | owner/editor/viewer 全链路统一 ACL | tenant/ACL negative suite | role browser matrix | PH1/PH3 |
| EKB-CR-FR-021 | AC-CR-021 | 会话 CRUD/切换/刷新恢复 | conversation contract tests | CHAT-E2E-01 | PH4 |
| EKB-CR-FR-022 | AC-CR-022 | 默认 0..N KB，Turn 保存实际快照 | snapshot DB tests | conversation defaults flow | PH4/PH6 |
| EKB-CR-FR-023 | AC-CR-023 | 各消息状态和 branch 持久化 | message graph tests | reload stopped/error/branch | PH4 |
| EKB-CR-FR-024 | AC-CR-024 | SSE 事件齐全、seq 单调、唯一终态 | SSE contract/property tests | CHAT-E2E-02 | PH4 |
| EKB-CR-FR-025 | AC-CR-025 | Stop owner scoped/幂等/partial persisted | actor/cancel integration | CHAT-E2E-03/08 | PH1/PH4 |
| EKB-CR-FR-026 | AC-CR-026 | Retry 新 attempt；Regenerate 新 branch | graph/idempotency tests | CHAT-E2E-04/05 | PH4 |
| EKB-CR-FR-027 | AC-CR-027 | active branch 决定后续上下文 | context graph tests | branch switch + next turn | PH4 |
| EKB-CR-FR-028 | AC-CR-028 | async title/fallback/user lock | title job tests | CHAT-E2E-07 | PH4 |
| EKB-CR-FR-029 | AC-CR-029 | Markdown/table/code 安全正确 | sanitizer/render tests | CHAT-E2E-02 | PH4 |
| EKB-CR-FR-030 | AC-CR-030 | Token Budget 计入全部构成且不超窗口 | tokenizer/budget tests | context diagnostics | PH4/PH5/PH6 |
| EKB-CR-FR-031 | AC-CR-031 | summary 有范围，保留最近完整 Turn | compaction tests | CHAT-E2E-06 | PH4 |
| EKB-CR-FR-032 | AC-CR-032 | Provider 与内部错误分类，无假回答 | failure classifier tests | error/retry browser | PH4 |
| EKB-CR-FR-033 | AC-CR-033 | UI/API/runtime 无 Web Search | forbidden import/contract scan | CHAT-E2E-10 | PH4 |
| EKB-CR-FR-034 | AC-CR-034 | attachment 独立 ID/owner/object/status/retention | schema/ACL tests | attachment detail | PH5 |
| EKB-CR-FR-035 | AC-CR-035 | 指定文件内容实际进入回答 | fixture answer assertions | ATT-E2E-01 | PH5 |
| EKB-CR-FR-036 | AC-CR-036 | 小文件 inline，大文件临时检索 | context manifest tests | ATT-E2E-02 | PH5 |
| EKB-CR-FR-037 | AC-CR-037 | native Vision 优先、fallback 披露 | route/capability tests | ATT-E2E-03/04 | PH5 |
| EKB-CR-FR-038 | AC-CR-038 | PNG/JPG/JPEG/WebP 服务端校验 | decoder/security suite | image upload matrix | PH5 |
| EKB-CR-FR-039 | AC-CR-039 | 消息关系恢复；撤权/删/过期/未就绪禁用 | lifecycle/ACL tests | ATT-E2E-06/07 | PH5 |
| EKB-CR-FR-040 | AC-CR-040 | Promotion 产生真实 KB version/index/audit | promotion integration | ATT-E2E-08 | PH5 |
| EKB-CR-FR-041 | AC-CR-041 | OpenAI/Anthropic/Gemini 原生 adapter 可真实流式 | adapter + provider E2E | provider real-call matrix | PH2 |
| EKB-CR-FR-042 | AC-CR-042 | 启用的 compatible profiles 差异规范化 | profile contract tests | provider real-call matrix | PH2 |
| EKB-CR-FR-043 | AC-CR-043 | personal/team ownership 和外发策略执行 | policy negative tests | model visibility/KB send | PH2 |
| EKB-CR-FR-044 | AC-CR-044 | available-models 是 selector 唯一数据源 | import/contract tests | add-refresh-selector-send | PH2 |
| EKB-CR-FR-045 | AC-CR-045 | 当前模型失效显式 fallback 或阻止 | policy/state tests | disable/delete flow | PH2 |
| EKB-CR-FR-046 | AC-CR-046 | Turn 保存并显示 actual route/usage/status | route event DB tests | real provider evidence | PH2/PH4 |
| EKB-CR-FR-047 | AC-CR-047 | key 加密/脱敏/轮换且扫描无明文 | secret regression/rotation | profile UI masked | PH1/PH2 |
| EKB-CR-FR-048 | AC-CR-048 | capability 完整，unknown fail closed | capability tests | selector/attachment behavior | PH2 |
| EKB-CR-FR-049 | AC-CR-049 | 合法 Logo 统一主题/尺寸/fallback | asset/license/lint | profile light/dark | PH2 |
| EKB-CR-FR-050 | AC-CR-050 | 0..N KB server ACL，不信任伪造 ID | retrieval ACL tests | RAG 0/1/N browser | PH6 |
| EKB-CR-FR-051 | AC-CR-051 | Strict 拒答，Enhanced 分隔常识 | answer policy evals | RAG scenario matrix | PH6 |
| EKB-CR-FR-052 | AC-CR-052 | 检索携带 version/page/sheet/paragraph/path | metadata fixture tests | citation viewer | PH6 |
| EKB-CR-FR-053 | AC-CR-053 | citation 持久且指向生成时版本 | citation DB/ACL tests | refresh/branch/version flow | PH6 |
| EKB-CR-FR-054 | AC-CR-054 | 删除/撤权/重建/不兼容内容不检索 | negative retrieval suite | RAG revoked content | PH6 |
| EKB-CR-FR-055 | AC-CR-055 | 指定内容资源 30 天；Provider/Model disable | schema/lifecycle tests | trash/type matrix | PH2 |
| EKB-CR-FR-056 | AC-CR-056 | deleted/expires 原子且可恢复 | transaction/clock tests | delete/restore flow | PH2 |
| EKB-CR-FR-057 | AC-CR-057 | 到期自动幂等 purge 并保留 audit | scheduler/purge integration | clock-controlled run | PH2 |
| EKB-CR-FR-058 | AC-CR-058 | Jobs Center 覆盖队列/dead/retry/cancel/heartbeat | jobs API tests | admin jobs browser | PH1/PH2 |
| EKB-CR-FR-059 | AC-CR-059 | production mode 无 fake success/reply/progress/catalog | mock/fixture boundary scan | QA real-chain evidence | PH7 |
| EKB-CR-FR-060 | AC-CR-060 | 各实施阶段 gate 后备份/原子发布/浏览器/回滚 | deploy pipeline tests | phase release records | PH1–PH8 |

## 3. 非功能需求追踪

| Requirement | Acceptance ID | 验收阈值 | 主要证据 | Phase |
|---|---|---|---|---|
| EKB-CR-NFR-001 | AC-CR-NFR-001 | tenant/actor/ACL 负测全部通过，无存在性泄露 | security matrix | PH1–PH6 |
| EKB-CR-NFR-002 | AC-CR-NFR-002 | crash/重复投递后无永久 RUNNING 和重复业务结果 | chaos/state tests | PH1/PH3/PH4 |
| EKB-CR-NFR-003 | AC-CR-NFR-003 | 目标数据下列表 p95≤400ms，内部 SSE 首事件 p95≤1s | load report | PH7 |
| EKB-CR-NFR-004 | AC-CR-NFR-004 | API/worker/scheduler/storage 边界可独立扩容启动 | deployment topology test | PH1 |
| EKB-CR-NFR-005 | AC-CR-NFR-005 | 成功后读可见；最终一致有 job/reconcile 证据 | transaction/reconcile tests | PH1–PH6 |
| EKB-CR-NFR-006 | AC-CR-NFR-006 | migration 重跑 no-op、verify/rollback rehearsal、checksum 不变 | migration reports | PH1/PH8 |
| EKB-CR-NFR-007 | AC-CR-NFR-007 | API/job/turn/provider/purge 均可按 trace 检索且脱敏 | observability tests | PH1–PH7 |
| EKB-CR-NFR-008 | AC-CR-NFR-008 | keyboard/aria/focus/reduced-motion 自动+人工通过 | a11y report | PH2/PH7 |
| EKB-CR-NFR-009 | AC-CR-NFR-009 | Light/Dark WCAG AA，主要路由残留=0 | contrast/screenshots | PH2/PH7 |
| EKB-CR-NFR-010 | AC-CR-NFR-010 | `/api/v1`/route/adapter/SSE 兼容套件全部通过 | compatibility suite | PH1–PH7 |
| EKB-CR-NFR-011 | AC-CR-NFR-011 | 通用审计/日志无消息正文或附件内容 | content leakage scan | PH1–PH7 |
| EKB-CR-NFR-012 | AC-CR-NFR-012 | 每个新增依赖有版本/license/SBOM/用途/替代 | supply-chain manifest | PH1–PH7 |
| EKB-CR-NFR-013 | AC-CR-NFR-013 | 所有核心旅程有真实浏览器证据，console=0 | browser evidence manifest | PH2–PH8 |
| EKB-CR-NFR-014 | AC-CR-NFR-014 | 原子切换中断≤5分钟且回滚数据无丢失 | production rehearsal/record | PH8 |

## 4. 知识库用户验收

- [ ] TXT 上传后，AI 能引用其固定内容回答。
- [ ] PDF 解析完成，页码引用和回答正确。
- [ ] 10+ 文件批量上传显示总体/单文件真实进度和失败原因。
- [ ] 多级目录路径保留，同名文件规则正确。
- [ ] 重新进入页面，文件和状态仍存在。
- [ ] 删除文件进入回收站。
- [ ] 恢复后重新出现并可检索。

## 5. AI 助手用户验收

- [ ] 连续会话、刷新恢复、会话切换和历史正确。
- [ ] Streaming、Markdown、Code Block 正常。
- [ ] Stop、Retry、Regenerate/分支正常。
- [ ] 文件和图片内容被真实使用。
- [ ] 模型服务新增模型自动出现并调用正确 Provider。
- [ ] 选择知识库后真实检索并返回持久引用。
- [ ] 与 grok-build 达到同等级别的普通对话能力和稳定性。

## 6. 最终 Acceptance Checklist

在实施开始前全部保持未勾选；括号内 AC 集合和 evidence manifest 必须全部通过，不能以单个截图替代：

- [ ] Dark Mode — AC-CR-001–006、AC-CR-NFR-008/009；THEME-E2E-01–04。
- [ ] Knowledge Base Upload — AC-CR-007–020；KB-E2E-01–10。
- [ ] Directory Upload — AC-CR-007–010/014；KB-E2E-03/04。
- [ ] Upload Progress — AC-CR-012–014；KB-E2E-03/07。
- [ ] Document Parsing — AC-CR-015–019；KB-E2E-01/02/08/09。
- [ ] AI Conversation — AC-CR-021–033；CHAT-E2E-01–10。
- [ ] Conversation Persistence — AC-CR-021–023/027/028；CHAT-E2E-01/05/07。
- [ ] Streaming — AC-CR-024/025、AC-CR-NFR-002/007；CHAT-E2E-02/03/09。
- [ ] Attachment — AC-CR-034–040；ATT-E2E-01/02/06–08。
- [ ] Image — AC-CR-037–039；ATT-E2E-03–07。
- [ ] Vision — AC-CR-037/038/048；ATT-E2E-03/04、PROVIDER-E2E-05。
- [ ] Model Provider — AC-CR-041–049；PROVIDER-E2E-01–05。
- [ ] Model Selector Sync — AC-CR-044–046；PROVIDER-E2E-01/02。
- [ ] RAG — AC-CR-050–054、AC-CR-NFR-001/005；RAG scenario manifest。
- [ ] Trash — AC-CR-055/056；TRASH-E2E-01/02。
- [ ] 30-Day Cleanup — AC-CR-057/058；TRASH-E2E-03–05。
- [ ] Browser Test — AC-CR-NFR-008/009/013；完整 browser evidence manifest。
- [ ] Grok Comparison — AC-CR-021–040；GROK-CMP-01–06 全部通过。

## 7. 验收签署

每个 Phase 由 Luna Max 提供实现/检查报告，Sol 审查 diff、证据和剩余风险。用户确认生产体验后才更新最终 checklist；不得因接近工期或仅“理论可用”而勾选。
