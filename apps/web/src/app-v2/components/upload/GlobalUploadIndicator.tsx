import { ArrowUp, Pause, Warning } from '@phosphor-icons/react'
import { useUploadTaskStore } from '../../services/uploadTaskStoreHook'
import type { UploadTaskStatus } from '../../types/upload-task'

const ACTIVE: UploadTaskStatus[] = ['SCANNING', 'READY', 'UPLOADING', 'PAUSED', 'PROCESSING']

export function GlobalUploadIndicator() {
  const { task, network } = useUploadTaskStore()
  if (!task || !ACTIVE.includes(task.status)) return null

  const isPaused = task.status === 'PAUSED'
  const isOffline = network === 'offline'
  const total = task.totalFiles
  const done = task.completedFiles + task.failedFiles + task.cancelledFiles
  const pct = total > 0 ? Math.round((done / total) * 100) : 0

  const goToUploads = () => {
    if (typeof window !== 'undefined') {
      window.location.hash = `#/knowledge/uploads?kb=${encodeURIComponent(task.kbId)}`
    }
  }

  return (
    <button type="button" className="ut-global-indicator" onClick={goToUploads} title="查看上传任务">
      <span className="ut-global-icon">
        {isOffline ? <Warning size={16} aria-hidden="true" /> : isPaused ? <Pause size={16} aria-hidden="true" /> : <ArrowUp size={16} aria-hidden="true" />}
      </span>
      <span className="ut-global-text">
        <strong>上传任务 · {task.status === 'PROCESSING' ? '知识处理中' : isPaused ? '已暂停' : '上传中'}</strong>
        <small>
          {done}/{total} 完成{isOffline ? ' · 网络已断开' : ''}
        </small>
      </span>
      <span className="ut-global-track" aria-hidden="true">
        <span className="ut-global-fill" style={{ width: `${pct}%` }} />
      </span>
    </button>
  )
}
