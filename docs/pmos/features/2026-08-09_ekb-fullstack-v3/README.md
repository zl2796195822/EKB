---
title: EKB 设计稿十页全栈闭环 v3 文档入口
date: 2026-08-09
status: 已批准
tier: 3
type: feature
authoritative: true
owner: Sol
version: 3.0
purpose: 提供 v3 authoritative 文档链的阅读顺序、权威关系、执行入口和当前状态
companion_docs: [./01_requirements.md, ./02_spec.md, ./03_plan.md, ./04_verification-matrix.md, ./03_plan_review.md, ../../../README.md]
open_issues: []
review_stage: Sol final gate passed
approval: user authorized docs-first fullstack execution direction; Sol approved technical/security/QA document gate on 2026-08-10; implementation/browser/migration evidence remains pending. Do not claim the user reviewed final wording.
---

# EKB 设计稿十页全栈闭环 v3

这是 EKB 设计稿十页真实前后端闭环的 v3 successor authoritative 文档链。Sol final gate 已于 2026-08-10 通过，v3 现为已批准的文档 authority，并取代 v2 冲突边界；v2 的 superseded-by v3 notice 见 [`EKB全前端重建实施文档_v2.0_2026-08-09.md`](../../../EKB全前端重建实施文档_v2.0_2026-08-09.md)。视觉真值仍是仓库根 [`设计稿/index.html`](../../../../设计稿/index.html)，响应式和 token 规则参考 [`apps/web/DESIGN.md`](../../../../apps/web/DESIGN.md)。

## 阅读顺序

1. [`01_requirements.md`](./01_requirements.md)：Tier 3/type feature 需求、十页旅程、52 FR、18 NFR 和最终交付边界。
2. [`02_spec.md`](./02_spec.md)：已批准的 API、SQL、migration chain、安全、adapter 和 rollout authority。
3. [`03_plan.md`](./03_plan.md)：已批准的 6 个竖切阶段、38 个 TDD 任务、依赖、回滚和最终 TN。
4. [`04_verification-matrix.md`](./04_verification-matrix.md)：FR/NFR 逐项证据、十页双视口和 API/DB/迁移/回滚硬门禁。
5. [`03_plan_review.md`](./03_plan_review.md)：两轮自审及最终一致性结论。

## Authoritative / superseded 关系

- v3 `01_requirements.md`、`02_spec.md`、`03_plan.md`、`04_verification-matrix.md` 是当前 feature 的执行 authority。
- v2 根文档当前保留原正文、历史执行记录和旧阶段证据，并已标记 superseded-by v3；v3 取代其中 frontend-only、禁止后端修改和将核心动作以 disabled/unavailable 代替真实交付的冲突边界。
- 根 API、数据模型、测试策略、架构文档只追加 v3 指针和兼容声明，不复制 v3 全套合同；具体内容以 v3 spec/matrix 为准。
- 所有新增 API/DB 变更 additive/backward-compatible；现有 FastAPI + SQLAlchemy、SQLite/PostgreSQL、`/api/v1`、AuthContext、audit、SSE v2 和 app-v2 adapters 保留。

## 执行入口

Sol 在完成本链文档审查后，按 [`03_plan.md`](./03_plan.md) 串行调度一个 `luna_max_worker`；首个业务实现任务从 P1/V3-T01 开始。每个阶段必须独立灰度并完成自己的 migration、API、adapter、页面和测试证据，不能把 migration、API、UI 按水平层分拆。

最终 TN 从仓库根执行：

```bash
cd /Users/alin/EKB
git diff --check -- .pmos docs
test -f docs/pmos/features/2026-08-09_ekb-fullstack-v3/04_verification-matrix.md
```

实现阶段另执行 `03_plan.md` 中的 backend pytest/ruff、frontend build/type、五版本 migration verify/rollback、contract/security 和 Codex in-app Browser 工作流。

## 当前状态

M0–M5 的前端/app-v2 已完成到现有 Team/Profile 端点接入；知识库/文档、助手 SSE v2 和 route manifest 的既有接入也已记录。所有缺后端而显示为 disabled/unavailable 的核心入口、Analytics/Apps/Recycle 占位、实时身份授权、trash/分享/标签/收藏/项目/附件/模型/联网搜索/聚合和凭据闭环均仍为 v3 pending。当前没有代码实现、migration 执行或浏览器 QA 证据；不得把本 doc-only milestone 当作交付完成。
