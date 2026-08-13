---
title: EKB 统一主题系统规格
status: Ready for Plan
tier: 3
type: feature
authoritative: true
scope: ekb-core-rebuild
version: 0.1
date: 2026-08-12
owner: Sol
---

# EKB 统一主题系统规格

## 1. 原则

保留现有 EKB 视觉语言，重建 token 和组件状态，不重新设计产品品牌。页面只表达语义，不决定 Dark Mode 颜色。本文件覆盖 [EKB-CR-FR-001 至 EKB-CR-FR-006](./01-requirements.md)。

## 2. Token 分层

```text
primitive palette
  -> semantic color tokens
     -> component tokens
        -> component variants/states
```

Primitive 只在 theme definition 中使用；业务 CSS/TSX 禁止直接引用。

### 2.1 必需语义 Token

| 类别 | Token 示例 |
|---|---|
| Canvas | `--color-bg-canvas`, `--color-bg-subtle` |
| Surface | `--color-surface`, `--color-surface-raised`, `--color-surface-overlay` |
| Border | `--color-border`, `--color-border-strong`, `--color-divider` |
| Text | `--color-text-primary`, `secondary`, `muted`, `inverse`, `link` |
| Interaction | `--color-hover`, `active`, `selected`, `focus-ring`, `disabled` |
| Input | `--color-input-bg`, `border`, `placeholder`, `invalid` |
| Status | `--color-success`, `warning`, `danger`, `info` 及各自 surface/text |
| Overlay | `--color-scrim`, `--shadow-raised`, `--shadow-modal` |
| Code | `--color-code-bg`, `text`, `border`, syntax tokens |

Spacing、radius、shadow、typography、motion 也必须 token 化，避免主题切换造成布局漂移。

## 3. 主题选择与持久化

用户偏好为 `light|dark|system`。启动顺序：内联 bootstrap 读取本地无敏感 preference → system media query → 在首屏渲染前设置 `data-theme`，避免闪烁。登录后可与服务端 preference 同步，最近显式用户操作优先。

主题改变只切换 root attribute；组件不得维护各自 dark state。

## 4. 组件合同

| 组件 | 必测状态 |
|---|---|
| Button | default/hover/active/focus/disabled/loading/danger |
| Input/Textarea | empty/filled/hover/focus/invalid/disabled/autofill |
| Select/Dropdown | trigger/menu/item hover/selected/disabled |
| Card | base/interactive/selected/error |
| Table | header/row hover/selected/sticky/empty/loading |
| Modal/Drawer | surface/scrim/header/body/footer/close/focus trap |
| Tooltip/Popover | elevated surface/border/shadow/arrow |
| Sidebar/Nav | base/hover/active/badge/divider |
| Toast/Banner | success/warning/error/info |
| Markdown/Code | prose/link/quote/table/inline code/fenced code |
| Upload/Jobs | stage/status/progress/error rows |

第三方组件通过 theme adapter 或 CSS variable bridge；无法适配者不得进入正式页面。

## 5. 对比度与状态表达

- 普通文字/背景、交互边界和 focus ring 满足 WCAG AA。
- Success/Failed/Running 不仅用颜色，同时使用文字和 icon。
- Disabled 与 muted 仍可读，但不可与 enabled 混淆。
- Chart/Logo 在两种主题有可辨方案；不对品牌 Logo 任意反色。
- 支持 `prefers-reduced-motion`，进度可保持信息更新但禁用非必要动画。

## 6. 迁移策略

1. 建立 token inventory 和 semantic mapping。
2. 实现 Light/Dark root themes 与组件基础层。
3. 按 Shell → shared overlays/forms/table → Knowledge/Assistant/Profile → 其他页面迁移。
4. 每个页面迁移时移除该页面 raw colors 和独立 dark override。
5. 增加 lint/CI allowlist：新 raw hex/rgb/hsl 仅允许 theme/brand asset 文件。
6. 最后删除未引用 legacy overrides，不进行全局盲目替换。

## 7. 浏览器验证矩阵

视口至少 desktop 与 390x844；每个主要路由在 Light/Dark 下覆盖：默认、加载、空、错误、permission、modal/drawer/dropdown/tooltip、hover/focus/selected。验证：

- 无浅色 surface 残留或不可读文字；
- 无横向溢出和主题切换布局跳动；
- computed styles 关键 token 来自 root；
- console error/warning 为零；
- 截图差异经人工审查，不能仅凭 snapshot 更新通过。

## 8. 代码门禁

- 对 app-v2 业务文件扫描 raw colors 和 `.dark`/`[data-theme]` 页面级 selector。
- Theme 文件、品牌 Logo 和经记录的可视化色板为有限 allowlist。
- 新组件 story/test 同时渲染两种主题。
- 第三方 portal 必须继承/显式获得当前 theme attribute。
