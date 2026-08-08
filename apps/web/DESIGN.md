# EKB · Galaxy Motion v4 设计系统

> 文档版本：v4 · Galaxy Motion 基线
> 生效日期：2026-08-08
> 适用范围：apps/web React 门户全部页面与组件

---

## 1. Token 表

### 1.1 颜色色板（CSS Variable）

| Token | 值 | 用途 |
|---|---|---|
| `--c-primary` | `#2563eb` | 主操作、焦点环、激活态 |
| `--c-primary-hover` | `#1d4ed8` | 主按钮 hover |
| `--c-primary-soft` | `rgba(37, 99, 235, 0.12)` | 主色浅色背景 chip |
| `--c-accent` | `#0d9488` | 辅助强调、引用色条、成功态 |
| `--c-accent-hover` | `#0f766e` | 辅助色 hover |
| `--c-ink-1` | `#0f172a` | 标题、正文主色 |
| `--c-ink-2` | `#334155` | 次正文、标签文字 |
| `--c-ink-3` | `#64748b` | 元信息、辅助文字 |
| `--c-paper` | `#fafbff` | 页面底层底色 |
| `--c-paper-raised` | `#ffffff` | 卡片、面板浮层底色 |
| `--c-rule` | `#e2e8f0` | 分割线、边框 |
| `--c-success` | `#10b981` | 成功指标、delta 上升 |
| `--c-danger` | `#ef4444` | 错误、delta 下降 |
| `--c-warning` | `#f59e0b` | 警告、处理中 |

### 1.2 圆角

| Token | 值 | 用途 |
|---|---|---|
| `--r-xs` | `4px` | 小标签、内联 chip |
| `--r-sm` | `8px` | 小按钮、输入框 |
| `--r-md` | `12px` | 普通卡片、面板 |
| `--r-lg` | `18px` | 大卡片、对话气泡 |
| `--r-xl` | `24px` | 英雄区容器、登录面板 |
| `--r-xxl` | `32px` | 外层包裹、有机大卡 |
| `--r-pill` | `999px` | pill 导航、chip、标签 |

### 1.3 字号阶梯

| Token | 值 | 用途 |
|---|---|---|
| `--fs-11` | `11px` | eyebrow 上标、角标 |
| `--fs-12` | `12px` | 元信息、chip 文字 |
| `--fs-13` | `13px` | 辅助文案、表格文字 |
| `--fs-14` | `14px` | 正文默认字号 |
| `--fs-15` | `15px` | 对话正文 |
| `--fs-16` | `16px` | 小标题、按钮文字 |
| `--fs-18` | `18px` | 次级标题 |
| `--fs-22` | `22px` | 卡片标题 |
| `--fs-28` | `28px` | 页面副标题 |
| `--fs-36` | `36px` | 页面主标题 H1 |

### 1.4 阴影

| Token | 值 | 用途 |
|---|---|---|
| `--shadow-1` | `0 2px 8px rgba(15, 23, 42, 0.06)` | 轻悬浮、chip 投影 |
| `--shadow-2` | `0 8px 24px rgba(15, 23, 42, 0.10)` | 卡片悬浮、下拉面板 |
| `--shadow-3` | `0 18px 50px rgba(15, 23, 42, 0.14)` | 登录面板、模态层、dock |

### 1.5 缓动曲线（Ease）

| Token | 值 | 用途 |
|---|---|---|
| `--ease-out` | `cubic-bezier(0.16, 1, 0.3, 1)` | 入场动画、元素出现 |
| `--ease-in-out` | `cubic-bezier(0.65, 0, 0.35, 1)` | 状态切换、hover 过渡 |

---

## 2. 组件规则

### 2.1 组件命名与结构

- 组件文件放在 `src/components/ui/` 下，大驼峰命名（如 `PillNavTabs.tsx`）
- Props 使用 `type` 定义，不使用 `interface`
- 所有组件必须 `export` 对应 Props 类型，供页面消费时复用
- 组件纯组合，不内部调用业务 API；状态由父组件注入

### 2.2 焦点与可访问性

- 可交互元素 `:focus-visible` 必须有 `3px` oklch 半透明主色 halo
- 所有图标按钮必须有 `aria-label`
- 颜色表达需配合图标或文案，不单独依赖颜色传达语义
- 目标点击区域 ≥ `40×40px`，窄屏例外时 ≥ `32×32px`

### 2.3 动效

- 默认过渡时长：`200ms`（状态）、`320ms`（入场）、`480ms`（大容器）
- 全部过渡/动画必须遵守 `prefers-reduced-motion: reduce` 降级为 0ms
- GSAP 仅在 CSS 不足时使用（如复杂 stagger 时间线），普通过渡优先 CSS

### 2.4 响应式断点

| 断点 | 宽度 | 说明 |
|---|---|---|
| `sm` | `≥ 560px` | 手机横屏以上 |
| `md` | `≥ 768px` | 平板以上 |
| `lg` | `≥ 1024px` | 笔记本以上 |
| `xl` | `≥ 1280px` | 桌面大屏 |

---

## 3. `.content-rail` 对齐基准

### 3.1 规则定义

`.content-rail` 是全站页面内容的统一水平容器基准，保证跨页的左边界与最大宽度对齐。

```css
.content-rail {
  width: 100%;
  max-width: 1200px;
  margin-inline: auto;
  padding-inline: clamp(20px, 4vw, 48px);
  box-sizing: border-box;
}
```

### 3.2 使用原则

1. 每个页面的顶级内容容器必须套 `.content-rail`
2. Hero 区、结果区、表单区、分页区各自独立套 `.content-rail`，避免嵌套过深
3. 量化对齐验收：评估 `hero / filters / result-summary / search-results` 中第一个元素的 `left` 值差 ≤ 1px
4. Dock 和 PillNav 是全局壳组件，不套 `.content-rail`，由自身布局保证与 rail 视觉对齐

---

## 4. 防滥用约定

- 不新增紫色渐变、玻璃拟态、炫光粒子背景作为"AI 装饰"
- 不自定义 token 同义词；如需新增先更新此表
- 不在组件中写死 `px` 边距/圆角，优先使用上述 CSS Variable
