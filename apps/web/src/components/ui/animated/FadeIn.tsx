/**
 * FadeIn — 基于 GSAP ScrollTrigger 的入场动画容器
 * 移植自 react-bits AnimatedContent / FadeContent
 * prefers-reduced-motion 时跳过动画直接显示
 */
import { useEffect, useRef, type ReactNode, type CSSProperties } from 'react'
import { gsap } from 'gsap'
import { ScrollTrigger } from 'gsap/ScrollTrigger'

gsap.registerPlugin(ScrollTrigger)

type Direction = 'up' | 'down' | 'left' | 'right' | 'none'

export type FadeInProps = {
  children: ReactNode
  direction?: Direction
  distance?: number
  duration?: number
  delay?: number
  threshold?: number
  className?: string
  style?: CSSProperties
  /** 是否只触发一次（默认 true） */
  once?: boolean
}

export function FadeIn({
  children,
  direction = 'up',
  distance = 24,
  duration = 0.55,
  delay = 0,
  threshold = 0.08,
  className,
  style,
  once = true,
}: FadeInProps) {
  const ref = useRef<HTMLDivElement>(null)

  useEffect(() => {
    const el = ref.current
    if (!el) return

    // 尊重系统减少动画偏好
    if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) {
      gsap.set(el, { opacity: 1, x: 0, y: 0 })
      return
    }

    const axisMap: Record<Direction, { x: number; y: number }> = {
      up:    { x: 0, y: distance },
      down:  { x: 0, y: -distance },
      left:  { x: distance, y: 0 },
      right: { x: -distance, y: 0 },
      none:  { x: 0, y: 0 },
    }

    const { x, y } = axisMap[direction]

    gsap.set(el, { opacity: 0, x, y, visibility: 'visible' })

    const tl = gsap.timeline({ paused: true, delay })
    tl.to(el, { opacity: 1, x: 0, y: 0, duration, ease: 'power3.out' })

    const st = ScrollTrigger.create({
      trigger: el,
      start: `top ${(1 - threshold) * 100}%`,
      onEnter: () => tl.play(),
      onEnterBack: once ? undefined : () => { tl.restart() },
    })

    return () => {
      tl.kill()
      st.kill()
    }
  }, [direction, distance, duration, delay, threshold, once])

  return (
    <div ref={ref} className={className} style={{ visibility: 'hidden', ...style }}>
      {children}
    </div>
  )
}
