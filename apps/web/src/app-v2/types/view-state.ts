export const PAGE_STATES = [
  'loading',
  'empty',
  'error',
  'permission-denied',
  'unavailable',
  'ready',
] as const

export type PageState = (typeof PAGE_STATES)[number]

export interface StateDetail {
  readonly state: PageState
  readonly message?: string
  readonly reason?: string
  readonly retryable?: boolean
}

export const PAGE_STATE_LABELS: Record<PageState, string> = {
  loading: '加载中',
  empty: '暂无数据',
  error: '加载失败',
  'permission-denied': '无权限',
  unavailable: '能力不可用',
  ready: '已就绪',
}
