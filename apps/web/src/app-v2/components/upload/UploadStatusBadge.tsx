import type { FileTaskStatus, UploadTaskStatus } from '../../types/upload-task'

const FILE_STATUS_META: Record<FileTaskStatus, { label: string; tone: string }> = {
  WAITING: { label: '排队中', tone: 'idle' },
  UPLOADING: { label: '上传中', tone: 'info' },
  UPLOADED: { label: '已上传', tone: 'info' },
  PARSING: { label: '解析中', tone: 'process' },
  CHUNKING: { label: '切片中', tone: 'process' },
  EMBEDDING: { label: '向量化中', tone: 'process' },
  INDEXING: { label: '索引中', tone: 'process' },
  SUCCESS: { label: '已完成', tone: 'success' },
  FAILED: { label: '失败', tone: 'danger' },
  CANCELLED: { label: '已取消', tone: 'muted' },
}

const TASK_STATUS_META: Record<UploadTaskStatus, { label: string; tone: string }> = {
  CREATED: { label: '已创建', tone: 'idle' },
  SCANNING: { label: '扫描中', tone: 'idle' },
  READY: { label: '待开始', tone: 'idle' },
  UPLOADING: { label: '上传中', tone: 'info' },
  PAUSED: { label: '已暂停', tone: 'warning' },
  PROCESSING: { label: '知识处理中', tone: 'process' },
  COMPLETED: { label: '全部完成', tone: 'success' },
  PARTIAL_FAILED: { label: '部分失败', tone: 'warning' },
  FAILED: { label: '失败', tone: 'danger' },
  CANCELLED: { label: '已取消', tone: 'muted' },
}

export function FileStatusBadge({ status }: { readonly status: FileTaskStatus }) {
  const meta = FILE_STATUS_META[status]
  return <span className={`ut-badge ut-badge--${meta.tone}`}>{meta.label}</span>
}

export function TaskStatusBadge({ status }: { readonly status: UploadTaskStatus }) {
  const meta = TASK_STATUS_META[status]
  return <span className={`ut-badge ut-badge--${meta.tone}`}>{meta.label}</span>
}
