import type { ApiClient } from '../../lib/api'
import type { UploadBatchItemResponse } from '../../types/api'
import { sha256File, sha256Hex } from '../crypto/sha256'
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

export const MAX_CLIENT_BATCH_FILES = 1000
export const MAX_CLIENT_BATCH_BYTES = 5_368_709_120

function partitionUploadItems(items: readonly BulkFileItem[]): readonly (readonly BulkFileItem[])[] {
  const partitions: BulkFileItem[][] = []
  let current: BulkFileItem[] = []
  let currentBytes = 0

  for (const item of items) {
    const exceedsCount = current.length >= MAX_CLIENT_BATCH_FILES
    const exceedsBytes = current.length > 0 && currentBytes + item.size > MAX_CLIENT_BATCH_BYTES
    if (exceedsCount || exceedsBytes) {
      partitions.push(current)
      current = []
      currentBytes = 0
    }
    current.push(item)
    currentBytes += item.size
  }
  if (current.length > 0) partitions.push(current)
  return partitions
}

async function defaultBuildBatchIdempotencyKey(
  kbId: string,
  mode: 'MULTI_FILE' | 'DIRECTORY',
  items: readonly BulkFileItem[],
  operationId: string,
): Promise<string> {
  const manifest = items
    .map((item) => `${item.path}\u0000${item.size}\u0000${item.file.lastModified ?? 0}`)
    .sort()
    .join('\n')
  const hash = await sha256Hex(new TextEncoder().encode(`${operationId}\n${kbId}\n${mode}\n${manifest}`))
  return `batch:${hash}`
}

function createUploadOperationId(): string {
  if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') {
    return crypto.randomUUID()
  }
  return `${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`
}

interface ServerUploadItem {
  readonly batchId: string
  readonly uploadItemId?: string
  readonly detectedMime?: string | null
  readonly accepted: boolean
  readonly replayStatus?: UploadBatchItemResponse['replay_status']
}

/**
 * 并发上传调度器（信号量模式）。
 *
 * - 先以整个目录/多文件清单创建一个服务端批次，再按受限并发上传对象
 * - 并发数可控（默认 4），避免浏览器并发请求过多
 * - 支持 AbortController 中断（取消未启动任务）
 * - 每一条状态变化立即 onProgress 回调
 */
export async function uploadBulkImpl(
  client: ApiClient,
  kbId: string,
  items: readonly BulkFileItem[],
  options?: BulkUploadOptions,
): Promise<BulkUploadResult> {
  const concurrency = Math.max(1, options?.concurrency ?? 4)
  const mode = options?.mode ?? 'MULTI_FILE'
  const operationId = options?.operationId ?? createUploadOperationId()
  const buildBatchIdempotencyKey = options?.buildBatchIdempotencyKey
    ?? ((targetKbId: string, targetMode: 'MULTI_FILE' | 'DIRECTORY', partition: readonly BulkFileItem[]) => (
      defaultBuildBatchIdempotencyKey(targetKbId, targetMode, partition, operationId)
    ))
  const signal = options?.signal
  const onProgress = options?.onProgress
  const onBatchCreated = options?.onBatchCreated
  const shouldSkipDuringUpload = options?.shouldSkipDuringUpload

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

  const serverItems = new Map<string, ServerUploadItem>()
  const resumeItems = options?.resumeItems ?? new Map()
  for (const item of items) {
    const resume = resumeItems.get(item.id)
    if (!resume) continue
    serverItems.set(item.id, {
      batchId: resume.batchId,
      uploadItemId: resume.uploadItemId,
      accepted: true,
    })
    const previous = perFile.get(item.id)!
    perFile.set(item.id, {
      ...previous,
      batchId: resume.batchId,
      uploadItemId: resume.uploadItemId,
    })
  }
  if (resumeItems.size > 0) emit()

  const itemsToCreate = items.filter((item) => !resumeItems.has(item.id))
  for (const partition of partitionUploadItems(itemsToCreate)) {
    const clientRequestId = await buildBatchIdempotencyKey(kbId, mode, partition)
    const batch = await client.createUploadBatch(kbId, {
      mode,
      client_request_id: clientRequestId,
      items: partition.map((item) => ({
        client_item_id: item.id,
        relative_path: item.path,
        byte_size: item.size,
        browser_mime: item.file.type || undefined,
      })),
    })
    onBatchCreated?.(batch.id)
    const responseByClientItemId = new Map(batch.items.map((item) => [item.client_item_id, item]))
    for (const item of partition) {
      const response = responseByClientItemId.get(item.id)
      const previous = perFile.get(item.id)!
      if (!response) {
        perFile.set(item.id, {
          ...previous,
          batchId: batch.batch_id,
          status: 'failed',
          error: {
            code: 'UPLOAD_PREFLIGHT_RESPONSE_INVALID',
            message: '服务端上传预检响应不完整，请重试。',
          },
        })
        continue
      }
      serverItems.set(item.id, {
        batchId: batch.batch_id,
        uploadItemId: response.upload_item_id ?? undefined,
        detectedMime: response.detected_mime,
        accepted: response.accepted,
        replayStatus: response.replay_status,
      })
      if (response.accepted && response.replay_status === 'ALREADY_COMPLETED') {
        perFile.set(item.id, {
          ...previous,
          batchId: batch.batch_id,
          uploadItemId: response.upload_item_id ?? undefined,
          status: 'success',
        })
      } else if (!response.accepted || !response.upload_item_id) {
        perFile.set(item.id, {
          ...previous,
          batchId: batch.batch_id,
          // Rejected rows have an internal ID for auditability, not for retry.
          // Retaining it would let the modal bypass server preflight on retry.
          uploadItemId: response.accepted ? response.upload_item_id ?? undefined : undefined,
          status: 'failed',
          error: {
            code: response.error_code ?? 'UPLOAD_PREFLIGHT_REJECTED',
            message: '服务端未接受该文件，请检查文件大小、相对路径和上传配额。',
          },
        })
      } else {
        perFile.set(item.id, {
          ...previous,
          batchId: batch.batch_id,
          uploadItemId: response.upload_item_id,
        })
      }
    }
    // Persist every created batch reference before the next partition starts.
    // If its POST fails, the UI can resume these original server items.
    emit()
  }
  const acceptedItems: BulkFileItem[] = []

  for (const item of items) {
    const serverItem = serverItems.get(item.id)
    const previous = perFile.get(item.id)!
    if (serverItem?.accepted && serverItem.replayStatus === 'ALREADY_COMPLETED') continue
    if (!serverItem || !serverItem.accepted || !serverItem.uploadItemId) {
      perFile.set(item.id, {
        ...previous,
        batchId: serverItem?.batchId,
        status: 'failed',
        error: {
          code: previous.error?.code ?? 'UPLOAD_PREFLIGHT_REJECTED',
          message: '服务端未接受该文件，请检查文件大小、相对路径和上传配额。',
        },
      })
      continue
    }
    perFile.set(item.id, {
      ...previous,
      batchId: serverItem.batchId,
      uploadItemId: serverItem.uploadItemId,
    })
    acceptedItems.push(item)
  }
  emit()

  // A batch is created before object bytes are transferred. Its item map is
  // authoritative, so individual workers never create invisible FILE batches.
  const queue = [...acceptedItems]

  const runOne = async (item: BulkFileItem) => {
    if (signal?.aborted) {
      const cur = perFile.get(item.id)!
      perFile.set(item.id, { ...cur, status: 'skipped', skippedReason: '已取消' })
      emit()
      return
    }
    if (shouldSkipDuringUpload?.(item.id)) {
      const cur = perFile.get(item.id)!
      perFile.set(item.id, { ...cur, status: 'skipped', skippedReason: '已取消' })
      emit()
      return
    }
    // uploading
    const prev = perFile.get(item.id)!
    perFile.set(item.id, { ...prev, status: 'uploading', uploadedBytes: 0, uploadPhase: 'hashing' })
    emit()
    try {
      const serverItem = serverItems.get(item.id)
      if (!serverItem?.uploadItemId) {
        throw new Error('上传批次缺少有效上传项')
      }
      // Hash before opening a session. Large files cannot consume session TTL
      // while their integrity check runs in the isolated hash worker.
      const checksum = await sha256File(item.file)
      // Sessions are deliberately opened just before the byte transfer. This
      // prevents a long directory queue from consuming the one-hour URL TTL.
      const session = await client.openUploadItemSession(serverItem.uploadItemId)
      if (session.already_completed) {
        const cur = perFile.get(item.id)!
        perFile.set(item.id, {
          ...cur,
          status: 'success',
          data: {
            docId: session.document_id ?? '',
            jobId: session.ingest_job_id ?? '',
            status: session.status ?? 'COMPLETED',
            traceId: session.ingest_job_id ?? '',
          },
        })
        return
      }
      const uploadUrl = session.upload_urls?.[0]
      if (!uploadUrl) {
        throw new Error('上传会话缺少有效上传地址')
      }
      const hashing = perFile.get(item.id)!
      perFile.set(item.id, { ...hashing, uploadPhase: 'transferring', uploadedBytes: 0 })
      emit()
      await client.putUploadObject(uploadUrl, item.file, (uploadedBytes) => {
        const current = perFile.get(item.id)
        if (!current || current.status !== 'uploading') return
        perFile.set(item.id, {
          ...current,
          uploadedBytes: Math.max(0, Math.min(item.size, Math.floor(uploadedBytes))),
        })
        emit()
      })
      const completed = await client.completeUploadItem(serverItem.uploadItemId, {
        sha256: checksum,
        detected_mime: serverItem.detectedMime ?? (item.file.type || undefined),
      })
      const data: UploadAcceptedView = {
        docId: completed.document_id,
        jobId: completed.ingest_job_id,
        status: completed.status,
        traceId: completed.ingest_job_id,
      }
      const cur = perFile.get(item.id)!
      perFile.set(item.id, {
        ...cur,
        status: 'success',
        data,
        uploadedBytes: item.size,
        batchId: serverItem.batchId,
        uploadItemId: serverItem.uploadItemId,
      })
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
      const next = queue.shift()!
      await runOne(next)
    }
  }

  // 启动 concurrency 个 worker
  const workers: Promise<void>[] = []
  for (let i = 0; i < concurrency; i += 1) {
    workers.push(worker())
  }
  await Promise.all(workers)

  if (signal?.aborted) {
    const pending = items.filter((item) => perFile.get(item.id)?.status === 'queued')
    await Promise.allSettled(pending.map(async (item) => {
      const serverItem = serverItems.get(item.id)
      try {
        if (serverItem?.uploadItemId) await client.abortUploadItem(serverItem.uploadItemId)
      } finally {
        const current = perFile.get(item.id)!
        perFile.set(item.id, { ...current, status: 'skipped', skippedReason: '已取消' })
      }
    }))
    emit()
  }

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
  return { summary, batchId: undefined }
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
