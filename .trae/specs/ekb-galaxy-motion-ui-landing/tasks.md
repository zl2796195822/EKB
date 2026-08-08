# EKB · Galaxy Motion v4 UI 落地 - 任务分解与实现计划（tasks.md）

> **验收文档**：配套阅读 `spec.md` 的 AC-1 ~ AC-8。所有任务完成后，必须对应 Checklist 打勾。

---

## [x] Task 1 · 工程基建 + 设计系统 + 路由骨架（P0 · 入口）
- **Priority**：high
- **Depends On**：None（Spec 批准后第 1 步开工）
- **Description**：
  1. 在 apps/web 下 `npm i gsap @phosphor-icons/react --save`（固定语义化版本，写回 package.json）。
  2. 在 apps/web 根初始化 `DESIGN.md`：记录 Galaxy Motion v4 的 token 表（颜色/圆角/字号/阴影/ease）+ 组件规则 + 对齐基准 `.content-rail` 规则。
  3. 在 apps/web 根初始化 `PRODUCT.md`：记录产品定位（和 docs/UI设计方案 一致）。
  4. 重写 / 扩展 `apps/web/src/styles.css`：
     - 保留现有 auth/app-shell 结构的「必需逻辑类」（如 `.primary-button`、`.global-error`、`.avatar`、`.sr-only`），避免破坏业务；
     - 在 CSS 顶部追加 Galaxy Motion v4 设计稿的 `:root` token（--c-primary / --c-accent / --c-paper-raised / --r-xs..xxl / --fs-* / --shadow-* / --ease-*）；
     - 新增 `.content-rail` 统一基准容器类（max-width、左右 padding、box-sizing、margin auto，用于所有页面）；
     - 新增 `.page` / `.page.active`（4 Tab 容器）、stagger 入场动画（`.stagger` 初始 transform+opacity，加 `.in` 类播放）；
     - 新增 Galaxy 粒子背景层 `.bg-galaxy`（用 `::before` / `::after` 叠加径向渐变，减少 JS 粒子；GSAP 粒子只在 hero 处可选注入）。
  5. 抽 `apps/web/src/hooks/useHashPage.ts`：自定义 SPA 路由 hook，支持默认页 `qa`、`goToPage(page)`、`useHashPage()` 返回 `[activePage, setPage]` + `hashchange` 监听 + 键盘快捷键 `⌘/Ctrl+1..4`；支持 `prefers-reduced-motion` hook。
  6. 抽 `apps/web/src/components/ui/PillNavTabs.tsx`（顶栏 4 Tab，on 态深色反色 pill）+ `DockBar.tsx`（底部 Dock 4 Phosphor 图标）。
  7. 改造 `apps/web/src/App.tsx` 外壳：保留登录态分支（auth-screen），登录后主内容替换为：顶栏 PillNav + 4 个 Page Container（`.page#page-qa/.page-kb/.page-search/.page-ops`）+ DockBar；4 个 Page 用 `React.lazy` + `<Suspense>` 分包：
     - `pages/QAPage.tsx`
     - `pages/KbPage.tsx`
     - `pages/SearchPage.tsx`
     - `pages/OpsPage.tsx`
     （此时 4 个页面内容可先用简单占位 `<section><h1>XXX 页</h1></section>`，在后续 Task 2-5 填充。）
  8. 登录页 auth-screen 视觉升级：用 Galaxy Motion 有机 auth-panel + 渐变背景 + bg-galaxy 层；保留现有 `handleLogin` API 调用和错误/加载状态。
- **Acceptance Criteria Addressed**：G1, G2, FR-1, FR-6, NFR-7, NFR-8, **AC-1, AC-2, AC-8**
- **Test Requirements**：
  - `programmatic` TR-1.1：4 种切页入口（Tab click / Dock click / 键盘 Ctrl+1..4 / URL hash change + refresh）active 状态正确，hash 同步。`document.querySelectorAll('.page.active').length === 1`
  - `programmatic` TR-1.2：`npm run build` 在 Task1 完成后仍然 exit code 0，tsc 0 error。
  - `programmatic` TR-1.3：`document.documentElement.style.getPropertyValue('--c-primary')` 非空，≥ 20 个 token 变量存在。
  - `human-judgement` TR-1.4：4 个 Tab 的 on 态视觉一致（深色 pill 背景），Dock 图标激活态有高亮。登录新面板视觉一致，输入框和按钮符合 Galaxy 风格（和 UI 设计稿近似而非 100% 像素对，但必须是有机非方盒）。
- **Notes**：Task1 是所有后续任务的前置。必须确保 build 绿、token 存在、路由骨架稳。

---

## [x] Task 2 · 通用 UI 组件库 + 设计稿原子化抽取（P0 · 构建块）
- **Priority**：high
- **Depends On**：Task 1
- **Description**：
  1. `apps/web/src/components/ui/` 目录建立 8 个核心基础组件（props 全 typed，TS strict）：
     - **GalaxyCard**：非对称圆角有机卡容器（border-radius 不对称，内边距，悬浮上抬 + 阴影扩散过渡，可选左色条 `colorStripe?: string | KbVisibility`）。
     - **HitCard**：检索命中卡容器（基于 GalaxyCard，扩展 corner-tag（topRight tag）、title、snippet、hit-highlight 关键词渲染 util、meta chips、类型色条）。
     - **ScopeChip**：8 源检索 chip 组组件（`items: Array<{label, value, count?}>` + `defaultValue` 或 `value` 受控 + `onChange`），on 态深色反色。
     - **CornerTag**：右上角 pill 角标（top-right 向外突出 4px，圆角 pill 左侧平直）。
     - **MetricCard**：运营面板指标卡（指标名 + 当前值 + delta（↑↓色）+ SVG mini sparkline（接收 `points: number[]` prop））。
     - **Pagination**：翻页 pill 组件（`totalPages`、`currentPage`、`onChange`），居中显示，1/2/3/…/N/上一页/下一页。
     - **GalaxyHero**：通用大搜索框/标题英雄区（标题/副标题/可选 secure-chip/可选大搜索框 input + 提交按钮/可选 scope chips 容器）。
     - **GalaxyFormField**：表单 label + select / input / textarea 的统一封装（focus halo 3px，Galaxy 风格圆角 + 浮动 label 可选）。
  2. `apps/web/src/utils/text.ts` 工具：
     - `markHitHighlight(snippet: string, query: string): ReactNode[]` — 用 `<mark class="hit-hl">` 荧光高亮匹配到的查询分词；对 query 做中文分词 + 英文单词分割（简单空格分隔即可）。
     - `relativeTime(iso: string): string` — 把 updated_at 格式化为相对时间（"3 小时前"）。
  3. CSS 追加对应组件类名的 Galaxy 样式：`.hit-card`、`.corner-tag`、`.scope-chip`、`.metric-card`、`.pagination-pill`、`.galaxy-hero`、`.galaxy-form-field` 等，样式从 v4 设计稿转写（注意保留非对称圆角值、阴影、过渡 duration/ease）。
  4. 单文件 Storybook（无 Storybook 库，用临时页 `/~ui-showcase` 或 `SearchPage` 占位里展示），所有组件至少有一种 on / off 态。
- **Acceptance Criteria Addressed**：G2, G4, FR-7, **AC-2, AC-4 (组件样式前置), AC-8**
- **Test Requirements**：
  - `programmatic` TR-2.1：8 个组件能在 React 组件树正确渲染，DOM 元素存在且 CSS class 正确；`tsc -b` 无 error。
  - `programmatic` TR-2.2：`markHitHighlight('紧急变更与审批签字', '变更 审批')` 返回的 JSX 中 `<mark class="hit-hl">` 出现 ≥ 2 次。
  - `human-judgement` TR-2.3：组件视觉与 v4 设计稿 1:1 对比（HitCard 非对称圆角 / 左色条 / CornerTag 突出 / ScopeChip on 态深色反色）。
  - `human-judgement` TR-2.4：focus-visible halo ≥ 3px oklch 半透明青色环，符合 WCAG AA。
- **Notes**：组件必须 props 纯组合，不内部调用 API；不要把状态（搜索 query、当前分页）写死在组件里。HitCard 接受 `SearchResult` 类型的 props。

---

## [x] Task 3 · 问答助手页（QAPage）现代化 + 流式逻辑合并（P1）
- **Priority**：high
- **Depends On**：Task 1, Task 2
- **Description**：
  1. 新建 `apps/web/src/pages/QAPage.tsx`：使用 GalaxyHero（标题「让每个答案回到证据」+ 权限 secure-chip + 8 ScopeChips）。
  2. 保留现有业务逻辑：App.tsx 中的 `ChatPanel`、`messages` state、`searchResults`、`conversationIdRef`、`handleAsk`（SSE 流式）、`handleFeedback`、`handleCancelAsk` 必须完全迁移 / 复用。不能重写 ApiClient 调用。
  3. 把 `ChatPanel` 从旧视觉升级：
     - 消息气泡 `.message-user`/`.message-assistant`：应用 Galaxy 有机非对称圆角。
     - 空会话区：empty-chat 升级为 Galaxy 风格（大图标、引导提问 4 个预设卡 Chip，点击自动填充输入）。
     - 流式光标 blink 动画保持，但支持 `prefers-reduced-motion` 降级为静态 `_`。
     - Composer 升级为 GalaxyForm（textarea + send / stop 按钮，Enter 提交，Shift+Enter 换行，Esc 取消）。
     - 引用区 `citation-list` 升级为 HitCard 风格：每 citation 一张 mini HitCard（带章节路径、版本、更新时间、左色条根据 mime）。
  4. 保留 `KnowledgeSidebar`：放入 QAPage 左侧（作为内容区的左 aside），样式升级为 Galaxy（KB 项 hover 态、文档列表）。
  5. 顶栏保留当前租户 + 用户 + 会话历史菜单（ConversationHistory），将其放入 `.content-rail` 内部顶栏，保证和 hero 区同对齐。
  6. 会话历史从下拉面板升级为右侧可展开的 Drawer（或保留下拉但样式升级为 Galaxy card + scroll 容器），保留 select / delete。
- **Acceptance Criteria Addressed**：G3, FR-2, FR-9, **AC-1, AC-3, AC-7, AC-8**
- **Test Requirements**：
  - `programmatic` TR-3.1：QA happy path 用 mock server（或真实 API 跑）→ 提交问题后 SSE token 数 ≥ 5，流式光标显示动画；取消按 Esc → finishReason=cancelled；反馈 UP → 状态显示反馈已发送。
  - `programmatic` TR-3.2：QAPage 主内容区 `.content-rail` 左边界 = GalaxyHero 主内容左边界（evaluate 差 ≤ 1px）。
  - `human-judgement` TR-3.3：新视觉和 v4 设计稿 QA 页「对话主场景 · 含证据链」近似。
  - `human-judgement` TR-3.4：空会话有引导提问 4 个 chip，点击即填入 composer。
- **Notes**：这是业务主流程，必须保证回归零；流式状态驱动、AbortController、引用合并逻辑**必须原封不动保留**，只改渲染层。

---

## [x] Task 4 · 检索中心页（SearchPage）真实 API 打通 + 量化对齐（P1 · 核心交付）
- **Priority**：high
- **Depends On**：Task 1, Task 2
- **Description**：
  1. 新建 `apps/web/src/pages/SearchPage.tsx`，结构严格对应 v4 设计稿：
     - `.search-hero`（GalaxyHero：大搜索框 + 8 ScopeChips + secure-chip「MULTI-SOURCE · 8 源检索」）。
     - `.search-filters`（4 列 grid：GalaxyFormField 封装的 4 个筛选项：知识库多 chip / MIME 类型 select / 时间范围 chip 组 / 作者部门 text 输入）。
     - `.result-summary`（命中统计 + 耗时 + 过滤后可见数 + 排序 mini tabs：综合/最新/仅文档/仅问答）。
     - `.search-results`（`SearchResult[]` 映射成 HitCard 列表，含 corner-tag：第 1 张「TOP 命中」、权限受限文档「权限受限」、FAQ 类型「QA 命中」）。
     - `.pagination`（Pagination 组件：16 页 mock，第一页 on）。
  2. 真实 API 接入：
     - 用现有 `client.search(query, kbId)` → 因为它只接受单 KB，先默认用「第一个授权的知识库」作为 kbId；如果多 KB 被选中（筛选器的「知识库多 chip」），就并行搜索多个 KB 然后前端合并（Promise.allSettled + 排序合并，按 score 降序）。
     - `markHitHighlight` 对每个 `SearchResult.snippet` 做关键词高亮。
     - 左色条根据 `mime_type`（或 SearchResult 里缺失时，根据 title 扩展名推断 pdf/doc/xlsx/ppt/qa-history → 映射到对应颜色）。
     - corner-tag 的「权限受限」：根据 `SearchResult.score` 或文档状态字段（如果 API 没返回权限信息 → 临时把第 5 条 mock 为权限受限；真实权限判断接入后迁移，在此项目用 mock 规则即可）。
  3. 筛选器过滤前端回退：当 MIME / 时间范围 / 作者 被选中时，对 `results` 做 Array.filter（前端回退），同时 result-summary 显示「过滤后 X 条可见」。
  4. 量化对齐保证：`hero/filters/result-summary/search-results` 四个主容器的内容区域**必须套 `.content-rail` 或用统一 padding-inline**，保证 evaluate 时 search-big（大搜索框）/ filters / hit-card:first-child 的 `left` 差 ≤ 1px，`width` 差 ≤ 1px。
  5. 响应式：≤ 768px `.search-filters` grid 变 2 列；≤ 560px 变 1 列。
- **Acceptance Criteria Addressed**：G4, G7, FR-4, FR-9, NFR-1/4/6，**AC-4, AC-7, AC-8**
- **Test Requirements**：
  - `programmatic` TR-4.1：真实 `client.search` 被调用次数 ≥ 1（页面首次有 query 时）；SearchResults DOM 元素数 === 接口返回长度；筛选后数量相应减少。
  - `programmatic` TR-4.2：量化对齐 evaluate 脚本（和 spec.md AC-4 一致） → 差 ≤ 1px。
  - `human-judgement` TR-4.3：视觉 6 项（scope on/4列/有机圆角/左色条类型色/荧光高亮/翻页居中）和 v4 设计稿一致。
  - `human-judgement` TR-4.4：corner-tag「TOP 命中」「权限受限」「QA 命中」正确出现至少 1 次/每个。
- **Notes**：量化对齐是交付门线（TR-4.2），不达标不进入 Task 5。

---

## [x] Task 5 · 知识库卡片页（KbPage）+ 运营面板（OpsPage）占位 + 整合验收（P1）
- **Priority**：medium → high（交付必须）
- **Depends On**：Task 1, Task 2, Task 3, Task 4
- **Description**：
  1. 新建 `apps/web/src/pages/KbPage.tsx`：
     - 顶部「新建知识库」Galaxy Form（输入 name + 可选 visibility select）→ 调用 `createKnowledgeBase` → 卡片即出现。
     - 中部 GalaxyCard 网格（`kb_ids.map` → GalaxyCard + visibility 左色条 + document_count pill + updated_at 相对时间 + Delete 图标 → 调 `deleteKnowledgeBase`）。
     - 卡片展开：点击卡片打开右侧 Drawer 面板（Galaxy style），显示 Upload 文件按钮 + 文档列表（保留 status badges、delete doc）。
  2. 新建 `apps/web/src/pages/OpsPage.tsx`：
     - 顶部标题「今日运行态势」+ 更新时间戳 + 状态环（SVG mock）。
     - 2×4 MetricCard（8 指标：问答量 1,284 +↑、准确率 89.2% +↑、拒答率 5.4% -↓、引用正确率 82% +↑、活跃用户 42、知识库 12、文档 1,284、P95 2.1s -↑）。
     - 2 张 SVG 图表卡：
       - 7 日问答量折线（`points = [120,142,98,210,268,302,354]`）
       - 各知识库命中率柱（7 KB，柱色用 --c-primary 渐变）
     - 2 个快捷入口按钮：审计日志 / 低置信队列 → 点击触发 toast「此模块即将上线（运营权限接入后可用）」（无跳转）。
  3. **4 页最终整合验收**：
     - 把 App.tsx 的 lazy 4 页替换为真实内容（不再占位）。
     - 全局状态：`knowledgeBases / documents / client / session / messages / searchResults / conversations / activeConversationId` 在 QAPage 和 KbPage 共享——必要时将这些状态从 App.tsx 「下放到 Context」或「通过 props 传递」。优先 props / composition；避免全局 Context 过度。
     - 保证切页不丢：QAPage 搜索输入、SearchPage 搜索条件、KbPage 当前选中 KB、OpsPage 当前筛选。
  4. **最终回归 + 构建**：全流程 build + 手动过 AC-1~AC-8 检查项，在 checklist.md 打勾。
- **Acceptance Criteria Addressed**：G5, G6, FR-3, FR-5, FR-8, **AC-1, AC-5, AC-6, AC-8**
- **Test Requirements**：
  - `programmatic` TR-5.1：KB 创建 / 删除 / Upload / Delete doc 都能真实调用 API（或在 mock 模式下调用次数正确）。
  - `programmatic` TR-5.2：OpsPage 8 MetricCard + 2 图表 + 2 按钮 DOM 元素存在；按钮点击 toast 出现并 3s 后消失。
  - `programmatic` TR-5.3：`npm run build` exit code 0，无 warning。
  - `human-judgement` TR-5.4：KB 卡片视觉符合 Galaxy（有机圆角、visibility 色条、文档数 pill）；Ops 页 8 指标卡 delta 颜色正确（↑绿↓红）。
  - `human-judgement` TR-5.5：4 页切页时 QA 消息不丢失、检索条件不丢。

---

## Task 执行顺序（DAG）
```
Task1 (基建)
 └─> Task2 (通用组件)
      ├─> Task3 (QA 页)
      ├─> Task4 (Search 页 + 量化对齐)
      └─> Task5 (KB + Ops + 整合验收)
```
Task3 和 Task4 可以在 Task2 完成后并行（如果有资源），但此项目默认顺序执行（单 Agent）。
