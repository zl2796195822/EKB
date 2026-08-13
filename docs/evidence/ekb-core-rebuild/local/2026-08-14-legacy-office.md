# Legacy Office 本地解析证据（2026-08-14）

## Goal

完成 FR-015 的本地真实 DOC/XLS/PPT 解析：通过隔离的 LibreOffice/`soffice`
将 legacy binary 转成 OOXML，再复用现有 DOCX/XLSX/PPTX parser；转换或读取失败
必须 fail-closed，不伪造成功。

## Implementation

- `application/msword` → `.docx` → `DocxParser`
- `application/vnd.ms-excel` → `.xlsx` → `XlsxParser`
- `application/vnd.ms-powerpoint` → `.pptx` → `PptxParser`
- 转换进程使用参数数组、`shell=False`、30 秒硬超时、`--headless`/`--safe-mode`
  与独立 LibreOffice user profile；输入、输出和 profile 均在私有临时目录内。
- 只使用安全 basename 生成临时文件名；输出必须是临时输出目录中的非空文件。
- `soffice` 缺失、启动失败、超时、非零退出、输出缺失/为空统一为
  `CONVERSION_FAILED`；错误 detail 仅保留稳定白名单字段。
- 转换成功后，目标 parser 失败仍返回目标 parser 原有稳定分类（例如
  `PARSER_CORRUPT`），不会把转换成功当作解析成功。
- 成功 metadata 标明 `format=legacy-converted`、`source_mime`、
  `converter=libreoffice` 和 `target_mime`。

## Verification

定向测试覆盖 legacy MIME resolve、soffice 缺失/失败/超时、成功转换链路、
basename 路径安全、错误脱敏和目标 parser 错误透传。运行结果记录在本次任务最终
报告中；服务器部署与生产凭据相关验收不属于本地证据。

## 真实本地 smoke 补充

- 已实际执行真实 `soffice` 的 XLS → XLSX 转换，并将生成的 OOXML 输入交给现有 `XlsxParser`；该链路 smoke 通过。
- 本证据不声称 DOC 或 PPT 已在真实 smoke 中成功转换/解析；DOC/PPT 相关结论仅限于定向测试、错误边界和实现路径验证。
- Office 定向测试结果：`14 passed`；相关 Ruff 与 `git diff --check` 通过。生产部署、生产凭据和服务器验收仍未执行。
