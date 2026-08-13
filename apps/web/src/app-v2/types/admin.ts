import type { AdapterError } from './adapters'
import type { PageState } from './view-state'

export interface InviteUserInput {
  readonly email: string
  readonly name: string
  readonly password: string
  readonly role: string
}

export interface InviteUserView {
  readonly id: string
  readonly email: string
  readonly name: string
  readonly role: string
  readonly tenantId: string
}

export interface InviteUserResult {
  readonly state: PageState
  readonly data?: InviteUserView
  readonly error?: AdapterError
}

export interface TenantView {
  readonly id: string
  readonly name: string
  readonly role: string
  readonly modelRoutingKey: string
  readonly egressPolicy: string
  readonly quotaDailyQa: number
  readonly quotaStorageDocs: number
  readonly quotaStorageBytesPerFile: number
}

export interface TenantCreateInput {
  readonly name: string
  readonly ownerEmail: string
  readonly ownerName: string
  readonly ownerPassword: string
  readonly modelRoutingKey: string
  readonly egressPolicy: string
  readonly quotaDailyQa: number
  readonly quotaStorageDocs: number
  readonly quotaStorageBytesPerFile: number
}

export interface TenantListResult {
  readonly state: PageState
  readonly data?: readonly TenantView[]
  readonly error?: AdapterError
}

export interface TenantWriteResult {
  readonly state: PageState
  readonly data?: TenantView
  readonly error?: AdapterError
}

/** 运营看板 view model：字段缺失时为 null，由页面呈现 unavailable。 */
export interface OpsDashboardView {
  readonly days: number
  readonly qaVolume: number | null
  readonly answered: number | null
  readonly refused: number | null
  readonly timeout: number | null
  readonly error: number | null
  readonly accuracy: number | null
  readonly refusalRate: number | null
  readonly satisfaction: number | null
  readonly feedbackUp: number | null
  readonly feedbackDown: number | null
  readonly pendingReviews: number | null
  readonly kbCount: number | null
  readonly docCount: number | null
  /** 响应实际返回的字段名，用于逐卡判断 unavailable。 */
  readonly presentFields: readonly string[]
}

export interface OpsDashboardResult {
  readonly state: PageState
  readonly data?: OpsDashboardView
  readonly error?: AdapterError
}

export interface AuditLogView {
  readonly id: string
  readonly action: string
  readonly actorId: string | null
  readonly tenantId: string | null
  readonly targetType: string | null
  readonly targetId: string | null
  readonly result: string
  readonly traceId: string | null
  readonly createdAt: string
}

export interface AuditLogListView {
  readonly items: readonly AuditLogView[]
  readonly nextCursor: string | null
  readonly traceId: string | null
}

export interface AuditLogListResult {
  readonly state: PageState
  readonly data?: AuditLogListView
  readonly error?: AdapterError
}

export interface AuditLogQueryInput {
  readonly action?: string
  readonly result?: string
  readonly pageSize?: number
  readonly cursor?: string
}

export interface ReviewItemView {
  readonly id: string
  readonly questionPreview: string
  readonly answerPreview: string
  readonly confidence: string
  readonly status: string
  readonly resolution: string | null
  readonly resolvedBy: string | null
  readonly createdAt: string
  readonly updatedAt: string
}

export interface ReviewItemListView {
  readonly items: readonly ReviewItemView[]
  readonly nextCursor: string | null
}

export interface ReviewItemListResult {
  readonly state: PageState
  readonly data?: ReviewItemListView
  readonly error?: AdapterError
}

export interface ReviewItemResult {
  readonly state: PageState
  readonly data?: ReviewItemView
  readonly error?: AdapterError
}

export interface ReviewItemQueryInput {
  readonly status?: string
  readonly pageSize?: number
  readonly cursor?: string
}

export interface ReviewItemUpdateInput {
  readonly status: 'PENDING' | 'REVIEWED' | 'RESOLVED'
  readonly resolution?: string
}

export interface SyncSourceView {
  readonly id: string
  readonly tenantId: string
  readonly kbId: string
  readonly name: string
  readonly sourceType: string
  readonly sourceUrl: string
  readonly cursor: string | null
  readonly status: string
  readonly lastSyncAt: string | null
  readonly lastSyncCount: number
  readonly errorMessage: string | null
  readonly retryCount: number
}

export interface SyncSourceListResult {
  readonly state: PageState
  readonly data?: readonly SyncSourceView[]
  readonly error?: AdapterError
}

export interface SyncSourceWriteResult {
  readonly state: PageState
  readonly data?: SyncSourceView
  readonly error?: AdapterError
}

export interface SyncSourceCreateInput {
  readonly kbId: string
  readonly name: string
  readonly sourceType?: 'WEBDAV' | 'TICKET' | 'API'
  readonly sourceUrl?: string
}

export interface SyncRunView {
  readonly sourceId: string
  readonly status: string
  readonly syncedCount: number
  readonly errorMessage: string | null
}

export interface SyncRunResult {
  readonly state: PageState
  readonly data?: SyncRunView
  readonly error?: AdapterError
}

export interface BackupView {
  readonly backupPath: string
  readonly backupSha256: string
  readonly backupSizeBytes: number
  readonly elapsedSeconds: number
  readonly tableCount: number
}

export interface BackupResult {
  readonly state: PageState
  readonly data?: BackupView
  readonly error?: AdapterError
}

export interface JobErrorView {
  readonly code: string | null
  readonly category: string | null
  readonly message: string | null
  readonly retryable: boolean | null
}

/** Jobs Center view deliberately excludes tenant_id and payload. */
export interface JobView {
  readonly id: string
  readonly jobType: string
  readonly state: string
  readonly priority: number
  readonly maxAttempts: number
  readonly availableAt: string
  readonly leaseOwner: string | null
  readonly leaseExpiresAt: string | null
  readonly heartbeatAt: string | null
  readonly errorCode: string | null
  readonly sanitizedError: JobErrorView | null
  readonly createdAt: string
  readonly updatedAt: string
}

export interface JobListQueryInput {
  readonly states?: readonly string[]
  readonly jobType?: string
  readonly limit?: number
}

export interface JobListView {
  readonly items: readonly JobView[]
  readonly count: number
}

export interface JobListResult {
  readonly state: PageState
  readonly data?: JobListView
  readonly error?: AdapterError
}

export interface JobResult {
  readonly state: PageState
  readonly data?: JobView
  readonly error?: AdapterError
}

export interface JobsCleanupView {
  readonly jobType: string
  readonly total: number
  readonly byState: Readonly<Record<string, number>>
  readonly recent: readonly JobView[]
}

export interface JobsRuntimeWorkerView {
  readonly workerId: string
  readonly workerType: string
  readonly queues: readonly string[]
  readonly version: string
  readonly heartbeatAt: string
  readonly startedAt: string
}

export interface JobsRuntimeLeaseView {
  readonly scheduleName: string
  readonly ownerId: string
  readonly leaseExpiresAt: string
  readonly fencingToken: number
}

export interface JobsRuntimeRunView {
  readonly id: string
  readonly scheduleName: string
  readonly scopeType: string
  readonly startedAt: string
  readonly endedAt: string | null
  readonly status: string
}

export interface JobsRuntimeView {
  readonly status: 'available' | 'unavailable' | string
  readonly workers: readonly JobsRuntimeWorkerView[]
  readonly leases: readonly JobsRuntimeLeaseView[]
  readonly recentRuns: readonly JobsRuntimeRunView[]
  readonly checkedAt: string
}

export interface JobsRuntimeResult {
  readonly state: PageState
  readonly data?: JobsRuntimeView
  readonly error?: AdapterError
}

export interface JobsCleanupResult {
  readonly state: PageState
  readonly data?: JobsCleanupView
  readonly error?: AdapterError
}

export interface AdminServices {
  readonly inviteUser: (input: InviteUserInput) => Promise<InviteUserResult>
  readonly listTenants: () => Promise<TenantListResult>
  readonly createTenant: (input: TenantCreateInput) => Promise<TenantWriteResult>
  readonly getOpsDashboard: (days: number) => Promise<OpsDashboardResult>
  readonly listAuditLogs: (input?: AuditLogQueryInput) => Promise<AuditLogListResult>
  readonly listReviewItems: (input?: ReviewItemQueryInput) => Promise<ReviewItemListResult>
  readonly getReviewItem: (itemId: string) => Promise<ReviewItemResult>
  readonly updateReviewItem: (
    itemId: string,
    input: ReviewItemUpdateInput,
  ) => Promise<ReviewItemResult>
  readonly listSyncSources: (kbId?: string) => Promise<SyncSourceListResult>
  readonly createSyncSource: (input: SyncSourceCreateInput) => Promise<SyncSourceWriteResult>
  readonly runSyncSource: (sourceId: string) => Promise<SyncRunResult>
  readonly triggerBackup: () => Promise<BackupResult>
  readonly listJobs: (input?: JobListQueryInput) => Promise<JobListResult>
  readonly getJob: (jobId: string) => Promise<JobResult>
  readonly cancelJob: (jobId: string) => Promise<JobResult>
  readonly getJobsCleanup: (recentLimit?: number) => Promise<JobsCleanupResult>
  readonly getJobsRuntime: (recentRunsLimit?: number) => Promise<JobsRuntimeResult>
}
