# 文档中心批量删除本地证据（2026-08-13）

## 范围

本证据只记录文档中心前端的本地切片，不代表生产部署，也不把逐条删除包装成新的批量删除 API。

修改文件：

- `apps/web/src/app-v2/pages/DocumentsPage.tsx`
- `apps/web/src/app-v2/components/documents/DocumentTable.tsx`
- `apps/web/src/app-v2/adapters/documents.ts`
- `apps/web/src/app-v2/tests/m3.test.ts`

## 已实现行为

- 文档表头和行支持真实选择，支持当前可见页全选，并显示当前选中数。
- 批量删除按钮随选择状态启用；执行前显示二次确认。
- 确认后按选中项逐条调用现有真实单文档 `DELETE`，不是新增或伪造一个“真实批量删除 API”。
- 删除成功的项目进入既有回收站；失败项目保留，并显示全成功或部分失败结果。
- 操作完成后重新读取服务端文档列表，清理已经不存在的 selection，筛选、分页或重载也不会保留 stale selection。
- 能力投影为 `documents.bulk-delete=available`，因为当前能力是已有真实单文档 DELETE 的逐条批处理。

## 本地浏览器验收

环境：本地 Vite 页面 `127.0.0.1:5173/#/documents`。

真实服务端文档列表中选择当前可见文档后观察到：

- 文案“已选择 6 项”；
- 按钮“批量删除 (6)”处于 enabled 状态；
- 本次验收未点击删除，因此没有误删现有本地真实文档。

浏览器会话存在既存 console 错误；本证据不写 `console=0`，也不把既存错误归因于本切片。

## 验证结果

```text
npm test -- --run     58 passed
typecheck             passed
build                 passed
build warning         existing chunk >500KB warning retained
git diff --check      passed
```

## 边界

- 当前实现是既有真实单文档 `DELETE` 的逐条批处理，不是已实现的真实批量删除后端 API。
- 生产 PostgreSQL/pgvector、对象存储、可靠队列/Worker、Provider、服务器备份、部署和回滚均 `NOT RUN`。
- 不记录凭据、token、私有地址或其他秘密；生产目标统一写作 `[production host]`，受管敏感值统一写作 `[REDACTED]`。
- 本地 SQLite/本地浏览器可见性不代表生产完成，不代表 PH0–PH8 全部完成。
