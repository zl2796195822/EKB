/**
 * 上传任务中心 —— 模块级单例 Store。
 *
 * 设计要点：
 * - 不绑定任何 React 组件，生命周期跨路由；切换页面 / 离开路由时上传与轮询继续。
 * - 真实传输复用 adapters/documents.uploadBulkImpl（已含并发信号量 + AbortController）。
 * - 上传完成（HTTP 落盘）后，轮询服务端 ingest 投影得到 PARSING/CHUNKING/EMBEDDING/INDEXING/SUCCESS。
 * - 严禁用上传 100% 冒充任务完成。
 */

import { extOf, kindFromExt, MAX_CONCURRENT_UPLOADS } from '../types/upload-task'
import type {
  FileKind,
  FileTask,
  FileTaskError,
  FileTaskStatus,
  HistoryBatch,
  ScanSummary,
  UploadTask,
  UploadTaskStatus,
} from '../types/upload-task'
import type { AdapterError, PageState } from '../types'
import type { V2Services } from '../types/services'
import type { BulkFileItem, BulkFileProgress, BulkUploadOptions } from '../types/documents'

const SESSION_KEY = 'ekb.upload-task.last-v1'

interface StoreState {
  task: UploadTask | null
  history: HistoryBatch[]
  historyState: PageState
  network: 'online' | 'offline'
}

function nowIso(): string {
  return new Date().toISOString()
}

function formatBytes(n: number): string {
  if (n <= 0) return '0 B'
  const units = ['B', 'KB', 'MB', 'GB', 'TB']
  let v = n
  let u = 0
  while (v >= 1024 && u < units.length - 1) {
    v /= 1024
    u += 1
  }
  return `${v.toFixed(v >= 10 || u === 0 ? 0 : 2)} ${units[u]}`
}

function dirOf(path: string): string {
  const idx = path.lastIndexOf('/')
  return idx >= 0 ? path.slice(0, idx) : ''
}

function leafOf(path: string): string {
  const idx = path.lastIndexOf('/')
  return idx >= 0 ? path.slice(idx + 1) : path
}

function makeClientItemId(index: number, path: string): string {
  let hash = 0x811c9dc5
  for (let offset = 0; offset < path.length; offset += 1) {
    hash ^= path.charCodeAt(offset)
    hash = Math.imul(hash, 0x01000193)
  }
  return `v-${index}-${(hash >>> 0).toString(36)}`
}

class UploadTaskStore {
  private services: V2Services | null = null
  private listeners = new Set<() => void>()
  private state: StoreState = { task: null, history: [], historyState: 'empty', network: 'online' }
  private snapshot: StoreState = this.state
  private commitTimer: ReturnType<typeof setTimeout> | null = null
  private abort: AbortController | null = null
  private pollTimer: ReturnType<typeof setInterval> | null = null
  private pollIntervalMs = 2500
  private speedSamples: { t: number; bytes: number }[] = []
  private lastUploadedBytes = 0
  private batchIds: string[] = []
  private onTaskCompleteCb: (() => void) | null = null
  private fileRefs = new Map<string, File>()
  private listenersBound = false

  init(services: V2Services): void {
    this.services = services
    if (typeof window !== 'undefined' && !this.listenersBound) {
      this.listenersBound = true
      window.addEventListener('online', this.handleOnline)
      window.addEventListener('offline', this.handleOffline)
      this.state = { ...this.state, network: navigator.onLine ? 'online' : 'offline' }
    }
  }

  setOnTaskComplete(cb: (() => void) | null): void {
    this.onTaskCompleteCb = cb
  }

  subscribe = (cb: () => void): (() => void) => {
    this.listeners.add(cb)
    return () => {
      this.listeners.delete(cb)
    }
  }

  getSnapshot = (): StoreState => this.snapshot

  private handleOnline = () => {
    this.state = { ...this.state, network: 'online' }
    this.commit()
    // 网络恢复后，若任务处于中断且用户未主动暂停，可自动继续（由页面调用 resume）。
  }

  private handleOffline = () => {
    this.state = { ...this.state, network: 'offline' }
    this.commit()
    const task = this.state.task
    if (task && (task.status === 'UPLOADING')) {
      this.pause(true)
    }
  }

  private scheduleCommit() {
    if (this.commitTimer) return
    this.commitTimer = setTimeout(() => {
      this.commitTimer = null
      this.commit()
    }, 200)
  }

  private commit() {
    this.snapshot = {
      task: this.state.task ? { ...this.state.task } : null,
      history: this.state.history,
      historyState: this.state.historyState,
      network: this.state.network,
    }
    this.listeners.forEach((cb) => cb())
  }

  // ---- 扫描阶段 ----

  scan(kbId: string, kbName: string, files: FileList | File[], mode: 'MULTI_FILE' | 'DIRECTORY'): void {
    const list = Array.from(files as ArrayLike<File>)
    const seen = new Set<string>()
    let uploadable = 0
    let skippedUnsupported = 0
    let skippedDuplicate = 0
    let skippedZeroByte = 0
    let totalBytes = 0
    const byKind: Record<string, number> = {}
    const fileMap = new Map<string, FileTask>()

    list.forEach((file, index) => {
      const relPath =
        mode === 'DIRECTORY' && (file as File & { webkitRelativePath?: string }).webkitRelativePath
          ? (file as File & { webkitRelativePath: string }).webkitRelativePath
          : file.name
      const ext = extOf(relPath)
      const kind = kindFromExt(ext) as FileKind
      const size = file.size

      let skip: string | null = null
      if (size <= 0) {
        skippedZeroByte += 1
        skip = '空文件（0 字节）不支持'
      } else if (kind === 'Other') {
        skippedUnsupported += 1
        skip = '文件格式不支持'
      }

      const dedupKey = `${relPath}|${size}|${file.lastModified ?? 0}`
      if (!skip && seen.has(dedupKey)) {
        skippedDuplicate += 1
        skip = '重复文件已跳过'
      }
      if (!skip) seen.add(dedupKey)

      const id = makeClientItemId(index, relPath)
      const task: FileTask = {
        id,
        name: leafOf(relPath),
        relativePath: relPath,
        dirPath: dirOf(relPath),
        kind,
        size,
        status: skip ? ('CANCELLED' as FileTaskStatus) : ('WAITING' as FileTaskStatus),
        uploadedBytes: 0,
        speed: 0,
        error: null,
        retryCount: 0,
        skippedReason: skip ?? undefined,
      }
      fileMap.set(id, task)
      if (!skip) {
        this.fileRefs.set(id, file)
        uploadable += 1
        totalBytes += size
        byKind[kind] = (byKind[kind] ?? 0) + 1
      }
    })

    const scan: ScanSummary = {
      total: list.length,
      uploadable,
      skippedUnsupported,
      skippedDuplicate,
      skippedZeroByte,
      totalBytes,
      byKind,
    }

    const task: UploadTask = {
      id: `task-${Date.now().toString(36)}`,
      kbId,
      kbName,
      status: 'READY',
      mode,
      scan,
      files: fileMap,
      batchId: null,
      totalFiles: list.length,
      waitingFiles: uploadable,
      uploadingFiles: 0,
      uploadedFiles: 0,
      processingFiles: 0,
      completedFiles: 0,
      failedFiles: 0,
      cancelledFiles: skippedZeroByte + skippedUnsupported + skippedDuplicate,
      totalBytes,
      uploadedBytes: 0,
      createdAt: nowIso(),
      startedAt: null,
      completedAt: null,
      speed: 0,
      error: null,
    }
    this.stopPolling()
    this.state = { ...this.state, task }
    this.persist(task)
    this.commit()
  }

  // ---- 上传阶段 ----

  private itemsFor(task: UploadTask, statuses: FileTaskStatus[]): BulkFileItem[] {
    const items: BulkFileItem[] = []
    task.files.forEach((ft) => {
      if (statuses.includes(ft.status)) {
        const file = this.fileRefs.get(ft.id)
        if (file) {
          items.push({ id: ft.id, file, path: ft.relativePath, size: ft.size })
        }
      }
    })
    return items
  }

  async start(): Promise<void> {
    const task = this.state.task
    if (!task || !this.services) return
    if (task.status === 'UPLOADING' || task.status === 'PROCESSING') return
    const items = this.itemsFor(task, ['WAITING'])
    if (items.length === 0) {
      // 没有待上传文件，直接进入处理轮询（例如全部已取消）
      this.beginProcessingIfNeeded()
      return
    }
    this.abort = new AbortController()
    this.speedSamples = []
    this.lastUploadedBytes = 0
    task.status = 'UPLOADING'
    task.startedAt = task.startedAt ?? nowIso()
    this.commit()

    const options: BulkUploadOptions = {
      mode: task.mode,
      concurrency: MAX_CONCURRENT_UPLOADS,
      operationId: `op-${task.id}-${Date.now().toString(36)}`,
      signal: this.abort.signal,
      onBatchCreated: (batchId) => {
        this.batchIds.push(batchId)
        task.batchId = batchId
        this.persist(task)
      },
      shouldSkipDuringUpload: (id) => {
        const ft = task.files.get(id)
        return ft?.status === 'CANCELLED'
      },
      onProgress: (progress) => this.applyProgress(progress),
    }

    try {
      await this.services.documents.uploadBulk(task.kbId, items, options)
    } catch {
      // 整批中断由 onProgress 的 skipped 映射处理；此处仅确保状态不卡死
    } finally {
      this.applyProgressTerminal(task)
      this.beginProcessingIfNeeded()
    }
  }

  private applyProgress(progress: { perFile: ReadonlyMap<string, BulkFileProgress> }) {
    const task = this.state.task
    if (!task) return
    progress.perFile.forEach((p, id) => {
      const ft = task.files.get(id)
      if (!ft) return
      const mapped = this.mapUploadStatus(p.status)
      ft.status = mapped
      ft.uploadedBytes = Math.max(0, Math.min(ft.size, p.uploadedBytes ?? (mapped === 'UPLOADED' ? ft.size : 0)))
      if (p.batchId && !ft.batchId) ft.batchId = p.batchId
      if (p.uploadItemId) ft.uploadItemId = p.uploadItemId
      if (p.status === 'failed' && p.error) {
        ft.error = this.normalizeError(p.error)
      }
      if (mapped === 'CANCELLED') ft.skippedReason = ft.skippedReason ?? '已取消'
    })
    this.recompute(task)
    this.scheduleCommit()
  }

  private applyProgressTerminal(task: UploadTask) {
    // 上传驱动结束后，处理阶段尚未开始的 UPLOADED 保持，等待轮询推进
    this.recompute(task)
    this.commit()
  }

  private mapUploadStatus(s: BulkFileProgress['status']): FileTaskStatus {
    switch (s) {
      case 'queued':
        return 'WAITING'
      case 'uploading':
        return 'UPLOADING'
      case 'success':
        return 'UPLOADED'
      case 'failed':
        return 'FAILED'
      case 'skipped':
        return 'CANCELLED'
    }
  }

  // ---- 处理阶段轮询 ----

  private beginProcessingIfNeeded() {
    const task = this.state.task
    if (!task) return
    const hasTransferred = task.uploadedFiles + task.failedFiles + task.cancelledFiles > 0
    if (!hasTransferred && task.waitingFiles > 0) {
      // 还在排队但还没开始（极端情况），保持
      return
    }
    if (this.batchIds.length === 0) {
      this.finishIfTerminal()
      return
    }
    task.status = 'PROCESSING'
    this.commit()
    this.startPolling()
  }

  private startPolling() {
    if (this.pollTimer) return
    this.pollTimer = setInterval(() => void this.poll(), this.pollIntervalMs)
    void this.poll()
  }

  private stopPolling() {
    if (this.pollTimer) {
      clearInterval(this.pollTimer)
      this.pollTimer = null
    }
  }

  private async poll() {
    const task = this.state.task
    if (!task || !this.services) return
    if (typeof document !== 'undefined' && document.hidden) {
      // 页面不可见时降低频率
      this.pollIntervalMs = 6000
      if (this.pollTimer) {
        clearInterval(this.pollTimer)
        this.pollTimer = setInterval(() => void this.poll(), this.pollIntervalMs)
      }
    } else if (this.pollIntervalMs !== 2500) {
      this.pollIntervalMs = 2500
      if (this.pollTimer) {
        clearInterval(this.pollTimer)
        this.pollTimer = setInterval(() => void this.poll(), this.pollIntervalMs)
      }
    }
    let changed = false
    // 反向遍历：同一 clientItemId 可能存在于多个批次（重试会新建批次），
    // 最新批次优先，避免旧失败批次把状态翻回 FAILED。
    const seen = new Set<string>()
    for (let b = this.batchIds.length - 1; b >= 0; b -= 1) {
      const batchId = this.batchIds[b]!
      const res = await this.services.documents.getUploadBatch(batchId)
      if (res.state !== 'ready' || !res.data) continue
      for (const item of res.data.items) {
        if (seen.has(item.clientItemId)) continue
        seen.add(item.clientItemId)
        const ft = task.files.get(item.clientItemId)
        if (!ft) continue
        const next = this.mapServerStatus(item.status, item.stage)
        if (next !== ft.status) {
          ft.status = next
          changed = true
        }
        if (item.error && next === 'FAILED') {
          ft.error = {
            code: (item.error.code as FileTaskError['code']) ?? 'UNKNOWN_ERROR',
            message: item.error.message ?? '处理失败',
            detail: item.error.detail,
            raw: item.error.detail ? JSON.stringify(item.error.detail) : undefined,
          }
        }
        if (item.jobId) ft.jobId = item.jobId
        if (item.id && !ft.uploadItemId) ft.uploadItemId = item.id
      }
    }
    if (changed) {
      this.recompute(task)
      this.commit()
    }
    this.finishIfTerminal()
  }

  private mapServerStatus(status: string, stage: string | null): FileTaskStatus {
    switch (status) {
      case 'ready':
        return 'SUCCESS'
      case 'failed':
        return 'FAILED'
      case 'cancelled':
        return 'CANCELLED'
      case 'queued':
      case 'uploading':
      case 'verifying':
        return 'UPLOADED'
      case 'processing':
      case 'indexing':
      default: {
        switch (stage) {
          case 'PARSING':
          case 'VALIDATING':
          case 'CONVERTING':
            return 'PARSING'
          case 'CHUNKING':
            return 'CHUNKING'
          case 'EMBEDDING':
            return 'EMBEDDING'
          case 'INDEXING':
            return 'INDEXING'
          default:
            return 'PARSING'
        }
      }
    }
  }

  private finishIfTerminal() {
    const task = this.state.task
    if (!task) return
    const active = task.uploadingFiles + task.processingFiles + task.waitingFiles
    if (active > 0) return
    this.stopPolling()
    if (task.failedFiles > 0 && task.completedFiles > 0) task.status = 'PARTIAL_FAILED'
    else if (task.failedFiles > 0 && task.completedFiles === 0) task.status = 'FAILED'
    else task.status = 'COMPLETED'
    task.completedAt = nowIso()
    task.speed = 0
    this.persist(task)
    this.commit()
    if (this.onTaskCompleteCb) this.onTaskCompleteCb()
  }

  // ---- 控制：暂停 / 继续 / 取消 / 重试 ----

  pause(silent = false) {
    const task = this.state.task
    if (!task) return
    if (task.status === 'UPLOADING') {
      this.abort?.abort()
      this.abort = null
      task.status = 'PAUSED'
      this.commit()
    }
    if (!silent) this.stopPolling()
  }

  resume() {
    const task = this.state.task
    if (!task) return
    if (task.status === 'PAUSED' || task.status === 'UPLOADING') {
      void this.start()
    }
  }

  cancelTask() {
    const task = this.state.task
    if (!task) return
    this.abort?.abort()
    this.abort = null
    this.stopPolling()
    task.files.forEach((ft) => {
      if (ft.status === 'WAITING' || ft.status === 'UPLOADING') {
        ft.status = 'CANCELLED'
        ft.skippedReason = '已取消'
      }
    })
    task.status = 'CANCELLED'
    task.completedAt = nowIso()
    this.recompute(task)
    this.persist(task)
    this.commit()
  }

  async cancelFile(id: string): Promise<void> {
    const task = this.state.task
    if (!task) return
    const ft = task.files.get(id)
    if (!ft) return
    if (ft.status === 'WAITING' || ft.status === 'UPLOADING' || ft.status === 'PARSING' || ft.status === 'CHUNKING' || ft.status === 'EMBEDDING' || ft.status === 'INDEXING') {
      ft.status = 'CANCELLED'
      ft.skippedReason = '已取消'
      if (ft.uploadItemId && this.services) {
        try {
          await this.services.documents.abortUploadItem(ft.uploadItemId)
        } catch {
          /* 尽力取消 */
        }
      }
      this.recompute(task)
      this.commit()
    }
  }

  async retryFailed(): Promise<void> {
    const task = this.state.task
    if (!task) return
    const failed = this.itemsFor(task, ['FAILED'])
    if (failed.length === 0) return
    failed.forEach((it) => {
      const ft = task.files.get(it.id)
      if (ft) {
        ft.status = 'WAITING'
        ft.error = null
        ft.retryCount += 1
      }
    })
    this.recompute(task)
    this.commit()
    await this.start()
  }

  async retryFile(id: string): Promise<void> {
    const task = this.state.task
    if (!task) return
    const ft = task.files.get(id)
    if (!ft || ft.status !== 'FAILED') return
    ft.status = 'WAITING'
    ft.error = null
    ft.retryCount += 1
    this.recompute(task)
    this.commit()
    await this.start()
  }

  // ---- 派生统计 ----

  private recompute(task: UploadTask) {
    let waiting = 0
    let uploading = 0
    let uploaded = 0
    let processing = 0
    let completed = 0
    let failed = 0
    let cancelled = 0
    let uploadedBytes = 0
    task.files.forEach((ft) => {
      switch (ft.status) {
        case 'WAITING':
          waiting += 1
          break
        case 'UPLOADING':
          uploading += 1
          uploadedBytes += ft.uploadedBytes
          break
        case 'UPLOADED':
          uploaded += 1
          uploadedBytes += ft.size
          break
        case 'PARSING':
        case 'CHUNKING':
        case 'EMBEDDING':
        case 'INDEXING':
          processing += 1
          uploadedBytes += ft.size
          break
        case 'SUCCESS':
          completed += 1
          uploadedBytes += ft.size
          break
        case 'FAILED':
          failed += 1
          break
        case 'CANCELLED':
          cancelled += 1
          break
      }
    })
    task.waitingFiles = waiting
    task.uploadingFiles = uploading
    task.uploadedFiles = uploaded
    task.processingFiles = processing
    task.completedFiles = completed
    task.failedFiles = failed
    task.cancelledFiles = cancelled
    task.uploadedBytes = uploadedBytes
    const now = Date.now()
    this.speedSamples.push({ t: now, bytes: uploadedBytes })
    this.speedSamples = this.speedSamples.filter((s) => now - s.t < 4000)
    if (this.speedSamples.length >= 2) {
      const first = this.speedSamples[0]!
      const dt = (now - first.t) / 1000
      const db = uploadedBytes - first.bytes
      task.speed = dt > 0 ? Math.max(0, db / dt) : 0
    }
    if (task.status === 'UPLOADING' && uploading === 0 && waiting === 0 && uploaded + processing + completed + failed > 0) {
      this.beginProcessingIfNeeded()
    }
  }

  // ---- 历史 ----

  async loadHistory(): Promise<void> {
    if (!this.services) return
    this.state = { ...this.state, historyState: 'loading' }
    this.commit()
    const res = await this.services.documents.listUploadBatches()
    if (res.state === 'ready' && res.data) {
      const kbNameById = new Map<string, string>()
      try {
        const kbRes = await this.services.knowledge.list()
        if (kbRes.state === 'ready' && kbRes.data) {
          kbRes.data.forEach((kb) => kbNameById.set(kb.id, kb.name))
        }
      } catch {
        /* 知识库列表可选 */
      }
      const history: HistoryBatch[] = res.data.map((b) => ({
        id: b.id,
        kbId: b.kbId,
        kbName: kbNameById.get(b.kbId) ?? b.kbId,
        mode: b.mode,
        status: b.status,
        itemCount: b.itemCount,
        totalBytes: b.totalBytes,
        createdAt: b.createdAt,
        successCount: b.counts?.ready ?? 0,
        failedCount: b.counts?.failed ?? 0,
      }))
      this.state = { ...this.state, history, historyState: 'ready' }
    } else {
      this.state = { ...this.state, historyState: 'error' }
    }
    this.commit()
  }

  // ---- 刷新恢复（服务端已落库文件） ----

  async restoreFromServer(): Promise<boolean> {
    if (typeof window === 'undefined') return false
    if (this.state.task) return false
    const raw = window.sessionStorage.getItem(SESSION_KEY)
    if (!raw || !this.services) return false
    try {
      const saved = JSON.parse(raw) as { kbId: string; kbName: string; batchIds: string[]; taskId: string }
      if (!saved.batchIds?.length) return false
      // 拉取最新批次投影，重建最小任务用于继续轮询
      const task: UploadTask = {
        id: saved.taskId,
        kbId: saved.kbId,
        kbName: saved.kbName,
        status: 'PROCESSING',
        mode: 'MULTI_FILE',
        scan: null,
        files: new Map(),
        batchId: saved.batchIds[saved.batchIds.length - 1] ?? null,
        totalFiles: 0,
        waitingFiles: 0,
        uploadingFiles: 0,
        uploadedFiles: 0,
        processingFiles: 0,
        completedFiles: 0,
        failedFiles: 0,
        cancelledFiles: 0,
        totalBytes: 0,
        uploadedBytes: 0,
        createdAt: nowIso(),
        startedAt: nowIso(),
        completedAt: null,
        speed: 0,
        error: null,
      }
      this.batchIds = saved.batchIds
      const restoredFiles = new Map<string, FileTask>()
      for (const batchId of saved.batchIds) {
        const res = await this.services.documents.getUploadBatch(batchId)
        if (res.state !== 'ready' || !res.data) continue
        for (const item of res.data.items) {
          const bytes = item.byteSize ?? 0
          // 刷新发生在真正落盘之前：浏览器已丢失 File 句柄，无法续传，明确提示重新选择。
          const fullyUploaded = item.status !== 'queued' && item.status !== 'uploading'
          const ft: FileTask = {
            id: item.clientItemId,
            name: leafOf(item.relativePath),
            relativePath: item.relativePath,
            dirPath: dirOf(item.relativePath),
            kind: kindFromExt(extOf(item.relativePath)) as FileKind,
            size: bytes,
            status: fullyUploaded ? this.mapServerStatus(item.status, item.stage) : 'FAILED',
            uploadedBytes: fullyUploaded ? bytes : 0,
            speed: 0,
            error: fullyUploaded
              ? null
              : { code: 'UPLOAD_INTERRUPTED', message: '刷新导致上传中断，请重新选择该文件以续传' },
            retryCount: item.attempts ?? 0,
            uploadItemId: item.id,
            jobId: item.jobId ?? undefined,
          }
          restoredFiles.set(ft.id, ft)
        }
      }
      if (restoredFiles.size === 0) return false
      task.files = restoredFiles
      this.recompute(task)
      this.state = { ...this.state, task }
      this.commit()
      this.startPolling()
      return true
    } catch {
      return false
    }
  }

  private persist(task: UploadTask) {
    if (typeof window === 'undefined') return
    try {
      window.sessionStorage.setItem(
        SESSION_KEY,
        JSON.stringify({ kbId: task.kbId, kbName: task.kbName, batchIds: this.batchIds, taskId: task.id }),
      )
    } catch {
      /* ignore */
    }
  }

  private normalizeError(err: AdapterError): FileTaskError {
    return {
      code: (err.code as FileTaskError['code']) ?? 'UNKNOWN_ERROR',
      message: err.message ?? '上传失败',
      detail: err.details,
      raw: err.requestId ? `request_id=${err.requestId}` : undefined,
    }
  }

  clearTask(): void {
    this.stopPolling()
    this.state = { ...this.state, task: null }
    this.batchIds = []
    if (typeof window !== 'undefined') window.sessionStorage.removeItem(SESSION_KEY)
    this.commit()
  }

  get formatBytes() {
    return formatBytes
  }
}

export const uploadTaskStore = new UploadTaskStore()
