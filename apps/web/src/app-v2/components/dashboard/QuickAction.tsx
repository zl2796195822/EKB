import type { ReactNode } from 'react'

interface QuickActionProps {
  readonly label: string
  readonly icon: ReactNode
  readonly href?: string
  readonly disabled?: boolean
  readonly reason?: string
}

export function QuickAction({ label, icon, href, disabled = false, reason }: QuickActionProps) {
  const describedBy = disabled && reason ? `v2-quick-action-reason-${label}` : undefined

  if (disabled) {
    return (
      <button
        type="button"
        className="v2-quick-action v2-quick-action--disabled"
        disabled
        aria-describedby={describedBy}
        title={reason}
      >
        <span className="v2-quick-action-icon" aria-hidden="true">
          {icon}
        </span>
        <span>{label}</span>
        {reason ? (
          <span className="v2-sr-only" id={describedBy}>
            {reason}
          </span>
        ) : null}
      </button>
    )
  }

  return (
    <a className="v2-quick-action" href={href}>
      <span className="v2-quick-action-icon" aria-hidden="true">
        {icon}
      </span>
      <span>{label}</span>
    </a>
  )
}
