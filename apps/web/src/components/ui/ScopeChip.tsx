import React from 'react'
import {
  SquaresFour,
  FileText,
  Question,
  ChatCircleText,
  ChatTeardropText,
  type IconProps,
} from '@phosphor-icons/react'

export type ScopeChipValue = 'all' | 'document' | 'qa' | 'chat' | 'faq'

export type ScopeChipItem = {
  label: string
  value: ScopeChipValue
  count?: number
}

export type ScopeChipProps = {
  items: ScopeChipItem[]
  value?: ScopeChipValue
  defaultValue?: ScopeChipValue
  onChange?: (value: ScopeChipValue) => void
  className?: string
}

const ICON_MAP: Record<ScopeChipValue, React.ComponentType<IconProps>> = {
  all: SquaresFour,
  document: FileText,
  qa: Question,
  chat: ChatCircleText,
  faq: ChatTeardropText,
}

export function ScopeChip({
  items,
  value,
  defaultValue,
  onChange,
  className,
}: ScopeChipProps): React.ReactElement {
  const [innerValue, setInnerValue] = React.useState<ScopeChipValue>(
    value ?? defaultValue ?? 'all',
  )

  React.useEffect(() => {
    if (value !== undefined) {
      setInnerValue(value)
    }
  }, [value])

  const active = value !== undefined ? value : innerValue

  const handleClick = (next: ScopeChipValue): void => {
    if (value === undefined) {
      setInnerValue(next)
    }
    onChange?.(next)
  }

  const containerCls = `scope-chip-group${className ? ` ${className}` : ''}`

  return (
    <div className={containerCls} role="tablist" aria-label="检索范围">
      {items.map((item) => {
        const Icon = ICON_MAP[item.value]
        const isActive = active === item.value
        const chipCls = `chip scope-chip${isActive ? ' scope-chip-active' : ''}`
        return (
          <button
            key={item.value}
            type="button"
            role="tab"
            aria-selected={isActive}
            className={chipCls}
            onClick={() => handleClick(item.value)}
          >
            <Icon className="scope-chip-icon" size={14} weight="fill" aria-hidden="true" />
            <span className="scope-chip-label">{item.label}</span>
            {typeof item.count === 'number' && (
              <span className="scope-chip-count">{item.count}</span>
            )}
          </button>
        )
      })}
    </div>
  )
}
