import {
  ArrowsHorizontal,
  ArrowsLeftRight,
  FileText,
  FolderOpen,
  FunnelSimple,
  MagnifyingGlass,
  UploadSimple,
  X,
} from '@phosphor-icons/react'
import { useCallback, useEffect, useMemo, useRef, useState, type FormEvent, type ReactNode } from 'react'
import { BatchUploadModal, DocumentTable, type DocumentTableRow } from '../components/documents'
import { StatePanel } from '../components/StatePanel'
import { StatusPill } from '../components/ui'
import type {
  AdapterError,
  DocumentDiffView,
  DocumentVersionView,
  DocumentView,
  KnowledgeBaseView,
  PageState,
  SearchHitView,
  V2PageProps,
} from '../types'

const PAGE_SIZE = 10

export function DocumentsPage({ services }: V2PageProps) {
  const [knowledgeState, setKnowledgeState] = useState<PageState>('loading')
  const [knowledgeBases, setKnowledgeBases] = useState<readonly KnowledgeBaseView[]>([])
  const [documentsState, setDocumentsState] = useState<PageState>('loading')
  const [documents, setDocuments] = useState<readonly DocumentTableRow[]>([])
  const [documentQuery, setDocumentQuery] = useState('')
  const [typeFilter, setTypeFilter] = useState('ALL')
  const [spaceFilter, setSpaceFilter] = useState('ALL')
  const [currentPage, setCurrentPage] = useState(1)
  const [selectedSearchKbId, setSelectedSearchKbId] = useState('')
  const [semanticQuery, setSemanticQuery] = useState('')
  const [searchState, setSearchState] = useState<PageState>('empty')
  const [searchHits, setSearchHits] = useState<readonly SearchHitView[]>([])
  const [notice, setNotice] = useState<string | null>(null)
  const [noticeError, setNoticeError] = useState<AdapterError | null>(null)
  const [busy, setBusy] = useState(false)
  const [detailState, setDetailState] = useState<PageState>('empty')
  const [detailDocument, setDetailDocument] = useState<DocumentView | null>(null)
  const [versionDocument, setVersionDocument] = useState<DocumentTableRow | null>(null)
  const [versionState, setVersionState] = useState<PageState>('empty')
  const [versions, setVersions] = useState<readonly DocumentVersionView[]>([])
  const [fromVersion, setFromVersion] = useState<number | null>(null)
  const [toVersion, setToVersion] = useState<number | null>(null)
  const [diffState, setDiffState] = useState<PageState>('empty')
  const [diff, setDiff] = useState<DocumentDiffView | null>(null)
  const uploadInputRef = useRef<HTMLInputElement | null>(null)
  const [batchUploadOpen, setBatchUploadOpen] = useState(false)

  const clearNotice = () => {
    setNotice(null)
    setNoticeError(null)
  }

  const showError = (error: AdapterError | undefined, fallback: string) => {
    setNotice(null)
    setNoticeError(error ?? { code: 'CLIENT_ERROR', message: fallback })
  }

  const loadDocuments = useCallback(async () => {
    setKnowledgeState('loading')
    setDocumentsState('loading')
    const knowledgeResult = await services.knowledge.list()
    setKnowledgeState(knowledgeResult.state)
    const spaces = knowledgeResult.data ?? []
    setKnowledgeBases(spaces)
    setSelectedSearchKbId((current) => current && spaces.some((space) => space.id === current) ? current : spaces[0]?.id ?? '')
    if (spaces.length === 0) {
      setDocuments([])
      setDocumentsState(knowledgeResult.state === 'permission-denied' || knowledgeResult.state === 'error' ? knowledgeResult.state : 'empty')
      return
    }
    const results = await Promise.all(spaces.map(async (space) => ({ space, result: await services.documents.list(space.id) })))
    const failed = results.find(({ result }) => result.state === 'error' || result.state === 'permission-denied')
    if (failed) {
      setDocuments([])
      setDocumentsState(failed.result.state)
      return
    }
    const merged = results.flatMap(({ space, result }) => (result.data ?? []).map((document) => ({ ...document, knowledgeBaseName: space.name })))
    setDocuments(merged)
    setDocumentsState(merged.length > 0 ? 'ready' : 'empty')
  }, [services.documents, services.knowledge])

  useEffect(() => {
    void loadDocuments()
  }, [loadDocuments])

  const filteredDocuments = useMemo(() => {
    const query = documentQuery.trim().toLowerCase()
    return documents.filter((document) => {
      const matchesQuery = !query || `${document.title} ${document.mimeType} ${document.status}`.toLowerCase().includes(query)
      const matchesSpace = spaceFilter === 'ALL' || document.kbId === spaceFilter
      const normalizedType = document.mimeType.toLowerCase()
      const matchesType = typeFilter === 'ALL' || (typeFilter === 'PDF' && normalizedType.includes('pdf')) || (typeFilter === 'TEXT' && normalizedType.startsWith('text/')) || (typeFilter === 'OFFICE' && normalizedType.includes('officedocument'))
      return matchesQuery && matchesSpace && matchesType
    })
  }, [documentQuery, documents, spaceFilter, typeFilter])
  const totalPages = Math.max(1, Math.ceil(filteredDocuments.length / PAGE_SIZE))
  const visibleDocuments = filteredDocuments.slice((currentPage - 1) * PAGE_SIZE, currentPage * PAGE_SIZE)

  useEffect(() => {
    setCurrentPage((page) => Math.min(page, totalPages))
  }, [totalPages])

  const handleUpload = async (file: File) => {
    if (!selectedSearchKbId) return
    setBusy(true)
    clearNotice()
    const result = await services.documents.upload(selectedSearchKbId, file)
    setBusy(false)
    if (!result.data || result.state !== 'ready') {
      showError(result.error, '文档上传失败')
      return
    }
    setNotice(`上传已接受：${result.data.status}，文档 ${result.data.docId}，任务 ${result.data.jobId}，trace ${result.data.traceId}。`)
    await loadDocuments()
  }

  const handleDelete = async (document: DocumentTableRow) => {
    if (!window.confirm(`确认删除文档「${document.title}」？`)) return
    setBusy(true)
    clearNotice()
    const result = await services.documents.remove(document.kbId, document.id)
    setBusy(false)
    if (result.state !== 'ready') {
      showError(result.error, '文档删除失败')
      return
    }
    setNotice('文档删除请求已完成，正在重新获取真实文档列表。')
    setSearchState('empty')
    setSearchHits([])
    await loadDocuments()
  }

  const handleRetry = async (document: DocumentTableRow) => {
    setBusy(true)
    clearNotice()
    const result = await services.documents.retry(document.kbId, document.id)
    setBusy(false)
    if (result.state !== 'ready') {
      showError(result.error, '文档重试失败')
      return
    }
    setNotice(`重试已接受：${result.data?.status ?? 'accepted'}，trace ${result.data?.traceId ?? '—'}。`)
    await loadDocuments()
  }

  const handleDetail = async (document: DocumentTableRow) => {
    setDetailState('loading')
    setDetailDocument(null)
    const result = await services.documents.get(document.kbId, document.id)
    setDetailState(result.state)
    setDetailDocument(result.data ?? null)
    if (result.error) showError(result.error, '文档详情加载失败')
  }

  const handleVersions = async (document: DocumentTableRow) => {
    setVersionDocument(document)
    setVersionState('loading')
    setVersions([])
    setDiff(null)
    setDiffState('empty')
    const result = await services.documents.listVersions(document.kbId, document.id)
    setVersionState(result.state)
    const loadedVersions = result.data ?? []
    setVersions(loadedVersions)
    setFromVersion(loadedVersions.at(-1)?.version ?? null)
    setToVersion(loadedVersions[0]?.version ?? null)
    if (result.error) showError(result.error, '版本历史加载失败')
  }

  const handleDiff = async () => {
    if (!versionDocument || fromVersion === null || toVersion === null || fromVersion === toVersion) return
    setDiffState('loading')
    const result = await services.documents.diff(versionDocument.kbId, versionDocument.id, fromVersion, toVersion)
    setDiffState(result.state)
    setDiff(result.data ?? null)
    if (result.error) showError(result.error, '版本差异加载失败')
  }

  const handleSemanticSearch = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    if (!selectedSearchKbId || !semanticQuery.trim()) return
    setSearchState('loading')
    setSearchHits([])
    const result = await services.search.search(semanticQuery.trim(), selectedSearchKbId)
    setSearchState(result.state)
    setSearchHits(result.data ?? [])
    if (result.error) showError(result.error, '检索失败')
  }

  return (
    <div className="v2-m3-documents-page" data-route-id="documents">
      <section className="v2-m3-page-heading v2-m3-global-heading" aria-labelledby="documents-page-title">
        <div>
          <p className="v2-eyebrow">KNOWLEDGE BASE / WORKSPACE</p>
          <h1 id="documents-page-title">文档中心</h1>
          <p>集中查看当前主体已授权知识库中的文档，并保留真实状态、版本和错误信息。</p>
        </div>
        <div className="v2-m3-heading-actions"><button type="button" className="v2-m3-secondary-button" disabled title="当前 API 没有批量删除端点"><FunnelSimple size={15} aria-hidden="true" />批量删除（不可用）</button><button type="button" className="v2-m3-secondary-button" onClick={() => setBatchUploadOpen(true)} disabled={!selectedSearchKbId || busy}><FolderOpen size={15} aria-hidden="true" />批量/目录上传</button><button type="button" className="v2-m3-primary-button" onClick={() => uploadInputRef.current?.click()} disabled={!selectedSearchKbId || busy}><UploadSimple size={15} aria-hidden="true" />上传文档</button><input ref={uploadInputRef} className="v2-m3-hidden-input" type="file" accept=".txt,.md,.pdf,.doc,.docx,.ppt,.pptx,.xls,.xlsx" onChange={(event) => { const file = event.target.files?.[0]; event.target.value = ''; if (file) void handleUpload(file) }} /></div>
      </section>
      {notice ? <div className="v2-m3-notice v2-m3-notice--success" role="status">{notice}</div> : null}
      {noticeError ? <div className="v2-m3-notice v2-m3-notice--error" role="alert"><span>{noticeError.message}<small>{formatErrorMeta(noticeError)}</small></span></div> : null}
      <section className="v2-m3-document-center-card" aria-label="文档列表">
        <div className="v2-m3-document-toolbar v2-m3-document-center-toolbar">
          <label className="v2-m3-search-input"><MagnifyingGlass size={15} aria-hidden="true" /><input value={documentQuery} onChange={(event) => { setDocumentQuery(event.target.value); setCurrentPage(1) }} placeholder="筛选已加载文档名称、类型或状态…" aria-label="筛选已加载文档名称、类型或状态" /><span>本地筛选</span></label>
          <label className="v2-m3-select-label"><span>类型</span><select value={typeFilter} onChange={(event) => { setTypeFilter(event.target.value); setCurrentPage(1) }}><option value="ALL">全部类型</option><option value="PDF">PDF</option><option value="TEXT">文本</option><option value="OFFICE">Office</option></select></label>
          <label className="v2-m3-select-label"><span>空间</span><select value={spaceFilter} onChange={(event) => { setSpaceFilter(event.target.value); setCurrentPage(1) }}><option value="ALL">全部空间</option>{knowledgeBases.map((space) => <option value={space.id} key={space.id}>{space.name}</option>)}</select></label>
          <label className="v2-m3-select-label"><span>语义空间</span><select value={selectedSearchKbId} onChange={(event) => setSelectedSearchKbId(event.target.value)} disabled={knowledgeState !== 'ready'}><option value="">选择空间</option>{knowledgeBases.map((space) => <option value={space.id} key={space.id}>{space.name}</option>)}</select></label>
        </div>
        <form className="v2-m3-semantic-search" onSubmit={handleSemanticSearch}><label className="v2-m3-search-input"><MagnifyingGlass size={15} aria-hidden="true" /><input value={semanticQuery} onChange={(event) => setSemanticQuery(event.target.value)} placeholder={selectedSearchKbId ? '对选定知识库执行真实语义检索…' : '先选择一个知识空间再执行语义检索'} aria-label="对选定知识库执行语义检索" /><button type="submit" disabled={!selectedSearchKbId || !semanticQuery.trim() || searchState === 'loading'}>语义检索</button></label><small>检索结果严格绑定选定知识库，不跨空间合并。</small></form>
        {searchState !== 'empty' ? <SearchResults state={searchState} query={semanticQuery} hits={searchHits} /> : null}
        {documentsState === 'error' || documentsState === 'permission-denied' ? <StatePanel state={documentsState} message="文档中心未能加载真实授权结果，未用本地样本替代。" /> : null}
        {documentsState === 'empty' && knowledgeState === 'empty' ? <StatePanel state="empty" message="当前主体没有可显示的知识库和文档。" /> : null}
        <DocumentTable documents={visibleDocuments} loading={documentsState === 'loading'} onDetail={(document) => void handleDetail(document)} onDelete={(document) => void handleDelete(document)} onRetry={(document) => void handleRetry(document)} onVersions={(document) => void handleVersions(document)} />
        <nav className="v2-m3-pagination" aria-label="文档分页"><span>共 {filteredDocuments.length} 条真实结果</span><div>{Array.from({ length: totalPages }, (_, index) => index + 1).slice(0, 7).map((page) => <button type="button" key={page} className={page === currentPage ? 'v2-m3-page-button v2-m3-page-button--active' : 'v2-m3-page-button'} onClick={() => setCurrentPage(page)} aria-current={page === currentPage ? 'page' : undefined}>{page}</button>)}</div></nav>
      </section>
      {detailDocument ? <Modal title="文档详情" onClose={() => setDetailDocument(null)}><DocumentDetail state={detailState} document={detailDocument} /></Modal> : detailState === 'loading' ? <Modal title="文档详情" onClose={() => setDetailState('empty')}><StatePanel state="loading" message="正在获取文档详情。" /></Modal> : null}
      {versionDocument ? <Modal title={`版本与 diff · ${versionDocument.title}`} onClose={() => setVersionDocument(null)}><VersionPanel state={versionState} versions={versions} diffState={diffState} diff={diff} fromVersion={fromVersion} toVersion={toVersion} onFromChange={setFromVersion} onToChange={setToVersion} onDiff={() => void handleDiff()} /></Modal> : null}
      <BatchUploadModal open={batchUploadOpen} kbId={selectedSearchKbId || null} services={services} onClose={() => setBatchUploadOpen(false)} onSuccess={() => void loadDocuments()} />
    </div>
  )
}

function SearchResults({ state, query, hits }: { readonly state: PageState; readonly query: string; readonly hits: readonly SearchHitView[] }) {
  if (state === 'loading') return <StatePanel state="loading" message={`正在执行真实语义检索：${query}`} />
  if (state === 'error' || state === 'permission-denied') return <StatePanel state={state} message="检索失败，结果未跨越选定空间。" />
  if (state === 'empty') return <StatePanel state="empty" message="选定知识库没有匹配结果。" />
  return <div className="v2-m3-search-results"><div className="v2-m3-context-heading"><strong>语义检索结果</strong><span>{hits.length} 条</span></div>{hits.map((hit) => <article key={hit.chunkId}><strong>{hit.title}</strong><span>{hit.sectionPath.join(' / ') || '未标记章节'} · {Math.round(hit.score * 100)}%</span><p>{hit.snippet}</p></article>)}</div>
}

function Modal({ title, onClose, children }: { readonly title: string; readonly onClose: () => void; readonly children: ReactNode }) {
  return <div className="v2-m3-modal-backdrop" role="presentation" onMouseDown={(event) => { if (event.currentTarget === event.target) onClose() }}><section className="v2-m3-modal" role="dialog" aria-modal="true" aria-label={title}><header><h2>{title}</h2><button type="button" className="v2-m3-icon-button" onClick={onClose} aria-label="关闭"><X size={17} aria-hidden="true" /></button></header>{children}</section></div>
}

function DocumentDetail({ state, document }: { readonly state: PageState; readonly document: DocumentView }) {
  if (state === 'loading') return <StatePanel state="loading" message="正在获取文档详情。" />
  return <div className="v2-m3-detail-grid"><div><span>名称</span><strong>{document.title}</strong></div><div><span>状态</span><StatusPill status={document.status} /></div><div><span>版本</span><strong>v{document.version}</strong></div><div><span>分块</span><strong>{document.chunkCount}</strong></div><div><span>MIME</span><strong>{document.mimeType || '—'}</strong></div><div><span>Checksum</span><strong className="v2-m3-breakable">{document.checksum || '—'}</strong></div>{document.failureReason ? <div className="v2-m3-detail-error"><span>失败原因</span><strong>{document.failureReason}</strong></div> : null}</div>
}

function VersionPanel({ state, versions, diffState, diff, fromVersion, toVersion, onFromChange, onToChange, onDiff }: { readonly state: PageState; readonly versions: readonly DocumentVersionView[]; readonly diffState: PageState; readonly diff: DocumentDiffView | null; readonly fromVersion: number | null; readonly toVersion: number | null; readonly onFromChange: (value: number) => void; readonly onToChange: (value: number) => void; readonly onDiff: () => void }) {
  if (state === 'loading') return <StatePanel state="loading" message="正在加载真实版本历史。" />
  if (state === 'permission-denied') return <StatePanel state="permission-denied" message="当前主体无权读取版本历史。" />
  if (state === 'error') return <StatePanel state="error" message="版本历史加载失败。" />
  if (versions.length === 0) return <StatePanel state="empty" message="服务端没有返回版本快照，暂不可进行 diff。" />
  return <div className="v2-m3-version-panel"><div className="v2-m3-version-list">{versions.map((version) => <div key={version.id}><strong>v{version.version}</strong><span>{version.chunkCount} 个分块</span><time dateTime={version.createdAt}>{formatDateTime(version.createdAt)}</time></div>)}</div>{versions.length < 2 ? <StatePanel state="unavailable" message="当前不到两版真实快照，diff 不可用。" /> : <><div className="v2-m3-diff-controls"><select value={fromVersion ?? ''} onChange={(event) => onFromChange(Number(event.target.value))} aria-label="对比起始版本">{versions.map((version) => <option key={version.id} value={version.version}>v{version.version}</option>)}</select><ArrowsHorizontal size={14} aria-hidden="true" /><select value={toVersion ?? ''} onChange={(event) => onToChange(Number(event.target.value))} aria-label="对比目标版本">{versions.map((version) => <option key={version.id} value={version.version}>v{version.version}</option>)}</select><button type="button" className="v2-m3-secondary-button" onClick={onDiff} disabled={fromVersion === toVersion || diffState === 'loading'}><ArrowsLeftRight size={14} aria-hidden="true" />{diffState === 'loading' ? '对比中…' : '加载 diff'}</button></div><DiffSummary state={diffState} diff={diff} /></>}</div>
}

function DiffSummary({ state, diff }: { readonly state: PageState; readonly diff: DocumentDiffView | null }) {
  if (state === 'loading') return <StatePanel state="loading" message="正在加载真实差异。" />
  if (state === 'error' || state === 'permission-denied') return <StatePanel state={state} message="版本差异不可用，未构造本地 diff。" />
  if (!diff) return null
  return <div className="v2-m3-diff-summary"><div><strong>{diff.added.length}</strong><span>新增</span></div><div><strong>{diff.removed.length}</strong><span>删除</span></div><div><strong>{(diff.changed?.length ?? 0) + (diff.unchanged?.length ?? 0)}</strong><span>服务端返回的共同/变更摘要</span></div><p>差异内容来自服务端版本摘要；未返回的正文不会在前端补造。</p></div>
}

function formatDateTime(value: string): string {
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString('zh-CN')
}

function formatErrorMeta(error: AdapterError): string {
  const status = error.status ? `HTTP ${error.status}` : ''
  const code = error.code ? `code ${error.code}` : ''
  const request = error.requestId ? `request ${error.requestId}` : ''
  const metadata = Object.entries({ ...error.details, ...error.upload })
    .filter(([, value]) => value !== undefined && value !== null && value !== '')
    .map(([key, value]) => `${key}=${Array.isArray(value) ? value.join(', ') : String(value)}`)
    .join(' · ')
  return [status, code, request, metadata].filter(Boolean).join(' · ')
}
