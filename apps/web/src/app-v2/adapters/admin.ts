import type {
  AuditLogListResponse,
  AuditLogQuery,
  AuditLogRecord,
  BackupResponse,
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
