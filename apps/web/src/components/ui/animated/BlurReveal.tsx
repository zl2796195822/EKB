/**
 * BlurReveal — 文字逐词/逐字模糊揭示动画（纯 GSAP，不依赖 framer-motion）
 * 移植自 react-bits BlurText 思路
 * prefers-reduced-motion 时直接显示文本
 */
import { useCallback, useEffect, useRef, type CSSProperties } from 'react'
import { gsap } from 'gsap'

export type BlurRevealProps = {
  text: string
  /** 按 'words' 还是 'chars' 拆分 */
  animateBy?: 'words' | 'chars'
  /** 每个元素的延迟步长（ms） */
  staggerMs?: number
  /** 单元素动画时长（s） */
  duration?: number
  /** 整体延迟（s） */
  delay?: number
  className?: string
  /** 应用到每个词/字的 span className */
  tokenClassName?: string
  style?: CSSProperties
  as?: 'p' | 'h1' | 'h2' | 'h3' | 'span'
}

export function BlurReveal({
  text,
  animateBy = 'words',
  staggerMs = 60,
  duration = 0.55,
  delay = 0,
  className,
  tokenClassName,
  style,
  as: Tag = 'p',
}: BlurRevealProps) {
  const containerRef = useRef<HTMLElement | null>(null)

  const animate = useCallback((el: HTMLElement | null) => {
    if (!el) return
    const reduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches
    const tokens = el.querySelectorAll('[data-blur-token]') as NodeListOf<HTMLSpanElement>
    if (!tokens.length) return
    if (reduced) {
      tokens.forEach((t: HTMLSpanElement) => {
        t.style.opacity = '1'
        t.style.filter = 'none'
        t.style.transform = 'none'
      })
      return
    }
    gsap.set(tokens, { opacity: 0, filter: 'blur(10px)', y: 12 })
    gsap.to(tokens, {
      opacity: 1,
      filter: 'blur(0px)',
      y: 0,
      duration,
      ease: 'power3.out',
      stagger: staggerMs / 1000,
      delay,
    })
  }, [staggerMs, duration, delay])

  useEffect(() => {
    animate(containerRef.current)
  }, [text, animateBy, animate])

  const setRef = useCallback((el: HTMLElement | null) => {
    containerRef.current = el
  }, [])

  const parts = animateBy === 'words' ? text.split(' ') : text.split('')
  const tagProps = { ref: setRef, className, style: { display: 'flex', flexWrap: 'wrap' as const, gap: animateBy === 'words' ? '0.3em' : '0', ...style } }

  return (
    <Tag {...(tagProps as object)}>
      {parts.map((part, i) => (
        <span
          key={i}
          data-blur-token
          className={tokenClassName}
          style={{ display: 'inline-block', willChange: 'opacity, filter, transform' }}
        >
          {part}
        </span>
      ))}
    </Tag>
  )
}
