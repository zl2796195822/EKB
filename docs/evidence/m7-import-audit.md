# M7 旧 UI 生产 import 审计

生成时间: 2026-08-10 21:36:20 +0800

## 1. 生产入口
```
$ cat apps/web/src/main.tsx
import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { AppV2 } from './app-v2/AppV2'

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <AppV2 />
  </StrictMode>,
)

```
## 2. 按真实路径解析每条相对 import（纯正则会误判 app-v2 自身的 pages/components，故用解析器）
```
$ node docs/evidence/import-boundary-check.mjs
scanned files: 89
legacy UI paths checked: apps/web/src/App.tsx, apps/web/src/pages, apps/web/src/components, apps/web/src/hooks, apps/web/src/utils, apps/web/src/styles.css, apps/web/src/styles.css.bak
legacy UI paths still present on disk: 0
capability-layer importers:
  - apps/web/src/app-v2/adapters/admin.ts
  - apps/web/src/app-v2/adapters/auth.ts
  - apps/web/src/app-v2/adapters/conversations.ts
  - apps/web/src/app-v2/adapters/documents.ts
  - apps/web/src/app-v2/adapters/feedback.ts
  - apps/web/src/app-v2/adapters/index.ts
  - apps/web/src/app-v2/adapters/knowledge.ts
  - apps/web/src/app-v2/adapters/qaStream.ts
  - apps/web/src/app-v2/adapters/search.ts
  - apps/web/src/app-v2/tests/auth.test.ts
  - apps/web/src/app-v2/tests/m4.test.ts
  - apps/web/src/app-v2/tests/m5.test.ts
  - apps/web/src/app-v2/tests/m6.test.ts
  - apps/web/src/app-v2/tests/v3.identity.test.ts
  - apps/web/src/lib/api.ts

Import boundary check: PASSED

```
## 3. 全仓对旧样式表的任何引用
```
$ rg -n 'styles\.css' apps/web/src apps/web/index.html apps/web/vite.config.ts
(no matches)

```
## 4. 旧 UI 组件名/hook 在生产代码中的任何残留引用
```
$ rg -n 'ChatPanel|ConversationHistory|DocVersionPanel|KnowledgeSidebar|StatusBadge|GalaxyCard|GalaxyHero|DockBar|PillNavTabs|HitCard|useHashPage' apps/web/src
(no matches)

```
## 5. src 目录现状（旧 UI 文件已不存在）
```
$ ls -1 apps/web/src
app-v2
lib
main.tsx
types
vite-env.d.ts

$ ls -1 apps/web/src/lib apps/web/src/types
apps/web/src/lib:
api.ts

apps/web/src/types:
api.ts

```
## 6. 构建产物中不再出现旧页面 chunk
```
$ ls -1 apps/web/dist/assets
index-D07PhUAA.css
index-Z6Efmw8V.js

```
