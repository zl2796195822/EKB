import {
  useState,
  useMemo,
  type FC,
  type FormEvent,
} from 'react'
import { MagnifyingGlass } from '@phosphor-icons/react'
import { HitCard, type CornerTagVariant } from '../components/ui'
import {
  ScopeChip,
  type ScopeChipValue,
} from '../components/ui/ScopeChip'
import { Pagination } from '../components/ui/Pagination'
import type {
  KnowledgeBase,
  SearchResult,
} from '../types/api'
import type { HitCardType } from '../components/ui/HitCard'

export interface SearchPageProps {
  searchResults: SearchResult[]
  knowledgeBases: KnowledgeBase[]
  selectedKbId: string
  searchQuery: string
  searchPageNum: number
  searchPageSize: number
  isLoading: boolean
  onChangeQuery: (v: string) => void
  onSubmitSearch: (e: FormEvent<HTMLFormElement>) => void
  onChangeKb: (kbId: string) => void
  onChangePage: (n: number) => void
}

type TimeRangeValue = 'all' | '24h' | '7d' | '30d'
type DocTypeValue = 'PDF' | 'Docx' | 'PPTx' | 'MD' | 'TXT' | 'HTML'
type SortValue = 'relevance' | 'latest'

const TIME_RANGE_OPTIONS: Array<{ label: string; value: TimeRangeValue }> = [
  { label: '全部时间', value: 'all' },
  { label: '24h 内', value: '24h' },
  { label: '7 天内', value: '7d' },
  { label: '30 天内', value: '30d' },
]

const DOC_TYPE_OPTIONS: DocTypeValue[] = ['PDF', 'Docx', 'PPTx', 'MD', 'TXT', 'HTML']

const SCOPE_ITEMS: Array<{ label: string; value: ScopeChipValue }> = [
  { label: '全部', value: 'all' },
  { label: '文档', value: 'document' },
  { label: '问答', value: 'qa' },
  { label: '对话', value: 'chat' },
  { label: '常见问题', value: 'faq' },
]

const DOC_TYPE_EXT_MAP: Record<string, DocTypeValue> = {
  pdf: 'PDF',
  doc: 'Docx',
  docx: 'Docx',
  ppt: 'PPTx',
  pptx: 'PPTx',
  md: 'MD',
  markdown: 'MD',
  txt: 'TXT',
  htm: 'HTML',
  html: 'HTML',
}

function inferDocType(title: string): string | undefined {
  const m = title.match(/\.([a-zA-Z0-9]+)$/)
  if (!m) return undefined
  const ext = m[1].toLowerCase()
  return DOC_TYPE_EXT_MAP[ext]
}

function inferHitType(title: string, kbName: string): HitCardType {
  const lower = `${title} ${kbName}`.toLowerCase()
  if (lower.includes('faq') || lower.includes('常见问题')) return 'faq'
  if (lower.includes('对话') || lower.includes('chat') || lower.includes('conversation')) return 'chat'
  if (lower.includes('问答') || lower.includes('question') || lower.includes('qa')) return 'qa'
  return 'document'
}

function getCornerTag(score: number): CornerTagVariant | undefined {
  if (score >= 0.90) return 'top'
  if (score >= 0.85) return 'authority'
  return undefined
}

const SearchPage: FC<SearchPageProps> = (props) => {
  const {
    searchResults,
    knowledgeBases,
    selectedKbId,
    searchQuery,
    searchPageNum,
    searchPageSize,
    isLoading,
    onChangeQuery,
    onSubmitSearch,
    onChangeKb,
    onChangePage,
  } = props

  const [scopeValue, setScopeValue] = useState<ScopeChipValue>('all')
  const [timeRange, setTimeRange] = useState<TimeRangeValue>('all')
  const [docTypes, setDocTypes] = useState<DocTypeValue[]>([])
  const [threshold, setThreshold] = useState(0.70)
  const [sortBy, setSortBy] = useState<SortValue>('relevance')
  const [searchMs, setSearchMs] = useState<number | null>(null)

  const kbNameMap = useMemo(() => {
    const map: Record<string, string> = {}
    for (const kb of knowledgeBases) {
      map[kb.id] = kb.name
    }
    return map
  }, [knowledgeBases])

  const filteredResults = useMemo(() => {
    let list = searchResults
    list = list.filter((r) => r.score >= threshold)
    if (sortBy === 'latest') {
      list = [...list].sort((a, b) => {
        const ta = a.updated_at ? new Date(a.updated_at).getTime() : 0
        const tb = b.updated_at ? new Date(b.updated_at).getTime() : 0
        return tb - ta
      })
    }
    return list
  }, [searchResults, threshold, sortBy])

  const pagedResults = useMemo(() => {
    const start = (searchPageNum - 1) * searchPageSize
    return filteredResults.slice(start, start + searchPageSize)
  }, [filteredResults, searchPageNum, searchPageSize])

  const toggleDocType = (t: DocTypeValue) => {
    setDocTypes((curr) =>
      curr.includes(t) ? curr.filter((x) => x !== t) : [...curr, t],
    )
  }

  return (
    <section className="search-page-shell">
      <div className="content-rail search-align-lock">
        <div className="search-page-header stagger in">
          <span className="page-chip">TASK 4 · 检索中心</span>
          <h1>检索中心</h1>
          <p className="search-page-sub">
            从授权知识库中快速定位原文段落，每条命中都能回溯到来源
          </p>
        </div>
      </div>

      <div className="content-rail search-align-lock">
        <form className="search-box-row galaxy-form-field-control" onSubmit={onSubmitSearch}>
          <input
            type="text"
            className="search-box-input"
            placeholder="输入关键词或自然语言问题，Enter 检索"
            value={searchQuery}
            onChange={(e) => onChangeQuery(e.target.value)}
            disabled={isLoading}
          />
          <button
            type="submit"
            className="search-box-btn primary-button"
            disabled={isLoading || !searchQuery.trim()}
          >
            <MagnifyingGlass size={18} weight="fill" aria-hidden="true" />
            <span>检索</span>
          </button>
        </form>
      </div>

      <div className="content-rail search-align-lock">
        <div className="search-scope-row">
          <ScopeChip
            items={SCOPE_ITEMS}
            value={scopeValue}
            onChange={(v) => setScopeValue(v)}
          />
        </div>
      </div>

      <div className="content-rail search-align-lock">
        <div className="search-filters">
          <div className="search-filter-col">
            <label className="search-filter-label">时间范围</label>
            <select
              className="search-filter-select"
              value={timeRange}
              onChange={(e) => setTimeRange(e.target.value as TimeRangeValue)}
            >
              {TIME_RANGE_OPTIONS.map((opt) => (
                <option key={opt.value} value={opt.value}>{opt.label}</option>
              ))}
            </select>
          </div>

          <div className="search-filter-col">
            <label className="search-filter-label">文档类型</label>
            <div className="search-filter-chips">
              {DOC_TYPE_OPTIONS.map((dt) => {
                const active = docTypes.includes(dt)
                return (
                  <button
                    key={dt}
                    type="button"
                    className={`chip search-doctype-chip${active ? ' is-active' : ''}`}
                    onClick={() => toggleDocType(dt)}
                  >
                    {dt}
                  </button>
                )
              })}
            </div>
          </div>

          <div className="search-filter-col">
            <label className="search-filter-label">知识库</label>
            <select
              className="search-filter-select"
              value={selectedKbId}
              onChange={(e) => onChangeKb(e.target.value)}
            >
              {knowledgeBases.map((kb) => (
                <option key={kb.id} value={kb.id}>{kb.name}</option>
              ))}
            </select>
          </div>

          <div className="search-filter-col">
            <label className="search-filter-label">
              相关性阈值：≥ {threshold.toFixed(2)}
            </label>
            <input
              type="range"
              className="search-filter-range"
              min={0.5}
              max={1.0}
              step={0.05}
              value={threshold}
              onChange={(e) => setThreshold(parseFloat(e.target.value))}
            />
          </div>
        </div>
      </div>

      <div className="content-rail search-align-lock">
        <div className="result-summary-row search-result-summary">
          <div className="result-summary-count">
            <span>共</span>
            <strong>{filteredResults.length}</strong>
            <span>条命中</span>
            {searchMs !== null && <span className="search-result-time">· 耗时 {searchMs} ms</span>}
            {isLoading && <span className="search-result-time">· 检索中…</span>}
          </div>
          <div className="result-summary-scope">
            <select
              className="search-sort-select"
              value={sortBy}
              onChange={(e) => setSortBy(e.target.value as SortValue)}
            >
              <option value="relevance">最相关</option>
              <option value="latest">最新</option>
            </select>
          </div>
        </div>
      </div>

      <div className="content-rail search-align-lock">
        <div className="search-hit-list">
          {pagedResults.length === 0 && (
            <div className="search-empty">
              <div className="search-empty-icon">
                <MagnifyingGlass size={28} weight="fill" />
              </div>
              <h3>暂无命中</h3>
              <p>
                {searchQuery.trim()
                  ? '没有找到匹配的检索结果，尝试调整关键词或降低相关性阈值。'
                  : '输入关键词开始检索。'}
              </p>
            </div>
          )}
          {pagedResults.map((result) => {
            const docTitle = result.title
            const kbLabel = kbNameMap[result.kb_id]
            const docType = inferDocType(docTitle)
            const hitType = inferHitType(docTitle, kbLabel ?? '')
            const cornerTag = getCornerTag(result.score)

            const queryTokens = searchQuery
              .split(/\s+/)
              .map((t) => t.trim())
              .filter(Boolean)
            const titleTokens = docTitle
              .split(/\s+/)
              .map((t) => t.trim())
              .filter(Boolean)
              .sort((a, b) => b.length - a.length)
              .slice(0, 3)
            const highlights: string[] = []
            for (const t of queryTokens) if (!highlights.includes(t)) highlights.push(t)
            for (const t of titleTokens) if (!highlights.includes(t)) highlights.push(t)
            if (highlights.length < 2) {
              const fallbackWords = (result.snippet ?? '')
                .split(/\s+/)
                .map((t) => t.trim())
                .filter((w) => w.length >= 3)
                .sort((a, b) => b.length - a.length)
              for (const w of fallbackWords) {
                if (!highlights.includes(w)) highlights.push(w)
                if (highlights.length >= 2) break
              }
            }

            return (
              <HitCard
                key={result.chunk_id}
                type={hitType}
                title={docTitle}
                score={result.score}
                snippet={result.snippet ?? ''}
                highlights={highlights}
                updatedAt={result.updated_at}
                sourceLabel={result.section_path?.[result.section_path.length - 1] || undefined}
                kbLabel={kbLabel}
                docType={docType}
                cornerTag={cornerTag}
                onViewOriginal={() => {
                  const url = (result as SearchResult & { source_url?: string }).source_url
                  if (url) {
                    window.open(url, '_blank', 'noopener,noreferrer')
                  } else {
                    alert('原文链接暂不可用')
                  }
                }}
                onShare={undefined}
                onMore={undefined}
              />
            )
          })}
        </div>
      </div>

      <div className="content-rail search-align-lock">
        <div className="search-pagination-wrap">
          <Pagination
            total={filteredResults.length}
            pageSize={searchPageSize}
            currentPage={searchPageNum}
            onChange={onChangePage}
          />
        </div>
      </div>
    </section>
  )
}

export default SearchPage
