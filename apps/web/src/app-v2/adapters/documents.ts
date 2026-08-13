import type { ApiClient } from '../../lib/api'
import type { AdapterCapability } from '../types'
import type {
  BulkFileItem,
  BulkFileProgress,
  BulkUploadOptions,
  BulkUploadProgress,
  BulkUploadResult,
  BulkUploadSummary,
  DocumentDiffChunkView,
  DocumentDiffView,
  DocumentVersionChunkView,
  DocumentVersionView,
  DocumentView,
  DocumentsServices,
  UploadAcceptedView,
  UploadBatchActionResult,
  UploadBatchItemView,
  UploadBatchView,
} from '../types'
import { stateForData, stateForError, toAdapterError } from './index'

export interface DocumentRef {
  readonly id: string
  readonly knowledgeSpaceId: string
  readonly title: string
}

export interface DocumentsAdapter {
  readonly service: DocumentsServices
}

function mapDocument(input: import('../../types/api').DocumentRecord): DocumentView {
  return {
    id: input.id,
    kbId: input.kb_id,
    title: input.title,
    status: input.status,
    version: input.version,
    mimeType: input.mime_type,
    checksum: input.checksum,
    chunkCount: input.chunk_count,
    fileSize: input.file_size,
    failureReason: input.failure_reason,
    createdAt: input.created_at,
    updatedAt: input.updated_at,
  }
}

function mapUpload(input: import('../../types/api').UploadAcceptedResponse): UploadAcceptedView {
  return { docId: input.doc_id, jobId: input.job_id, status: input.status, traceId: input.trace_id }
}

function mapUploadBatch(input: import('../../types/api').UploadBatchProjection): UploadBatchView {
  return {
    id: input.id,
    kbId: input.kb_id,
    mode: input.mode,
    status: input.status as UploadBatchView['status'],
    itemCount: input.item_count,
    totalBytes: input.total_bytes,
    createdAt: input.created_at,
    updatedAt: input.updated_at,
    counts: input.counts ?? {},
    items: input.items.map((item): UploadBatchItemView => ({
      id: item.id,
      clientItemId: item.client_item_id,
      relativePath: item.relative_path,
      status: item.status as UploadBatchItemView['status'],
      sourceStatus: item.source_status,
      byteSize: item.byte_size,
      uploadedBytes: item.uploaded_bytes,
      stage: item.stage ?? null,
      attemptId: item.attempt_id ?? null,
      attemptNo: item.attempt_no ?? null,
      attempts: item.attempts ?? 0,
      progress: item.progress,
      versionId: item.version_id,
      jobId: item.job_id,
      error: item.error,
    })),
  }
}

function mapVersionChunk(input: import('../../types/api').DocumentVersionChunk): DocumentVersionChunkView {
  return {
    chunkIndex: input.chunk_index,
    sectionPath: input.section_path ?? [],
    contentHash: input.content_hash,
    contentPreview: input.content_preview,
  }
}

function mapVersion(input: import('../../types/api').DocumentVersionRecord): DocumentVersionView {
  return {
    id: input.id,
    docId: input.doc_id,
    version: input.version,
    checksum: input.checksum,
    chunkCount: input.chunk_count,
    contentSnapshot: input.content_snapshot.map(mapVersionChunk),
    createdAt: input.created_at,
  }
}

function mapDiffChunk(input: import('../../types/api').DiffChunk): DocumentDiffChunkView {
  return {
    chunkIndex: input.chunk_index,
    contentHash: input.content_hash,
    content: input.content,
    contentPreview: input.content_preview,
    before: input.before,
    after: input.after,
    sectionPath: input.section_path ?? [],
  }
}

function mapDiff(input: import('../../types/api').DocumentDiff): DocumentDiffView {
  return {
    docId: input.doc_id,
    fromVersion: input.from_version,
    toVersion: input.to_version,
    fromChunkCount: input.from_chunk_count,
    toChunkCount: input.to_chunk_count,
    added: input.added.map(mapDiffChunk),
    removed: input.removed.map(mapDiffChunk),
    changed: (input.changed ?? []).map(mapDiffChunk),
    unchanged: (input.unchanged ?? []).map(mapDiffChunk),
  }
}

function errorResult<T>(error: unknown, message: string) {
  const mapped = toAdapterError(error, message)
  return { state: stateForError(mapped), error: mapped }
}

function defaultBuildIdempotencyKey(kbId: string, item: BulkFileItem): string {
  const file = item.file
  const stamp = file.lastModified ?? 0
  return `kb:${kbId};p:${item.path};s:${item.size};m:${stamp}`
}

async function sha256File(file: File): Promise<string> {
  const digest = await crypto.subtle.digest('SHA-256', await file.arrayBuffer())
  return Array.from(new Uint8Array(digest), (byte) => byte.toString(16).padStart(2, '0')).join('')
}

/**
 * 并发上传调度器（信号量模式）。
 *
 * - 使用客户端单文件 uploadDocument 接口（天然异步，后台解析不阻塞 HTTP）
 * - 并发数可控（默认 4），避免浏览器并发请求过多
 * - 支持 AbortController 中断（取消未启动任务）
 * - 每一条状态变化立即 onProgress 回调
 */
async function uploadBulkImpl(
  client: ApiClient,
  kbId: string,
  items: readonly BulkFileItem[],
  options?: BulkUploadOptions,
): Promise<BulkUploadResult> {
  const concurrency = Math.max(1, options?.concurrency ?? 4)
  const buildIdem = options?.buildIdempotencyKey ?? defaultBuildIdempotencyKey
  const signal = options?.signal
  const onProgress = options?.onProgress

  // 初始化 perFile（全量 queued）
  const perFile = new Map<string, BulkFileProgress>()
  for (const it of items) {
    perFile.set(it.id, {
      id: it.id,
      path: it.path,
      size: it.size,
      status: 'queued',
    })
  }
  const initialProgress: BulkUploadProgress = {
    total: items.length,
    completed: 0,
    failed: 0,
    skipped: 0,
    perFile: new Map(perFile),
  }
  onProgress?.(initialProgress)

  const emit = () => {
    let completed = 0
    let failed = 0
    let skipped = 0
    for (const v of perFile.values()) {
      if (v.status === 'success') completed += 1
      else if (v.status === 'failed') failed += 1
      else if (v.status === 'skipped') skipped += 1
    }
    onProgress?.({
      total: items.length,
      completed,
      failed,
      skipped,
      perFile: new Map(perFile),
    })
  }

  // 队列 + 信号量
  const queue = [...items]
  let running = 0

  const runOne = async (item: BulkFileItem) => {
    if (signal?.aborted) {
      const cur = perFile.get(item.id)!
      perFile.set(item.id, { ...cur, status: 'skipped', skippedReason: '已取消' })
      emit()
      return
    }
    // uploading
    const prev = perFile.get(item.id)!
    perFile.set(item.id, { ...prev, status: 'uploading' })
    emit()
    try {
      const idem = buildIdem(kbId, item)
      const checksum = await sha256File(item.file)
      const batch = await client.createUploadBatch(kbId, {
        mode: 'FILE',
        client_request_id: idem,
        items: [{
          client_item_id: item.id,
          relative_path: item.path,
          byte_size: item.size,
          browser_mime: item.file.type || undefined,
          sha256: checksum,
        }],
      })
      const accepted = batch.items.find((entry) => entry.client_item_id === item.id)
      if (!accepted?.accepted || !accepted.upload_item_id || !accepted.upload_session?.upload_urls[0]) {
        throw new Error(accepted?.error_code || '上传预检未接受文件')
      }
      const withServerIds = perFile.get(item.id)!
      perFile.set(item.id, { ...withServerIds, batchId: batch.batch_id, uploadItemId: accepted.upload_item_id, status: 'uploading' })
      emit()
      await client.putUploadObject(accepted.upload_session.upload_urls[0], item.file)
      const completed = await client.completeUploadItem(accepted.upload_item_id, {
        sha256: checksum,
        detected_mime: accepted.detected_mime ?? (item.file.type || undefined),
      })
      const data: UploadAcceptedView = {
        docId: completed.document_id,
        jobId: completed.ingest_job_id,
        status: completed.status,
        traceId: completed.ingest_job_id,
      }
      const cur = perFile.get(item.id)!
      perFile.set(item.id, { ...cur, status: 'success', data, batchId: batch.batch_id, uploadItemId: accepted.upload_item_id })
    } catch (error) {
      const mapped = toAdapterError(error, '文档上传失败')
      const cur = perFile.get(item.id)!
      perFile.set(item.id, { ...cur, status: 'failed', error: mapped })
    } finally {
      emit()
    }
  }

  const worker = async () => {
    while (queue.length > 0 && !signal?.aborted) {
      running += 1
      const next = queue.shift()!
      try {
        await runOne(next)
      } finally {
        running -= 1
      }
    }
  }

  // 启动 concurrency 个 worker
  const workers: Promise<void>[] = []
  for (let i = 0; i < concurrency; i += 1) {
    workers.push(worker())
  }
  await Promise.all(workers)

  // 构造 summary
  const successItems: BulkFileProgress[] = []
  const failedItems: BulkFileProgress[] = []
  const skippedItems: BulkFileProgress[] = []
  for (const v of perFile.values()) {
    if (v.status === 'success') successItems.push(v)
    else if (v.status === 'failed') failedItems.push(v)
    else skippedItems.push(v)
  }
  const summary: BulkUploadSummary = {
    total: items.length,
    successCount: successItems.length,
    failedCount: failedItems.length,
    skippedCount: skippedItems.length,
    successItems: successItems as BulkUploadSummary['successItems'],
    failedItems: failedItems as BulkUploadSummary['failedItems'],
    skippedItems: skippedItems as BulkUploadSummary['skippedItems'],
  }
  return { summary }
}

export function createDocumentsAdapter(client: ApiClient): DocumentsServices {
  return {
    list: async (kbId) => {
      try {
        const data = (await client.listDocuments(kbId)).map(mapDocument)
        return { state: stateForData(data), data }
      } catch (error) {
        return errorResult(error, '文档列表加载失败')
      }
    },
    get: async (kbId, docId) => {
      try {
        return { state: 'ready', data: mapDocument(await client.getDocument(kbId, docId)) }
      } catch (error) {
        return errorResult(error, '文档详情加载失败')
      }
    },
    upload: async (kbId, file) => {
      try {
        return { state: 'ready', data: mapUpload(await client.uploadDocument(kbId, file)) }
      } catch (error) {
        return errorResult(error, '文档上传失败')
      }
    },
    uploadBulk: async (kbId, items, options) => uploadBulkImpl(client, kbId, items, options),
    remove: async (kbId, docId) => {
      try {
        await client.deleteDocument(kbId, docId)
        return { state: 'ready' }
      } catch (error) {
        return errorResult(error, '文档删除失败')
      }
    },
    retry: async (kbId, docId) => {
      try {
        const result = await client.retryDocument(kbId, docId)
        return { state: 'ready', data: { status: result.status, traceId: result.trace_id } }
      } catch (error) {
        return errorResult(error, '文档重试失败')
      }
    },
    listVersions: async (kbId, docId) => {
      try {
        const data = (await client.listDocumentVersions(kbId, docId)).map(mapVersion)
        return { state: stateForData(data), data }
      } catch (error) {
        return errorResult(error, '版本历史加载失败')
      }
    },
    diff: async (kbId, docId, from, to) => {
      try {
        return { state: 'ready', data: mapDiff(await client.getDocumentDiff(kbId, docId, from, to)) }
      } catch (error) {
        return errorResult(error, '版本差异加载失败')
      }
    },
    listUploadBatches: async () => {
      try {
        const data = (await client.listUploadBatches(30)).items.map(mapUploadBatch)
        return { state: stateForData(data), data }
      } catch (error) {
        return errorResult(error, '上传批次列表加载失败')
      }
    },
    getUploadBatch: async (batchId) => {
      try {
        return { state: 'ready', data: mapUploadBatch(await client.getUploadBatch(batchId)) }
      } catch (error) {
        return errorResult(error, '上传批次状态加载失败')
      }
    },
    retryUploadJob: async (jobId): Promise<UploadBatchActionResult> => {
      try {
        const result = await client.retryIngestJob(jobId)
        return { state: 'ready', data: { status: 'queued', attemptId: result.attempt_id, attemptNo: result.attempt_no } }
      } catch (error) {
        return errorResult(error, '摄取任务重试失败')
      }
    },
    cancelUploadJob: async (jobId): Promise<UploadBatchActionResult> => {
      try {
        const result = await client.cancelIngestJob(jobId)
        return { state: 'ready', data: { status: result.status } }
      } catch (error) {
        return errorResult(error, '摄取任务取消失败')
      }
    },
    abortUploadItem: async (itemId): Promise<UploadBatchActionResult> => {
      try {
        const result = await client.abortUploadItem(itemId)
        return { state: 'ready', data: { status: result.status } }
      } catch (error) {
        return errorResult(error, '上传项取消失败')
      }
    },
  }
}

export const DOCUMENT_CAPABILITIES = [
  {
    id: 'documents.list-upload-delete-retry',
    status: 'available',
  },
  {
    id: 'documents.versions-diff',
    status: 'available',
  },
  {
    id: 'documents.bulk-delete',
    status: 'available',
    reason: '使用现有真实单文档 DELETE 逐条执行并支持部分失败反馈。',
  },
] as const satisfies readonly AdapterCapability[]
