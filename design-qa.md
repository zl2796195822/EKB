# EKB app-v2 Design QA Report

**生成时间**: 2026-08-10T22:05:00+08:00
**构建**: `apps/web/dist/` (tsc + vite build 通过)
**测试环境**:
- 后端: uvicorn @ 127.0.0.1:8023 (QA 副本库 `/tmp/ekb_qa.db`)
- 前端预览: vite preview @ 127.0.0.1:4173 (含 /api 反代)
- 设计源: python http.server @ 127.0.0.1:4180
- 浏览器: Google Chrome 151 (Playwright channel)

## 1. 路由可达性（10/10）

| # | 路由 | Hash | 桌面可达 | 窄屏可达 | 登录态 |
|---|------|------|---------|---------|--------|
| 1 | dashboard | #/dashboard | ✅ | ✅ | ✅ |
| 2 | knowledge | #/knowledge | ✅ | ✅ | ✅ |
| 3 | assistant | #/assistant | ✅ | ✅ | ✅ |
| 4 | documents | #/documents | ✅ | ✅ | ✅ |
| 5 | team | #/team | ✅ | ✅ | ✅ |
| 6 | analytics | #/analytics | ✅ | ✅ | ✅ |
| 7 | apps | #/apps | ✅ | ✅ | ✅ |
| 8 | recycle | #/recycle | ✅ | ✅ | ✅ |
| 9 | profile | #/profile | ✅ | ✅ | ✅ |
| 10 | modules | #/modules | ✅ | ✅ | ✅ |

**结论**: 10/10 路由在登录后均可访问，hash 与路由清单一致。

## 2. React Runtime Error（0/20 页面·视口）

| 视口 | 页面数 | console error | pageerror | warning |
|------|--------|--------------|-----------|---------|
| desktop | 10 | 0 | 0 | 0 |
| narrow | 10 | 0 | 0 | 0 |

**结论**: 十页双视口均无 React runtime error。

## 3. 同视口视觉对照

### 3.1 对照方法
- 设计源：`设计稿/index.html`（已编译 bundle，非源码）
- 实现：`apps/web/dist/`（AppV2 入口）
- 桌面视口：1181 × 1027（与视觉基线一致）
- 窄屏视口：390 × 844（移动设备典型尺寸）
- 截图存放：`output/design-qa/v2/{design,impl}/{route}-{viewport}.png`
- 结构快照：`output/design-qa/v2/qa-report.json`

### 3.2 逐页对照结果

#### 工作台 / Dashboard
- **桌面**: 全局壳（品牌+侧栏+顶栏+内容区）完整渲染；问候语、快捷操作、五卡、趋势/分布/动态/标签/健康度区域均可见；unavailable 能力以 StatePanel 正确呈现（不造数）。
- **窄屏**: 侧栏可折叠，主操作不遮挡。
- **P0**: 0 | **P1**: 0 | **P2**: 0

#### 知识库 / Knowledge Base
- **桌面**: 三段式布局（左空间导航+中文件区+右概览）；真实 KB「金博」加载成功，显示 2 文档、1 成员、OWNER 角色；文档表格有版本/查看/下载操作。
- **窄屏**: 表格因 `min-width:680px` > 390px 出现横向溢出标记，但 `.v2-m3-table-wrap` 已设 `overflow-x: auto`，符合基线「表格允许横向滚动」规则。侧栏与概览正确折叠。
- **P0**: 0 | **P1**: 0 | **P2**: 0（溢出为预期行为，非缺陷）

#### AI 助手 / AI Assistant
- **桌面**: 专用顶栏+左会话列表+中聊天区+右上下文面板；composer、发送/取消按钮可见；SSE 流式入口就绪。
- **窄屏**: 左右栏可折叠，输入区不被遮挡。
- **P0**: 0 | **P1**: 0 | **P2**: 0

#### 文档中心 / Document Center
- **桌面**: 全局壳+文档列表；搜索、上传、筛选工具栏可见。
- **窄屏**: 操作区纵向堆叠，主功能可达。
- **P0**: 0 | **P1**: 0 | **P2**: 0

#### 团队与权限 / Team & Permissions
- **桌面**: 租户/成员/邀请 tabs 可见；真实数据加载（owner/member 角色）。
- **窄屏**: Tabs 保持水平排列，表单可读。
- **P0**: 0 | **P1**: 0 | **P2**: 0

#### 数据看板 / Analytics
- **桌面**: 四指标卡+趋势图(unavailable)+分布饼图+质量网格+治理面板(审计日志/审核/同步/备份)；ops dashboard 真实返回数据（302 文档、101 KB、133 QA 等）。
- **窄屏**: 卡片可堆叠，治理 tabs 可见。
- **P0**: 0 | **P1**: 0 | **P2**: 0

#### 应用中心 / App Center
- **桌面**: 应用市场占位（6 tile，全部 disabled/install 不可用）；明确标注无应用中心端点。
- **窄屏**: Tiles 单列堆叠。
- **P0**: 0 | **P1**: 0 | **P2**: 0

#### 回收站 / Recycle Bin
- **桌面**: 保留期说明+空状态（明确标注无回收站端点）；清空/还原/永久删除均 disabled。
- **窄屏**: 说明文案完整。
- **P0**: 0 | **P1**: 0 | **P2**: 0

#### 个人中心 / Profile & Settings
- **桌面**: 资料/安全/通知 tabs；保存状态可见。
- **窄屏**: 表单纵向排列。
- **P0**: 0 | **P1**: 0 | **P2**: 0

#### 模块地图 / Module Map
- **桌面**: 九个业务模块 tile + 返回工作台按钮；编号/名称/说明清晰。
- **窄屏**: Tiles 单列堆叠。
- **P0**: 0 | **P1**: 0 | **P2**: 0

### 3.3 汇总

| 严重级别 | 桌面 | 窄屏 | 合计 |
|----------|------|------|------|
| P0（阻断） | 0 | 0 | **0** |
| P1（重要） | 0 | 0 | **0** |
| P2（轻微） | 0 | 0* | **0** |

> \* 知识库窄屏表格横向溢出为 `overflow-x: auto` 预期行为，符合基线「表格允许横向滚动」规则，不计入 P2。

## 4. 后端流程与权限/错误/SSE 负向用例

### 4.1 认证流程
- ✅ 正确口令登录 → 200 + access_token
- ✅ 错误口令 → 401 UNAUTHENTICATED（含 request_id）
- ✅ 无 token 访问 /api/v1/me → 401

### 4.2 Adapter 契约测试（vite-node）
| 测试套件 | 结果 |
|----------|------|
| auth (runAuthAdapterTests) | PASS |
| m2 (runM2ContractTests) | PASS |
| m3 (runM3ContractTests) | PASS |
| m4 (runM4ContractTests) | PASS |
| m5 (runM5ContractTests) | PASS |
| m6 (runM6ContractTests) | PASS |
| v3.identity (vitest) | 6/6 PASS |

### 4.3 M6 关键负向用例（m6.test.ts）
- ✅ opsDashboard days clamp: 0→1, 365→90, NaN→7
- ✅ opsDashboard 空 → empty state
- ✅ 403 → permission-denied state
- ✅ 500 → error state
- ✅ audit list 空 → empty state
- ✅ review update 不传 resolution → payload 无 resolution key
- ✅ sync source name trim
- ✅ backup 返回真实元数据（sha256/tableCount）
- ✅ analytics-visitor-trend / application-installation / recycle-restore-purge 均 unavailable

## 5. 构建验证

```
$ cd apps/web && npm run build
✓ tsc -b && vite built in ~1.6s
dist/assets/index-{hash}.css   91 kB
dist/assets/index-{hash}.js    565 kB (gzip: 148 kB)
```

## 6. Import 边界审计

```
$ node docs/evidence/import-boundary-check.mjs
scanned files: 89
legacy UI paths still present on disk: 0
capability-layer importers: 15 (仅 adapters/ + tests/ + lib/api.ts)
Import boundary check: PASSED
```

详见 `docs/evidence/m7-import-audit.md`。

## 7. 后端未修改证明

```
$ git diff --no-ext-diff --binary -- apps/api/ekb_api/routers/admin.py apps/api/ekb_api/routers/me.py | shasum -a 256
5cf5a75c33d6533ead2bc76e7621eae2efdf238fc338116a08656beb693eb440
```
与 M5/M6 起始基线 SHA 一致，未改动后端。

## 8. 旧 UI 清理证据

- 切换前备份: `docs/evidence/legacy-ui-backup-2026-08-10.tar.gz` (137 KB, 38 文件)
- 切换后 `src/` 仅剩: `app-v2/`, `lib/api.ts`, `types/api.ts`, `main.tsx`, `vite-env.d.ts`
- 旧 UI 组件名残留引用: 0
- 旧 styles.css 引用: 0

---

**Final result: PASSED**
