import type {
  AnalyticsActivityItemRecord,
  AnalyticsActivityResponse,
  AnalyticsDistributionResponse,
  AnalyticsOverviewResponse,
  AnalyticsTrendResponse,
} from '../../types/api'
import type {
  AnalyticsAccessKind,
  AnalyticsActivityItemView,
  AnalyticsActivityResult,
  AnalyticsActivityView,
  AnalyticsDistributionResult,
  AnalyticsDistributionView,
  AnalyticsOverviewResult,
  AnalyticsOverviewView,
  AnalyticsResourceKind,
  AnalyticsServices,
  AnalyticsTrendResult,
  AnalyticsTrendView,
} from '../types/analytics'
import { ANALYTICS_ACCESS_KINDS, ANALYTICS_RESOURCE_TYPES } from '../types/analytics'
import { stateForError, toAdapterError } from './index'

/** app-v2 只依赖这四个只读方法，避免把整个 ApiClient 表面积带进 UI 层。 */
export interface AnalyticsApiClient {
  getAnalyticsOverview(days?: number): Promise<AnalyticsOverviewResponse>
  getAnalyticsTrend(days?: number): Promise<AnalyticsTrendResponse>
  getAnalyticsDistribution(days?: number, limit?: number): Promise<AnalyticsDistributionResponse>
  getAnalyticsActivity(limit?: number): Promise<AnalyticsActivityResponse>
}

export const ANALYTICS_DEFAULT_DAYS = 7
export const ANALYTICS_TREND_DAYS = 14
export const ANALYTICS_ACTIVITY_LIMIT = 10

function mapResourceKind(value: string): AnalyticsResourceKind {
  return (ANALYTICS_RESOURCE_TYPES as readonly string[]).includes(value)
    ? (value as AnalyticsResourceKind)
    : 'DOCUMENT'
}

/** 投影表的 access_kind 是自由文本列，未知值兜底成 VIEW 而不是抛错。 */
function mapAccessKind(value: string): AnalyticsAccessKind {
  return (ANALYTICS_ACCESS_KINDS as readonly string[]).includes(value)
    ? (value as AnalyticsAccessKind)
    : 'VIEW'
}

function mapOverview(input: AnalyticsOverviewResponse): AnalyticsOverviewView {
  return {
    days: input.days,
    since: input.since,
    accesses: input.accesses,
    visitors: input.visitors,
    qaVolume: input.qa_volume,
    kbCount: input.kb_count,
    docCount: input.doc_count,
    memberCount: input.member_count,
    accessesToday: input.accesses_today,
    qaToday: input.qa_today,
  }
}

function mapTrend(input: AnalyticsTrendResponse): AnalyticsTrendView {
  const points = input.points.map((point) => ({
    day: point.day,
    accesses: point.accesses,
    visitors: point.visitors,
  }))
  const peak = points.reduce((max, point) => Math.max(max, point.accesses), 0)
  const total = points.reduce((sum, point) => sum + point.accesses, 0)
  return { days: input.days, points, peak, total }
}

function mapDistribution(input: AnalyticsDistributionResponse): AnalyticsDistributionView {
  const totalAccesses = input.items.reduce((sum, item) => sum + item.accesses, 0)
  const items = input.items.map((item) => ({
    kbId: item.kb_id,
    name: item.name,
    documents: item.documents,
    accesses: item.accesses,
    share: totalAccesses > 0 ? Math.round((item.accesses / totalAccesses) * 1000) / 10 : 0,
  }))
  return { days: input.days, items, totalAccesses }
}

function mapActivityItem(input: AnalyticsActivityItemRecord): AnalyticsActivityItemView {
  return {
    id: input.id,
    resourceType: mapResourceKind(input.resource_type),
    resourceId: input.resource_id,
    // 资源被永久删除后标题查不回来，用占位串而不是留空，避免列表出现空行。
    title: input.title?.trim() || '（已删除资源）',
    accessKind: mapAccessKind(input.access_kind),
    actorId: input.actor_id,
    actorName: input.actor_name,
    occurredAt: input.occurred_at,
  }
}

function mapActivity(input: AnalyticsActivityResponse): AnalyticsActivityView {
  return { items: input.items.map(mapActivityItem), limit: input.limit }
}

function failure(error: unknown, fallbackMessage: string) {
  const mapped = toAdapterError(error, fallbackMessage)
  return { state: stateForError(mapped), error: mapped }
}

export function createAnalyticsAdapter(client: AnalyticsApiClient): AnalyticsServices {
  return {
    getOverview: async (days = ANALYTICS_DEFAULT_DAYS): Promise<AnalyticsOverviewResult> => {
      try {
        const data = mapOverview(await client.getAnalyticsOverview(days))
        return { state: 'ready', data }
      } catch (error) {
        return failure(error, '概览指标加载失败')
      }
    },

    getTrend: async (days = ANALYTICS_TREND_DAYS): Promise<AnalyticsTrendResult> => {
      try {
        const data = mapTrend(await client.getAnalyticsTrend(days))
        // 日期轴是后端零填充的，恒有点位；真正的空态判据是窗口内一次访问都没有。
        return { state: data.total > 0 ? 'ready' : 'empty', data }
      } catch (error) {
        return failure(error, '访问趋势加载失败')
      }
    },

    getDistribution: async (
      days = ANALYTICS_DEFAULT_DAYS,
      limit?: number,
    ): Promise<AnalyticsDistributionResult> => {
      try {
        const data = mapDistribution(await client.getAnalyticsDistribution(days, limit))
        return { state: data.items.length > 0 ? 'ready' : 'empty', data }
      } catch (error) {
        return failure(error, '内容使用分布加载失败')
      }
    },

    getActivity: async (limit = ANALYTICS_ACTIVITY_LIMIT): Promise<AnalyticsActivityResult> => {
      try {
        const data = mapActivity(await client.getAnalyticsActivity(limit))
        return { state: data.items.length > 0 ? 'ready' : 'empty', data }
      } catch (error) {
        return failure(error, '最近活动加载失败')
      }
    },
  }
}

export type AnalyticsAdapter = AnalyticsServices

export const ANALYTICS_CAPABILITIES = [
  { id: 'analytics.overview', status: 'available' },
  { id: 'analytics.access-trend', status: 'available' },
  { id: 'analytics.kb-distribution', status: 'available' },
  { id: 'analytics.recent-activity', status: 'available' },
  { id: 'analytics.quality-metrics', status: 'available' },
  {
    id: 'analytics.export',
    status: 'unavailable',
    reason: '后端没有导出端点，报表下载需要另起一条链路。',
  },
  {
    id: 'analytics.custom-range',
    status: 'unavailable',
    reason: '后端只接受 days（1-90）滚动窗口，暂不支持任意起止日期。',
  },
] as const
