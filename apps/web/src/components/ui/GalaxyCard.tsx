import React from 'react'
import { GlareCard } from './animated/GlareCard'

export type GalaxyCardVariant = 'default' | 'document' | 'kb' | 'conversation'

export type GalaxyCardProps = {
  children: React.ReactNode
  variant?: GalaxyCardVariant
  leftAccent?: boolean
  className?: string
  onClick?: () => void
  /** 是否启用 GlareHover 光晕（默认 true） */
  glare?: boolean
}

export function GalaxyCard({
  children,
  variant = 'default',
  leftAccent = false,
  className,
  onClick,
  glare = true,
}: GalaxyCardProps): React.ReactElement {
  const accentCls = leftAccent ? `galaxy-card-left-accent left-bar-${variant}` : ''
  const clickable = onClick ? 'galaxy-card-clickable' : ''
  const cls = `galaxy-card galaxy-card-${variant} ${accentCls} ${clickable}${
    className ? ` ${className}` : ''
  }`.trim()

  const inner = (
    <>
      {leftAccent && <span className="galaxy-card-accent-bar left-bar-stripe" aria-hidden="true" />}
      <div className="galaxy-card-inner">{children}</div>
    </>
  )

  if (onClick) {
    return (
      <GlareCard className={cls} glareOpacity={glare ? 0.10 : 0}>
        <button type="button" style={{ display: 'contents' }} onClick={onClick}>
          {inner}
        </button>
      </GlareCard>
    )
  }

  return (
    <GlareCard className={cls} glareOpacity={glare ? 0.10 : 0}>
      {inner}
    </GlareCard>
  )
}
