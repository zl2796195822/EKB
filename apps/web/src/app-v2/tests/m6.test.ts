import { ApiClientError } from '../../lib/api'
import {
  ADMIN_CAPABILITIES,
  OPS_DASHBOARD_MAX_DAYS,
  OPS_DASHBOARD_MIN_DAYS,
  clampOpsDays,
  createAdminAdapter,
  type AdminApiClient,
} from '../adapters/admin'
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
  SyncSourceRecord,
} from '../../types/api'

function assert(condition: unknown, message: string): asserts condition {
  if (!condition) throw new Error(message)
}

function expectEqual<T>(actual: T, expected: T, message: string): void {
  assert(actual === expected, `${message}: expected ${String(expected)}, received ${String(actual)}`)
}

const opsResponse: OpsDashboardResponse = {
  qa_volume: 128,
  answered: 100,
  refused: 20,
  timeout: 5,
  error: 3,
  accuracy: 0.78,
  refusal_rate: 0.15,
  satisfaction: null,
  feedback_up: 40,
  feedback_down: 6,
  pending_reviews: 9,
  kb_count: 4,
  doc_count: 260,
}

const auditRecord: AuditLogRecord = {
  id: 'audit-1',
  tenant_id: 'tenant-1',
  actor_id: 'user-1',
  action: 'kb.create',
  target_type: 'kb',
  target_id: 'kb-1',
  result: 'SUCCESS',
  trace_id: 'trace-1',
  metadata: { source: 'web' },
  ip_hash: null,
  user_agent_hash: null,
  created_at: '2026-08-10T02:00:00Z',
}

const reviewRecord: ReviewItemRecord = {
  id: 'review-1',
  conversation_id: 'conv-1',
  message_id: 'msg-1',
  question_preview: '发票如何红冲？',
  answer_preview: '需要在 ERP 中发起红字信息表。',
  confidence: 'LOW',
  evidence_summary: [],
  status: 'PENDING',
  resolution: null,
  resolved_by: null,
  created_at: '2026-08-10T02:10:00Z',
  updated_at: '2026-08-10T02:10:00Z',
}

const syncSourceRecord: SyncSourceRecord = {
  id: 'source-1',
  tenant_id: 'tenant-1',
  kb_id: 'kb-1',
  name: 'WebDAV 主库',
  source_type: 'WEBDAV',
  source_url: 'https://dav.example.com/kb',
  cursor: null,
  status: 'IDLE',
  last_sync_at: '2026-08-09T10:00:00Z',
  last_sync_count: 12,
  error_message: null,
  retry_count: 0,
  created_at: '2026-08-01T00:00:00Z',
  updated_at: '2026-08-09T10:00:00Z',
}

const syncRunResponse: SyncRunResponse = {
  source_id: 'source-1',
  status: 'SUCCESS',
  synced_count: 7,
  error_message: null,
}

const backupResponse: BackupResponse = {
  backup_path: '/var/backups/ekb-2026-08-10.tar.gz',
  backup_sha256: 'abc123',
  backup_size_bytes: 1048576,
  elapsed_seconds: 3.5,
  tables: [{ name: 'documents' }, { name: 'chunks' }],
}

export async function runM6ContractTests(): Promise<void> {
  const opsDaysCalls: number[] = []
  const auditQueries: AuditLogQuery[] = []
  const reviewQueries: ReviewItemQuery[] = []
  const reviewUpdates: Array<{ itemId: string; payload: ReviewItemUpdate }> = []
  const syncKbIds: Array<string | undefined> = []
  const syncRunIds: string[] = []
  let backupCalls = 0
  let opsShouldBeEmpty = false
  let opsShouldFailForbidden = false
  let auditShouldFailServerError = false
  let auditEmpty = false

  const client: AdminApiClient = {
    inviteUser: async () => {
      throw new Error('M6 tests must not call the invite endpoint')
    },
    listTenants: async () => {
      throw new Error('M6 tests must not call the tenant list endpoint')
    },
    createTenant: async () => {
      throw new Error('M6 tests must not call the tenant create endpoint')
    },
    getOpsDashboard: async (days) => {
      opsDaysCalls.push(days ?? -1)
      if (opsShouldFailForbidden) {
        throw new ApiClientError(403, 'PERMISSION_DENIED', '当前账号无权查看运营看板', 'request-403')
      }
      if (opsShouldBeEmpty) return {} as OpsDashboardResponse
      return opsResponse
    },
    listAuditLogs: async (query = {}) => {
      auditQueries.push(query)
      if (auditShouldFailServerError) {
        throw new ApiClientError(500, 'INTERNAL_ERROR', '审计日志服务不可用', 'request-500')
      }
      const response: AuditLogListResponse = auditEmpty
        ? { results: [], next_cursor: null, trace_id: 'trace-empty' }
        : { results: [auditRecord], next_cursor: 'cursor-2', trace_id: 'trace-list' }
      return response
    },
    listReviewItems: async (query = {}) => {
      reviewQueries.push(query)
      const response: ReviewItemListResponse = {
        results: [reviewRecord],
        next_cursor: null,
      }
      return response
    },
    getReviewItem: async (itemId) => ({ ...reviewRecord, id: itemId }),
    updateReviewItem: async (itemId, payload) => {
      reviewUpdates.push({ itemId, payload })
      return { ...reviewRecord, id: itemId, status: payload.status, resolution: payload.resolution ?? null }
    },
    listSyncSources: async (kbId) => {
      syncKbIds.push(kbId)
      return [syncSourceRecord]
    },
    createSyncSource: async (payload) => ({
      ...syncSourceRecord,
      kb_id: payload.kb_id,
      name: payload.name,
    }),
    runSyncSource: async (sourceId) => {
      syncRunIds.push(sourceId)
      return syncRunResponse
    },
    triggerBackup: async () => {
      backupCalls += 1
      return backupResponse
    },
  }

  const admin = createAdminAdapter(client)

  // 1. days 参数按服务端 Query(ge=1, le=90) 夹紧。
  expectEqual(clampOpsDays(0), OPS_DASHBOARD_MIN_DAYS, 'clampOpsDays raises values below the minimum')
  expectEqual(clampOpsDays(365), OPS_DASHBOARD_MAX_DAYS, 'clampOpsDays lowers values above the maximum')
  expectEqual(clampOpsDays(30.9), 30, 'clampOpsDays truncates fractional days')
  expectEqual(clampOpsDays(Number.NaN), 7, 'clampOpsDays falls back to the default range')

  // 2. 运营看板映射真实字段，不虚构指标。
  const dashboard = await admin.getOpsDashboard(120)
  expectEqual(dashboard.state, 'ready', 'ops dashboard maps a populated response')
  expectEqual(opsDaysCalls[0], OPS_DASHBOARD_MAX_DAYS, 'ops dashboard forwards the clamped days value')
  expectEqual(dashboard.data?.days, OPS_DASHBOARD_MAX_DAYS, 'ops dashboard view records the clamped days')
  expectEqual(dashboard.data?.qaVolume, 128, 'ops dashboard maps qa_volume')
  expectEqual(dashboard.data?.docCount, 260, 'ops dashboard maps doc_count')
  expectEqual(dashboard.data?.satisfaction, null, 'ops dashboard keeps a null metric as null instead of zero')
  assert(
    dashboard.data?.presentFields.includes('accuracy') === true,
    'ops dashboard records accuracy as a present field',
  )
  assert(
    dashboard.data?.presentFields.includes('satisfaction') === false,
    'ops dashboard excludes null metrics from present fields',
  )

  // 3. 空看板落到 empty 而不是 ready。
  opsShouldBeEmpty = true
  const emptyDashboard = await admin.getOpsDashboard(7)
  expectEqual(emptyDashboard.state, 'empty', 'ops dashboard without any metric maps to the empty state')
  opsShouldBeEmpty = false

  // 4. 401/403 → permission-denied 负向用例。
  opsShouldFailForbidden = true
  const forbidden = await admin.getOpsDashboard(7)
  expectEqual(forbidden.state, 'permission-denied', 'a 403 maps to the permission-denied state')
  expectEqual(forbidden.error?.status, 403, 'the permission error keeps the HTTP status')
  expectEqual(forbidden.error?.requestId, 'request-403', 'the permission error keeps the request id')
  opsShouldFailForbidden = false

  // 5. 审计日志映射与游标。
  const audit = await admin.listAuditLogs({ action: 'kb.create', pageSize: 20 })
  expectEqual(audit.state, 'ready', 'audit list maps a non-empty response')
  expectEqual(auditQueries[0]?.action, 'kb.create', 'audit list forwards the action filter')
  expectEqual(auditQueries[0]?.page_size, 20, 'audit list maps pageSize to the existing page_size param')
  expectEqual(audit.data?.items[0]?.traceId, 'trace-1', 'audit list maps trace_id per record')
  expectEqual(audit.data?.nextCursor, 'cursor-2', 'audit list maps next_cursor')

  auditEmpty = true
  const emptyAudit = await admin.listAuditLogs()
  expectEqual(emptyAudit.state, 'empty', 'an empty audit result maps to the empty state')
  auditEmpty = false

  auditShouldFailServerError = true
  const failedAudit = await admin.listAuditLogs()
  expectEqual(failedAudit.state, 'error', 'a 500 maps to the error state')
  expectEqual(failedAudit.error?.code, 'INTERNAL_ERROR', 'the error keeps the backend error code')
  auditShouldFailServerError = false

  // 6. 审核队列读取与更新。
  const reviews = await admin.listReviewItems({ status: 'PENDING' })
  expectEqual(reviews.state, 'ready', 'review list maps a non-empty response')
  expectEqual(reviewQueries[0]?.status, 'PENDING', 'review list forwards the status filter')
  expectEqual(reviews.data?.items[0]?.confidence, 'LOW', 'review list maps confidence')

  const detail = await admin.getReviewItem('review-9')
  expectEqual(detail.state, 'ready', 'review detail maps a successful response')
  expectEqual(detail.data?.id, 'review-9', 'review detail maps the requested id')

  const reviewed = await admin.updateReviewItem('review-1', { status: 'REVIEWED' })
  expectEqual(reviewed.state, 'ready', 'review update maps a successful response')
  expectEqual(reviewUpdates[0]?.payload.status, 'REVIEWED', 'review update forwards the status')
  assert(
    Object.prototype.hasOwnProperty.call(reviewUpdates[0]?.payload ?? {}, 'resolution') === false,
    'review update omits resolution when the caller did not provide one',
  )

  const resolved = await admin.updateReviewItem('review-1', { status: 'RESOLVED', resolution: '已补充文档' })
  expectEqual(resolved.data?.resolution, '已补充文档', 'review update maps the returned resolution')
  expectEqual(reviewUpdates[1]?.payload.resolution, '已补充文档', 'review update forwards the resolution')

  // 7. 同步源读取与运行（同步 ≠ 应用安装）。
  const sources = await admin.listSyncSources('kb-1')
  expectEqual(sources.state, 'ready', 'sync source list maps a non-empty response')
  expectEqual(syncKbIds[0], 'kb-1', 'sync source list forwards the kb filter')
  expectEqual(sources.data?.[0]?.lastSyncCount, 12, 'sync source list maps last_sync_count')

  const created = await admin.createSyncSource({ kbId: 'kb-2', name: '  票据同步  ' })
  expectEqual(created.state, 'ready', 'sync source create maps a successful response')
  expectEqual(created.data?.name, '票据同步', 'sync source create trims the name')

  const run = await admin.runSyncSource('source-1')
  expectEqual(run.state, 'ready', 'sync run maps a successful response')
  expectEqual(syncRunIds[0], 'source-1', 'sync run forwards the source id')
  expectEqual(run.data?.syncedCount, 7, 'sync run maps synced_count')

  // 8. 备份返回真实元数据。
  const backup = await admin.triggerBackup()
  expectEqual(backup.state, 'ready', 'backup maps a successful response')
  expectEqual(backupCalls, 1, 'backup calls the endpoint exactly once')
  expectEqual(backup.data?.backupSha256, 'abc123', 'backup maps backup_sha256')
  expectEqual(backup.data?.tableCount, 2, 'backup maps the table count')

  // 9. 缺失能力必须显式声明为 unavailable，页面据此渲染 unavailable 而不是造数。
  const capabilityStatus = (id: string): string | undefined =>
    ADMIN_CAPABILITIES.find((capability) => capability.id === id)?.status

  expectEqual(capabilityStatus('admin.ops-dashboard'), 'available', 'ops dashboard capability is available')
  expectEqual(capabilityStatus('admin.audit-read'), 'available', 'audit capability is available')
  expectEqual(capabilityStatus('admin.reviews-read-update'), 'available', 'reviews capability is available')
  expectEqual(capabilityStatus('admin.sync-sources'), 'available', 'sync sources capability is available')
  expectEqual(capabilityStatus('admin.backup'), 'available', 'backup capability is available')
  expectEqual(
    capabilityStatus('admin.analytics-visitor-trend'),
    'unavailable',
    'visitor trend has no backend endpoint and stays unavailable',
  )
  expectEqual(
    capabilityStatus('admin.application-installation'),
    'unavailable',
    'application installation has no backend endpoint and stays unavailable',
  )
  expectEqual(
    capabilityStatus('admin.recycle-restore-purge'),
    'unavailable',
    'recycle bin has no backend endpoint and stays unavailable',
  )
}

void runM6ContractTests().then(() => console.log('M6 contract tests: PASS'))
