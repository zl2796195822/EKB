import type { ReactElement } from 'react'
import type { DocumentStatus } from '../types/api'

const labels: Record<DocumentStatus, string> = {
  PROCESSING: '处理中',
  READY: '可检索',
  FAILED: '失败',
  DELETED: '已下线',
}

export function StatusBadge({ status }: { status: DocumentStatus }): ReactElement {
  return <span className={`status status-${status.toLowerCase()}`}>{labels[status]}</span>
}
