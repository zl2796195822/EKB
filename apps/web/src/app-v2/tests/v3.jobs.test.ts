import { afterEach, describe, expect, it, vi } from 'vitest'
import { ApiClient, ApiClientError } from '../../lib/api'
import {
  createAdminAdapter,
  type AdminApiClient,
} from '../adapters/admin'
import type {
  JobViewResponse,
  JobsCleanupResponse,
  JobsListQuery,
  JobsListResponse,
} from '../../types/api'

const job: JobViewResponse = {
  id: 'job-1',
  tenant_id: 'tenant-must-not-reach-view',
  job_type: 'document_ingest',
  idempotency_key: 'idem-1',
  state: 'RUNNING',
  priority: 10,
  max_attempts: 3,
  payload: { secret: 'payload正文不得进入页面' },
  available_at: '2026-08-13T00:00:00Z',
  lease_owner: 'worker-1',
  lease_expires_at: '2026-08-13T00:05:00Z',
  heartbeat_at: '2026-08-13T00:04:00Z',
  error_code: 'PROVIDER_FAIL',
  sanitized_error: {
    code: 'PROVIDER_FAIL',
    category: 'provider',
    message: '安全错误消息',
    retryable: true,
    raw_provider_response: '不得进入 view model',
  },
  created_at: '2026-08-13T00:00:00Z',
  updated_at: '2026-08-13T00:04:00Z',
}

const jobsResponse: JobsListResponse = {
  items: [job],
  count: 1,
}

const cleanupResponse: JobsCleanupResponse = {
  job_type: 'retention_purge',
  total: 4,
  by_state: { SUCCEEDED: 2, FAILED: 1, RETRY_WAIT: 1 },
  recent: [job],
}

function noCall(name: string): () => Promise<never> {
  return async () => {
    throw new Error(`${name} must not be called by Jobs Center tests`)
  }
}

function stubClient(overrides: Partial<AdminApiClient> = {}): AdminApiClient {
  return {
    inviteUser: noCall('inviteUser'),
    listTenants: noCall('listTenants'),
    createTenant: noCall('createTenant'),
    getOpsDashboard: noCall('getOpsDashboard'),
    listAuditLogs: noCall('listAuditLogs'),
    listReviewItems: noCall('listReviewItems'),
    getReviewItem: noCall('getReviewItem'),
    updateReviewItem: noCall('updateReviewItem'),
    listSyncSources: noCall('listSyncSources'),
    createSyncSource: noCall('createSyncSource'),
    runSyncSource: noCall('runSyncSource'),
    triggerBackup: noCall('triggerBackup'),
    listJobs: async () => jobsResponse,
    getJob: async () => job,
    cancelJob: async () => job,
    getJobsCleanup: async () => cleanupResponse,
    ...overrides,
  }
}

afterEach(() => {
  vi.restoreAllMocks()
})

describe('Jobs Center · API contract and adapter boundary', () => {
  it('serializes bounded repeated state filters and URL-encodes query values', async () => {
    const requests: { url: string; method: string }[] = []
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      requests.push({ url: String(input), method: init?.method ?? 'GET' })
      const path = new URL(String(input), 'http://example.test').pathname
      const responseBody = path.endsWith('/cleanup')
        ? cleanupResponse
        : path.endsWith('/cancel')
          ? job
          : { items: [], count: 0 }
      return new Response(JSON.stringify(responseBody), {
        status: 200,
        headers: { 'content-type': 'application/json' },
      })
    }))

    const client = new ApiClient('/api/v1')
    await client.listJobs({
      states: ['QUEUED', 'RUNNING'],
      job_type: 'retention purge',
      limit: 999,
    })
    await client.getJobsCleanup(999)
    await client.cancelJob('job/1')

    const url = new URL(requests[0]!.url, 'http://example.test')
    expect(url.pathname).toBe('/api/v1/jobs')
    expect(url.searchParams.getAll('states')).toEqual(['QUEUED', 'RUNNING'])
    expect(url.searchParams.get('job_type')).toBe('retention purge')
    expect(url.searchParams.get('limit')).toBe('500')
    expect(requests[0]?.method).toBe('GET')
    expect(new URL(requests[1]!.url, 'http://example.test').searchParams.get('recent_limit')).toBe('100')
    expect(new URL(requests[2]!.url, 'http://example.test').pathname).toContain('/jobs/job%2F1/cancel')
    expect(requests[2]?.method).toBe('POST')
  })

  it('maps list and cleanup responses without exposing tenant or payload fields', async () => {
    const seenQueries: JobsListQuery[] = []
    const adapter = createAdminAdapter(stubClient({
      listJobs: async (query) => {
        seenQueries.push(query ?? {})
        return jobsResponse
      },
      getJobsCleanup: async (recentLimit) => {
        expect(recentLimit).toBe(100)
        return cleanupResponse
      },
    }))

    const list = await adapter.listJobs({
      states: [' QUEUED ', 'RUNNING'],
      jobType: ' retention_purge ',
      limit: 999,
    })
    const cleanup = await adapter.getJobsCleanup(999)

    expect(list.state).toBe('ready')
    expect(seenQueries[0]).toMatchObject({
      states: ['QUEUED', 'RUNNING'],
      job_type: 'retention_purge',
      limit: 500,
    })
    expect(list.data?.items[0]).toMatchObject({
      id: 'job-1',
      jobType: 'document_ingest',
      leaseOwner: 'worker-1',
      heartbeatAt: job.heartbeat_at,
      errorCode: 'PROVIDER_FAIL',
    })
    expect(list.data?.items[0]).not.toHaveProperty('tenantId')
    expect(list.data?.items[0]).not.toHaveProperty('payload')
    expect(list.data?.items[0]?.sanitizedError).toEqual({
      code: 'PROVIDER_FAIL',
      category: 'provider',
      message: '安全错误消息',
      retryable: true,
    })

    expect(cleanup.state).toBe('ready')
    expect(cleanup.data).toMatchObject({
      jobType: 'retention_purge',
      total: 4,
      byState: { SUCCEEDED: 2, FAILED: 1, RETRY_WAIT: 1 },
    })
    expect(cleanup.data?.recent[0]?.jobType).toBe('document_ingest')
  })

  it('calls the real cancel endpoint through the adapter and maps the returned state', async () => {
    const cancelled: JobViewResponse = {
      ...job,
      state: 'CANCELLED',
      lease_owner: null,
      lease_expires_at: null,
      heartbeat_at: null,
      sanitized_error: null,
    }
    const cancelledIds: string[] = []
    const adapter = createAdminAdapter(stubClient({
      cancelJob: async (jobId) => {
        cancelledIds.push(jobId)
        return cancelled
      },
    }))

    const result = await adapter.cancelJob('job-1')

    expect(result.state).toBe('ready')
    expect(result.data?.state).toBe('CANCELLED')
    expect(cancelledIds).toEqual(['job-1'])
  })

  it('maps ApiClientError permission failures to the existing permission-denied state', async () => {
    const adapter = createAdminAdapter(stubClient({
      listJobs: async () => {
        throw new ApiClientError(403, 'PERMISSION_DENIED', '当前主体无权查看作业', 'request-jobs-403')
      },
    }))

    const result = await adapter.listJobs()

    expect(result.state).toBe('permission-denied')
    expect(result.error).toMatchObject({
      code: 'PERMISSION_DENIED',
      status: 403,
      requestId: 'request-jobs-403',
      message: '当前主体无权查看作业',
    })
  })

  it('maps non-permission API failures to the existing error state', async () => {
    const adapter = createAdminAdapter(stubClient({
      getJobsCleanup: async () => {
        throw new ApiClientError(503, 'JOBS_UNAVAILABLE', '作业服务暂时不可用')
      },
    }))

    const result = await adapter.getJobsCleanup()

    expect(result.state).toBe('error')
    expect(result.error?.code).toBe('JOBS_UNAVAILABLE')
    expect(result.error?.status).toBe(503)
  })
})
