import React from 'react'
import { ArrowUp, ArrowDown } from '@phosphor-icons/react'
import { CountUpValue } from './animated/CountUpValue'

export type MetricCardProps = {
  label: string
  value: string | number
  delta?: number
  className?: string
}

export function MetricCard({ label, value, delta, className }: MetricCardProps): React.ReactElement {
  const hasDelta = typeof delta === 'number' && isFinite(delta)
  const deltaPositive = hasDelta ? (delta as number) >= 0 : false
  const deltaSign = deltaPositive ? '+' : ''
  const cls = `metric-card galaxy-card galaxy-card-default${className ? ` ${className}` : ''}`

  const numericValue = typeof value === 'number' ? value : parseFloat(String(value))
  const isNumeric = !isNaN(numericValue)

  return (
    <div className={cls}>
      <div className="galaxy-card-inner">
        <div className="metric-card-body">
          <span className="metric-card-label">{label}</span>
          <span className="metric-card-value">
            {isNumeric ? (
              <CountUpValue value={numericValue} duration={1.4} />
            ) : (
              value
            )}
          </span>
          {hasDelta && (
            <span
              className={`metric-card-delta metric-card-delta-${
                deltaPositive ? 'up' : 'down'
              }`}
              aria-label={deltaPositive ? '上升' : '下降'}
            >
              {deltaPositive ? (
                <ArrowUp size={12} weight="bold" aria-hidden="true" />
              ) : (
                <ArrowDown size={12} weight="bold" aria-hidden="true" />
              )}
              {deltaSign}
              {Math.abs(delta as number)}
              %
            </span>
          )}
        </div>
      </div>
    </div>
  )
}
