import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { ArrowLeft, ArrowCounterClockwise, FloppyDisk, Pause, Play, X } from '@phosphor-icons/react'
import { uploadTaskStore } from '../services/uploadTaskStore'
import { useUploadTaskStore } from '../services/uploadTaskStoreHook'
import { UploadDropzone } from '../components/upload/UploadDropzone'
import { UploadFileTable } from '../components/upload/UploadFileTable'
import { UploadHistory } from '../components/upload/UploadHistory'
import { TaskStatusBadge } from '../components/upload/UploadStatusBadge'
import type { FileTask } from '../types/upload-task'
import type { V2PageProps } from '../types/route'
import '../components/upload/upload-task.css'

type FilterKey = 'ALL' | 'ACTIVE' | 'SUCCESS' | 'FAILED' | 'CANCELLED'

const FILTERS: { key: FilterKey; label: string }[] = [
  { key: 'ALL', label: '全部' },
  { key: 'ACTIVE', label: '进行中' },
  { key: 'SUCCESS', label: '已完成' },
  { key: 'FAILED', label: '失败' },
  { key: 'CANCELLED', label: '已取消' },
]

function readKbParam(): string {
  if (typeof window === 'undefined') return ''
  const [, query] = window.location.hash.split('?')
  if (!query) return ''
  return new URLSearchParams(query).get('kb')?.trim() ?? ''
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

function formatEta(seconds: number | null): string {
  if (seconds === null || !Number.isFinite(seconds) || seconds <= 0) return '—'
  const s = Math.round(seconds)
  if (s < 60) return `${s} 秒`
  const m = Math.floor(s / 60)
  const r = s % 60
  return `${m} 分 ${r} 秒`
}

export function UploadTaskPage({ services }: V2PageProps) {
  const { task, history, historyState, network } = useUploadTaskStore()
  const [kbName, setKbName] = useState('')
  const [filter, setFilter] = useState<FilterKey>('ALL')
  const [search, setSearch] = useState('')
  const initialized = useRef(false)

  const kbId = useMemo(() => readKbParam(), [])

  useEffect(() => {
    uploadTaskStore.init(services)
  }, [services])

  useEffect(() => {
    if (initialized.current) return
    initialized.current = true
    void uploadTaskStore.restoreFromServer()
    void uploadTaskStore.loadHistory()
  }, [])

  useEffect(() => {
    if (!kbId) return
    void (async () => {
      const res = await services.knowledge.list()
      if (res.state === 'ready' && res.data) {
        setKbName(res.data.find((kb) => kb.id === kbId)?.name ?? '知识库')
      }
    })()
  }, [kbId, services.knowledge])

  const handleFiles = useCallback(
    (files: FileList | File[], mode: 'MULTI_FILE' | 'DIRECTORY') => {
      if (!kbId) return
      uploadTaskStore.scan(kbId, kbName || kbId, files, mode)
    },
    [kbId, kbName],
  )

  const visibleFiles = useMemo<readonly FileTask[]>(() => {
    if (!task) return []
    const q = search.trim().toLowerCase()
    const list = Array.from(task.files.values())
    return list
      .filter((ft) => {
        if (filter === 'ALL') return true
        if (filter === 'SUCCESS') return ft.status === 'SUCCESS'
        if (filter === 'FAILED') return ft.status === 'FAILED'
        if (filter === 'CANCELLED') return ft.status === 'CANCELLED'
        if (filter === 'ACTIVE') {
          return ['WAITING', 'UPLOADING', 'UPLOADED', 'PARSING', 'CHUNKING', 'EMBEDDING', 'INDEXING'].includes(ft.status)
        }
        return true
      })
      .filter((ft) => (q ? ft.relativePath.toLowerCase().includes(q) : true))
      .sort((a, b) => a.relativePath.localeCompare(b.relativePath))
  }, [task, filter, search])

  if (!kbId) {
    return (
      <div className="ut-page">
        <header className="ut-head">
          <button type="button" className="ut-link-back" onClick={() => { window.location.hash = '#/knowledge' }}>
            <ArrowLeft size={16} aria-hidden="true" />返回知识库
          </button>
        </header>
        <StateNotice message="请先在知识库中选择一个目标空间，再发起上传任务。" />
      </div>
    )
  }

  const isTerminal = task ? ['COMPLETED', 'PARTIAL_FAILED', 'FAILED', 'CANCELLED'].includes(task.status) : false
  const showDropzone = !task || isTerminal
  const uploadPct = task && task.totalBytes > 0 ? Math.round((task.uploadedBytes / task.totalBytes) * 100) : 0
  const processPct = task && task.totalFiles > 0 ? Math.round(((task.completedFiles + task.failedFiles) / task.totalFiles) * 100) : 0
  const etaSeconds =
    task && task.speed > 0 ? (task.totalBytes - task.uploadedBytes) / task.speed : null
  const canStart = task && (task.status === 'READY' || task.status === 'PAUSED')
  const canPause = task && task.status === 'UPLOADING'
  const canCancel = task && (task.status === 'UPLOADING' || task.status === 'PROCESSING' || task.status === 'PAUSED' || task.status === 'READY')
  const hasFailed = task ? task.failedFiles > 0 : false

  return (
    <div className="ut-page">
      <header className="ut-head">
        <div>
          <p className="ut-eyebrow">KNOWLEDGE BASE / UPLOAD TASK CENTER</p>
          <h1>上传任务中心</h1>
          <p className="ut-subtitle">
            {kbName ? `目标知识库：${kbName}` : `知识库 ${kbId}`} · 上传与知识处理解耦，切换页面不中断
          </p>
        </div>
        <button type="button" className="ut-link-back" onClick={() => { window.location.hash = `#/knowledge?kb=${encodeURIComponent(kbId)}` }}>
          <ArrowLeft size={16} aria-hidden="true" />返回知识库
        </button>
      </header>

      {network === 'offline' ? (
        <div className="ut-network-banner" role="alert">
          <span>网络已断开，上传已自动暂停。恢复连接后可在本页继续。</span>
        </div>
      ) : null}

      {task && !showDropzone ? (
        <section className="ut-task-panel" aria-label="上传任务详情">
          <div className="ut-task-head">
            <TaskStatusBadge status={task.status} />
            <span className="ut-task-mode">{task.mode === 'DIRECTORY' ? '目录上传' : '多文件上传'}</span>
            <span className="ut-task-counts">
              {task.totalFiles} 个文件 · {formatBytes(task.uploadedBytes)}/{formatBytes(task.totalBytes)}
            </span>
            {task.speed > 0 && task.status === 'UPLOADING' ? (
              <span className="ut-task-speed">{formatBytes(task.speed)}/s · 剩余 {formatEta(etaSeconds)}</span>
            ) : null}
          </div>

          <div className="ut-dual-progress">
            <div className="ut-progress-block">
              <div className="ut-progress-label">
                <span>上传进度</span>
                <span>{uploadPct}%</span>
              </div>
              <div className="ut-progress-track"><div className="ut-progress-fill" style={{ width: `${uploadPct}%` }} /></div>
              <div className="ut-progress-sub">
                {task.uploadedFiles} 已上传 · {task.uploadingFiles} 上传中 · {task.waitingFiles} 排队
              </div>
            </div>
            <div className="ut-progress-block">
              <div className="ut-progress-label">
                <span>知识处理进度（解析/切片/向量化/索引）</span>
                <span>{processPct}%</span>
              </div>
              <div className="ut-progress-track"><div className="ut-progress-fill ut-progress-fill--process" style={{ width: `${processPct}%` }} /></div>
              <div className="ut-progress-sub">
                {task.completedFiles} 已完成 · {task.processingFiles} 处理中 · {task.failedFiles} 失败
              </div>
            </div>
          </div>

          <div className="ut-controls">
            {canStart ? (
              <button type="button" className="ut-btn ut-btn--primary" onClick={() => void uploadTaskStore.start()}>
                <Play size={15} aria-hidden="true" />{task.status === 'PAUSED' ? '继续上传' : '开始上传'}
              </button>
            ) : null}
            {canPause ? (
              <button type="button" className="ut-btn ut-btn--secondary" onClick={() => uploadTaskStore.pause()}>
                <Pause size={15} aria-hidden="true" />暂停
              </button>
            ) : null}
            {hasFailed ? (
              <button type="button" className="ut-btn ut-btn--secondary" onClick={() => void uploadTaskStore.retryFailed()}>
                <ArrowCounterClockwise size={15} aria-hidden="true" />重试全部失败（{task.failedFiles}）
              </button>
            ) : null}
            {canCancel ? (
              <button type="button" className="ut-btn ut-btn--ghost" onClick={() => uploadTaskStore.cancelTask()}>
                <X size={15} aria-hidden="true" />取消任务
              </button>
            ) : null}
            <button type="button" className="ut-btn ut-btn--ghost" onClick={() => uploadTaskStore.clearTask()}>
              <FloppyDisk size={15} aria-hidden="true" />清空并开始新上传
            </button>
          </div>

          <div className="ut-toolbar">
            <div className="ut-filters">
              {FILTERS.map((f) => (
                <button
                  key={f.key}
                  type="button"
                  className={filter === f.key ? 'ut-chip ut-chip--active' : 'ut-chip'}
                  onClick={() => setFilter(f.key)}
                >
                  {f.label}
                </button>
              ))}
            </div>
            <input
              className="ut-search"
              value={search}
              onChange={(event) => setSearch(event.target.value)}
              placeholder="搜索文件名 / 路径"
              aria-label="搜索文件名或路径"
            />
          </div>

          <UploadFileTable
            files={visibleFiles}
            onCancel={(id) => void uploadTaskStore.cancelFile(id)}
            onRetry={(id) => void uploadTaskStore.retryFile(id)}
          />
        </section>
      ) : null}

      {showDropzone ? (
        <section className="ut-dropzone-section" aria-label="选择要上传的文件">
          {task && isTerminal ? (
            <p className="ut-terminal-note">
              上一任务已结束（{task.status}）。可重新选择文件发起新的上传任务。
            </p>
          ) : null}
          <UploadDropzone onFiles={handleFiles} />
        </section>
      ) : null}

      <section className="ut-history-section" aria-label="历史上传任务">
        <h2 className="ut-section-title">历史上传任务</h2>
        <UploadHistory history={history} state={historyState} />
      </section>
    </div>
  )
}

function StateNotice({ message }: { readonly message: string }) {
  return (
    <div className="ut-state-notice">
      <p>{message}</p>
    </div>
  )
}
