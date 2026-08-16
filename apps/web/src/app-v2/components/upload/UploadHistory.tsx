import type { HistoryBatch } from '../../types/upload-task'

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

function formatDateTime(value: string): string {
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString('zh-CN')
}

const STATUS_LABEL: Record<string, string> = {
  ready: '完成',
  completed: '完成',
  partial: '部分完成',
  failed: '失败',
  cancelled: '已取消',
  processing: '处理中',
}

export function UploadHistory({ history, state }: { readonly history: readonly HistoryBatch[]; readonly state: string }) {
  if (state === 'loading') return <p className="ut-empty">正在加载历史上传任务…</p>
  if (state === 'error') return <p className="ut-empty ut-empty--error">历史任务加载失败。</p>
  if (history.length === 0) return <p className="ut-empty">暂无历史上传任务。</p>
  return (
    <div className="ut-history">
      {history.map((batch) => (
        <div className="ut-history-row" key={batch.id}>
          <div className="ut-history-main">
            <span className="ut-history-name">{batch.kbName}</span>
            <span className="ut-history-mode">{batch.mode === 'DIRECTORY' ? '目录' : '多文件'}</span>
          </div>
          <div className="ut-history-meta">
            <span>{batch.itemCount} 个文件</span>
            <span>{formatBytes(batch.totalBytes)}</span>
            <span className={`ut-history-status ut-history-status--${batch.status}`}>{STATUS_LABEL[batch.status] ?? batch.status}</span>
            <span className="ut-history-time">{formatDateTime(batch.createdAt)}</span>
          </div>
          {batch.failedCount > 0 ? (
            <div className="ut-history-fail">{batch.failedCount} 个失败</div>
          ) : null}
        </div>
      ))}
    </div>
  )
}
