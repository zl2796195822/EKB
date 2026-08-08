import React from 'react'

export type CornerTagVariant = 'top' | 'authority' | 'restricted' | 'fresh'

export type CornerTagProps = {
  variant: CornerTagVariant
  className?: string
}

const LABEL_MAP: Record<CornerTagVariant, string> = {
  top: 'TOP',
  authority: '权威',
  restricted: '权限受限',
  fresh: '新',
}

export function CornerTag({ variant, className }: CornerTagProps): React.ReactElement {
  const cls = `corner-tag corner-tag-${variant}${className ? ` ${className}` : ''}`
  return (
    <span className={cls} role="status">
      {LABEL_MAP[variant]}
    </span>
  )
}
