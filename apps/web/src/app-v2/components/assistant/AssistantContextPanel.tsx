import { ArrowSquareOut, BookOpenText, CaretDown, CaretRight, CheckCircle, FileText, Gauge, MagnifyingGlass, Plus, Sparkle } from '@phosphor-icons/react'
import { useEffect, useState } from 'react'
import { SearchField } from '../ui'
import { StatePanel } from '../StatePanel'
import { APP_V2_SESSION_STORAGE_KEY } from '../../adapters/auth'
import type { DocumentView, KnowledgeBaseView, PageState, SearchHitView, CitationView } from '../../types'

const CHAT_API_BASE = (import.meta as ImportMeta & { readonly env?: { readonly VITE_API_BASE_URL?: string } }).env?.VITE_API_BASE_URL ?? '/api/v1'

/** GET /chat/turns/{turn_id}/context 返回的安全诊断信息（spec 02 §11.2）。 */
interface TurnContextInfo {
  readonly available: boolean
  readonly manifest_hash?: string
  readonly available_input_tokens?: number
  readonly used_input_tokens?: number
  readonly reserved_output_tokens?: number
  readonly provider_safety_margin?: number
  readonly compaction_summary_id?: string | null
  readonly included_message_count?: number
  readonly dropped_message_count?: number
  readonly evidence_count?: number
  readonly compaction_strategy?: string
}

function readAuthToken(): string {
  try {
    const raw = window.sessionStorage.getItem(APP_V2_SESSION_STORAGE_KEY)
    if (!raw) return ''
    const parsed = JSON.parse(raw) as { accessToken?: unknown }
    return typeof parsed.accessToken === 'string' ? parsed.accessToken : ''
  } catch {
    return ''
  }
}

interface AssistantContextPanelProps {
  readonly open: boolean
  readonly selectedKnowledgeBase: KnowledgeBaseView | null
  readonly citations: readonly CitationView[]
  readonly searchQuery: string
  readonly searchState: PageState
  readonly searchHits: readonly SearchHitView[]
  readonly previewState: PageState
  readonly previewDocument: DocumentView | null
  /** PH4-6：当前诊断所用 turn_id（最后一条 assistant 消息的 turn_id，或当前流 turn）。 */
  readonly turnId?: string
  readonly onSearchQueryChange: (value: string) => void
  readonly onSearch: () => void
  readonly onOpenCitation: (documentId: string) => void
  readonly onOpenSearchHit: (documentId: string) => void
}

export function AssistantContextPanel({
  open,
  selectedKnowledgeBase,
  citations,
  searchQuery,
  searchState,
  searchHits,
  previewState,
  previewDocument,
  turnId,
  onSearchQueryChange,
  onSearch,
  onOpenCitation,
  onOpenSearchHit,
}: AssistantContextPanelProps) {
  const className = open ? 'v2-m4-context v2-m4-context--mobile-open' : 'v2-m4-context'

  // PH4-6 Context 诊断面板：默认折叠，展开且有 turnId 时拉取 context manifest。
  const [diagOpen, setDiagOpen] = useState(false)
  const [diagData, setDiagData] = useState<TurnContextInfo | null>(null)
  const [diagState, setDiagState] = useState<PageState>('empty')

  useEffect(() => {
    if (!diagOpen || !turnId) {
      setDiagData(null)
      setDiagState('empty')
      return
    }
    let cancelled = false
    setDiagState('loading')
    void (async () => {
      const token = readAuthToken()
      if (!token) {
        if (!cancelled) setDiagState('error')
        return
      }
      try {
        const resp = await fetch(`${CHAT_API_BASE}/chat/turns/${encodeURIComponent(turnId)}/context`, {
          headers: { Authorization: `Bearer ${token}` },
        })
        if (!resp.ok || cancelled) {
          if (!cancelled) setDiagState('error')
          return
        }
        const data = (await resp.json()) as TurnContextInfo
        if (cancelled) return
        setDiagData(data)
        setDiagState(data.available ? 'ready' : 'empty')
      } catch {
        if (!cancelled) setDiagState('error')
      }
    })()
    return () => { cancelled = true }
  }, [diagOpen, turnId])

  return (
    <aside className={className} aria-label="知识库上下文">
      <div className="v2-m4-context-title">
        <div>
          <p className="v2-eyebrow">CONTEXT</p>
          <h2>知识库上下文</h2>
        </div>
        <Sparkle size={17} aria-hidden="true" />
      </div>
      <section className="v2-m4-context-card">
        <div className="v2-m4-context-card-heading">
          <BookOpenText size={16} aria-hidden="true" />
          <span>当前知识库</span>
          {selectedKnowledgeBase ? <CheckCircle size={15} aria-hidden="true" /> : <small className="v2-m4-context-disconnected">未连接</small>}
        </div>
        {selectedKnowledgeBase ? (
          <>
            <strong>{selectedKnowledgeBase.name}</strong>
            <small>{selectedKnowledgeBase.documentCount} 个文档 · {selectedKnowledgeBase.role}</small>
          </>
        ) : (
          <StatePanel state="empty" message="发送前必须选择一个授权知识库。" />
        )}
      </section>

      <section className="v2-m4-context-section">
        <div className="v2-m4-section-heading"><h3>已用文档</h3><span>{citations.length}</span></div>
        {citations.length === 0 ? (
          <StatePanel state="empty" message="当前回答还没有引用。" />
        ) : (
          <div className="v2-m4-citation-list">
            {citations.map((citation) => (
              <button type="button" className="v2-m4-document-link" key={citation.citationId} onClick={() => onOpenCitation(citation.documentId)}>
                <FileText size={15} aria-hidden="true" />
                <span><strong>{citation.title}</strong><small>{citation.sectionPath.join(' / ') || '未提供章节'}</small></span>
              </button>
            ))}
          </div>
        )}
      </section>

      <section className="v2-m4-context-section">
        <div className="v2-m4-section-heading"><h3>相关知识</h3><Sparkle size={14} aria-hidden="true" /></div>
        <div className="v2-m4-context-search">
          <SearchField value={searchQuery} onChange={onSearchQueryChange} placeholder="在当前知识库检索" ariaLabel="搜索当前知识库" compact disabled={!selectedKnowledgeBase} disabledReason={!selectedKnowledgeBase ? '请先选择授权知识库' : undefined} />
          <button type="button" className="v2-m4-search-button" onClick={onSearch} disabled={!selectedKnowledgeBase || !searchQuery.trim() || searchState === 'loading'} aria-label="搜索相关知识">
            <MagnifyingGlass size={14} aria-hidden="true" />
          </button>
        </div>
        {searchState === 'loading' ? <StatePanel state="loading" message="正在检索当前授权知识库。" /> : null}
        {searchState === 'error' || searchState === 'permission-denied' ? <StatePanel state={searchState} message="相关知识加载失败。" /> : null}
        {searchState === 'empty' && searchQuery.trim() ? <StatePanel state="empty" message="当前知识库没有返回匹配结果。" /> : null}
        {searchState === 'empty' && !searchQuery.trim() ? <StatePanel state="empty" message="输入问题后查看当前授权知识库的相关片段。" /> : null}
        {searchHits.length > 0 ? (
          <div className="v2-m4-search-results">
            {searchHits.map((hit) => (
              <button type="button" key={hit.chunkId} className="v2-m4-search-result" onClick={() => onOpenSearchHit(hit.documentId)}>
                <span><strong>{hit.title}</strong><small>{hit.sectionPath.join(' / ') || '相关片段'}</small><em>{hit.snippet}</em></span>
                <small>{Math.round(hit.score * 100)}%</small>
              </button>
            ))}
          </div>
        ) : null}
      </section>

      <section className="v2-m4-context-section v2-m4-preview-section">
        <div className="v2-m4-section-heading"><h3>文档预览</h3><FileText size={14} aria-hidden="true" /></div>
        {previewState === 'loading' ? <StatePanel state="loading" message="正在加载真实文档元数据。" /> : null}
        {previewState === 'error' || previewState === 'permission-denied' ? <StatePanel state={previewState} message="文档详情加载失败。" /> : null}
        {previewState === 'empty' ? <StatePanel state="empty" message="点击引用或检索结果查看文档元数据。" /> : null}
        {previewDocument ? (
          <div className="v2-m4-preview-card">
            <strong>{previewDocument.title}</strong>
            <dl>
              <div><dt>状态</dt><dd>{previewDocument.status}</dd></div>
              <div><dt>版本</dt><dd>v{previewDocument.version}</dd></div>
              <div><dt>分块</dt><dd>{previewDocument.chunkCount}</dd></div>
              <div><dt>更新时间</dt><dd>{new Date(previewDocument.updatedAt).toLocaleString('zh-CN')}</dd></div>
            </dl>
            <div className="v2-m4-preview-actions">
              <button type="button" disabled title="当前 API 没有正文/原文打开端点"><ArrowSquareOut size={14} aria-hidden="true" />打开原文</button>
              <button type="button" disabled title="当前 API 没有加入引用端点"><Plus size={14} aria-hidden="true" />加入引用</button>
            </div>
          </div>
        ) : null}
      </section>

      <section className="v2-m4-context-section v2-m4-context-diag">
        <button
          type="button"
          className="v2-m4-section-heading v2-m4-context-diag-toggle"
          aria-expanded={diagOpen}
          onClick={() => setDiagOpen((value) => !value)}
          disabled={!turnId}
          title={turnId ? '展开查看当前 turn 的 context 诊断（token 预算 / 压缩范围 / 组件计数）' : '发起问答后可查看当前 turn 的 context 诊断'}
        >
          {diagOpen ? <CaretDown size={14} aria-hidden="true" /> : <CaretRight size={14} aria-hidden="true" />}
          <h3>Context 诊断</h3>
          <Gauge size={14} aria-hidden="true" />
        </button>
        {diagOpen ? (
          turnId ? (
            <>
              {diagState === 'loading' ? <StatePanel state="loading" message="正在加载当前 turn 的 context 诊断。" /> : null}
              {diagState === 'error' || diagState === 'permission-denied' ? <StatePanel state={diagState} message="Context 诊断加载失败。" /> : null}
              {diagState === 'empty' ? <StatePanel state="empty" message="当前 turn 暂无 context 诊断记录。" /> : null}
              {diagState === 'ready' && diagData ? (
                <div className="v2-m4-preview-card">
                  <strong>context manifest</strong>
                  <dl>
                    <div><dt>上下文窗口</dt><dd>{diagData.available_input_tokens ?? 0}</dd></div>
                    <div><dt>已用输入 token</dt><dd>{diagData.used_input_tokens ?? 0}</dd></div>
                    <div><dt>预留输出 token</dt><dd>{diagData.reserved_output_tokens ?? 0}</dd></div>
                    <div><dt>提供方安全余量</dt><dd>{diagData.provider_safety_margin ?? 0}</dd></div>
                    <div><dt>压缩策略</dt><dd>{diagData.compaction_strategy ?? 'none'}</dd></div>
                    <div><dt>压缩摘要</dt><dd>{diagData.compaction_summary_id || '无'}</dd></div>
                    <div><dt>纳入消息数</dt><dd>{diagData.included_message_count ?? 0}</dd></div>
                    <div><dt>丢弃消息数</dt><dd>{diagData.dropped_message_count ?? 0}</dd></div>
                    <div><dt>证据片段数</dt><dd>{diagData.evidence_count ?? 0}</dd></div>
                    <div><dt>manifest_hash</dt><dd>{diagData.manifest_hash || '—'}</dd></div>
                  </dl>
                </div>
              ) : null}
            </>
          ) : (
            <StatePanel state="empty" message="发起问答后可查看当前 turn 的 context 诊断。" />
          )
        ) : null}
      </section>
    </aside>
  )
}
