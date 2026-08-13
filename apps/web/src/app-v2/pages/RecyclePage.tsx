import {
  ArrowCounterClockwise,
  ChatCircleDots,
  ClockCounterClockwise,
  FileText,
  MagnifyingGlass,
  Stack,
  Trash,
  WarningCircle,
} from '@phosphor-icons/react'
import { useCallback, useEffect, useMemo, useState, type FormEvent } from 'react'
import { StatePanel } from '../components'
import { TRASH_RESOURCE_TYPES, TRASH_RETENTION_DAYS } from '../types'
import type {
  PageState,
  TrashItemView,
  TrashPageView,
  TrashResourceKind,
  V2PageProps,
} from '../types'

const PAGE_SIZE = 20

const TYPE_LABELS: Record<TrashResourceKind, string> = {
  KB: '知识库',
  DOCUMENT: '文档',
  CONVERSATION: '会话',
}

const BLOCKED_REASONS: Record<string, string> = {
  parent_deleted: '所属知识库仍在回收站，请先还原知识库。',
}

type TypeFilter = TrashResourceKind | 'ALL'

type Notice = { readonly tone: 'ok' | 'error'; readonly text: string } | null

function typeIcon(kind: TrashResourceKind) {
  if (kind === 'KB') return <Stack size={15} aria-hidden="true" />
  if (kind === 'CONVERSATION') return <ChatCircleDots size={15} aria-hidden="true" />
  return <FileText size={15} aria-hidden="true" />
}

function blockedText(item: TrashItemView): string {
  return BLOCKED_REASONS[item.blockedReason ?? ''] ?? `「${item.title}」当前不可还原。`
}

function formatTimestamp(value: string | null): string {
  if (!value) return '—'
  return value.slice(0, 19).replace('T', ' ')
}

/** expires_at 是后端算好的到期时间，这里只做"还剩几天"的展示换算。 */
function daysLeft(expiresAt: string): number | null {
  const parsed = Date.parse(expiresAt.endsWith('Z') ? expiresAt : `${expiresAt}Z`)
  if (Number.isNaN(parsed)) return null
  return Math.ceil((parsed - Date.now()) / 86_400_000)
}

function RetentionCell({ expiresAt }: { readonly expiresAt: string }) {
  const left = daysLeft(expiresAt)
  if (left === null) return <span className="v2-trash-retention">{formatTimestamp(expiresAt)}</span>
  if (left <= 0) {
    return (
      <span className="v2-trash-retention" data-tone="overdue">
        已到期
      </span>
    )
  }
  return (
    <span className="v2-trash-retention" data-tone={left <= 7 ? 'soon' : 'normal'}>
      剩 {left} 天
    </span>
  )
}

export function RecyclePage({ services }: V2PageProps) {
  const trash = services.trash
  const [typeFilter, setTypeFilter] = useState<TypeFilter>('ALL')
  const [keywordInput, setKeywordInput] = useState('')
  const [keyword, setKeyword] = useState('')
  const [offset, setOffset] = useState(0)
  const [state, setState] = useState<PageState>('loading')
  const [page, setPage] = useState<TrashPageView | null>(null)
  const [errorMessage, setErrorMessage] = useState<string | null>(null)
  const [notice, setNotice] = useState<Notice>(null)
  const [busyId, setBusyId] = useState<string | null>(null)
  const [clearing, setClearing] = useState(false)

  const load = useCallback(
    async (nextOffset: number) => {
      setState('loading')
      const result = await trash.list({
        resourceType: typeFilter === 'ALL' ? null : typeFilter,
        query: keyword,
        limit: PAGE_SIZE,
        offset: nextOffset,
      })
      setState(result.state)
      setPage(result.data ?? null)
      setErrorMessage(result.error?.message ?? null)
    },
    [keyword, trash, typeFilter],
  )

  useEffect(() => {
    void load(offset)
  }, [load, offset])

  /** 删光当前页最后一条时往前退一页，避免停在空白页。 */
  const refresh = useCallback(async () => {
    const total = page?.total ?? 0
    const nextOffset = offset > 0 && total - 1 <= offset ? Math.max(0, offset - PAGE_SIZE) : offset
    if (nextOffset !== offset) setOffset(nextOffset)
    else await load(offset)
  }, [load, offset, page])

  const applyFilter = useCallback((next: TypeFilter) => {
    setTypeFilter(next)
    setOffset(0)
    setNotice(null)
  }, [])

  const submitSearch = useCallback(
    (event: FormEvent) => {
      event.preventDefault()
      setKeyword(keywordInput.trim())
      setOffset(0)
      setNotice(null)
    },
    [keywordInput],
  )

  const handleRestore = useCallback(
    async (item: TrashItemView) => {
      if (!item.restorable) {
        setNotice({ tone: 'error', text: blockedText(item) })
        return
      }
      setBusyId(item.id)
      const result = await trash.restore(item.id)
      setBusyId(null)
      if (result.error) {
        setNotice({ tone: 'error', text: `还原「${item.title}」失败：${result.error.message}` })
        return
      }
      setNotice({ tone: 'ok', text: `已还原「${item.title}」。` })
      await refresh()
    },
    [refresh, trash],
  )

  const handlePurge = useCallback(
    async (item: TrashItemView) => {
      const confirmed =
        typeof window === 'undefined' ||
        window.confirm(`永久删除「${item.title}」？该操作会连同其子数据一起清除，且不可恢复。`)
      if (!confirmed) return
      setBusyId(item.id)
      const result = await trash.purge(item.id)
      setBusyId(null)
      if (result.error) {
        setNotice({ tone: 'error', text: `永久删除「${item.title}」失败：${result.error.message}` })
        return
      }
      setNotice({ tone: 'ok', text: `已永久删除「${item.title}」。` })
      await refresh()
    },
    [refresh, trash],
  )

  const handleClear = useCallback(async () => {
    const scope = typeFilter === 'ALL' ? '回收站全部内容' : `全部${TYPE_LABELS[typeFilter]}`
    const confirmed =
      typeof window === 'undefined' || window.confirm(`确认清空${scope}？此操作不可恢复。`)
    if (!confirmed) return
    setClearing(true)
    const result = await trash.clear(typeFilter === 'ALL' ? null : typeFilter)
    setClearing(false)
    if (result.error) {
      setNotice({ tone: 'error', text: `清空失败：${result.error.message}` })
      return
    }
    setNotice({ tone: 'ok', text: `已永久删除 ${result.purged ?? 0} 项。` })
    setOffset(0)
    await load(0)
  }, [load, trash, typeFilter])

  const counts = page?.counts ?? {}
  const total = page?.total ?? 0
  const items = page?.items ?? []
  const isBusy = state === 'loading' || clearing || busyId !== null
  const canPrev = offset > 0 && !isBusy
  const canNext = offset + PAGE_SIZE < total && !isBusy

  // counts 始终是全量分类计数（不受当前筛选影响），所以"全部"要用它们的和，
  // 不能用 total —— total 是当前筛选下的条数。
  const filters = useMemo(() => {
    const byKind = TRASH_RESOURCE_TYPES.map((kind) => ({
      id: kind as TypeFilter,
      label: TYPE_LABELS[kind],
      count: counts[kind] ?? 0,
    }))
    const all = byKind.reduce((sum, item) => sum + item.count, 0)
    return [{ id: 'ALL' as TypeFilter, label: '全部', count: all }, ...byKind]
  }, [counts])

  const emptyMessage =
    state === 'error' || state === 'permission-denied'
      ? (errorMessage ?? '回收站接口调用失败。')
      : state === 'loading'
        ? '正在读取回收站。'
        : keyword || typeFilter !== 'ALL'
          ? '当前筛选条件下没有已删除项。'
          : '回收站是空的。'

  return (
    <div className="v2-m5-page v2-m6-recycle-page">
      <header className="v2-m5-page-heading">
        <div>
          <p className="v2-eyebrow">KNOWLEDGE BASE / WORKSPACE</p>
          <h1>回收站</h1>
          <p>
            已删除的知识库、文档与会话在此暂存 {TRASH_RETENTION_DAYS} 天，数据来自真实 /trash 接口。
          </p>
        </div>
        <button
          type="button"
          className="v2-m5-secondary-button v2-trash-danger-button"
          onClick={() => void handleClear()}
          aria-busy={clearing}
        >
          <Trash size={15} aria-hidden="true" />
          {typeFilter === 'ALL' ? '清空回收站' : `清空${TYPE_LABELS[typeFilter]}`}
        </button>
      </header>

      {notice ? (
        <div
          className={`v2-m5-notice ${notice.tone === 'error' ? 'v2-m5-notice--error' : 'v2-m5-notice--success'}`}
          role={notice.tone === 'error' ? 'alert' : 'status'}
        >
          <WarningCircle size={15} aria-hidden="true" />
          <span>{notice.text}</span>
        </div>
      ) : null}

      <section className="v2-m6-retention-bar" aria-label="保留期说明">
        <div>
          <ClockCounterClockwise size={20} aria-hidden="true" />
          <div>
            <strong>保留期 {TRASH_RETENTION_DAYS} 天</strong>
            <p>
              后端在删除时写入 expires_at，本页按该字段计算剩余天数。调度器会按 30 天窗口幂等领取到期任务并执行永久删除；
              本地开发进程每 60 秒运行一次。
            </p>
          </div>
        </div>
        <form className="v2-trash-search" role="search" onSubmit={submitSearch}>
          <MagnifyingGlass size={15} aria-hidden="true" />
          <input
            type="search"
            value={keywordInput}
            placeholder="按名称搜索"
            aria-label="按名称搜索回收站条目"
            onChange={(event) => setKeywordInput(event.target.value)}
          />
          <button type="submit" className="v2-m5-secondary-button">
            搜索
          </button>
        </form>
      </section>

      <section className="v2-m6-recycle-card" aria-labelledby="m6-recycle-title">
        <div className="v2-m5-panel-intro">
          <div>
            <h2 id="m6-recycle-title">已删除项</h2>
            <p>还原会把资源恢复到原位置；永久删除会连同分片、版本、消息等下游数据一并清除。</p>
          </div>
          <ArrowCounterClockwise size={20} aria-hidden="true" />
        </div>

        <div className="v2-trash-filters" role="tablist" aria-label="按资源类型筛选">
          {filters.map((filter) => (
            <button
              type="button"
              key={filter.id}
              role="tab"
              aria-selected={typeFilter === filter.id}
              className={typeFilter === filter.id ? 'is-active' : ''}
              onClick={() => applyFilter(filter.id)}
            >
              {filter.label}
              <span className="v2-trash-filter-count">{filter.count}</span>
            </button>
          ))}
        </div>

        <div className="v2-m5-table-wrap">
          <table className="v2-m5-table">
            <thead>
              <tr>
                <th scope="col">名称</th>
                <th scope="col">类型</th>
                <th scope="col">所属空间</th>
                <th scope="col">删除时间</th>
                <th scope="col">剩余保留</th>
                <th scope="col">操作</th>
              </tr>
            </thead>
            <tbody>
              {state === 'ready' && items.length > 0 ? (
                items.map((item) => (
                  <tr key={item.id}>
                    <td>
                      <span className="v2-trash-title">{item.title || item.resourceId}</span>
                      {item.blockedReason ? (
                        <small className="v2-trash-blocked">{blockedText(item)}</small>
                      ) : null}
                    </td>
                    <td>
                      <span className="v2-trash-type">
                        {typeIcon(item.resourceType)}
                        {TYPE_LABELS[item.resourceType]}
                      </span>
                    </td>
                    <td>{item.parentTitle ?? '—'}</td>
                    <td>{formatTimestamp(item.deletedAt)}</td>
                    <td>
                      <RetentionCell expiresAt={item.expiresAt} />
                    </td>
                    <td>
                      <div className="v2-m6-row-actions">
                        <button
                          type="button"
                          className="v2-m5-secondary-button"
                          aria-disabled={!item.restorable}
                          aria-busy={busyId === item.id}
                          title={item.restorable ? undefined : blockedText(item)}
                          onClick={() => void handleRestore(item)}
                        >
                          <ArrowCounterClockwise size={14} aria-hidden="true" />
                          还原
                        </button>
                        <button
                          type="button"
                          className="v2-m5-secondary-button v2-trash-danger-button"
                          aria-busy={busyId === item.id}
                          onClick={() => void handlePurge(item)}
                        >
                          <Trash size={14} aria-hidden="true" />
                          永久删除
                        </button>
                      </div>
                    </td>
                  </tr>
                ))
              ) : (
                <tr>
                  <td colSpan={6} className="v2-m6-empty-cell">
                    <StatePanel state={state === 'ready' ? 'empty' : state} message={emptyMessage} />
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>

        <div className="v2-m6-recycle-footer">
          <span>
            共 <strong>{total}</strong> 项
            {total > 0 ? `（第 ${Math.floor(offset / PAGE_SIZE) + 1} 页）` : ''}
          </span>
          <div className="v2-m6-row-actions">
            <button
              type="button"
              className="v2-m5-secondary-button"
              aria-busy={state === 'loading'}
              onClick={() => void load(offset)}
            >
              刷新
            </button>
            <button
              type="button"
              className="v2-m5-secondary-button"
              aria-disabled={!canPrev}
              onClick={() => {
                if (canPrev) setOffset(Math.max(0, offset - PAGE_SIZE))
              }}
            >
              上一页
            </button>
            <button
              type="button"
              className="v2-m5-secondary-button"
              aria-disabled={!canNext}
              onClick={() => {
                if (canNext) setOffset(offset + PAGE_SIZE)
              }}
            >
              下一页
            </button>
          </div>
        </div>
      </section>
    </div>
  )
}
