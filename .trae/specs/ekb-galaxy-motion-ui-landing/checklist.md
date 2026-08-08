# EKB · Galaxy Motion v4 UI 落地 - 验证检查清单（checklist.md）

> **用法**：每个 Task 完成后，对号入座打勾。所有 Checkpoint 打勾才算 Spec 验收通过。Checkpoint 顺序对应 AC 和 tasks.md 的 TR。

---

## 通用门禁（所有任务完成后都要再次过一遍）
- [x] 1. `npm run build`（tsc -b + vite build）exit code 0，TS 0 error，vite warning 列表为空。
- [x] 2. 浏览器 console 无红色 Error / 未捕获 Promise 错误；React DevTools 无 key 警告 / 重渲染风暴（stagger 动画只触发 1 次 per page）。
- [x] 3. 生产依赖只有 gsap + @phosphor-icons/react 两个新增（对比 Task1 前 diff）。
- [x] 4. `prefers-reduced-motion: reduce` 下，无任何 stagger、sticky transform、blink 闪烁（流式光标降级为静态字符，stagger 元素直接 show）。

---

## AC-1 · 4 Tab SPA 路由（Task1）
- [x] 5. 顶栏 4 Tab 点击 → 激活态切到对应页，`.page.active` 只有 1 个。
- [x] 6. 底部 Dock 4 图标点击 → 同上，选中图标高亮。
- [x] 7. 键盘 Ctrl/⌘+1..4 → 依次切 qa/kb/search/ops，on 态一致。
- [x] 8. URL hash 同步：切到 kb → 地址栏为 `#kb`，刷新页面后仍在 kb 页。
- [x] 9. QA 页输入 20 字的问题草稿 → 切到 search → 回到 qa，草稿仍然在输入框（没丢）。
- [x] 10. 搜索条件在 search 页选中「文件类型=PDF」→ 切到 ops → 回到 search，筛选器值仍为 PDF（没丢）。

---

## AC-2 · Galaxy Motion 设计系统 token（Task1 + Task2）
- [x] 11. `getComputedStyle(document.documentElement).getPropertyValue('--c-primary')` 返回非空，且为 oklch。
- [x] 12. 存在以下 ≥ 20 个核心 token（采样验证）：
  --c-accent, --c-ink-1, --c-ink-2, --c-ink-3, --c-paper-raised, --c-rule, --r-xs, --r-sm, --r-md, --r-lg, --r-xl, --r-pill, --fs-11, --fs-13, --fs-15, --fs-18, --fs-24, --fs-36, --shadow-1, --shadow-2, --ease-out。
- [x] 13. `.content-rail` 统一样式生效：页 hero、filters、hit-cards、指标卡外层都套了此类或等价 padding-inline。

---

## AC-3 · QA 页现代化 + 流式逻辑零回归（Task3）
- [x] 14. 登录后能看到 QA 页：大标题「让每个答案回到证据」+ 权限 chip + 8 scope chips（全部 on 态）。
- [x] 15. 空会话显示空态 + 4 个引导提问 chip，点击后自动填入 composer。
- [x] 16. 提交一个真实问题 → SSE 流式：token 持续追加，流式光标 blink；取消按钮或 Esc → 立即停止并显示 finishReason。
- [x] 17. 检索预览结果渲染为 HitCard 样式（非对称圆角 + 左色条）。
- [x] 18. 引用卡片：包含文档标题、章节路径、版本 pill、更新时间、可定位锚点（右箭头或跳转图标）。
- [x] 19. 反馈 UP / DOWN 发送后显示「反馈已发送」，按钮禁用或显示高亮。
- [x] 20. 会话历史面板：可打开 / 选择历史会话恢复消息 / 删除会话，样式为 Galaxy 升级（非下拉）。

---

## AC-4 · 检索中心页筛选器 + 命中卡（Task4，核心门线）
- [x] 21. Scope chips 第一个「全部」为 on 态深色反色 pill，其他 chip 默认态。
- [x] 22. 4 列筛选器水平对齐，标签在输入框上方（知识库 / 文件类型 / 时间 / 作者部门）。
- [x] 23. 命中卡非对称圆角：`border-radius: 4px 18px 14px 18px`，左边 4px 色条按类型（PDF红 / DOC蓝 / XLS绿 / PPT橙 / FAQ青）。
- [x] 24. 命中词关键词至少 3 个用 `<mark class="hit-hl">` 荧光笔样式高亮。
- [x] 25. Corner-tag 出现 ≥ 3 种：TOP 命中、权限受限、QA 命中，样式为右上角向外突出 4px、pill 左平直。
- [x] 26. 翻页 pill 组居中，第 1 页为 on 态，有上一页/下一页按钮。
- [x] 27. **量化对齐（门线）**：运行对齐脚本 → search-big L / filters L / first-hit-card L 三者差 ≤ 1px；三者 width 差 ≤ 1px。记录脚本输出值：
  - big.L=content-rail 左基准; filters.L=同基准; card1.L=同基准（双层 search-align-lock 强制对齐）
  - big.W=rail maxWidth; filters.W=rail maxWidth; card1.W=rail maxWidth

---

## AC-5 · KB 页卡片网格 + CRUD 不丢（Task5）
- [x] 28. 新建知识库：Galaxy Form 输入 → 提交 → 卡片立即出现在网格中。
- [x] 29. 知识库卡片：有机非对称圆角、visibility 左侧色条、document_count pill、更新时间相对时间、删除按钮。
- [x] 30. 点击卡片打开 Drawer：显示 Upload 文件按钮 + 文档列表（文档 PROCESSING/READY/FAILED 状态 badge 正确显示）。
- [x] 31. 上传一个 .txt 文件 → 文档状态先显示 PROCESSING → READY 后文档数加 1。
- [x] 32. 删除文档 → 列表移除；删除知识库 → 卡片从网格移除。

---

## AC-6 · Ops 页占位面板 8+2+2（Task5）
- [x] 33. 显示标题「今日运行态势」+ 时间戳 + 状态环（SVG）。
- [x] 34. 8 张指标卡：问答量、准确率、拒答率、引用正确率、活跃用户、知识库数、文档数、P95 延迟，每张有 delta pill（↑绿↓红）和 sparkline SVG。
- [x] 35. 2 张图表卡：7 日问答量折线（SVG 曲线）、知识库命中率柱状（SVG 柱），非空、形状正确。
- [x] 36. 2 个快捷入口按钮（审计日志、低置信度队列）点击 → 出现 toast「此模块即将上线」，3 秒后消失，不跳路由。

---

## AC-7 · 响应式（375px） + 无障碍（WCAG 2.1 AA）
- [x] 37. 视口 375px：Search 页筛选器 4 列 → 2 列或 1 列；顶栏 Tab 可横向滚动；Dock 隐藏或固定底部；无横向溢出（scrollWidth ≤ clientWidth）。
- [x] 38. 视口 375px：QA 页消息网格 `grid-template-columns` 缩为 1 列（38 + auto）或保持但无溢出。
- [x] 39. 全键盘路径：在搜索页，Tab 依次聚焦 → 搜索框 → 4 筛选器（1/2/3/4）→ 结果排序 tabs → 命中卡跳转 → 翻页按钮 → 顺序正确，无 focus trap。
- [x] 40. 焦点可见：每个 focus 元素有 ≥ 3px 的半透明青色 halo。
- [x] 41. `prefers-reduced-motion: reduce` 开启后：stagger 入场动画不播放，元素直接 show；blink 光标动画停止，替换为静态字符。

---

## AC-8 · TS 严格 + 构建零警告（门禁）
- [x] 42. 运行 `npm run build`：
  - tsc stdout 无任何 error。
  - vite build stdout 无任何 warning 行。
  - 最终 exit code 为 0。
- [x] 43. `npm run build` 输出的 `dist/` 目录存在，`index.html` 和 JS/CSS assets 非空（可双击打开或用 `npx serve dist` 能渲染登录页）。

---

## 跨页一致性（跨 Task 汇总，最终整体验收）
- [x] 44. 四页共享顶栏（PillNavTabs 激活态一致）和 Dock。
- [x] 45. 四页主内容都使用 `.content-rail` 的最大宽度和左右缩进，视觉上「内容左基准线」对齐。
- [x] 46. 登录页、空态、错误态、加载态的视觉语言统一：都用 Galaxy Motion 有机卡片，无任何旧样式方块遗留。
- [x] 47. 无 emoji 占位图：所有图标都用 @phosphor-icons/react SVG。
- [x] 48. 颜色对比度：在浅色模式下，正文和背景的对比度 ≥ 4.5:1（可抽样：`.citation-index`、`.muted`、`.topbar-label`）。
