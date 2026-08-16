import { ArrowCounterClockwise, X } from '@phosphor-icons/react'
import type { FileTask } from '../../types/upload-task'
import { FileStatusBadge } from './UploadStatusBadge'
import { uploadTaskStore } from '../../services/uploadTaskStore'

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

function processStageLabel(status: FileTask['status']): string | null {
  switch (status) {
    case 'PARSING':
      return '解析'
    case 'CHUNKING':
      return '切片'
    case 'EMBEDDING':
      return '向量化'
    case 'INDEXING':
      return '索引'
    default:
      return null
  }
}

interface UploadFileTableProps {
  readonly files: readonly FileTask[]
  readonly onCancel: (id: string) => void
  readonly onRetry: (id: string) => void
}

export function UploadFileTable({ files, onCancel, onRetry }: UploadFileTableProps) {
  if (files.length === 0) {
    return <p className="ut-empty">没有符合条件的文件。</p>
  }
  return (
    <div className="ut-file-table" role="table" aria-label="上传文件列表">
      <div className="ut-file-row ut-file-row--head" role="row">
        <span role="columnheader">文件</span>
        <span role="columnheader">状态</span>
        <span role="columnheader">进度</span>
        <span role="columnheader">大小</span>
        <span role="columnheader">操作</span>
      </div>
      {files.map((ft) => {
        const isUploading = ft.status === 'UPLOADING'
        const isProcessing = ['PARSING', 'CHUNKING', 'EMBEDDING', 'INDEXING'].includes(ft.status)
        const isDone = ft.status === 'SUCCESS'
        const isFailed = ft.status === 'FAILED'
        const pct = ft.size > 0 ? Math.round((ft.uploadedBytes / ft.size) * 100) : isDone ? 100 : 0
        const stage = processStageLabel(ft.status)
        return (
          <div className="ut-file-row" role="row" key={ft.id}>
            <div className="ut-file-name" role="cell" title={ft.relativePath}>
              {ft.dirPath ? <small className="ut-file-dir">{ft.dirPath}/</small> : null}
              <span>{ft.name}</span>
            </div>
            <div role="cell"><FileStatusBadge status={ft.status} /></div>
            <div className="ut-file-progress" role="cell">
              <div className="ut-progress-track" aria-hidden="true">
                <div
                  className={`ut-progress-fill ${isProcessing ? 'ut-progress-fill--process' : isDone ? 'ut-progress-fill--done' : isFailed ? 'ut-progress-fill--fail' : ''}`}
                  style={{ width: `${pct}%` }}
                />
              </div>
              <span className="ut-progress-meta">
                {isUploading
                  ? `${pct}% · ${uploadTaskStore.formatBytes(ft.speed)}/s`
                  : isProcessing && stage
                    ? `${stage}中…`
                    : isDone
                      ? '完成'
                      : isFailed
                        ? '失败'
                        : ft.status === 'CANCELLED'
                          ? '已取消'
                          : `${pct}%`}
              </span>
              {isFailed && ft.error ? (
                <details className="ut-file-error">
                  <summary>{ft.error.message}</summary>
                  {ft.error.raw ? <pre>{ft.error.raw}</pre> : null}
                </details>
              ) : null}
            </div>
            <div role="cell" className="ut-file-size">{formatBytes(ft.size)}</div>
            <div className="ut-file-actions" role="cell">
              {isFailed ? (
                <button type="button" className="ut-icon-btn" onClick={() => onRetry(ft.id)} title="重试该文件" aria-label={`重试 ${ft.name}`}>
                  <ArrowCounterClockwise size={15} aria-hidden="true" />
                </button>
              ) : null}
              {['WAITING', 'UPLOADING', 'PARSING', 'CHUNKING', 'EMBEDDING', 'INDEXING'].includes(ft.status) ? (
                <button type="button" className="ut-icon-btn ut-icon-btn--danger" onClick={() => onCancel(ft.id)} title="取消该文件" aria-label={`取消 ${ft.name}`}>
                  <X size={15} aria-hidden="true" />
                </button>
              ) : null}
            </div>
          </div>
        )
      })}
    </div>
  )
}
