import type {
  AuditLogListResponse,
  AuditLogQuery,
  AuditLogRecord,
  BackupResponse,
  JobViewResponse,
  JobsCleanupResponse,
  JobsListQuery,
  JobsListResponse,
  JobsRuntimeResponse,
  OpsDashboardResponse,
  ReviewItemListResponse,
  ReviewItemQuery,
  ReviewItemRecord,
  ReviewItemUpdate,
  SyncRunResponse,
  SyncSourceCreate,
  SyncSourceRecord,
  TenantCreate,
  TenantResponse,
  UserInvite,
  UserInviteResponse,
} from '../../types/api'
import type {
  AdapterCapability,
  AdapterError,
  AdminServices,
  AuditLogQueryInput,
  AuditLogView,
  BackupView,
  InviteUserInput,
  OpsDashboardView,
  ReviewItemQueryInput,
  ReviewItemUpdateInput,
  ReviewItemView,
  SyncRunView,
  SyncSourceCreateInput,
  SyncSourceView,
  TenantCreateInput,
  TenantView,
} from '../types'
import type {
  JobListQueryInput,
  JobView,
  JobsCleanupView,
  JobsRuntimeResult,
  JobsRuntimeView,
} from '../types/admin'
import { stateForData, stateForError, toAdapterError } from './index'

export interface AdminApiClient {
  inviteUser(payload: UserInvite): Promise<UserInviteResponse>
  listTenants(): Promise<TenantResponse[]>
  createTenant(payload: TenantCreate): Promise<TenantResponse>
  getOpsDashboard(days?: number): Promise<OpsDashboardResponse>
  listAuditLogs(query?: AuditLogQuery): Promise<AuditLogListResponse>
  listReviewItems(query?: ReviewItemQuery): Promise<ReviewItemListResponse>
  getReviewItem(itemId: string): Promise<ReviewItemRecord>
  updateReviewItem(itemId: string, payload: ReviewItemUpdate): Promise<ReviewItemRecord>
  listSyncSources(kbId?: string): Promise<SyncSourceRecord[]>
  createSyncSource(payload: SyncSourceCreate): Promise<SyncSourceRecord>
  runSyncSource(sourceId: string): Promise<SyncRunResponse>
  triggerBackup(): Promise<BackupResponse>
  listJobs?: (query?: JobsListQuery) => Promise<JobsListResponse>
  getJob?: (jobId: string) => Promise<JobViewResponse>
  cancelJob?: (jobId: string) => Promise<JobViewResponse>
  getJobsCleanup?: (recentLimit?: number) => Promise<JobsCleanupResponse>
  getJobsRuntime?: (recentRunsLimit?: number) => Promise<JobsRuntimeResponse>
}

export type AdminAdapter = AdminServices

export interface OpsDashboard {
  readonly days: number
  readonly availableFields: readonly string[]
}

/** ops dashboard 允许的天数区间与服务端 Query(ge=1, le=90) 一致。 */
export const OPS_DASHBOARD_MIN_DAYS = 1
export const OPS_DASHBOARD_MAX_DAYS = 90

export function clampOpsDays(days: number): number {
  if (!Number.isFinite(days)) return 7
  return Math.min(OPS_DASHBOARD_MAX_DAYS, Math.max(OPS_DASHBOARD_MIN_DAYS, Math.trunc(days)))
}

const OPS_NUMERIC_FIELDS = [
  'qa_volume',
  'answered',
  'refused',
  'timeout',
  'error',
  'accuracy',
  'refusal_rate',
  'satisfaction',
  'feedback_up',
  'feedback_down',
  'pending_reviews',
  'kb_count',
  'doc_count',
] as const

function numberOrNull(value: unknown): number | null {
  return typeof value === 'number' && Number.isFinite(value) ? value : null
}

function mapOpsDashboard(input: OpsDashboardResponse, days: number): OpsDashboardView {
  const source = input as unknown as Record<string, unknown>
  const presentFields = OPS_NUMERIC_FIELDS.filter(
    (field) => source[field] !== undefined && source[field] !== null,
  )

  return {
    days,
    qaVolume: numberOrNull(source.qa_volume),
    answered: numberOrNull(source.answered),
    refused: numberOrNull(source.refused),
    timeout: numberOrNull(source.timeout),
    error: numberOrNull(source.error),
    accuracy: numberOrNull(source.accuracy),
    refusalRate: numberOrNull(source.refusal_rate),
    satisfaction: numberOrNull(source.satisfaction),
    feedbackUp: numberOrNull(source.feedback_up),
    feedbackDown: numberOrNull(source.feedback_down),
    pendingReviews: numberOrNull(source.pending_reviews),
    kbCount: numberOrNull(source.kb_count),
    docCount: numberOrNull(source.doc_count),
    presentFields,
  }
}

function mapAuditLog(input: AuditLogRecord): AuditLogView {
  return {
    id: input.id,
    action: input.action,
    actorId: input.actor_id,
    tenantId: input.tenant_id,
    targetType: input.target_type,
    targetId: input.target_id,
    result: input.result,
    traceId: input.trace_id,
    createdAt: input.created_at,
  }
}

function mapReviewItem(input: ReviewItemRecord): ReviewItemView {
  return {
    id: input.id,
    questionPreview: input.question_preview,
    answerPreview: input.answer_preview,
    confidence: input.confidence,
    status: input.status,
    resolution: input.resolution,
    resolvedBy: input.resolved_by,
    createdAt: input.created_at,
    updatedAt: input.updated_at,
  }
}

function mapSyncSource(input: SyncSourceRecord): SyncSourceView {
  return {
    id: input.id,
    tenantId: input.tenant_id,
    kbId: input.kb_id,
    name: input.name,
    sourceType: input.source_type,
    sourceUrl: input.source_url,
    cursor: input.cursor,
    status: input.status,
    lastSyncAt: input.last_sync_at,
    lastSyncCount: input.last_sync_count,
    errorMessage: input.error_message,
    retryCount: input.retry_count,
  }
}

function mapSyncRun(input: SyncRunResponse): SyncRunView {
  return {
    sourceId: input.source_id,
    status: input.status,
    syncedCount: input.synced_count,
    errorMessage: input.error_message,
  }
}

function mapBackup(input: BackupResponse): BackupView {
  return {
    backupPath: input.backup_path,
    backupSha256: input.backup_sha256,
    backupSizeBytes: input.backup_size_bytes,
    elapsedSeconds: input.elapsed_seconds,
    tableCount: Array.isArray(input.tables) ? input.tables.length : 0,
  }
}

const JOBS_MIN_LIMIT = 1
const JOBS_MAX_LIMIT = 500
const JOBS_DEFAULT_LIMIT = 100
const JOBS_MAX_STATES = 8
const JOBS_STATE_MAX_LENGTH = 32
const JOBS_TYPE_MAX_LENGTH = 64
const JOBS_CLEANUP_MIN_LIMIT = 1
const JOBS_CLEANUP_MAX_LIMIT = 100
const JOBS_CLEANUP_DEFAULT_LIMIT = 10

function boundedInteger(value: number | undefined, fallback: number, min: number, max: number): number {
  if (value === undefined || !Number.isFinite(value)) return fallback
  return Math.min(max, Math.max(min, Math.trunc(value)))
}

function boundedText(value: unknown, maxLength: number): string | null {
  if (typeof value !== 'string') return null
  const trimmed = value.trim()
  return trimmed ? trimmed.slice(0, maxLength) : null
}

function mapJobError(input: JobViewResponse): JobView['sanitizedError'] {
  const source = input.sanitized_error
  const record = source && typeof source === 'object' ? source as Record<string, unknown> : null
  const code = boundedText(record?.code, 128) ?? boundedText(input.error_code, 128)
  const category = boundedText(record?.category, 128)
  const message = boundedText(record?.message, 512)
  const retryable = typeof record?.retryable === 'boolean' ? record.retryable : null
  if (!code && !category && !message && retryable === null) return null
  return { code, category, message, retryable }
}

function mapJob(input: JobViewResponse): JobView {
  const sanitizedError = mapJobError(input)
  return {
    id: input.id,
    jobType: input.job_type,
    state: input.state,
    priority: input.priority,
    maxAttempts: input.max_attempts,
    availableAt: input.available_at,
    leaseOwner: boundedText(input.lease_owner, 128),
    leaseExpiresAt: input.lease_expires_at,
    heartbeatAt: input.heartbeat_at,
    errorCode: sanitizedError?.code ?? null,
    sanitizedError,
    createdAt: input.created_at,
    updatedAt: input.updated_at,
  }
}

function mapJobs(input: JobsListResponse) {
  const items = Array.isArray(input.items) ? input.items.map(mapJob) : []
  return { items, count: Number.isFinite(input.count) ? input.count : items.length }
}

function mapCleanup(input: JobsCleanupResponse): JobsCleanupView {
  const byState: Record<string, number> = {}
  for (const [rawState, rawCount] of Object.entries(input.by_state ?? {})) {
    const state = boundedText(rawState, JOBS_STATE_MAX_LENGTH)
    if (state && typeof rawCount === 'number' && Number.isFinite(rawCount)) {
      byState[state] = Math.max(0, Math.trunc(rawCount))
    }
  }
  return {
    jobType: boundedText(input.job_type, JOBS_TYPE_MAX_LENGTH) ?? '—',
    total: Number.isFinite(input.total) ? Math.max(0, Math.trunc(input.total)) : 0,
    byState,
    recent: Array.isArray(input.recent) ? input.recent.map(mapJob) : [],
  }
}

function mapRuntime(input: JobsRuntimeResponse): JobsRuntimeView {
  const workers = Array.isArray(input.workers) ? input.workers.map((worker) => ({
    workerId: boundedText(worker.worker_id, 128) ?? '—',
    workerType: boundedText(worker.worker_type, 128) ?? '—',
    queues: Array.isArray(worker.queues)
      ? worker.queues
        .filter((queue): queue is string => typeof queue === 'string')
        .map((queue) => queue.trim().slice(0, 128))
        .filter(Boolean)
        .slice(0, 32)
      : [],
    version: boundedText(worker.version, 128) ?? '—',
    heartbeatAt: worker.heartbeat_at,
    startedAt: worker.started_at,
  })) : []
  const leases = Array.isArray(input.leases) ? input.leases.map((lease) => ({
    scheduleName: boundedText(lease.schedule_name, JOBS_TYPE_MAX_LENGTH) ?? '—',
    ownerId: boundedText(lease.owner_id, 128) ?? '—',
    leaseExpiresAt: lease.lease_expires_at,
    fencingToken: Number.isFinite(lease.fencing_token) ? Math.trunc(lease.fencing_token) : 0,
  })) : []
  const recentRuns = Array.isArray(input.recent_runs) ? input.recent_runs.map((run) => ({
    id: boundedText(run.id, 128) ?? '—',
    scheduleName: boundedText(run.schedule_name, JOBS_TYPE_MAX_LENGTH) ?? '—',
    scopeType: boundedText(run.scope_type, 32) ?? '—',
    startedAt: run.started_at,
    endedAt: run.ended_at,
    status: boundedText(run.status, 32) ?? '—',
  })) : []
  return {
    status: boundedText(input.status, 32) ?? 'unavailable',
    workers,
    leases,
    recentRuns,
    checkedAt: input.checked_at,
  }
}

function normalizeJobQuery(input: JobListQueryInput = {}): JobsListQuery {
  const states = input.states
    ?.map((state) => boundedText(state, JOBS_STATE_MAX_LENGTH))
    .filter((state): state is string => state !== null)
    .slice(0, JOBS_MAX_STATES)
  const jobType = boundedText(input.jobType, JOBS_TYPE_MAX_LENGTH)
  return {
    states,
    job_type: jobType ?? undefined,
    limit: boundedInteger(input.limit, JOBS_DEFAULT_LIMIT, JOBS_MIN_LIMIT, JOBS_MAX_LIMIT),
  }
}

function mapInvite(input: UserInviteResponse) {
  return {
    id: input.id,
    email: input.email,
    name: input.name,
    role: input.role,
    tenantId: input.tenant_id,
  }
}

function mapTenant(input: TenantResponse): TenantView {
  return {
    id: input.id,
    name: input.name,
    role: input.role,
    modelRoutingKey: input.model_routing_key,
    egressPolicy: input.egress_policy,
    quotaDailyQa: input.quota_daily_qa,
    quotaStorageDocs: input.quota_storage_docs,
    quotaStorageBytesPerFile: input.quota_storage_bytes_per_file,
  }
}

function errorResult(error: unknown, fallbackMessage: string): {
  readonly state: 'error' | 'permission-denied'
  readonly error: AdapterError
} {
  const mapped = toAdapterError(error, fallbackMessage)
  return { state: stateForError(mapped) as 'error' | 'permission-denied', error: mapped }
}

function toUserInvitePayload(input: InviteUserInput): UserInvite {
  return {
    email: input.email.trim(),
    name: input.name.trim(),
    password: input.password,
    role: input.role,
  }
}

function toTenantCreatePayload(input: TenantCreateInput): TenantCreate {
  return {
    name: input.name.trim(),
    owner_email: input.ownerEmail.trim(),
    owner_name: input.ownerName.trim(),
    owner_password: input.ownerPassword,
    model_routing_key: input.modelRoutingKey.trim(),
    egress_policy: input.egressPolicy.trim(),
    quota_daily_qa: input.quotaDailyQa,
    quota_storage_docs: input.quotaStorageDocs,
    quota_storage_bytes_per_file: input.quotaStorageBytesPerFile,
  }
}

export function createAdminAdapter(client: AdminApiClient): AdminServices {
  return {
    inviteUser: async (input) => {
      try {
        return { state: 'ready', data: mapInvite(await client.inviteUser(toUserInvitePayload(input))) }
      } catch (error) {
        return errorResult(error, '邀请用户失败')
      }
    },
    listTenants: async () => {
      try {
        const data = (await client.listTenants()).map(mapTenant)
        return { state: stateForData(data), data }
      } catch (error) {
        return errorResult(error, '租户列表加载失败')
      }
    },
    createTenant: async (input) => {
      try {
        return { state: 'ready', data: mapTenant(await client.createTenant(toTenantCreatePayload(input))) }
      } catch (error) {
        return errorResult(error, '租户创建失败')
      }
    },
    getOpsDashboard: async (days) => {
      const safeDays = clampOpsDays(days)
      try {
        const data = mapOpsDashboard(await client.getOpsDashboard(safeDays), safeDays)
        return { state: data.presentFields.length > 0 ? 'ready' : 'empty', data }
      } catch (error) {
        return errorResult(error, '运营看板加载失败')
      }
    },
    listAuditLogs: async (input = {}) => {
      try {
        const response = await client.listAuditLogs({
          action: input.action,
          result: input.result,
          page_size: input.pageSize,
          cursor: input.cursor,
        })
        const items = response.results.map(mapAuditLog)
        return {
          state: stateForData(items),
          data: { items, nextCursor: response.next_cursor, traceId: response.trace_id },
        }
      } catch (error) {
        return errorResult(error, '审计日志加载失败')
      }
    },
    listReviewItems: async (input = {}) => {
      try {
        const response = await client.listReviewItems({
          status: input.status,
          page_size: input.pageSize,
          cursor: input.cursor,
        })
        const items = response.results.map(mapReviewItem)
        return { state: stateForData(items), data: { items, nextCursor: response.next_cursor } }
      } catch (error) {
        return errorResult(error, '低置信度审核队列加载失败')
      }
    },
    getReviewItem: async (itemId) => {
      try {
        return { state: 'ready', data: mapReviewItem(await client.getReviewItem(itemId)) }
      } catch (error) {
        return errorResult(error, '审核项加载失败')
      }
    },
    updateReviewItem: async (itemId, input) => {
      try {
        const payload: ReviewItemUpdate = input.resolution === undefined
          ? { status: input.status }
          : { status: input.status, resolution: input.resolution }
        return { state: 'ready', data: mapReviewItem(await client.updateReviewItem(itemId, payload)) }
      } catch (error) {
        return errorResult(error, '审核项更新失败')
      }
    },
    listSyncSources: async (kbId) => {
      try {
        const data = (await client.listSyncSources(kbId)).map(mapSyncSource)
        return { state: stateForData(data), data }
      } catch (error) {
        return errorResult(error, '同步源列表加载失败')
      }
    },
    createSyncSource: async (input) => {
      try {
        const payload: SyncSourceCreate = {
          kb_id: input.kbId,
          name: input.name.trim(),
          ...(input.sourceType ? { source_type: input.sourceType } : {}),
          ...(input.sourceUrl ? { source_url: input.sourceUrl.trim() } : {}),
        }
        return { state: 'ready', data: mapSyncSource(await client.createSyncSource(payload)) }
      } catch (error) {
        return errorResult(error, '同步源创建失败')
      }
    },
    runSyncSource: async (sourceId) => {
      try {
        return { state: 'ready', data: mapSyncRun(await client.runSyncSource(sourceId)) }
      } catch (error) {
        return errorResult(error, '同步源运行失败')
      }
    },
    triggerBackup: async () => {
      try {
        return { state: 'ready', data: mapBackup(await client.triggerBackup()) }
      } catch (error) {
        return errorResult(error, '备份触发失败')
      }
    },
    listJobs: async (input = {}) => {
      if (!client.listJobs) return errorResult(new Error('Jobs Center API client unavailable'), '作业列表加载失败')
      try {
        const data = mapJobs(await client.listJobs(normalizeJobQuery(input)))
        return { state: stateForData(data.items), data }
      } catch (error) {
        return errorResult(error, '作业列表加载失败')
      }
    },
    getJob: async (jobId) => {
      if (!client.getJob) return errorResult(new Error('Jobs Center API client unavailable'), '作业详情加载失败')
      try {
        return { state: 'ready', data: mapJob(await client.getJob(jobId)) }
      } catch (error) {
        return errorResult(error, '作业详情加载失败')
      }
    },
    cancelJob: async (jobId) => {
      if (!client.cancelJob) return errorResult(new Error('Jobs Center API client unavailable'), '作业取消失败')
      try {
        return { state: 'ready', data: mapJob(await client.cancelJob(jobId)) }
      } catch (error) {
        return errorResult(error, '作业取消失败')
      }
    },
    getJobsCleanup: async (recentLimit = JOBS_CLEANUP_DEFAULT_LIMIT) => {
      if (!client.getJobsCleanup) return errorResult(new Error('Jobs Center API client unavailable'), '清理投影加载失败')
      const safeRecentLimit = boundedInteger(
        recentLimit,
        JOBS_CLEANUP_DEFAULT_LIMIT,
        JOBS_CLEANUP_MIN_LIMIT,
        JOBS_CLEANUP_MAX_LIMIT,
      )
      try {
        const data = mapCleanup(await client.getJobsCleanup(safeRecentLimit))
        return { state: data.total > 0 ? 'ready' : 'empty', data }
      } catch (error) {
        return errorResult(error, '清理投影加载失败')
      }
    },
    getJobsRuntime: async (recentRunsLimit = 20): Promise<JobsRuntimeResult> => {
      if (!client.getJobsRuntime) return errorResult(new Error('Jobs Center runtime API client unavailable'), '运行时状态加载失败')
      const safeRecentRunsLimit = boundedInteger(recentRunsLimit, 20, 1, 100)
      try {
        const data = mapRuntime(await client.getJobsRuntime(safeRecentRunsLimit))
        return { state: data.status === 'available' ? 'ready' : 'unavailable', data }
      } catch (error) {
        return errorResult(error, '运行时状态加载失败')
      }
    },
  }
}

export const ADMIN_CAPABILITIES = [
  {
    id: 'admin.users-invite',
    status: 'available',
  },
  {
    id: 'admin.tenants-list-create',
    status: 'available',
  },
  {
    id: 'admin.ops-dashboard',
    status: 'available',
  },
  {
    id: 'admin.audit-read',
    status: 'available',
  },
  {
    id: 'admin.reviews-read-update',
    status: 'available',
  },
  {
    id: 'admin.sync-sources',
    status: 'available',
  },
  {
    id: 'admin.backup',
    status: 'available',
  },
  {
    id: 'admin.jobs-center',
    status: 'available',
  },
  {
    id: 'admin.analytics-visitor-trend',
    status: 'unavailable',
    reason: 'ops dashboard 只返回聚合计数，没有按日访问量或独立访客序列端点。',
  },
  {
    id: 'admin.user-list-role-crud',
    status: 'unavailable',
    reason: '当前后端只有邀请端点，没有用户列表或角色 CRUD 端点。',
  },
  {
    id: 'admin.application-installation',
    status: 'unavailable',
    reason: '应用目录、安装、连接和卸载端点当前不存在。',
  },
  {
    id: 'admin.recycle-restore-purge',
    status: 'unavailable',
    reason: '回收站列表、还原、永久删除和清空端点当前不存在。',
  },
] as const satisfies readonly AdapterCapability[]
