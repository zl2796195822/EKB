import { readFileSync } from 'node:fs'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { ApiClient, ApiClientError } from '../../lib/api'
import {
  ANALYTICS_ACTIVITY_LIMIT,
  ANALYTICS_CAPABILITIES,
  ANALYTICS_DEFAULT_DAYS,
  ANALYTICS_TREND_DAYS,
  createAnalyticsAdapter,
  type AnalyticsApiClient,
} from '../adapters/analytics'
import type {
  AnalyticsActivityResponse,
  AnalyticsDistributionResponse,
  AnalyticsOverviewResponse,
  AnalyticsTrendResponse,
} from '../../types/api'

const overviewResponse: AnalyticsOverviewResponse = {
  days: 7,
  since: '2026-08-04T00:00:00Z',
  accesses: 412,
  visitors: 9,
  qa_volume: 258,
  kb_count: 3,
  doc_count: 2,
  member_count: 11,
  accesses_today: 27,
  qa_today: 14,
}

const trendResponse: AnalyticsTrendResponse = {
  days: 3,
  points: [
    { day: '2026-08-08', accesses: 12, visitors: 3 },
    { day: '2026-08-09', accesses: 0, visitors: 0 },
    { day: '2026-08-10', accesses: 37, visitors: 5 },
  ],
}

const distributionResponse: AnalyticsDistributionResponse = {
  days: 7,
  items: [
    { kb_id: 'kb-1', name: '金博', documents: 2, accesses: 30 },
    { kb_id: 'kb-2', name: '运维 SOP', documents: 0, accesses: 10 },
  ],
}

const activityResponse: AnalyticsActivityResponse = {
  items: [
    {
      id: 'evt-1',
      resource_type: 'KB',
      resource_id: 'kb-1',
      title: '金博',
      access_kind: 'CREATE',
      actor_id: 'user-1',
      actor_name: '管理员',
      occurred_at: '2026-08-10T16:14:51Z',
    },
    {
      id: 'evt-2',
      resource_type: 'WIDGET',
      resource_id: 'doc-9',
      title: '   ',
      access_kind: 'TELEPORT',
      actor_id: 'user-2',
      actor_name: null,
      occurred_at: '2026-08-10T15:02:00Z',
    },
  ],
  limit: 10,
}

function stubClient(overrides: Partial<AnalyticsApiClient> = {}): AnalyticsApiClient {
  return {
    getAnalyticsOverview: async () => overviewResponse,
    getAnalyticsTrend: async () => trendResponse,
    getAnalyticsDistribution: async () => distributionResponse,
    getAnalyticsActivity: async () => activityResponse,
    ...overrides,
  }
}

afterEach(() => {
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

describe('Phase 3 · 数据看板适配器', () => {
  it('maps the snake_case overview into camelCase view models', async () => {
    const result = await createAnalyticsAdapter(stubClient()).getOverview()

    expect(result.state).toBe('ready')
    expect(result.data).toMatchObject({
      days: 7,
      accesses: 412,
      visitors: 9,
      qaVolume: 258,
      kbCount: 3,
      docCount: 2,
      memberCount: 11,
      accessesToday: 27,
      qaToday: 14,
    })
  })

  it('derives peak/total for the trend chart instead of leaving it to each consumer', async () => {
    const result = await createAnalyticsAdapter(stubClient()).getTrend()

    expect(result.state).toBe('ready')
    expect(result.data?.peak).toBe(37)
    expect(result.data?.total).toBe(49)
    // 零填充的日期轴必须原样保留，否则折线会把没有访问的那天连过去。
    expect(result.data?.points.map((point) => point.day)).toEqual([
      '2026-08-08',
      '2026-08-09',
      '2026-08-10',
    ])
  })

  it('treats an all-zero day axis as empty, not ready', async () => {
    const result = await createAnalyticsAdapter(
      stubClient({
        getAnalyticsTrend: async () => ({
          days: 2,
          points: [
            { day: '2026-08-09', accesses: 0, visitors: 0 },
            { day: '2026-08-10', accesses: 0, visitors: 0 },
          ],
        }),
      }),
    ).getTrend()

    expect(result.state).toBe('empty')
    expect(result.data?.points).toHaveLength(2)
  })

  it('computes distribution share against the window total', async () => {
    const result = await createAnalyticsAdapter(stubClient()).getDistribution()

    expect(result.state).toBe('ready')
    expect(result.data?.totalAccesses).toBe(40)
    expect(result.data?.items.map((item) => item.share)).toEqual([75, 25])

    // 全零访问量不能除出 NaN。
    const quiet = await createAnalyticsAdapter(
      stubClient({
        getAnalyticsDistribution: async () => ({
          days: 7,
          items: [{ kb_id: 'kb-1', name: '金博', documents: 2, accesses: 0 }],
        }),
      }),
    ).getDistribution()
    expect(quiet.data?.items[0]?.share).toBe(0)
  })

  it('falls back on unknown enum values and blank titles from the free-text projection', async () => {
    const result = await createAnalyticsAdapter(stubClient()).getActivity()

    expect(result.state).toBe('ready')
    expect(result.data?.items[0]).toMatchObject({
      resourceType: 'KB',
      accessKind: 'CREATE',
      actorName: '管理员',
    })
    expect(result.data?.items[1]).toMatchObject({
      resourceType: 'DOCUMENT',
      accessKind: 'VIEW',
      title: '（已删除资源）',
      actorName: null,
    })
  })

  it('reports empty distribution/activity rather than a fake zero row', async () => {
    const adapter = createAnalyticsAdapter(
      stubClient({
        getAnalyticsDistribution: async () => ({ days: 7, items: [] }),
        getAnalyticsActivity: async () => ({ items: [], limit: 10 }),
      }),
    )

    expect((await adapter.getDistribution()).state).toBe('empty')
    expect((await adapter.getActivity()).state).toBe('empty')
  })

  it('maps 403 to permission-denied and other failures to error', async () => {
    const denied = await createAnalyticsAdapter(
      stubClient({
        getAnalyticsOverview: async () => {
          throw new ApiClientError(403, 'PERMISSION_DENIED', '当前账号无权查看访问统计')
        },
      }),
    ).getOverview()
    expect(denied.state).toBe('permission-denied')
    expect(denied.error?.message).toBe('当前账号无权查看访问统计')

    const broken = await createAnalyticsAdapter(
      stubClient({
        getAnalyticsTrend: async () => {
          throw new Error('boom')
        },
      }),
    ).getTrend()
    expect(broken.state).toBe('error')
    expect(broken.error?.code).toBe('CLIENT_ERROR')
  })

  it('passes the caller days through instead of pinning the default', async () => {
    const seen: (number | undefined)[] = []
    const adapter = createAnalyticsAdapter(
      stubClient({
        getAnalyticsOverview: async (days) => {
          seen.push(days)
          return overviewResponse
        },
        getAnalyticsTrend: async (days) => {
          seen.push(days)
          return trendResponse
        },
        getAnalyticsActivity: async (limit) => {
          seen.push(limit)
          return activityResponse
        },
      }),
    )

    await adapter.getOverview(30)
    await adapter.getTrend()
    await adapter.getActivity()

    expect(seen).toEqual([30, ANALYTICS_TREND_DAYS, ANALYTICS_ACTIVITY_LIMIT])
    expect(ANALYTICS_DEFAULT_DAYS).toBe(7)
  })
})

describe('Phase 3 · /analytics 契约与页面边界', () => {
  it('serializes the /analytics contract in ApiClient', async () => {
    const requests: { url: string; method: string }[] = []
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      requests.push({ url: String(input), method: init?.method ?? 'GET' })
      return new Response(JSON.stringify({ items: [], points: [], days: 7, limit: 10 }), {
        status: 200,
        headers: { 'content-type': 'application/json' },
      })
    })
    vi.stubGlobal('fetch', fetchMock)

    const client = new ApiClient('/api/v1')
    await client.getAnalyticsOverview(30)
    await client.getAnalyticsTrend(90)
    await client.getAnalyticsDistribution(7, 8)
    await client.getAnalyticsActivity(12)

    const paths = requests.map((request) => new URL(request.url, 'http://example.test'))
    expect(paths.map((url) => url.pathname)).toEqual([
      '/api/v1/analytics/overview',
      '/api/v1/analytics/trend',
      '/api/v1/analytics/distribution',
      '/api/v1/analytics/activity',
    ])
    expect(paths[0]!.searchParams.get('days')).toBe('30')
    expect(paths[1]!.searchParams.get('days')).toBe('90')
    expect(paths[2]!.searchParams.get('limit')).toBe('8')
    expect(paths[3]!.searchParams.get('limit')).toBe('12')
    expect(requests.every((request) => request.method === 'GET')).toBe(true)
  })

  it('keeps AnalyticsPage behind the service boundary and free of unavailable charts', () => {
    const source = readFileSync(new URL('../pages/AnalyticsPage.tsx', import.meta.url), 'utf8')

    expect(source).toContain('services.analytics')
    expect(source).not.toMatch(/src\/lib\/api|from '\.\.\/\.\.\/lib\/api'/)
    expect(source).not.toMatch(/\bfetch\s*\(/)
    expect(source).not.toMatch(/style=\{\{/)
    // Phase 3 的核心目标：趋势图和分布图不能再是"后端没有该能力"的占位。
    expect(source).not.toMatch(/state="unavailable"/)
  })

  it('declares export/custom-range as the only unavailable capabilities', () => {
    const statusOf = (id: string): string | undefined =>
      ANALYTICS_CAPABILITIES.find((capability) => capability.id === id)?.status

    expect(statusOf('analytics.overview')).toBe('available')
    expect(statusOf('analytics.access-trend')).toBe('available')
    expect(statusOf('analytics.kb-distribution')).toBe('available')
    expect(statusOf('analytics.recent-activity')).toBe('available')
    expect(statusOf('analytics.quality-metrics')).toBe('available')
    expect(statusOf('analytics.export')).toBe('unavailable')
    expect(statusOf('analytics.custom-range')).toBe('unavailable')
  })
})
