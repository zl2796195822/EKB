import React from 'react'
import { CaretLeft, CaretRight } from '@phosphor-icons/react'

export type PaginationProps = {
  total: number
  pageSize: number
  currentPage: number
  onChange?: (page: number) => void
  className?: string
}

function getPageNumbers(totalPages: number, current: number): Array<number | 'ellipsis'> {
  if (totalPages <= 7) {
    return Array.from({ length: totalPages }, (_, i) => i + 1)
  }
  const pages: Array<number | 'ellipsis'> = [1]
  if (current > 3) pages.push('ellipsis')
  const start = Math.max(2, current - 1)
  const end = Math.min(totalPages - 1, current + 1)
  for (let i = start; i <= end; i++) pages.push(i)
  if (current < totalPages - 2) pages.push('ellipsis')
  pages.push(totalPages)
  return pages
}

export function Pagination({
  total,
  pageSize,
  currentPage,
  onChange,
  className,
}: PaginationProps): React.ReactElement {
  const totalPages = Math.max(1, Math.ceil(total / Math.max(1, pageSize)))
  const safeCurrent = Math.min(Math.max(1, currentPage), totalPages)
  const pages = getPageNumbers(totalPages, safeCurrent)

  const handleChange = (next: number): void => {
    if (next < 1 || next > totalPages || next === safeCurrent) return
    onChange?.(next)
  }

  const prevDisabled = safeCurrent <= 1
  const nextDisabled = safeCurrent >= totalPages

  const containerCls = `pagination-pill${className ? ` ${className}` : ''}`

  return (
    <nav className={containerCls} role="navigation" aria-label="分页导航">
      <button
        type="button"
        className="pagination-btn pagination-prev"
        onClick={() => handleChange(safeCurrent - 1)}
        disabled={prevDisabled}
        aria-label="上一页"
      >
        <CaretLeft size={14} weight="bold" aria-hidden="true" />
        <span>Prev</span>
      </button>
      <ul className="pagination-list">
        {pages.map((page, idx) => {
          if (page === 'ellipsis') {
            return (
              <li key={`ellipsis-${idx}`} className="pagination-item pagination-ellipsis" aria-hidden="true">
                …
              </li>
            )
          }
          const isCurrent = page === safeCurrent
          return (
            <li key={page} className="pagination-item">
              <button
                type="button"
                className={`pagination-number${isCurrent ? ' pagination-number-active' : ''}`}
                onClick={() => handleChange(page)}
                aria-current={isCurrent ? 'page' : undefined}
                aria-label={`第 ${page} 页`}
              >
                {page}
              </button>
            </li>
          )
        })}
      </ul>
      <button
        type="button"
        className="pagination-btn pagination-next"
        onClick={() => handleChange(safeCurrent + 1)}
        disabled={nextDisabled}
        aria-label="下一页"
      >
        <span>Next</span>
        <CaretRight size={14} weight="bold" aria-hidden="true" />
      </button>
    </nav>
  )
}
