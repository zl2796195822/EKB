import {
  ArrowClockwise,
  CheckCircle,
  FolderOpen,
  Files,
  MinusCircle,
  X,
  XCircle,
  WarningCircle,
  Play,
  Stop,
  CaretDown,
  CaretUp,
} from '@phosphor-icons/react'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import type {
  BulkFileItem,
  BulkFileProgress,
  BulkUploadProgress,
  BulkUploadResult,
  V2Services,
  KnowledgeBaseView,
} from '../../types'
import { toAdapterError } from '../../adapters'

/** 文件格式白名单（与后端 ALLOWED_EXTENSIONS 完全一致） */
const ALLOWED_EXTENSIONS = new Set([
  '.txt',
  '.md',
  '.pdf',
  '.doc',
  '.docx',
  '.ppt',
  '.pptx',
  '.xls',
  '.xlsx',
])

const ACCEPT_ATTR = Array.from(ALLOWED_EXTENSIONS).join(',')

const CONCURRENCY_OPTIONS = [1, 2, 4, 8] as const

interface BatchUploadModalProps {
  readonly open: boolean
  readonly kbId: string | null
  readonly services: V2Services
  readonly onClose: () => void
  /** 至少 1 条上传成功后触发（通常用于刷新父页文档列表） */
  readonly onSuccess?: () => void
}

type Stage = 'selecting' | 'uploading' | 'finished'

function extOf(name: string): string {
  const dot = name.lastIndexOf('.')
  return dot >= 0 ? name.slice(dot).toLowerCase() : ''
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

function labelForExt(ext: string): string {
  switch (ext) {
    case '.pdf':
      return 'PDF'
    case '.doc':
    case '.docx':
      return 'Word'
    case '.ppt':
    case '.pptx':
      return 'PPT'
    case '.xls':
    case '.xlsx':
      return 'Excel'
    case '.txt':
      return 'TXT'
    case '.md':
      return 'Markdown'
    default:
      return ext.slice(1).toUpperCase() || '其他'
  }
}

interface FileEntry {
  readonly key: string
  readonly item: BulkFileItem
  readonly ext: string
  readonly kindLabel: string
  readonly skippedReason: string | null
}

function buildEntriesFromFiles(files: FileList | null): {
  readonly entries: FileEntry[]
  readonly scanned: number
  readonly skippedUnsupported: number
  readonly skippedDuplicate: number
} {
  if (!files || files.length === 0) {
    return { entries: [], scanned: 0, skippedUnsupported: 0, skippedDuplicate: 0 }
  }
  const entries: FileEntry[] = []
  const seen = new Set<string>()
  let scanned = 0
  let skippedUnsupported = 0
  let skippedDuplicate = 0
  for (let i = 0; i < files.length; i += 1) {
    const file = files[i]!
    scanned += 1
    const path: string = (file as File & { webkitRelativePath?: string }).webkitRelativePath || file.name
    const ext = extOf(path)
    if (!ALLOWED_EXTENSIONS.has(ext)) {
      skippedUnsupported += 1
      entries.push({
        key: `u-${i}-${path}`,
        item: { id: `u-${i}-${path}`, file, path, size: file.size },
        ext,
        kindLabel: labelForExt(ext),
        skippedReason: '文件格式不支持',
      })
      continue
    }
    const dedupKey = `${path}|${file.size}|${file.lastModified ?? 0}`
    if (seen.has(dedupKey)) {
      skippedDuplicate += 1
      entries.push({
        key: `d-${i}-${path}`,
        item: { id: `d-${i}-${path}`, file, path, size: file.size },
        ext,
        kindLabel: labelForExt(ext),
        skippedReason: '同路径同大小的重复文件，已跳过',
      })
      continue
    }
    seen.add(dedupKey)
    const id = `v-${i}-${path}`
    entries.push({
      key: id,
      item: { id, file, path, size: file.size },
      ext,
      kindLabel: labelForExt(ext),
      skippedReason: null,
    })
  }
  return { entries, scanned, skippedUnsupported, skippedDuplicate }
}

interface SummaryStats {
  readonly totalSize: number
  readonly countsByKind: Record<string, number>
  readonly uploadableCount: number
  readonly skippedCount: number
}

function computeStats(entries: readonly FileEntry[]): SummaryStats {
  const countsByKind: Record<string, number> = {}
  let totalSize = 0
  let uploadableCount = 0
  let skippedCount = 0
  for (const e of entries) {
    totalSize += e.item.size
    if (e.skippedReason) {
      skippedCount += 1
    } else {
      uploadableCount += 1
      countsByKind[e.kindLabel] = (countsByKind[e.kindLabel] ?? 0) + 1
    }
  }
  return { totalSize, countsByKind, uploadableCount, skippedCount }
}

function StatusDot({ status }: { status: BulkFileProgress['status'] }) {
  switch (status) {
    case 'queued':
      return <span className="v2-m3-status-dot v2-m3-status-dot--queued" aria-label="排队中" title="排队中" />
    case 'uploading':
      return <span className="v2-m3-status-dot v2-m3-status-dot--uploading" aria-label="上传中" title="上传中" />
    case 'success':
      return <CheckCircle size={16} aria-hidden="true" className="v2-m3-status-icon v2-m3-status-icon--success" />
    case 'failed':
      return <XCircle size={16} aria-hidden="true" className="v2-m3-status-icon v2-m3-status-icon--failed" />
    case 'skipped':
      return <WarningCircle size={16} aria-hidden="true" className="v2-m3-status-icon v2-m3-status-icon--skipped" />
  }
}

const KIND_ORDER = ['PDF', 'Word', 'PPT', 'Excel', 'TXT', 'Markdown'] as const

export function BatchUploadModal({ open, kbId, services, onClose, onSuccess }: BatchUploadModalProps) {
  const [tab, setTab] = useState<'directory' | 'files'>('directory')
  const [stage, setStage] = useState<Stage>('selecting')
  const [concurrency, setConcurrency] = useState<(typeof CONCURRENCY_OPTIONS)[number]>(4)
  const [entries, setEntries] = useState<FileEntry[]>([])
  const [progress, setProgress] = useState<BulkUploadProgress | null>(null)
  const [successCount, setSuccessCount] = useState(0)
  const [failedCount, setFailedCount] = useState(0)
  const [skippedCount, setSkippedCount] = useState(0)
  const [showSkipped, setShowSkipped] = useState(true)
  const [summaryMsg, setSummaryMsg] = useState<string | null>(null)
  // 覆盖式的 KB 选择：弹窗内可选；kbId 作为默认值和兜底
  const [selectedKbId, setSelectedKbId] = useState<string | null>(kbId)
  const [kbListState, setKbListState] = useState<'loading' | 'ready' | 'error'>('loading')
  const [kbList, setKbList] = useState<readonly KnowledgeBaseView[]>([])
  const [kbError, setKbError] = useState<string | null>(null)

  const directoryInputRef = useRef<HTMLInputElement | null>(null)
  const filesInputRef = useRef<HTMLInputElement | null>(null)
  const abortRef = useRef<AbortController | null>(null)
  // 用于 startUpload 的 fatal 分支在 setSummaryMsg 回调里读到最新计数（避免闭包陈旧值）
  const progressRef = useRef<BulkUploadProgress | null>(null)
  const successCountRef = useRef(0)
  const failedCountRef = useRef(0)
  const skippedCountRef = useRef(0)
  useEffect(() => { progressRef.current = progress }, [progress])
  useEffect(() => { successCountRef.current = successCount }, [successCount])
  useEffect(() => { failedCountRef.current = failedCount }, [failedCount])
  useEffect(() => { skippedCountRef.current = skippedCount }, [skippedCount])

  // 有效 kbId：优先弹窗内选择，其次父组件传入
  const effectiveKbId = selectedKbId ?? kbId

  const loadKbList = useCallback(async () => {
    setKbListState('loading')
    setKbError(null)
    try {
      const res = await services.knowledge.list()
      if (res.state === 'ready' && res.data) {
        setKbList(res.data)
        setKbListState('ready')
        if (!selectedKbId && !kbId && res.data.length > 0) {
          setSelectedKbId(res.data[0].id)
        }
      } else {
        setKbListState('error')
        setKbError(res.error?.message || '知识库列表加载失败')
      }
    } catch (err: unknown) {
      setKbListState('error')
      setKbError(err instanceof Error ? err.message : '知识库列表加载失败')
    }
  }, [services, kbId])

  useEffect(() => {
    if (open) {
      setStage('selecting')
      setEntries([])
      setProgress(null)
      setSuccessCount(0)
      setFailedCount(0)
      setSkippedCount(0)
      setSummaryMsg(null)
      setShowSkipped(true)
      setSelectedKbId(kbId) // 以父组件当前选中为默认
      if (directoryInputRef.current) directoryInputRef.current.value = ''
      if (filesInputRef.current) filesInputRef.current.value = ''
      void loadKbList()
    }
  }, [open, kbId, loadKbList])

  const stats = useMemo(() => computeStats(entries), [entries])

  const sortedEntries = useMemo(() => {
    return [...entries].sort((a, b) => {
      // 目录层级先按 path 排序，然后 skipped 优先在后（不挡主要内容），
      // 最后按大小降序（大文件先传，排队利用更充分）
      if (!!a.skippedReason !== !!b.skippedReason) return a.skippedReason ? 1 : -1
      if (a.item.path !== b.item.path) return a.item.path.localeCompare(b.item.path, 'zh-Hans-CN')
      return b.item.size - a.item.size
    })
  }, [entries])

  const displayEntries = useMemo(() => {
    if (showSkipped) return sortedEntries
    return sortedEntries.filter((e) => !e.skippedReason)
  }, [sortedEntries, showSkipped])

  // 分页：每页 50 条（避免 500+ 条同时渲染）
  const PAGE_SIZE = 50
  const [page, setPage] = useState(1)
  const totalDisplayEntries = displayEntries.length
  const totalPages = Math.max(1, Math.ceil(totalDisplayEntries / PAGE_SIZE))
  const currentPage = page > totalPages ? totalPages : page
  const visibleEntries = useMemo(() => {
    const start = (currentPage - 1) * PAGE_SIZE
    return displayEntries.slice(start, start + PAGE_SIZE)
  }, [displayEntries, currentPage])
  useEffect(() => { setPage(1) }, [showSkipped, entries.length])

  const findProgress = useCallback(
    (id: string): BulkFileProgress | null => {
      if (!progress) return null
      return progress.perFile.get(id) ?? null
    },
    [progress],
  )

  const handlePickDirectory = () => directoryInputRef.current?.click()
  const handlePickFiles = () => filesInputRef.current?.click()

  const handleDirectoryChange = (event: React.ChangeEvent<HTMLInputElement>) => {
    // NOTE: 必须 **先读 FileList，再清 value**——Chromium/Blink 下 value='' 会立即把
    // input.files 重置为空（FileList 与 value 镜像），若先清 value 会导致“选完目录后没反应/条目是 0”。
    const input = event.currentTarget
    const files = input.files
    const built = buildEntriesFromFiles(files)
    input.value = ''
    setEntries(built.entries)
    setStage('selecting')
    setProgress(null)
    setSuccessCount(0)
    setFailedCount(0)
    setSkippedCount(built.skippedUnsupported + built.skippedDuplicate)
  }

  const handleFilesChange = (event: React.ChangeEvent<HTMLInputElement>) => {
    const input = event.currentTarget
    const files = input.files
    const built = buildEntriesFromFiles(files)
    input.value = ''
    setEntries(built.entries)
    setStage('selecting')
    setProgress(null)
    setSuccessCount(0)
    setFailedCount(0)
    setSkippedCount(built.skippedUnsupported + built.skippedDuplicate)
  }

  const progressFor = (entry: FileEntry): BulkFileProgress['status'] => {
    if (entry.skippedReason) return 'skipped'
    const p = findProgress(entry.item.id)
    return p?.status ?? 'queued'
  }

  const startUpload = async () => {
    const targetKbId = effectiveKbId
    if (!targetKbId) return
    const uploadable = entries.filter((e) => !e.skippedReason)
    if (uploadable.length === 0) return
    const ac = new AbortController()
    abortRef.current = ac
    setStage('uploading')
    setSuccessCount(0)
    setFailedCount(0)
    setSummaryMsg(null)
    setSkippedCount(entries.filter((e) => !!e.skippedReason).length)
    let result: BulkUploadResult | null = null
    let fatal: unknown = null
    try {
      result = await services.documents.uploadBulk(targetKbId, uploadable.map((u) => u.item), {
        concurrency,
        signal: ac.signal,
        onProgress: (next) => {
          setProgress(next)
          setSuccessCount(next.completed)
          setFailedCount(next.failed)
          setSkippedCount(entries.filter((e) => !!e.skippedReason).length + next.skipped)
        },
      })
    } catch (err: unknown) {
      // 并发调度/网络级异常也不能静默吞；把剩下还没完成的条目统一标成失败，用户能明确看到问题。
      fatal = err
      setProgress((prev) => {
        const basePerFile = prev?.perFile ?? new Map<string, BulkFileProgress>()
        const now = new Map(basePerFile)
        const mappedErr = toAdapterError(err, '批量上传失败')
        let extraFailed = 0
        for (const u of uploadable) {
          const v = now.get(u.item.id)
          if (!v || v.status === 'queued' || v.status === 'uploading') {
            if (v && v.status !== 'queued' && v.status !== 'uploading') continue
            if (!v) {
              now.set(u.item.id, {
                id: u.item.id,
                path: u.item.path,
                size: u.item.size,
                status: 'failed',
                error: mappedErr,
              })
              extraFailed += 1
              continue
            }
            now.set(u.item.id, { ...v, status: 'failed', error: mappedErr })
            extraFailed += 1
          }
        }
        const nextTotal = uploadable.length
        let completed = 0
        let failed = 0
        let skipped = 0
        for (const val of now.values()) {
          if (val.status === 'success') completed += 1
          else if (val.status === 'failed') failed += 1
          else if (val.status === 'skipped') skipped += 1
        }
        return { total: nextTotal, completed, failed, skipped, perFile: now }
      })
    } finally {
      abortRef.current = null
      setStage('finished')
    }
    if (result) {
      const { summary } = result
      setSuccessCount(summary.successCount)
      setFailedCount(summary.failedCount)
      setSkippedCount(entries.filter((e) => !!e.skippedReason).length + summary.skippedCount)
      const extra = fatal ? `（执行异常：${fatal instanceof Error ? fatal.message : String(fatal)}）` : ''
      setSummaryMsg(
        `上传完成：共 ${summary.total} 条，成功 ${summary.successCount}，失败 ${summary.failedCount}，跳过 ${summary.skippedCount}。${extra}`,
      )
      if (summary.successCount > 0) onSuccess?.()
      return
    }
    // 走了 fatal 分支，summary 基于当前 progress 拼一段，保证页面始终有可感知结果
    setSummaryMsg((prev) => {
      if (prev) return prev
      const p = progressRef.current
      const total = p?.total ?? uploadable.length
      const ok = successCountRef.current
      const bad = failedCountRef.current
      const skip = skippedCountRef.current - entries.filter((e) => !!e.skippedReason).length
      const msg = fatal instanceof Error ? fatal.message : String(fatal)
      return `批量上传中断：共 ${total} 条，成功 ${ok}，失败 ${bad}，跳过 ${Math.max(0, skip)}。错误：${msg}`
    })
  }

  const cancelUpload = () => {
    abortRef.current?.abort()
  }

  const retryFailed = async () => {
    const targetKbId = effectiveKbId
    if (!targetKbId || stage !== 'finished') return
    const failedIds = new Set<string>(
      (
        progress?.perFile?.values()
          ? Array.from(progress.perFile.values()).filter((v) => v.status === 'failed')
          : []
      ).map((v) => v.id),
    )
    if (failedIds.size === 0) return
    const uploadable = entries.filter((e) => !e.skippedReason && failedIds.has(e.item.id))
    if (uploadable.length === 0) return
    // 重传前移除旧进度中的失败条目，以便重新显示为 queued
    setProgress((prev) => {
      if (!prev) return prev
      const nextPerFile = new Map(prev.perFile)
      for (const u of uploadable) nextPerFile.delete(u.item.id)
      return { ...prev, perFile: nextPerFile, total: prev.total, completed: prev.completed, failed: prev.failed - uploadable.length, skipped: prev.skipped }
    })
    const ac = new AbortController()
    abortRef.current = ac
    setStage('uploading')
    setFailedCount((n) => Math.max(0, n - uploadable.length))
    setSummaryMsg(null)
    let result: BulkUploadResult | null = null
    let fatal: unknown = null
    try {
      result = await services.documents.uploadBulk(targetKbId, uploadable.map((u) => u.item), {
        concurrency,
        signal: ac.signal,
        onProgress: (next) => {
          setProgress((prev) => {
            if (!prev) {
              setSuccessCount(next.completed)
              setFailedCount(next.failed)
              return next
            }
            // 合入 perFile（已存在的覆盖）
            const merged = new Map(prev.perFile)
            for (const [k, v] of next.perFile) merged.set(k, v)
            const completed = (prev.completed - failedPreviouslyFailed(prev, failedIds)) + next.completed
            const failed = prev.failed - countStillFailedButNowDone(prev, next, failedIds) + next.failed
            const skipped = prev.skipped
            setSuccessCount(completed)
            setFailedCount(failed)
            return {
              total: prev.total,
              completed,
              failed,
              skipped,
              perFile: merged,
            }
          })
        },
      })
    } catch (err: unknown) {
      fatal = err
      const mappedErr = toAdapterError(err, '重传失败')
      setProgress((prev) => {
        if (!prev) return prev
        const merged = new Map(prev.perFile)
        let failedNow = prev.failed
        for (const u of uploadable) {
          const v = merged.get(u.item.id)
          if (!v || v.status === 'queued' || v.status === 'uploading') {
            if (!v) {
              merged.set(u.item.id, {
                id: u.item.id,
                path: u.item.path,
                size: u.item.size,
                status: 'failed',
                error: mappedErr,
              })
              failedNow += 1
              continue
            }
            if (v.status === 'failed') continue
            merged.set(u.item.id, { ...v, status: 'failed', error: mappedErr })
            failedNow += 1
          }
        }
        let completed = 0
        let failed = 0
        let skipped = 0
        for (const val of merged.values()) {
          if (val.status === 'success') completed += 1
          else if (val.status === 'failed') failed += 1
          else if (val.status === 'skipped') skipped += 1
        }
        return { total: prev.total, completed, failed, skipped, perFile: merged }
      })
    } finally {
      abortRef.current = null
      setStage('finished')
    }
    if (result) {
      const { summary } = result
      setSuccessCount((prev) => prev + summary.successCount)
      setFailedCount((prev) => Math.max(0, prev - uploadable.length + summary.failedCount))
      setSkippedCount(entries.filter((e) => !!e.skippedReason).length + (progress?.skipped ?? 0) + summary.skippedCount)
      setSummaryMsg(
        `失败项重传完成：共 ${summary.total} 条，成功 ${summary.successCount}，失败 ${summary.failedCount}，跳过 ${summary.skippedCount}。`,
      )
      if (summary.successCount > 0) onSuccess?.()
      return
    }
    const msg = fatal instanceof Error ? fatal.message : String(fatal)
    setSummaryMsg(`失败项重传中断：${msg}`)
  }

  const isUploading = stage === 'uploading'
  const hasEntries = entries.length > 0
  const uploadableCount = stats.uploadableCount
  const totalProgress = progress?.total ?? uploadableCount
  const doneProgress = successCount + failedCount + (progress?.skipped ?? 0)
  const pct = totalProgress > 0 ? Math.min(100, Math.round((doneProgress / totalProgress) * 100)) : 0
  const successPct = totalProgress > 0 ? Math.round((successCount / totalProgress) * 100) : 0
  const failedPct = totalProgress > 0 ? Math.round((failedCount / totalProgress) * 100) : 0

  if (!open) return null

  return (
    <div
      className="v2-m3-modal-backdrop"
      role="presentation"
      onMouseDown={(event) => {
        if (event.currentTarget === event.target && stage !== 'uploading') onClose()
      }}
    >
      <section
        className="v2-m3-modal v2-m3-modal--batch-upload"
        role="dialog"
        aria-modal="true"
        aria-label="批量/目录上传文档"
      >
        <header>
          <h2>批量上传文档</h2>
          <button
            type="button"
            className="v2-m3-icon-button"
            onClick={onClose}
            aria-label="关闭批量上传对话框"
            disabled={isUploading}
          >
            <X size={17} aria-hidden="true" />
          </button>
        </header>

        <div className="v2-m3-modal-body">
          {/* Tab 切换 */}
          <div className="v2-m3-tabs" role="tablist" aria-label="上传方式">
            <button
              type="button"
              role="tab"
              aria-selected={tab === 'directory'}
              className={`v2-m3-tab ${tab === 'directory' ? 'is-active' : ''}`}
              onClick={() => setTab('directory')}
              disabled={isUploading}
            >
              <FolderOpen size={15} aria-hidden="true" />
              上传目录
            </button>
            <button
              type="button"
              role="tab"
              aria-selected={tab === 'files'}
              className={`v2-m3-tab ${tab === 'files' ? 'is-active' : ''}`}
              onClick={() => setTab('files')}
              disabled={isUploading}
            >
              <Files size={15} aria-hidden="true" />
              批量多文件
            </button>
          </div>

          {/* 隐藏的两个 input */}
          <input
            ref={directoryInputRef}
            className="v2-m3-hidden-input"
            type="file"
            // @ts-expect-error: webkitdirectory 是浏览器扩展属性，TS DOM lib 类型不稳定
            webkitdirectory=""
            directory=""
            multiple
            onChange={handleDirectoryChange}
            aria-label="选择目录"
          />
          <input
            ref={filesInputRef}
            className="v2-m3-hidden-input"
            type="file"
            accept={ACCEPT_ATTR}
            multiple
            onChange={handleFilesChange}
            aria-label="选择多文件"
          />

          {/* 选择区（未开始前） */}
          {stage === 'selecting' ? (
            <div className="v2-m3-batch-actions">
              <div className="v2-m3-batch-pick">
                {tab === 'directory' ? (
                  <button type="button" className="v2-m3-primary-button" onClick={handlePickDirectory}>
                    <FolderOpen size={15} aria-hidden="true" />
                    选择目录
                  </button>
                ) : (
                  <button type="button" className="v2-m3-primary-button" onClick={handlePickFiles}>
                    <Files size={15} aria-hidden="true" />
                    选择多个文档
                  </button>
                )}
                <p className="v2-m3-muted">
                  {tab === 'directory'
                    ? '选择后将自动递归解析目录中的 PDF / Word / PPT / Excel / TXT / Markdown 文档，其他格式自动跳过。'
                    : '支持一次选择多个文档（PDF / Word / PPT / Excel / TXT / Markdown）。'}
                </p>
                <p className="v2-m3-muted v2-m3-muted--small">
                  允许的扩展名：{Array.from(ALLOWED_EXTENSIONS).join('、')}
                </p>
              </div>
              {hasEntries ? (
                <div className="v2-m3-batch-summary">
                  <div className="v2-m3-batch-summary-row">
                    <span>
                      扫描：<strong>{entries.length}</strong> 个文件
                      {stats.totalSize > 0 ? `，总计 ${formatBytes(stats.totalSize)}` : ''}
                    </span>
                    <span>
                      可上传 <strong className="v2-m3-text-success">{stats.uploadableCount}</strong>，
                      跳过 <strong className="v2-m3-text-skipped">{stats.skippedCount}</strong>
                    </span>
                  </div>
                  <ul className="v2-m3-kind-chips">
                    {KIND_ORDER.filter((k) => stats.countsByKind[k]).map((k) => (
                      <li key={k} className="v2-m3-kind-chip v2-m3-kind-chip--k">
                        {k}：{stats.countsByKind[k]}
                      </li>
                    ))}
                    {Object.keys(stats.countsByKind)
                      .filter((k) => !(KIND_ORDER as readonly string[]).includes(k))
                      .map((k) => (
                        <li key={k} className="v2-m3-kind-chip">
                          {k}：{stats.countsByKind[k]}
                        </li>
                      ))}
                  </ul>
                </div>
              ) : null}
            </div>
          ) : null}

          {/* 进度条（上传中 / 结束后显示） */}
          {isUploading || stage === 'finished' ? (
            <div className="v2-m3-progress-wrap" aria-live="polite">
              <div className="v2-m3-progress-meta">
                <span>
                  上传进度：<strong>{pct}%</strong>（{doneProgress}/{totalProgress}）
                </span>
                <span>
                  成功 <strong className="v2-m3-text-success">{successCount}</strong>
                  {failedCount > 0 ? (
                    <>
                      ，失败 <strong className="v2-m3-text-failed">{failedCount}</strong>
                    </>
                  ) : null}
                  {skippedCount > 0 ? (
                    <>
                      ，跳过 <strong className="v2-m3-text-skipped">{skippedCount}</strong>
                    </>
                  ) : null}
                </span>
              </div>
              <div className="v2-m3-progress-bar" role="progressbar" aria-valuenow={pct} aria-valuemin={0} aria-valuemax={100}>
                <div
                  className="v2-m3-progress-bar__segment v2-m3-progress-bar__success"
                  style={{ width: `${successPct}%` }}
                  aria-hidden="true"
                />
                <div
                  className="v2-m3-progress-bar__segment v2-m3-progress-bar__failed"
                  style={{ width: `${failedPct}%` }}
                  aria-hidden="true"
                />
              </div>
              {summaryMsg ? <p className="v2-m3-progress-summary">{summaryMsg}</p> : null}
            </div>
          ) : null}

          {/* 工具栏 */}
          {hasEntries ? (
            <div className="v2-m3-batch-toolbar">
              <label className="v2-m3-toolbar-field">
                并发数：
                <select
                  className="v2-m3-select"
                  value={concurrency}
                  disabled={isUploading}
                  onChange={(e) => setConcurrency(Number(e.target.value) as (typeof CONCURRENCY_OPTIONS)[number])}
                >
                  {CONCURRENCY_OPTIONS.map((n) => (
                    <option key={n} value={n}>
                      {n}
                    </option>
                  ))}
                </select>
              </label>
              <label className="v2-m3-toolbar-toggle">
                <input
                  type="checkbox"
                  checked={showSkipped}
                  disabled={isUploading}
                  onChange={(e) => setShowSkipped(e.target.checked)}
                />
                显示跳过的文件
              </label>
            </div>
          ) : null}

          {/* 文件列表 */}
          {hasEntries ? (
            <div className="v2-m3-file-list" aria-label="待上传文件列表">
              <div className="v2-m3-file-list-header">
                <div>文件（相对路径）</div>
                <div>类型</div>
                <div>大小</div>
                <div>状态</div>
              </div>
              <ul className="v2-m3-file-list-body">
                {displayEntries.length === 0 ? (
                  <li className="v2-m3-file-list-empty">当前已隐藏跳过文件。</li>
                ) : (
                  visibleEntries.map((entry) => {
                    const p = findProgress(entry.item.id)
                    const status = progressFor(entry)
                    const statusText: Record<BulkFileProgress['status'], string> = {
                      queued: '排队中',
                      uploading: '上传中',
                      success: '上传成功（后台解析中）',
                      failed: '上传失败',
                      skipped: '已跳过',
                    }
                    return (
                      <li key={entry.key} className={`v2-m3-file-row v2-m3-file-row--${status}`}>
                        <div className="v2-m3-file-row__path" title={entry.item.path}>
                          <span className="v2-m3-file-row__icon">
                            {status === 'skipped' ? <MinusCircle size={14} aria-hidden="true" /> : null}
                            {status === 'success' ? <CheckCircle size={14} aria-hidden="true" /> : null}
                            {status === 'failed' ? <XCircle size={14} aria-hidden="true" /> : null}
                          </span>
                          <span className="v2-m3-file-row__name">{entry.item.path}</span>
                        </div>
                        <div className="v2-m3-file-row__kind">{entry.kindLabel}</div>
                        <div className="v2-m3-file-row__size">{formatBytes(entry.item.size)}</div>
                        <div className="v2-m3-file-row__status">
                          <StatusDot status={status} />
                          <span>{entry.skippedReason ?? statusText[status]}</span>
                          {p?.error ? (
                            <span className="v2-m3-file-row__error" title={p.error.message}>
                              {p.error.code || p.error.message}
                            </span>
                          ) : null}
                        </div>
                      </li>
                    )
                  })
                )}
              </ul>
              {/* 分页条（总数超一页才显示） */}
              {totalPages > 1 ? (
                <div className="v2-m3-file-list-pagination" role="navigation" aria-label="文件列表分页">
                  <button
                    type="button"
                    className="v2-m3-secondary-button v2-m3-secondary-button--sm"
                    onClick={() => setPage((p) => Math.max(1, p - 1))}
                    disabled={currentPage <= 1}
                  >
                    上一页
                  </button>
                  <span className="v2-m3-file-list-pagination__meta">
                    第 <strong>{currentPage}</strong> / {totalPages} 页，共 {totalDisplayEntries} 条
                  </span>
                  <button
                    type="button"
                    className="v2-m3-secondary-button v2-m3-secondary-button--sm"
                    onClick={() => setPage((p) => Math.min(totalPages, p + 1))}
                    disabled={currentPage >= totalPages}
                  >
                    下一页
                  </button>
                </div>
              ) : null}
            </div>
          ) : stage === 'selecting' ? null : null}

          {/* 无 KB 提示 */}
          {!effectiveKbId && (
            <div className="v2-m3-row-block" role="alert">
              <div className="v2-m3-row-block__title">选择目标知识库</div>
              {kbError ? (
                <p className="v2-m3-row-error">{kbError}</p>
              ) : (
                <p className="v2-m3-muted">
                  {kbListState === 'loading'
                    ? '正在加载知识库列表…'
                    : '未检测到可用的知识库。请先从文档中心左侧创建或选择一个知识库空间。'}
                </p>
              )}
              {kbListState === 'ready' && kbList.length > 0 ? (
                <label className="v2-m3-toolbar-field">
                  目标知识库：
                  <select
                    className="v2-m3-select"
                    value={effectiveKbId || ''}
                    onChange={(e) => setSelectedKbId(e.target.value || null)}
                    disabled={isUploading}
                  >
                    <option value="">（请选择）</option>
                    {kbList.map((kb) => (
                      <option key={kb.id} value={kb.id}>
                        {kb.name}（{kb.documentCount} 篇）
                      </option>
                    ))}
                  </select>
                </label>
              ) : null}
            </div>
          )}

          {effectiveKbId && hasEntries ? (
            <div className="v2-m3-row-block">
              <label className="v2-m3-toolbar-field">
                目标知识库：
                <select
                  className="v2-m3-select"
                  value={effectiveKbId || ''}
                  onChange={(e) => setSelectedKbId(e.target.value || null)}
                  disabled={isUploading}
                >
                  {kbListState === 'ready' && kbList.length === 0
                    ? (
                      <option value={effectiveKbId}>{effectiveKbId.slice(0, 8)}…</option>
                    )
                    : kbList.map((kb) => (
                      <option key={kb.id} value={kb.id}>
                        {kb.name}（{kb.documentCount} 篇）
                      </option>
                    ))}
                </select>
                {kbError ? <span className="v2-m3-text-failed">{kbError}</span> : null}
              </label>
            </div>
          ) : null}
        </div>

        {/* 底部操作区 */}
        <footer className="v2-m3-modal-footer">
          {stage === 'selecting' ? (
            <>
              <button type="button" className="v2-m3-secondary-button" onClick={onClose} disabled={false}>
                取消
              </button>
              <button
                type="button"
                className="v2-m3-primary-button"
                onClick={() => void startUpload()}
                disabled={!hasEntries || uploadableCount === 0 || !effectiveKbId || isUploading}
              >
                <Play size={15} aria-hidden="true" />
                开始上传（{uploadableCount}）
              </button>
            </>
          ) : null}

          {isUploading ? (
            <>
              <button type="button" className="v2-m3-secondary-button" onClick={cancelUpload}>
                <Stop size={15} aria-hidden="true" />
                取消未开始任务
              </button>
              <span className="v2-m3-muted">上传中…解析在服务端异步执行，不会阻塞上传。</span>
            </>
          ) : null}

          {stage === 'finished' ? (
            <>
              <button type="button" className="v2-m3-secondary-button" onClick={onClose}>
                关闭
              </button>
              {failedCount > 0 ? (
                <button type="button" className="v2-m3-primary-button v2-m3-primary-button--warn" onClick={() => void retryFailed()}>
                  <ArrowClockwise size={15} aria-hidden="true" />
                  重传失败项（{failedCount}）
                </button>
              ) : (
                <button type="button" className="v2-m3-primary-button" onClick={onClose} autoFocus>
                  {showSkipped ? <CaretUp size={15} aria-hidden="true" /> : <CaretDown size={15} aria-hidden="true" />}
                  完成
                </button>
              )}
            </>
          ) : null}
        </footer>
      </section>
    </div>
  )
}

function failedPreviouslyFailed(prev: BulkUploadProgress, failedIds: Set<string>): number {
  let n = 0
  for (const v of prev.perFile.values()) if (v.status === 'failed' && failedIds.has(v.id)) n += 1
  return n
}

function countStillFailedButNowDone(
  prev: BulkUploadProgress,
  next: BulkUploadProgress,
  failedIds: Set<string>,
): number {
  // 只减去这次重传列表中 status 不再是 failed 的条目（success/skipped/uploading 都会离开 failed 计数）
  let n = 0
  for (const v of prev.perFile.values()) {
    if (v.status === 'failed' && failedIds.has(v.id) && next.perFile.has(v.id)) {
      const after = next.perFile.get(v.id)!
      if (after.status !== 'failed') n += 1
    }
  }
  return n
}
