import type { ReactNode } from 'react'
import { DashboardUnavailable } from './DashboardUnavailable'

interface DashboardMetricCardProps {
  readonly label: string
  readonly icon: ReactNode
  readonly tone: 'blue' | 'violet' | 'green' | 'orange'
  readonly value?: number | null
  readonly hint?: string
  readonly loading?: boolean
}

function formatNumber(value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(value)) return '—'
  return value.toLocaleString('zh-CN')
}

export function DashboardMetricCard({ label, icon, tone, value, hint, loading }: DashboardMetricCardProps) {
  const hasValue = typeof value === 'number'
  return (
    <article className="v2-dashboard-metric" aria-label={label}>
      <div>
        <span className="v2-dashboard-metric-label">{label}</span>
        {loading ? (
          <div className="v2-dashboard-metric-skeleton" aria-label="加载中" />
        ) : hasValue ? (
          <>
            <strong className="v2-dashboard-metric-number" aria-live="polite">{formatNumber(value)}</strong>
            {hint ? <small className="v2-dashboard-metric-hint">{hint}</small> : null}
          </>
        ) : (
          <DashboardUnavailable message="当前未提供工作台聚合数据。" />
        )}
      </div>
      <span className={`v2-dashboard-metric-icon v2-dashboard-metric-icon--${tone}`} aria-hidden="true">
        {icon}
      </span>
    </article>
  )
}
