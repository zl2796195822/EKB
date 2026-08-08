import React from 'react'

export type GalaxyFormFieldProps = {
  label?: React.ReactNode
  children: React.ReactNode
  helperText?: React.ReactNode
  isError?: boolean
  htmlFor?: string
  className?: string
}

export function GalaxyFormField({
  label,
  children,
  helperText,
  isError = false,
  htmlFor,
  className,
}: GalaxyFormFieldProps): React.ReactElement {
  const errorCls = isError ? 'galaxy-form-field-error' : ''
  const cls = `galaxy-form-field ${errorCls}${className ? ` ${className}` : ''}`.trim()

  return (
    <div className={cls}>
      {label && (
        <label
          className="galaxy-form-field-label"
          {...(htmlFor ? { htmlFor } : {})}
        >
          {label}
        </label>
      )}
      <div className="galaxy-form-field-control">{children}</div>
      {helperText && (
        <p className={`galaxy-form-field-helper${isError ? ' galaxy-form-field-helper-error' : ''}`}>
          {helperText}
        </p>
      )}
    </div>
  )
}
