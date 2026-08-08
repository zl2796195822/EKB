/**
 * GlareCard — 卡片悬停时随鼠标位置出现光晕反射
 * 移植自 react-bits GlareHover
 * prefers-reduced-motion 时禁用光晕
 */
import { useRef, type ReactNode, type CSSProperties } from 'react'

export type GlareCardProps = {
  children: ReactNode
  className?: string
  style?: CSSProperties
  glareOpacity?: number
  glareColor?: string
}

export function GlareCard({
  children,
  className,
  style,
  glareOpacity = 0.10,
  glareColor = '#ffffff',
}: GlareCardProps) {
  const containerRef = useRef<HTMLDivElement>(null)
  const glareRef = useRef<HTMLDivElement>(null)

  const handleMouseMove = (e: React.MouseEvent<HTMLDivElement>) => {
    if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) return
    const el = containerRef.current
    const glare = glareRef.current
    if (!el || !glare) return

    const rect = el.getBoundingClientRect()
    const x = e.clientX - rect.left
    const y = e.clientY - rect.top
    const xPct = (x / rect.width) * 100
    const yPct = (y / rect.height) * 100

    glare.style.background = `radial-gradient(circle at ${xPct}% ${yPct}%, ${glareColor} 0%, transparent 65%)`
    glare.style.opacity = String(glareOpacity)
  }

  const handleMouseLeave = () => {
    const glare = glareRef.current
    if (glare) glare.style.opacity = '0'
  }

  return (
    <div
      ref={containerRef}
      className={className}
      style={{ position: 'relative', ...style }}
      onMouseMove={handleMouseMove}
      onMouseLeave={handleMouseLeave}
    >
      {children}
      <div
        ref={glareRef}
        aria-hidden="true"
        style={{
          position: 'absolute',
          inset: 0,
          borderRadius: 'inherit',
          pointerEvents: 'none',
          opacity: 0,
          transition: 'opacity 200ms ease',
        }}
      />
    </div>
  )
}
