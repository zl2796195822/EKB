import type { PageState } from '../types'
import { PAGE_STATE_LABELS } from '../types'

interface StatePanelProps {
  readonly state: PageState
  readonly message?: string
  readonly reason?: string
}

const DEFAULT_MESSAGES: Record<PageState, string> = {
  loading: '正在等待真实 API 数据。',
  empty: '当前范围没有可显示的数据。',
  error: '真实 API 返回错误，等待后续重试入口。',
  'permission-denied': '当前主体没有访问该资源的权限。',
  unavailable: '当前后端能力尚未提供，保留视觉结构但不触发假请求。',
  ready: '真实 API 数据已就绪。',
}

export function StatePanel({ state, message, reason }: StatePanelProps) {
  return (
    <div className="v2-state-panel" data-state={state} role={state === 'error' ? 'alert' : 'status'}>
      <strong>{PAGE_STATE_LABELS[state]}</strong>
      <p>{message ?? DEFAULT_MESSAGES[state]}</p>
      {reason ? <small>{reason}</small> : null}
    </div>
  )
}
