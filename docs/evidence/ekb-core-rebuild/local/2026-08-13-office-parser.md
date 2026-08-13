# 2026-08-13 Office parser 本地证据

## 结论

DONE-LOCAL，仅证明本地 parser 与测试闭环，不代表生产摄取或生产完成。

## 代码与测试依据

- 已推送 commit `5a008f17251fa8e37ee8f44980afd923055a8957`：`services/parsers/registry.py` 增加真实 XLSX/PPTX parser，依赖 `openpyxl` / `python-pptx`。
- `apps/api/tests/v4/test_ph3_registry_office.py` 使用 XLSX/PPTX 字节样本，覆盖正常解析，以及空文件、损坏文件、旧 Office 输入的稳定 fail-closed。
- XLSX 以只读方式读取 workbook/sheet/row/cell 结构，PPTX 读取 slide/shape/table/notes（可读时）结构；不执行宏。
- 该切片保持 LLM-only：不引入本地大模型、mock、伪向量或伪成功。

## 验证结果

- Office parser 定向回归：`38 passed`。
- 后端全量：`370 passed, 2 skipped`。
- 前端：`58 passed`；typecheck/build 通过。
- 上述全量数字是当前工作树回归快照，不是仅由本 parser 切片单独造成的结果。

## 浏览器边界

本次浏览器只验收当前 Documents、Assistant、Jobs Center 的真实数据；未在浏览器声称或证明成功摄取 XLSX/PPTX。

## 生产边界

以下仍为 `NOT RUN/BLOCKED`：生产 PostgreSQL/pgvector、Provider/Embedding 出网、对象存储、可靠队列/Worker/Scheduler、服务器备份/部署/回滚。生产目标统一使用 `[production host]`，敏感值统一使用 `[REDACTED]`；本证据不记录凭据、token 或私密地址。

## 2026-08-13 browser OOXML validation

- 后续本地真实浏览器验证：在 `Documents → 批量/目录上传 → 批量多文件` 中选择 `/tmp/ekb-parser-qa.xlsx` 和 `/tmp/ekb-parser-qa.pptx`；两份仅为临时文件，不进 Git。
- UI 扫描识别为 Excel/PPT，各 1 个；真实上传完成 `2/2`；服务端分别创建真实 batch/job/document 状态。
- worker 处理后两个条目均到达 `CHUNKING · 50%`；解析器未报 `MIME_UNSUPPORTED` 或 `PARSER_CORRUPT`。由于本地没有远程 Embedding provider/profile，真实终态为 `EMBEDDING_UNAVAILABLE/FAILED`；不得写成 `READY`、成功索引或远程 Provider 成功。
- 浏览器仍有历史 auth-related console errors；不声称 `console=0`。
- 生产 PostgreSQL/pgvector、Provider 出网、对象存储、可靠队列/Worker/Scheduler、服务器备份/部署/回滚仍为 `NOT RUN/BLOCKED`。生产目标统一使用 `[production host]`，受管敏感值统一使用 `[REDACTED]`；不记录 token、password 或私密地址。
