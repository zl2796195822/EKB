import { StatePanel } from '../StatePanel'

interface DashboardUnavailableProps {
  readonly message: string
  readonly className?: string
}

export function DashboardUnavailable({ message, className = '' }: DashboardUnavailableProps) {
  return (
    <div className={`v2-dashboard-unavailable ${className}`.trim()}>
      <StatePanel state="unavailable" message={message} />
    </div>
  )
}
