/**
 * 上传任务中心领域类型。
 *
 * 设计原则：
 * - UploadTask 是用户在知识库内发起的一次上传动作（可能包含数百到数千文件）。
 * - 每个文件是独立的 FileTask，拥有自己的状态机。
 * - 上传（HTTP 传输到对象存储）与知识处理（解析/切片/Embedding/索引）是两条独立进度，
 *   严禁用上传 100% 冒充任务完成。
 */

export type UploadTaskStatus =
  | 'CREATED'
  | 'SCANNING'
  | 'READY'
  | 'UPLOADING'
  | 'PAUSED'
  | 'PROCESSING'
  | 'COMPLETED'
  | 'PARTIAL_FAILED'
  | 'FAILED'
  | 'CANCELLED'

export type FileTaskStatus =
  | 'WAITING'
  | 'UPLOADING'
  | 'UPLOADED'
  | 'PARSING'
  | 'CHUNKING'
  | 'EMBEDDING'
  | 'INDEXING'
  | 'SUCCESS'
  | 'FAILED'
  | 'CANCELLED'

export type FileKind = 'PDF' | 'Word' | 'PPT' | 'Excel' | 'TXT' | 'Markdown' | 'CSV' | 'Image' | 'Other'

export type UploadErrorCode =
  | 'NETWORK_ERROR'
  | 'FILE_TOO_LARGE'
  | 'UNSUPPORTED_TYPE'
  | 'AUTH_ERROR'
  | 'UPLOAD_STORAGE_ERROR'
  | 'FOLDER_CREATE_ERROR'
  | 'PARSE_ERROR'
  | 'CHUNK_ERROR'
  | 'EMBEDDING_ERROR'
  | 'INDEX_ERROR'
  | 'PATH_INVALID'
  | 'OBJECT_CHECKSUM_MISMATCH'
  | 'OBJECT_SIZE_MISMATCH'
  | 'CANCELLED'
  | 'UNKNOWN_ERROR'

export interface FileTaskError {
  readonly code: UploadErrorCode | string
  readonly message: string
  readonly detail?: Readonly<Record<string, unknown>>
  /** 原始后端错误信息（展开详情时显示） */
  readonly raw?: string
}

export interface FileTask {
  readonly id: string
  /** 文档标题（叶子名） */
  readonly name: string
  /** 完整相对路径（含目录），用于保留目录结构 */
  readonly relativePath: string
  /** 所在目录（不含文件名） */
  readonly dirPath: string
  readonly kind: FileKind
  readonly size: number
  status: FileTaskStatus
  /** 已传输到对象存储的字节数 */
  uploadedBytes: number
  /** 最新速度采样（字节/秒） */
  speed: number
  error: FileTaskError | null
  retryCount: number
  /** 服务端上传批次中的 item id（用于续传/abort/轮询） */
  uploadItemId?: string
  batchId?: string
  /** 服务端摄取 job id（处理阶段轮询用） */
  jobId?: string
  /** 跳过原因（扫描阶段判定为不可上传） */
  skippedReason?: string
}

export interface ScanSummary {
  readonly total: number
  readonly uploadable: number
  readonly skippedUnsupported: number
  readonly skippedDuplicate: number
  readonly skippedZeroByte: number
  readonly totalBytes: number
  readonly byKind: Record<string, number>
}

export interface UploadTask {
  readonly id: string
  readonly kbId: string
  readonly kbName: string
  status: UploadTaskStatus
  mode: 'MULTI_FILE' | 'DIRECTORY'
  scan: ScanSummary | null
  files: ReadonlyMap<string, FileTask>
  /** 当前活跃的服务端上传批次 id（用于轮询处理阶段） */
  batchId: string | null
  totalFiles: number
  waitingFiles: number
  uploadingFiles: number
  uploadedFiles: number
  processingFiles: number
  completedFiles: number
  failedFiles: number
  cancelledFiles: number
  totalBytes: number
  uploadedBytes: number
  createdAt: string
  startedAt: string | null
  completedAt: string | null
  /** 上传阶段速度（字节/秒），基于最近采样 */
  speed: number
  error: string | null
}

export interface HistoryBatch {
  readonly id: string
  readonly kbId: string
  readonly kbName: string
  readonly mode: string
  readonly status: string
  readonly itemCount: number
  readonly totalBytes: number
  readonly createdAt: string
  readonly successCount: number
  readonly failedCount: number
}

export const MAX_CONCURRENT_UPLOADS = 4

export const ACCEPTED_EXTENSIONS: ReadonlySet<string> = new Set([
  '.txt',
  '.md',
  '.pdf',
  '.doc',
  '.docx',
  '.ppt',
  '.pptx',
  '.xls',
  '.xlsx',
  '.csv',
])

export const FILE_KIND_LABELS: Record<FileKind, string> = {
  PDF: 'PDF',
  Word: 'Word',
  PPT: 'PPT',
  Excel: 'Excel',
  TXT: 'TXT',
  Markdown: 'Markdown',
  CSV: 'CSV',
  Image: '图片',
  Other: '其他',
}

export function extOf(name: string): string {
  const dot = name.lastIndexOf('.')
  return dot >= 0 ? name.slice(dot).toLowerCase() : ''
}

export function kindFromExt(ext: string): FileKind {
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
    case '.csv':
      return 'CSV'
    case '.txt':
      return 'TXT'
    case '.md':
      return 'Markdown'
    case '.png':
    case '.jpg':
    case '.jpeg':
    case '.gif':
    case '.bmp':
    case '.webp':
      return 'Image'
    default:
      return 'Other'
  }
}
