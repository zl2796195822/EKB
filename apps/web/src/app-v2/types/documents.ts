import type { AdapterError } from './adapters'
import type { PageState } from './view-state'

export interface DocumentView {
  readonly id: string
  readonly kbId: string
  readonly title: string
  readonly status: 'PROCESSING' | 'INDEXING' | 'READY' | 'FAILED' | 'DELETED'
  readonly version: number
  readonly mimeType: string
  readonly checksum: string
  readonly chunkCount: number
  readonly fileSize?: number | null
  readonly failureReason: string | null
  readonly createdAt: string
  readonly updatedAt: string
}

export interface DocumentListResult {
  readonly state: PageState
  readonly data?: readonly DocumentView[]
  readonly error?: AdapterError
}

export interface DocumentItemResult {
  readonly state: PageState
  readonly data?: DocumentView
  readonly error?: AdapterError
}

export interface UploadResult {
  readonly state: PageState
  readonly data?: UploadAcceptedView
  readonly error?: AdapterError
}

export interface UploadAcceptedView {
  readonly docId: string
  readonly jobId: string
  readonly status: string
  readonly traceId: string
}

export interface DocumentVersionChunkView {
  readonly chunkIndex?: number
  readonly sectionPath: readonly string[]
  readonly contentHash?: string
  readonly contentPreview?: string
}

export interface DocumentVersionView {
  readonly id: string
  readonly docId: string
  readonly version: number
  readonly checksum: string
  readonly chunkCount: number
  readonly contentSnapshot: readonly DocumentVersionChunkView[]
  readonly createdAt: string
}

export interface DocumentDiffChunkView {
  readonly chunkIndex?: number
  readonly contentHash?: string
  readonly content?: string
  readonly contentPreview?: string
  readonly before?: string
  readonly after?: string
  readonly sectionPath: readonly string[]
}

export interface DocumentDiffView {
  readonly docId?: string
  readonly fromVersion: number
  readonly toVersion: number
  readonly fromChunkCount?: number
  readonly toChunkCount?: number
  readonly added: readonly DocumentDiffChunkView[]
  readonly removed: readonly DocumentDiffChunkView[]
  readonly changed: readonly DocumentDiffChunkView[]
  readonly unchanged: readonly DocumentDiffChunkView[]
}

export interface DocumentVersionsResult {
  readonly state: PageState
  readonly data?: readonly DocumentVersionView[]
  readonly error?: AdapterError
}

export interface DocumentDiffResult {
  readonly state: PageState
  readonly data?: DocumentDiffView
  readonly error?: AdapterError
}

export interface DocumentsServices {
  readonly list: (kbId: string) => Promise<DocumentListResult>
  readonly get: (kbId: string, docId: string) => Promise<DocumentItemResult>
  readonly upload: (kbId: string, file: File) => Promise<UploadResult>
  readonly uploadBulk: (
    kbId: string,
    items: readonly BulkFileItem[],
    options?: BulkUploadOptions,
  ) => Promise<BulkUploadResult>
  readonly remove: (kbId: string, docId: string) => Promise<{ readonly state: PageState; readonly error?: AdapterError }>
  readonly retry: (kbId: string, docId: string) => Promise<{ readonly state: PageState; readonly data?: { readonly status: string; readonly traceId: string }; readonly error?: AdapterError }>
  readonly listVersions: (kbId: string, docId: string) => Promise<DocumentVersionsResult>
  readonly diff: (kbId: string, docId: string, from: number, to: number) => Promise<DocumentDiffResult>
  readonly listUploadBatches: () => Promise<{ readonly state: PageState; readonly data?: readonly UploadBatchView[]; readonly error?: AdapterError }>
  readonly getUploadBatch: (batchId: string) => Promise<UploadBatchResult>
  readonly retryUploadJob: (jobId: string) => Promise<UploadBatchActionResult>
  readonly cancelUploadJob: (jobId: string) => Promise<UploadBatchActionResult>
  readonly abortUploadItem: (itemId: string) => Promise<UploadBatchActionResult>
}

export type UploadCenterStatus = 'queued' | 'uploading' | 'verifying' | 'processing' | 'indexing' | 'ready' | 'failed' | 'cancelled'

export interface UploadBatchItemView {
  readonly id: string
  readonly clientItemId: string
  readonly relativePath: string
  readonly status: UploadCenterStatus
  readonly sourceStatus?: string
  readonly byteSize?: number
  readonly uploadedBytes?: number
  readonly stage: string | null
  readonly attemptId: string | null
  readonly attemptNo: number | null
  readonly attempts: number
  readonly progress: Readonly<Record<string, unknown>> | null
  readonly versionId: string | null
  readonly jobId: string | null
  readonly error: { readonly code: string; readonly message: string; readonly retryable: boolean; readonly detail?: Readonly<Record<string, unknown>> } | null
}

export interface UploadBatchView {
  readonly id: string
  readonly kbId: string
  readonly mode: string
  readonly status: UploadCenterStatus
  readonly itemCount: number
  readonly totalBytes: number
  readonly createdAt: string
  readonly updatedAt: string
  readonly counts: Readonly<Record<string, number>>
  readonly items: readonly UploadBatchItemView[]
}

export interface UploadBatchResult {
  readonly state: PageState
  readonly data?: UploadBatchView
  readonly error?: AdapterError
}

export interface UploadBatchActionResult {
  readonly state: PageState
  readonly data?: { readonly status: string; readonly attemptId?: string; readonly attemptNo?: number }
  readonly error?: AdapterError
}

/**
 * 批量/目录上传 —— 单个文件的元信息。
 * path 使用 File.webkitRelativePath（目录上传时）或回退到 file.name（多选文件时）。
 */
export interface BulkFileItem {
  readonly id: string
  readonly file: File
  /** 相对路径；单选时退化为文件名 */
  readonly path: string
  readonly size: number
}

export type BulkFileStatus =
  | 'queued'
  | 'uploading'
  | 'success'
  | 'failed'
  | 'skipped'

export interface BulkFileProgress {
  readonly id: string
  readonly path: string
  readonly size: number
  readonly status: BulkFileStatus
  readonly data?: UploadAcceptedView
  readonly error?: AdapterError
  readonly skippedReason?: string
  readonly batchId?: string
  readonly uploadItemId?: string
}

export interface BulkUploadProgress {
  readonly total: number
  readonly completed: number
  readonly failed: number
  readonly skipped: number
  readonly perFile: ReadonlyMap<string, BulkFileProgress>
}

export interface BulkUploadOptions {
  /** 并发数，默认 4 */
  readonly concurrency?: number
  /** 单文件上传前的幂等 key 生成器；默认 kbId+path+size+lastModified */
  readonly buildIdempotencyKey?: (kbId: string, item: BulkFileItem) => string
  readonly onProgress?: (progress: BulkUploadProgress) => void
  /** 可中断信号（AbortController.signal）；触发后未启动任务不再启动 */
  readonly signal?: AbortSignal
}

export interface BulkUploadSummary {
  readonly total: number
  readonly successCount: number
  readonly failedCount: number
  readonly skippedCount: number
  readonly successItems: readonly (BulkFileProgress & { status: 'success' })[]
  readonly failedItems: readonly (BulkFileProgress & { status: 'failed' })[]
  readonly skippedItems: readonly (BulkFileProgress & { status: 'skipped' })[]
}

export interface BulkUploadResult {
  readonly summary: BulkUploadSummary
}
