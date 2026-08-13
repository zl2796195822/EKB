import { BookOpen } from '@phosphor-icons/react'

interface BrandMarkProps {
  readonly compact?: boolean
}

export function BrandMark({ compact = false }: BrandMarkProps) {
  return (
    <span className={compact ? 'v2-brand-mark v2-brand-mark--compact' : 'v2-brand-mark'} aria-hidden="true">
      <BookOpen size={compact ? 16 : 19} weight="bold" />
    </span>
  )
}
