/**
 * CountUpValue — 数值从 0 滚动到目标值（基于 GSAP）
 * 移植自 react-bits CountUp 思路
 * 进入视口时触发，prefers-reduced-motion 时直接显示目标值
 */
import { useEffect, useRef, useState, type CSSProperties } from 'react'
import { gsap } from 'gsap'
import { ScrollTrigger } from 'gsap/ScrollTrigger'

gsap.registerPlugin(ScrollTrigger)

export type CountUpValueProps = {
  value: number
  duration?: number
  decimals?: number
  suffix?: string
  prefix?: string
  className?: string
  style?: CSSProperties
}

export function CountUpValue({
  value,
  duration = 1.4,
  decimals = 0,
  suffix = '',
  prefix = '',
  className,
  style,
}: CountUpValueProps) {
  const ref = useRef<HTMLSpanElement>(null)
  const [displayed, setDisplayed] = useState(0)

  useEffect(() => {
    const el = ref.current
    if (!el) return

    if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) {
      setDisplayed(value)
      return
    }

    const obj = { val: 0 }

    const tl = gsap.timeline({ paused: true })
    tl.to(obj, {
      val: value,
      duration,
      ease: 'power2.out',
      onUpdate: () => setDisplayed(obj.val),
      onComplete: () => setDisplayed(value),
    })

    const st = ScrollTrigger.create({
      trigger: el,
      start: 'top 90%',
      onEnter: () => tl.play(),
      once: true,
    })

    return () => {
      tl.kill()
      st.kill()
    }
  }, [value, duration])

  const formatted =
    decimals > 0
      ? displayed.toFixed(decimals)
      : Math.round(displayed).toLocaleString()

  return (
    <span ref={ref} className={className} style={style}>
      {prefix}{formatted}{suffix}
    </span>
  )
}
