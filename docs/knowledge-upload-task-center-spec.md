# 知识库上传系统重构 SPEC（Upload Task Center）

> 状态：2026-08-14 初版
> 范围：废弃阻塞式批量上传弹窗，重构为知识库内部独立「上传任务」页面，并修复当前批量上传失败问题。
> 仓库：`apps/web`（React + TS + Vite，hash 路由）与 `apps/api`（FastAPI，SQLite/PostgreSQL）。

---

## 一、当前实现

### 1.1 知识库页面
- 路径：`apps/web/src/app-v2/pages/KnowledgePage.tsx`
- 路由：`#/knowledge`（hash 路由清单见 `app-v2/routes.ts`），用 `KnowledgeSpaceRail` 左栏选中知识库，`selectedKbId` 为组件内状态。
- 选中的 KB id 通过 hash query `?kb=<id>` 在页面内传递（`readKbParam()`）。

### 1.2 批量上传入口
- `KnowledgePage.tsx` 两处按钮「批量/目录上传」打开 `BatchUploadModal`（`app-v2/components/documents/BatchUploadModal.tsx`，约 1000 行）。
- 也暴露单文件「上传知识」走 `services.documents.upload(kbId, file)`。

### 1.3 上传 Modal
- 文件：`app-v2/components/documents/BatchUploadModal.tsx`
- 问题：阻塞式大 Modal；上传中 `disabled={isUploading}` 无法关闭/取消；所有文件以 `<li>` 列表内联渲染；状态仅有「排队中/上传中/成功/失败」。

### 1.4 上传 API（前端 → 后端）
前端适配器 `uploadBulkImpl`（`app-v2/adapters/documents.ts`）：
1. `client.createUploadBatch(kbId, {mode, client_request_id, items})` → `POST /api/v1/kb/{kb}/uploads/batches`
2. 每个 accepted item：`client.openUploadItemSession(itemId)` → `POST /api/v1/kb/uploads/items/{id}/session`
3. `client.putUploadObject(uploadUrl, file, onProgress)` → `PUT /api/v1/kb/uploads/objects?object_key=...`
4. `client.completeUploadItem(itemId, {sha256, detected_mime})` → `POST /api/v1/kb/uploads/items/{id}/complete`
5. 支持 `AbortController` 取消、幂等 `client_request_id`、断点 resume。

### 1.5 后端上传 / 摄取模型（已存在，健壮）
- 路由：`ekb_api/routers/kb_upload.py`（prefix `/kb`，挂 `/api/v1`）
- 服务：`ekb_api/services/storage.py`（UploadService：preflight、批次、会话、complete、abort、projection）、`ekb_api/services/ingestion.py`（IngestService + `create_version_for_item`）。
- 表：`upload_batches` / `upload_items` / `upload_sessions` / `source_objects` / `ingest_jobs` / `ingest_job_attempts` / `ingest_stages`。
- `ingest_job_attempts.state` 已含 `WAITING/VALIDATING/CONVERTING/PARSING/CHUNKING/EMBEDDING/INDEXING/SUCCEEDED/FAILED/CANCELLED`，后端已能区分解析、切片、Embedding、索引阶段。

### 1.6 目录上传机制
- 前端用 `file.webkitRelativePath` 作为 `relative_path` 传给后端 `createUploadBatch`。
- 后端 `normalize_relative_path()` 做 NFC 归一化、拒绝 `..`/绝对路径/保留名/控制字符，规范化后存入 `upload_items.normalized_relative_path` 与 `documents.normalized_relative_path`。
- 文档入库（`create_version_for_item`）按 `normalized_relative_path` 去重并写入 `documents`，**但当前并未创建 `folders` 表层级**（路径以字符串形式挂在文档上）。

### 1.7 文件存储逻辑
- `UploadService` 通过 `StorageClient` 协议写入对象存储。
- 本地开发：`LocalFilesystemStorageClient`，仅当 `EKB_ENV=development` 且设了 `EKB_OBJECT_STORAGE_LOCAL_ROOT` 时启用，上传 URL 为受保护的 API 路由 `/api/v1/kb/uploads/objects?object_key=...`。
- 生产：`S3CompatibleStorageClient`（MinIO/S3 预签名 PUT）。

### 1.8 文档解析逻辑
- 对象落盘后在 `complete_upload` 中调用 `create_version_for_item` 创建 `document_versions`（状态 `UPLOADED`），并投递 `ingest_jobs`（状态 `QUEUED`）。
- 实际解析/切片/Embedding/索引由后台 worker（`services/ingest_worker.py` / `scheduler.py`）消费 `ingest_jobs` 执行，阶段写入 `ingest_job_attempts`。

### 1.9 Embedding / 向量化 / 索引逻辑
- 解析器注册表：`ekb_api/services/parsers/registry.py`（支持 PDF/Word/Excel/PPT/TXT/MD 等）。
- 摄取 worker 走 PARSING → CHUNKING → EMBEDDING → INDEXING，最终 `documents.status=READY`（实际为 `ingest_job` SUCCEEDED 后文档可被检索）。

### 1.10 当前任务状态管理
- 前端：Modal 内 `useState` 持有 `progress`（`BulkUploadProgress`），强绑定 Modal 组件生命周期 → 离开页面即丢失。
- 后端：以 `upload_batches` + `upload_items` + `ingest_jobs` 为权威状态，`getUploadBatch` 提供可轮询投影。

### 1.11 当前数据库模型
- `upload_batches(id, tenant_id, knowledge_base_id, created_by, mode, status, client_request_id, item_count, total_bytes, created_at, updated_at)`
- `upload_items(id, batch_id, client_item_id, display_path, normalized_relative_path, byte_size, expected_sha256, source_object_id, status, uploaded_bytes, error_code, error_detail)`
- `upload_sessions(id, tenant_id, upload_item_id, method, provider_upload_id, state, part_size, expires_at, ...)`
- `ingest_jobs` / `ingest_job_attempts` / `ingest_stages` / `source_objects` / `documents`(含 `normalized_relative_path`, `mime_type`, `status`, `checksum`, `chunk_count`)。

---

## 二、当前存在的问题

| 问题 | 原因 |
|---|---|
| Modal 无法关闭 | 上传中 `onClose` 被 `disabled={isUploading}` 锁死，且 backdrop 点击关闭也要求 `stage !== 'uploading'`。 |
| Cancel 不可用 | 仅有「取消未开始任务」按钮，调用 `abort()`，但语义是 abort 整个 signal，已上传文件无法合理取消；文案误导。 |
| 用户被锁死 | 上传状态全部存在于 Modal 组件 state，离开页面队列即停，期间不能使用知识库其他功能。 |
| 批量上传失败 | **真实 Root Cause 见第三节**：对象存储未配置，批次创建直接 503。 |
| 进度不真实 | 当前仅区分排队/上传/成功/失败，未区分「HTTP 上传完成」与「解析/切片/Embedding/索引完成」；UI 把上传 100% 近似当完成。 |
| 目录结构 | 路径字符串已保存，但 UI 不呈现 folder 层级，且无 `folders` 表。 |
| 上传队列 | 已有真实并发队列（默认 4），但被锁在 Modal 内。 |
| 并发限制 | 默认 4，可调（1/2/4/8），但散在 Modal。 |
| 失败重试 | Modal 支持「重传失败项」，但依赖 resumeItems，易碎。 |
| 刷新恢复 | 本地 File 对象刷新即丢失，无恢复 UX。 |
| 后台任务 | 无：队列绑定 Modal 组件。 |
| 状态混为一谈 | 上传完成即显示「上传成功（后台解析中）」但无独立 Processing 进度；READ 状态与 ingest 完成未严格区分到检索层。 |

---

## 三、Root Cause（选择目录后点击上传直接失败）

**结论：部署服务器 `/opt/ekb/api/.env` 未配置对象存储，导致上传批次创建接口直接返回 503。**

实证（已 SSH 只读核对 `103.236.93.60`）：
1. 服务器运行 MinIO（`/opt/ekb/minio/bin/minio server ... --address 127.0.0.1:9000`），`/opt/ekb/minio/.env` 有 `MINIO_ROOT_USER` / `MINIO_ROOT_PASSWORD` 与 `EKB_OBJECT_STORAGE_ACCESS_KEY` / `EKB_OBJECT_STORAGE_SECRET_KEY`。
2. **但 `/opt/ekb/api/.env` 完全没有 `EKB_OBJECT_STORAGE_ENDPOINT` / `EKB_OBJECT_STORAGE_BUCKET` / `EKB_OBJECT_STORAGE_REGION`**（grep 无匹配）。
3. 代码路径：`kb_upload.create_batch` → `_uploads()` → `UploadService()` → `build_storage_client()`（`ekb_api/services/storage.py:429`）。当未设 `EKB_OBJECT_STORAGE_LOCAL_ROOT`（仅开发环境）且无 `EKB_OBJECT_STORAGE_ENDPOINT` 时，抛 `ObjectStorageUnavailable` → 路由捕获为 `ApiError(503, "OBJECT_STORAGE_UNAVAILABLE", "对象存储未配置，无法接收上传")`。
4. 前端 `uploadBulkImpl` 第一步 `createUploadBatch` 即 503 → 整个上传立即失败，表现为「选择目录后点击上传直接失败」。
5. 本地 `.env`（`EKB_ENV=development`）同样未设 `EKB_OBJECT_STORAGE_LOCAL_ROOT`，本地也会 503。

**修复**：在生产 API 的 `.env` 增加对象存储配置指向已有的 MinIO；本地开发增加 `EKB_OBJECT_STORAGE_LOCAL_ROOT`。配置后 `build_storage_client()` 返回 `S3CompatibleStorageClient`/`LocalFilesystemStorageClient`，批次创建即正常。无需修改上传/摄取业务逻辑代码。

---

## 四、新架构方案

### 4.1 页面与路由
- 新增路由 `#/knowledge/uploads`（沿用现有 hash 清单式 routes.ts），`layout: 'knowledge'`，页面 `UploadTaskPage`。
- KB id 通过 hash query `?kb=<id>` 传递（沿用 `readKbParam()` 约定）。
- 原「批量/目录上传」按钮改为 `window.location.hash = '#/knowledge/uploads?kb=' + selectedKbId`。
- 废弃并删除 `BatchUploadModal` 在 `KnowledgePage` 的引用（测试引用单独处理，见第六节）。

### 4.2 状态解耦（关键）
- 新增模块级单例 `uploadTaskStore`（`app-v2/services/uploadTaskStore.ts`），**不绑定任何组件**，持有当前 `UploadTask` 与 `FileTask[]`，生命周期跨路由。
- `uploadBulkImpl`（已有真实并发队列 + AbortController）作为传输驱动器，由 store 控制 start/pause/resume/cancel/retry。
- 上传完成后，store 轮询 `getUploadBatch` 获取真实处理阶段（PARSING/CHUNKING/EMBEDDING/INDEXING）与最终 SUCCESS。

### 4.3 状态机
- UploadTask：`CREATED / SCANNING / READY / UPLOADING / PAUSED / PROCESSING / COMPLETED / PARTIAL_FAILED / FAILED / CANCELLED`
- FileTask：`WAITING / UPLOADING / UPLOADED / PARSING / CHUNKING / EMBEDDING / INDEXING / SUCCESS / FAILED / CANCELLED`
- 后端 ingest 阶段映射为 PARSING/CHUNKING/EMBEDDING/INDEXING；前端不模拟进度，全部来自服务端投影。

### 4.4 队列与并发
- `MAX_CONCURRENT_UPLOADS = 4`（写死在 config，杜绝 magic number）。
- Worker 信号量：取 4 个 → UPLOADING → 完成自动 dequeue 下一个；单文件失败仅该文件 FAILED，不阻断队列。

### 4.5 进度区分
- **文件上传进度**：`uploadedBytes / totalBytes`（来自 onProgress 的真实字节流）。
- **知识处理进度**：`processingFiles(SUCCESS)/total`，来自服务端 ingest 投影。
- 上传 100% ≠ 完成；只有 ingest SUCCEEDED（文档 READY）才算 SUCCESS。

### 4.6 组件拆分（`app-v2/components/upload/`）
`UploadDropzone / UploadScanSummary / UploadTaskSummary / UploadOverallProgress / UploadProcessingProgress / UploadTaskActions / UploadFileTable / UploadFileRow / UploadStatusBadge / UploadFileProgress / UploadTaskFilters / UploadTaskSearch / UploadHistory / UploadTaskDetail / UploadErrorPanel / UploadNetworkBanner / GlobalUploadIndicator`。

### 4.7 后台续传与刷新
- 队列在 store（模块级）运行，切换路由继续；KB 页面头部显示轻量 `GlobalUploadIndicator`，点击回到 Upload 页。
- 刷新：本地 File 对象丢失时，提示「N 个未上传本地文件需重新选择，服务器已收到的不受影响」；已落库文件从 `getUploadBatch` 恢复处理状态。

---

## 五、验收（P0 必做）
1. 配置对象存储后，单个文件 / 目录可真实上传成功（含中文路径、空格、括号）。
2. 独立 Upload Task 页面；并发 ≤4；可暂停/继续/取消/重试；单文件失败不影响整体。
3. 上传与知识处理进度双轨区分；处理阶段来自服务端。
4. 上传期间可离开知识库、返回后任务继续；KB 头部有入口/指示器。
5. 历史任务来自真实 `listUploadBatches`。
