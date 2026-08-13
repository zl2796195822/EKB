import type { AdapterError } from './adapters'
import type { PageState } from './view-state'

/** 与 migrations/v3_003_analytics.py 的 _TARGET_TYPE_MAP 对齐。 */
export const ANALYTICS_RESOURCE_TYPES = ['KB', 'DOCUMENT', 'CONVERSATION'] as const

export type AnalyticsResourceKind = (typeof ANALYTICS_RESOURCE_TYPES)[number]

/** 与 migrations/v3_003_analytics.py 的 _ACCESS_KIND_RULES 对齐。 */
export const ANALYTICS_ACCESS_KINDS = [
  'VIEW',
  'ASK',
  'CREATE',
  'UPDATE',
  'DELETE',
  'RESTORE',
  'SHARE',
] as const

export type AnalyticsAccessKind = (typeof ANALYTICS_ACCESS_KINDS)[number]

export interface AnalyticsOverviewView {
  readonly days: number
  readonly since: string
  readonly accesses: number
  readonly visitors: number
  readonly qaVolume: number
  readonly kbCount: number
  readonly docCount: number
  readonly memberCount: number
  readonly accessesToday: number
  readonly qaToday: number
}

export interface AnalyticsTrendPointView {
  readonly day: string
  readonly accesses: number
  readonly visitors: number
}

export interface AnalyticsTrendView {
  readonly days: number
  readonly points: readonly AnalyticsTrendPointView[]
  /** 纵轴刻度上界，图表直接用，避免每个消费方各算一遍。 */
  readonly peak: number
  readonly total: number
}

export interface AnalyticsDistributionItemView {
  readonly kbId: string
  readonly name: string
  readonly documents: number
  readonly accesses: number
  /** 占总访问量的百分比（0-100，一位小数）。总量为 0 时为 0。 */
  readonly share: number
}

export interface AnalyticsDistributionView {
  readonly days: number
  readonly items: readonly AnalyticsDistributionItemView[]
  readonly totalAccesses: number
}

export interface AnalyticsActivityItemView {
  readonly id: string
  readonly resourceType: AnalyticsResourceKind
  readonly resourceId: string
  readonly title: string
  readonly accessKind: AnalyticsAccessKind
  readonly actorId: string | null
  readonly actorName: string | null
  readonly occurredAt: string
}

export interface AnalyticsActivityView {
  readonly items: readonly AnalyticsActivityItemView[]
  readonly limit: number
}

export interface AnalyticsOverviewResult {
  readonly state: PageState
  readonly data?: AnalyticsOverviewView
  readonly error?: AdapterError
}

export interface AnalyticsTrendResult {
  readonly state: PageState
  readonly data?: AnalyticsTrendView
  readonly error?: AdapterError
}

export interface AnalyticsDistributionResult {
  readonly state: PageState
  readonly data?: AnalyticsDistributionView
  readonly error?: AdapterError
}

export interface AnalyticsActivityResult {
  readonly state: PageState
  readonly data?: AnalyticsActivityView
  readonly error?: AdapterError
}

export interface AnalyticsServices {
  getOverview(days?: number): Promise<AnalyticsOverviewResult>
  getTrend(days?: number): Promise<AnalyticsTrendResult>
  getDistribution(days?: number, limit?: number): Promise<AnalyticsDistributionResult>
  getActivity(limit?: number): Promise<AnalyticsActivityResult>
}
