import { CheckCircle, CircleNotch, WarningCircle, XCircle } from '@phosphor-icons/react'

export type StatusPillKind = 'PROCESSING' | 'INDEXING' | 'READY' | 'FAILED' | 'DELETED'

const STATUS_META: Record<StatusPillKind, { label: string; tone: string }> = {
  PROCESSING: { label: '处理中', tone: 'processing' },
  INDEXING: { label: '索引中', tone: 'processing' },
  READY: { label: '已就绪', tone: 'ready' },
  FAILED: { label: '失败', tone: 'failed' },
  DELETED: { label: '已删除', tone: 'deleted' },
}

export function StatusPill({ status }: { readonly status: StatusPillKind }) {
  const meta = STATUS_META[status]
  const icon =
    status === 'READY' ? <CheckCircle size={13} aria-hidden="true" /> :
    status === 'FAILED' ? <XCircle size={13} aria-hidden="true" /> :
    status === 'DELETED' ? <WarningCircle size={13} aria-hidden="true" /> :
    <CircleNotch size={13} aria-hidden="true" />

  return (
    <span className={`v2-m3-status-pill v2-m3-status-pill--${meta.tone}`}>
      {icon}
      {meta.label}
    </span>
  )
}
