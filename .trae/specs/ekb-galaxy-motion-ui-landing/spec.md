# EKB · Galaxy Motion v4 设计落地 - 产品需求规格说明书（PRD / Spec）

## Overview
- **Summary**：将 HTML 高保真设计稿 `ekb_ui_design_v4__galaxy-motion.html`（Galaxy Motion Edition，4 Tab SPA：问答助手 / 知识库 / 检索中心 / 运营面板）无损落地到真实前端工程 `apps/web`（Vite7 + React19 + TypeScript + Plain CSS），并与现有登录流、SSE 问答流、知识库 CRUD、会话历史、反馈等真实 API 业务逻辑合并。
- **Purpose**：解决当前 v0.1 前端 UI 视觉与官方 UI 设计文档（`docs/UI设计方案_v1.0.md`）脱节的问题，从「朴素极简企业绿」升级到「Galaxy Motion — 信任优先 + 高效工作 + 克制化动效 + Anti-Slop 有机容器」的 v4 设计语言，同时补齐缺失的 3 个核心页面骨架（检索中心 / 知识库网格 / 运营仪表盘）。
- **Target Users**：内部运维工程师（QA 问答）、知识管理员（知识库管理 + 检索）、平台管理员（运营看板 + 审计入口）、外部客户租户（白标门户入口：问答 + 检索）。

## Goals
- **G1 · 4 Tab SPA 架构落成**：顶栏 Tab + 底部 Dock + URL hash 同步 + 键盘快捷键（Cmd/Ctrl+1/2/3/4），四页面路由互切不丢状态（搜索条件、知识库选中、当前会话、运营筛选）。
- **G2 · Galaxy Motion 设计系统落盘**：把 v4 设计稿的 CSS 变量 token（色彩/字号/圆角/阴影/ease）、非对称有机圆角、左侧色条、荧光高亮、角标、Galaxy 粒子背景、stagger 入场动画等，转写为工程内 `DESIGN.md` + `styles.css`（CSS 变量）+ 通用组件，作为项目唯一视觉事实来源。
- **G3 · 问答助手页（QA）现代化**：保持现有 SSE 流式、引用、反馈、会话历史、知识库侧边栏等业务逻辑**完全可用**，视觉升级为 Galaxy Motion 风格：英雄区标题、Scope chips、证据链卡片、消息气泡非对称圆角、流式光标克制动效、mini 会话面板。
- **G4 · 检索中心页（Search）真实 API 打通**：保留 v4 设计稿的 scope chips + 4 列筛选器 + 有机命中卡（非对称圆角 + 左色条 + corner-tag + mark.hit-hl 荧光高亮 + 翻页 pill）**全部视觉细节**，用 `ApiClient.search()` 多 KB 检索，**筛选器控件绑真实字段**（kb_ids / mime 类型 / 时间区间 / 作者部门）。
- **G5 · 知识库页（KB）卡片化**：从左侧单列列表升级为 Galaxy 卡片网格（非对称圆角、文档数量 pill、可见性徽章、最近更新时间、hover 微动效 + 翻转展示），保留 Create/Delete KB 和 Upload/Delete Doc 的业务能力。
- **G6 · 运营面板页（Ops）骨架占位**：8 张运行指标卡（问答量、准确率、拒答率、引用正确率、活跃用户、知识库数、文档数、P95 延迟）+ 2 个占位折线/柱图（用 SVG mock，不做真实图表库引入）+ 2 个操作快捷入口（审计查看 / 低置信度队列），预留 `/admin` 接口接入点。
- **G7 · 量化对齐 & 视觉审查自动化留痕**：每页关键区块（搜索框/筛选器/命中卡、英雄区/指标卡、顶栏/内容区）必须遵守「统一基准容器」原则，用浏览器量化 box-model 验证对齐差 = 0；建立视觉检查点清单（人工判断）。
- **G8 · 响应式 + 可访问性**：断点 1100px（侧边栏缩为导航条）、768px（顶栏 Tab 垂直堆叠、筛选器变 2 列），符合 WCAG AA：语义化 HTML、可见焦点、`prefers-reduced-motion` 全部动效降级、键盘可达。

## Non-Goals (Out of Scope)
- **后端改动**：不修改任何 `apps/api`（Python FastAPI 业务、Schema、接口、权限），前端只调用现有 API；后端功能缺口（如搜索多 KB 筛选参数）后续单独立项。
- **真实运营数据接入**：运营面板只用 mock 指标值，不接入 `/admin` 路由（权限和真实 API 需单独项目）。
- **深色模式**：v4 设计稿为浅色模式，深色模式后续版本迭代，不在本项目。
- **组件库/构建器替换**：不引入 Tailwind、shadcn/ui、Next.js、React Router。项目保持 Vite + React + Plain CSS + 自定义 SPA 路由。
- **Storybook / 视觉回归测试平台**：不建立 Storybook，只保留人工视觉审查清单 + 浏览器量化对齐脚本（evaluate）。
- **文档预览器**：知识库的「文档内容在线阅读」暂不做，保留上传/删除。
- **国际化 i18n**：v1 只支持中文，文案常量放页面组件内，不抽 i18n。
- **营销官网 / 外部登录品牌壳**：登录页视觉升级，但不做租户选择、SSO、客户门户白标壳。

## Background & Context
### 现有资产（必须利用、不得丢弃）
1. **现有前端工程** `apps/web`：Vite 7 + React 19 + TS + Plain CSS。`App.tsx` 实现了：登录流（ApiClient.login + refresh）、左侧 `KnowledgeSidebar`（知识库列表 + 文档列表 + Upload/Create/Delete）、顶栏 `ConversationHistory`、`ChatPanel`（SSE 流式问答 + 证据引用预览 + 反馈），完全可用（已通过 M1/M4-5 QA 用真实接口验证）。
2. **现有 API 契约** `apps/web/src/lib/api.ts` + `types/api.ts`：已封装 16+ 个核心 API（Auth、KB CRUD、Doc CRUD、Search、QA SSE、Feedback、Conversations）。本项目必须继续使用 `ApiClient`，不重写。
3. **高保真 HTML 设计稿** `output/ekb_ui_design_v4__galaxy-motion.html`：~1700 行，包含 4 页完整容器 + GSAP/原生 stagger 入场 + Galaxy 粒子动画 + 设计 tokens + 交互细节 + 量化对齐规则（已验证 filters-vs-big = 0、card-vs-big = 0）。设计语言：有机非对称圆角（不对称 border-radius）、左侧色条按类型区分、荧光笔 mark.hit-hl、右上角 pill 角标、非立方体 Grid Card、scope chips on 态深色反色、Pill 翻页、底部 Dock 快捷导航、Galaxy 粒子背景层。
4. **官方 UI 设计文档** `docs/UI设计方案_v1.0.md`：已声明「信任优先 / 高效工作 / 边界可见 / 状态完整 / 克制动效 / 一致性优先」六项设计原则，候选 token 色彩与 Galaxy v4 完全一致（oklch 蓝青 primary 55% / 青绿 accent 65% / 暖灰背景 98%）。
5. **Skills 目录使用规范** `docs/开发Skills与使用规范_v1.0.md`：galaxy（微组件）、gsap-skills（时间线）、taste-skill（Anti-Slop）、react-best-practices（性能/重渲染）、web-design-guidelines（可访问性/表单/响应式/动效）。
6. **对齐工程实践**：经验 1447092（检索中心 CSS 对齐统一基准、量化差=0）必须延续到其他 3 页。经验 737534（不要零散 padding 覆盖，一条规则 + 一个对齐基准容器类 `.content-rail` 搞定所有页）。

### 关键技术约束（必须遵循）
- **React 19 + TS strict**：不降级；所有组件 props 显式 typed，无 any。
- **Plain CSS 不引入 Tailwind/shadcn**：v4 的样式用 CSS 变量（`--c-*`、`--r-*`、`--fs-*`、`--ease-*`）迁移，和现有 `styles.css` 风格保持一致。
- **不引入 react-router**：用 hashchange + `useHashPage()` 自定义 hook，和 v4 设计稿 `goToPage()` 逻辑保持一致（无需新依赖，零路由冲突）。
- **动效库**：v4 用了 GSAP CDN，落地时 `npm i gsap --save`（版本固定 ~3.12）；但 `prefers-reduced-motion` 开启时，所有时间线替换为 CSS `transition: all 0s` 或直接跳过 stagger。
- **图标库**：`npm i @phosphor-icons/react --save`（轻量，SVG，支持 Tree-shaking），不混合 emoji 占位图，不引入 lucide（避免双图标库）。
- **无新增图表库**：运营面板折线/柱图用手写 SVG mock，不引入 recharts/echarts（减少依赖，减少 bundle）。

## Functional Requirements
- **FR-1（4 Tab SPA 路由）**：顶栏导航（Pill Nav）、底部 Dock（4 图标）、键盘 Ctrl/⌘+1..4 三种入口切换 4 页；URL hash 同步（`#qa` / `#kb` / `#search` / `#ops`），刷新保留当前页。切换时仅激活 `.page.active`，页面内用户输入不丢（搜索框输入、当前会话、知识库选中、筛选器值）。
- **FR-2（QA 问答页视觉迁移 + 逻辑合并）**：Hero 标题「让每个答案回到证据」+ 租户/权限范围 chip + 8 Scope 源检索 chips（视觉用，不改变现有单 KB 问答 API）；消息列表保留 `ChatPanel` 流式逻辑，但用户/助手气泡替换为非对称圆角样式，引用卡片升级为 hit-card 风格（左侧色条 + 版本 pill + 章节路径）；底部 Composer 升级为 galaxy 风格（focus halo + 有机圆角 + Stop 按钮占位）；会话历史面板从下拉升级为 Drawer 或保持但样式升级。
- **FR-3（KB 知识库卡片页）**：顶部「新建知识库」Galaxy Form 卡片 + 网格卡片列表（每个 KB 一张 Galaxy Card：非对称圆角、左侧色条按 visibility、文档数量 pill、更新时间、作者、Delete 图标按钮、hover 时轻量上抬 + 阴影扩散、可选翻转显示「最近文档 Top3」）；卡片展开/侧边抽屉显示文档列表（保留 Upload + Delete 逻辑）。
- **FR-4（Search 检索中心页 · 核心页面）**：Hero 搜索区（大搜索框 + scope chips + 权限 pill）→ 4 列筛选器 Grid：知识库多选 chip / MIME 类型 / 时间范围 pill / 作者部门输入 → 结果摘要 bar（命中数 + 耗时 + 过滤后可见数 + 排序 mini tabs）→ 命中卡列表（`SearchResult[]` 渲染，类型左色条：PDF红 / DOC蓝 / XLS绿 / PPT橙 / QA青，不对称圆角 4/18/14/18，`corner-tag` 「TOP命中/权限受限/QA命中」，`mark.hit-hl` 荧光笔关键词高亮，底部 meta chips（库归属/作者/更新时间/阅读数/权限））→ 翻页 pill 组（居中）。筛选器「知识库」和「类型」和「时间范围」需能传递到 `ApiClient.search()` 的参数（如果后端参数暂时不支持多 kb，用单 kb 搜索 + 前端过滤回退，API 扩展记为后续 TODO）。
- **FR-5（Ops 运营面板占位页）**：2x4 指标卡（每张卡：指标名、当前值、环比 delta pill、mini sparkline SVG）→ 2 个占位图表卡（7 日问答量折线、各知识库命中率柱图，用 SVG mock）→ 快捷操作入口（审计日志查看、低置信度问答队列，用空占位按钮跳转 404 或显示「此模块即将上线」）。页面展示「今日运行态势」标题 + 运行状态环（mock）。
- **FR-6（登录页视觉升级）**：保留现有登录 API 和表单逻辑，把面板升级为 Galaxy Motion 风格：有机 auth-panel、渐变背景、微光晕、Galaxy 粒子背景层（动效禁用时降级为纯色）。
- **FR-7（通用组件抽取）**：`components/ui/` 下抽取 Galaxy 基础组件：`GalaxyCard.tsx`、`HitCard.tsx`、`ScopeChip.tsx`、`CornerTag.tsx`、`MetricCard.tsx`、`Pagination.tsx`、`PillNavTabs.tsx`、`DockBar.tsx`、`GalaxyHero.tsx`、`GalaxyFormField.tsx`、`markHitHighlight(text, query)` util。所有组件 prop typed，避免布尔膨胀（优先组合 / children 模式）。
- **FR-8（页面状态完整）**：4 页 + 登录页都补齐：加载态（骨架屏 / 占位）、空态（空知识库、无搜索结果、无权限看板）、错误态（网络错误、无权限、接口失败重试）、处理中（搜索 loading、上传进度、问答流式）。
- **FR-9（对齐基准容器）**：所有页面主内容**必须套统一类 `.content-rail`**（左右 padding 一致、最大宽度一致），用 browser_evaluate 可量化验证关键对齐差=0（如 QA hero vs 消息区、Search scope chips vs filters vs summary vs hit-cards）。

## Non-Functional Requirements
- **NFR-1（性能）**：首屏 bundle ≤ 280KB gzip；GSAP 和图标按需导入，不在首屏未命中页加载（`React.lazy` 4 个页组件，按需分包）。
- **NFR-2（动画性能）**：GSAP 时间线仅在 `prefers-reduced-motion: no-preference` 时启用，动画只触发 transform 和 opacity，不触发重排；低端 macOS + 2.5G 主频页面滚动 FPS ≥ 40。
- **NFR-3（可访问性 WCAG 2.1 AA）**：所有可交互元素 tab 可达、焦点可见 halo ≥3px、语义化 `<nav> <main> <section> <article> <button>`、色彩对比度正文 ≥4.5:1、辅助 ≥3:1；读屏能识别流式光标动画停止（`aria-live:polite`）。
- **NFR-4（响应式）**：3 个断点 → ≥1280px 桌面 / 1100px 侧边条折叠 / ≤768px 移动端：筛选器 4→2 列，顶栏 Tab 横向滚动，Dock 4 图标隐藏，命中卡宽度 100%。
- **NFR-5（键盘路径）**：顶栏 Tab `←/→` 切换，回车激活；翻页 `PgUp/PgDn` 高亮页码；搜索 `⌘K` 聚焦大搜索框；4 页 `⌘+1..4`；问答提交 `Enter`，换行 `Shift+Enter`；取消问答 `Esc`。
- **NFR-6（TypeScript Strict）**：`tsc -b` 0 错误，无 `any`、非空断言最小化，无 any 静默。
- **NFR-7（构建）**：`npm run build`（`tsc -b && vite build`）无警告，构建产物在 `apps/web/dist`。
- **NFR-8（包体积）**：不引入未使用依赖（Tailwind、React Router、recharts、echarts、lucide-react 等全部不引入）；生产 `package.json` dependencies 只新增 gsap + @phosphor-icons/react 两项。

## Constraints
- **Technical**：Vite7 + React19 + TypeScript strict + Plain CSS；只允许新增 **gsap** 和 **@phosphor-icons/react** 两个 runtime 依赖。不允许引入 SSR、Next.js、Tailwind、React Router、组件库（MUI/Antd/Radix）、图表库。
- **Business**：必须在 v4 设计稿和已有业务逻辑之间做合并，**不能丢弃**现有 QA 流式（SSE）、反馈、会话、知识库 CRUD、上传、权限错误态等。
- **Timeline**：交付分 4 个垂直切片（Task1→Task4），每个切片完成后由用户可通过 `npm run dev` 肉眼验收，不等到最后一次性合。
- **Dependencies**：后端 FastAPI 服务本地运行时端口 8023（默认 `VITE_API_BASE_URL`），前端默认 `http://127.0.0.1:8023/api/v1`，不得硬编码修改。

## Assumptions
- **A1**：用户已同意使用 Galaxy Motion v4 的设计语言（之前 4 轮反馈和设计稿迭代都以 v4 通过）。
- **A2**：`ApiClient.search()` 当前参数是 `(query, kbId)`，不支持多 KB + 时间 + mime 过滤；本项目前端先做「前端过滤回退」（搜索全 KB 再过滤），后端参数扩展列为后续任务（不在此 Spec 范围）。
- **A3**：运营面板 API（指标/审计/低置信队列）暂时不可用，本项目用 mock 数据硬编码在组件里，不请求 API。
- **A4**：侧边栏（KnowledgeSidebar）现有 UI 是列表式，此项目将它并入「问答助手」和「知识库」两页的上下文侧边；4 Tab 导航以顶栏 + Dock 为主。
- **A5**：登录后用户 `session.tenants[0]?.name` 显示在顶栏的方式仍保留。
- **A6**：项目本地已可运行 `apps/web` 的 dev server（`npm run dev`）。

## Acceptance Criteria

### AC-1：4 Tab SPA 路由完整可用
- **Given**：用户已登录，处于 QA 页。
- **When**：用户点击顶栏「知识库」Tab、或点击底部 Dock 第 2 个图标、或按键盘 `⌘+2`、或修改 URL hash 为 `#kb` 刷新页面。
- **Then**：4 个入口都能把激活态切到「知识库」页，显示 KB 卡片网格（非空态/空态），`window.location.hash` 同步为 `#kb`，刷新后仍停在 KB 页；QA 页的搜索输入和会话不会因为切页丢失。
- **Verification**：`programmatic`（4 种入口×hash state×refresh 不丢 active 状态，`getBoundingClientRect` 页面可见）+ `human-judgment`（Tab on 态高亮、Dock 图标正确选中）。

### AC-2：Galaxy Motion 设计系统 token 正确落盘
- **Given**：任何页面已加载。
- **When**：读取 `:root` CSS 变量。
- **Then**：存在 v4 设计稿中的完整 token 族（色彩 --c-primary / --c-accent / --c-paper-raised / --c-ink-1..3、圆角 --r-xs..xxl、阴影 --shadow-1..3、字号 --fs-11..-36、动效 ease 函数 --ease-out / --ease-in-out），颜色 oklch 值和 v4 一致。
- **Verification**：`programmatic`（`getComputedStyle(document.documentElement)` 读取 ≥ 20 个核心 token 存在且非默认）。

### AC-3：问答助手页保留 SSE 流 + 业务能力
- **Given**：用户已登录、有授权知识库。
- **When**：输入一个问题并按 Enter（或发送按钮）。
- **Then**：
  1. SSE 流式事件 token 被追加到助手消息，流式光标 blink 动画正常。
  2. 检索预览 `searchResults` 被渲染为新的 HitCard 风格（非对称圆角 + 左色条）。
  3. 引用到达时被合并到消息的 citations 列表，显示为引用子卡（章节路径 + 版本号 + 跳转锚点）。
  4. 反馈 UP/DOWN 按钮依然可发，发送后显示「反馈已发送」。
  5. 取消按钮（或 Esc）可中止生成，消息 finishReason = cancelled。
- **Verification**：`programmatic`（QA 流 happy path：ApiClient.search 被调用、ask SSE token 数 > 0、feedback 成功返回 2xx）+ `human-judgment`（视觉：气泡、引用卡、反馈条均为 Galaxy 新样式，无回归）。

### AC-4：检索中心页筛选器与命中卡样式符合预期 + 对齐量化差=0
- **Given**：用户在检索中心页（`#search`）。
- **When**：页面渲染完 hero/filters/summary/hit-cards。
- **Then**：
  1. Scope chips 第一个「全部」是 on 态（深色反色 pill）。
  2. 4 列筛选器：知识库下拉 / 文件类型 / 时间 / 作者部门，水平对齐。
  3. 命中卡非对称圆角 `border-radius: 4px 18px 14px 18px`，左边距 4px 色条（PDF=红 DOC=蓝 XLS=绿 PPT=橙 QA=青）。
  4. 命中词关键词「生产变更/审批/签字」用 mark.hit-hl 荧光笔高亮。
  5. 命中卡右上角 corner-tag（第 1 张「TOP命中」、权限受限「权限受限」）。
  6. 翻页 pill 组居中，第 1 页高亮。
  7. **量化对齐**：`big = querySelector('.search-big').getBoundingClientRect()`，`filters = .search-filters`，`cardFirst = .hit-card:first-child`，它们的 `left` 差 ≤ 1px，`width` 差 ≤ 1px。
- **Verification**：`human-judgment`（视觉检查 1-6 项）+ `programmatic`（第 7 项 evaluate 量化差 ≤1px）。

### AC-5：知识库页卡片网格可用 + Upload/Delete 不丢
- **Given**：登录用户有 ≥ 1 个知识库。
- **When**：点击「知识库」页 Tab。
- **Then**：
  1. 显示知识库卡片网格（每行 2 或 3 列，响应式），卡片非对称圆角，左侧色条按 visibility（PRIVATE 灰 / TEAM 蓝 / PUBLIC 绿）。
  2. 每卡片显示：name、desc 截断、document_count pill、updated_at 相对时间、visibility badge、delete 图标。
  3. 新建知识库表单（Galaxy Form）可输入 name → 调 `createKnowledgeBase` → 卡片立即出现。
  4. 点击卡片打开文档抽屉/展开面板 → Upload 文件调用 `uploadDocument`，状态 status badge 正确显示 PROCESSING/READY/FAILED；Delete doc 正确移除。
- **Verification**：`programmatic`（CRUD API 调用次数 + state 更新次数）+ `human-judgment`（卡片视觉正确，抽屉内文档样式升级）。

### AC-6：运营面板 8 指标卡 + 2 图表 + 2 快捷入口占位
- **Given**：用户进入 #ops 页（无论是否有 admin 权限，UI 都展示，只是按钮提示）。
- **When**：页面加载完成。
- **Then**：
  1. 显示「今日运行态势」标题 + 时间戳。
  2. 8 张指标卡（问答量/准确率/拒答率/引用正确率/活跃用户/知识库数/文档数/P95延迟），每张有 mock 数据和环比 delta pill（绿色↑红↓），右侧 SVG 迷你 sparkline。
  3. 2 张图表卡：7 日问答量折线（SVG）、知识库命中率柱（SVG）。
  4. 2 个快捷入口按钮（审计日志/低置信度队列）点击显示「此模块即将上线，已记录跳转意图」toast（不跳真实页）。
- **Verification**：`human-judgment`（布局、视觉、对齐、delta 色正确、图表非空）+ `programmatic`（8 卡 + 2 图 + 2 按钮 DOM 元素存在，非空）。

### AC-7：响应式 + 无障碍
- **Given**：窗口宽度 = 375px（移动端），且 `prefers-reduced-motion: reduce`。
- **When**：打开检索中心页。
- **Then**：筛选器 4 列变 2 列（或 1 列垂直），Tab 可横向滚动，Dock 不显示（或固定底部横条），命中卡宽度 100% 且无横向溢出；所有 GSAP 入场动画被跳过（元素直接显示），焦点仍然可见，键盘 Tab 顺序正确（搜索框 → 筛选 1→4 → 命中卡 Tab 顺序）。
- **Verification**：`human-judgment`（375px 视觉 + 手动 Tab 顺序）+ `programmatic`（MediaQueryList reduced-motion 时 GSAP 时间线不被创建）。

### AC-8：TS 严格 + 构建零警告
- **Given**：代码提交完成。
- **When**：在 apps/web 运行 `npm run build`（tsc -b + vite build）。
- **Then**：tsc 报 0 错误；vite build 0 警告；output bundle 中 `@phosphor-icons/react` 和 `gsap` 已 tree-shaken，非生产依赖未打入。
- **Verification**：`programmatic`（命令返回 0 exit code，stdout 无 TS error，vite warning 列表为空）。

## Open Questions
- [ ] **Q1**：运营面板的 mock 数据展示，是否在按钮点击时就需要跳转到真实「审计日志」「低置信度队列」页骨架，还是显示 toast？（此 Spec 默认 toast，但如果用户希望骨架页可一起建，Task 5 会新增。）
- [ ] **Q2**：登录页是否保留现有「用户名/密码 / 输入框 + 按钮」的简单结构，还是加入「租户选择下拉」（现在 UI 文档中提到租户入口，但现有 API 只有 email/password 登录，无租户选择）？（默认不做，保留现有）
- [ ] **Q3**：知识库卡片的「翻转展示最近 Top3 文档」的 3D flip，是否保留（需要翻转动效 + Galaxy Cards 中 `JoseIsra_white-pug-89` 3D flip 的实现思路），还是 hover 直接显示信息？（默认做 hover 信息，如需要 flip 再提升优先级）
- [ ] **Q4**：`⌘K` 全局搜索是否需要当用户在任意 Tab 都聚焦到检索中心的搜索框并切页？（此 Spec 默认：切到 #search 并聚焦大搜索框，如果不需要可取消。）
