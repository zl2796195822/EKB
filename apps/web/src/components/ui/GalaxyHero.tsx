import React, { useEffect, useRef } from 'react'
import { RocketLaunch, Sparkle, type IconProps } from '@phosphor-icons/react'
import { gsap } from 'gsap'

export type GalaxyHeroProps = {
  eyebrow?: string
  title: React.ReactNode
  subtitle?: React.ReactNode
  primaryLabel?: string
  secondaryLabel?: string
  onPrimary?: () => void
  onSecondary?: () => void
  primaryIcon?: React.ComponentType<IconProps>
  secondaryIcon?: React.ComponentType<IconProps>
  actionSlot?: React.ReactNode
  className?: string
  /** 是否启用入场动画（默认 true） */
  animated?: boolean
}

export function GalaxyHero({
  eyebrow,
  title,
  subtitle,
  primaryLabel = '立即体验',
  secondaryLabel = '了解更多',
  onPrimary,
  onSecondary,
  primaryIcon: PrimaryIcon = Sparkle,
  secondaryIcon: SecondaryIcon = RocketLaunch,
  actionSlot,
  className,
  animated = true,
}: GalaxyHeroProps): React.ReactElement {
  const cls = `galaxy-hero${className ? ` ${className}` : ''}`
  const innerRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!animated) return
    const el = innerRef.current
    if (!el) return
    if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) return

    const children = Array.from(el.children)
    gsap.set(children, { opacity: 0, y: 18 })
    gsap.to(children, {
      opacity: 1,
      y: 0,
      duration: 0.55,
      ease: 'power3.out',
      stagger: 0.10,
      delay: 0.05,
    })
  }, [animated])

  return (
    <section className={cls} aria-labelledby="galaxy-hero-title">
      <div className="galaxy-hero-orb galaxy-hero-orb-1" aria-hidden="true" />
      <div className="galaxy-hero-orb galaxy-hero-orb-2" aria-hidden="true" />
      <div className="galaxy-hero-inner" ref={innerRef}>
        {eyebrow && (
          <p className="galaxy-hero-eyebrow">
            <Sparkle size={12} weight="fill" aria-hidden="true" />
            {eyebrow}
          </p>
        )}
        <h1 id="galaxy-hero-title" className="galaxy-hero-title">
          {title}
        </h1>
        {subtitle && <p className="galaxy-hero-subtitle">{subtitle}</p>}
        <div className="galaxy-hero-actions">
          {onPrimary && primaryLabel && (
            <button type="button" className="galaxy-hero-btn galaxy-hero-btn-primary" onClick={onPrimary}>
              <PrimaryIcon size={16} weight="fill" aria-hidden="true" />
              {primaryLabel}
            </button>
          )}
          {onSecondary && secondaryLabel && (
            <button type="button" className="galaxy-hero-btn galaxy-hero-btn-secondary" onClick={onSecondary}>
              <SecondaryIcon size={16} weight="fill" aria-hidden="true" />
              {secondaryLabel}
            </button>
          )}
        </div>
        {actionSlot && <div className="galaxy-hero-slot">{actionSlot}</div>}
      </div>
    </section>
  )
}
