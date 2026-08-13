import type { ReactNode } from 'react'

interface DashboardPanelProps {
  readonly id: string
  readonly title: string
  readonly icon: ReactNode
  readonly action?: ReactNode
  readonly className?: string
  readonly children: ReactNode
}

export function DashboardPanel({ id, title, icon, action, className = '', children }: DashboardPanelProps) {
  return (
    <section className={`v2-dashboard-panel ${className}`.trim()} aria-labelledby={id}>
      <div className="v2-dashboard-panel-header">
        <div className="v2-dashboard-panel-title">
          <span aria-hidden="true">{icon}</span>
          <h2 id={id}>{title}</h2>
        </div>
        {action}
      </div>
      {children}
    </section>
  )
}
