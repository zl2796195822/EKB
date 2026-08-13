import { MagnifyingGlass } from '@phosphor-icons/react'
import type { ChangeEvent } from 'react'

interface SearchFieldProps {
  readonly value: string
  readonly onChange: (value: string) => void
  readonly placeholder: string
  readonly compact?: boolean
  readonly shortcut?: string
  readonly ariaLabel: string
  readonly disabled?: boolean
  readonly disabledReason?: string
}

export function SearchField({
  value,
  onChange,
  placeholder,
  compact = false,
  shortcut,
  ariaLabel,
  disabled = false,
  disabledReason,
}: SearchFieldProps) {
  const handleChange = (event: ChangeEvent<HTMLInputElement>) => onChange(event.target.value)

  return (
    <label className={disabled ? 'v2-search-field v2-search-field--disabled' : compact ? 'v2-search-field v2-search-field--compact' : 'v2-search-field'} title={disabledReason}>
      <MagnifyingGlass size={compact ? 15 : 16} aria-hidden="true" />
      <input aria-label={ariaLabel} value={value} onChange={handleChange} placeholder={placeholder} disabled={disabled} />
      {shortcut ? <kbd>{shortcut}</kbd> : null}
    </label>
  )
}
