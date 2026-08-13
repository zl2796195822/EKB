import {
  ArrowClockwise,
  ArrowsHorizontal,
  ArrowsLeftRight,
  FileText,
  FolderOpen,
  FunnelSimple,
  MagnifyingGlass,
  Stop,
  UploadSimple,
  X,
} from '@phosphor-icons/react'
import { useCallback, useEffect, useMemo, useRef, useState, type FormEvent, type ReactNode } from 'react'
import { BatchUploadModal, DocumentTable, UPLOAD_CENTER_STORAGE_KEY, type DocumentTableRow } from '../components/documents'
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
  UploadBatchItemView,
  UploadBatchView,
  V2PageProps,
} from '../types'

const PAGE_SIZE = 10

function formatBytes(n: number): string {
  if (n <= 0) return '0 B'
  const units = ['B', 'KB', 'MB', 'GB', 'TB']
  let value = n
  let index = 0
  while (value >= 1024 && index < units.length - 1) {
    value /= 1024
    index += 1
  }
  return `${value.toFixed(value >= 10 || index === 0 ? 0 : 2)} ${units[index]}`
}

export function DocumentsPage({ services }: V2PageProps) {
  const [knowledgeState, setKnowledgeState] = useState<PageState>('loading')
  const [knowledgeBases, setKnowledgeBases] = useState<readonly KnowledgeBaseView[]>([])
  const [documentsState, setDocumentsState] = useState<PageState>('loading')
  const [documents, setDocuments] = useState<readonly DocumentTableRow[]>([])
  const [selectedDocumentKeys, setSelectedDocumentKeys] = useState<ReadonlySet<string>>(new Set())
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
  const [uploadCenterState, setUploadCenterState] = useState<PageState>('loading')
  const [uploadBatches, setUploadBatches] = useState<readonly UploadBatchView[]>([])
  const [uploadErrors, setUploadErrors] = useState<readonly AdapterError[]>([])
  const [uploadActionKey, setUploadActionKey] = useState<string | null>(null)

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

  const loadUploadCenter = useCallback(async () => {
    setUploadCenterState('loading')
    const errors: AdapterError[] = []
    let batchIds: string[] = []
    try {
      const stored = JSON.parse(window.localStorage.getItem(UPLOAD_CENTER_STORAGE_KEY) ?? '[]') as unknown
      if (Array.isArray(stored)) {
        batchIds = stored
          .map((value) => value && typeof value === 'object' && 'batchId' in value ? (value as { batchId?: unknown }).batchId : null)
          .filter((value): value is string => typeof value === 'string' && value.length > 0 && value.length <= 128)
      }
    } catch {
      errors.push({ code: 'UPLOAD_CENTER_STORAGE_INVALID', message: '上传中心本地索引不可读，未使用本地样本替代。' })
    }

    const listResult = await services.documents.listUploadBatches()
    if (listResult.error) errors.push(listResult.error)
    const serverBatches = (listResult.data ?? []).slice(0, 30)
    const knownIds = new Set(serverBatches.map((batch) => batch.id))
    const fallbackIds = Array.from(new Set(batchIds))
      .slice(-30)
      .filter((batchId) => !knownIds.has(batchId))
      .slice(0, Math.max(0, 30 - serverBatches.length))
    const fallbackResults = await Promise.all(fallbackIds.map((batchId) => services.documents.getUploadBatch(batchId)))
    const fallbackBatches = fallbackResults.flatMap((result) => {
      if (result.error) errors.push(result.error)
      return result.data ? [result.data] : []
    })
    const loaded: UploadBatchView[] = []
    const seen = new Set<string>()
    for (const batch of [...serverBatches, ...fallbackBatches]) {
      if (seen.has(batch.id)) continue
      seen.add(batch.id)
      loaded.push(batch)
      if (loaded.length >= 30) break
    }
    setUploadBatches(loaded)
    setUploadErrors(errors)
    setUploadCenterState(loaded.length > 0 ? 'ready' : errors.length > 0 ? 'error' : 'empty')
  }, [services.documents])

  useEffect(() => {
    void loadDocuments()
  }, [loadDocuments])

  useEffect(() => {
    void loadUploadCenter()
  }, [loadUploadCenter])

  useEffect(() => {
    if (!documents.some((document) => document.status === 'PROCESSING' || document.status === 'INDEXING')) return
    const timer = window.setInterval(() => void loadDocuments(), 2000)
    return () => window.clearInterval(timer)
  }, [documents, loadDocuments])

  useEffect(() => {
    if (!uploadBatches.some((batch) => batch.status !== 'ready' && batch.status !== 'failed' && batch.status !== 'cancelled')) return
    const timer = window.setInterval(() => void loadUploadCenter(), 2500)
    return () => window.clearInterval(timer)
  }, [loadUploadCenter, uploadBatches])

  const handleUploadAction = async (item: UploadBatchItemView, action: 'retry' | 'cancel') => {
    const jobId = item.jobId
    const itemId = item.id
    if (action === 'retry' && (!jobId || !item.error?.retryable)) return
    if (action === 'cancel' && !jobId && !itemId) return
    const key = `${action}:${jobId ?? itemId}`
    setUploadActionKey(key)
    setUploadErrors([])
    const result = action === 'retry' && jobId
      ? await services.documents.retryUploadJob(jobId)
      : action === 'cancel' && jobId
        ? await services.documents.cancelUploadJob(jobId)
        : await services.documents.abortUploadItem(itemId)
    setUploadActionKey(null)
    if (result.state !== 'ready') {
      setUploadErrors(result.error ? [result.error] : [{ code: 'UPLOAD_ACTION_FAILED', message: '上传操作失败，服务端状态未改变。' }])
      return
    }
    await loadUploadCenter()
  }

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

  useEffect(() => {
    const existingKeys = new Set(documents.map(documentSelectionKey))
    setSelectedDocumentKeys((current) => {
      const retained = new Set(Array.from(current).filter((key) => existingKeys.has(key)))
      return retained.size === current.size ? current : retained
    })
  }, [documents])

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

  const toggleDocumentSelection = (document: DocumentTableRow) => {
    if (busy) return
    const key = documentSelectionKey(document)
    setSelectedDocumentKeys((current) => {
      const next = new Set(current)
      if (next.has(key)) next.delete(key)
      else next.add(key)
      return next
    })
  }

  const toggleAllVisibleDocuments = () => {
    if (busy || visibleDocuments.length === 0) return
    const visibleKeys = visibleDocuments.map(documentSelectionKey)
    const allSelected = visibleKeys.every((key) => selectedDocumentKeys.has(key))
    setSelectedDocumentKeys((current) => {
      const next = new Set(current)
      for (const key of visibleKeys) {
        if (allSelected) next.delete(key)
        else next.add(key)
      }
      return next
    })
  }

  const handleBulkDelete = async () => {
    if (busy) return
    const targets = documents.filter((document) => selectedDocumentKeys.has(documentSelectionKey(document)))
    if (targets.length === 0 || !window.confirm(`确认删除选中的 ${targets.length} 个文档？删除后文档会进入回收站。`)) return
    setBusy(true)
    clearNotice()
    const succeeded: DocumentTableRow[] = []
    const failed: Array<{ readonly document: DocumentTableRow; readonly error: AdapterError }> = []
    try {
      for (const document of targets) {
        const result = await services.documents.remove(document.kbId, document.id)
        if (result.state === 'ready') {
          succeeded.push(document)
        } else {
          failed.push({
            document,
            error: result.error ?? { code: 'DOCUMENT_DELETE_FAILED', message: '服务端未接受删除请求。' },
          })
        }
      }
      const succeededKeys = new Set(succeeded.map(documentSelectionKey))
      setSelectedDocumentKeys((current) => new Set(Array.from(current).filter((key) => !succeededKeys.has(key))))
      setSearchState('empty')
      setSearchHits([])
      await loadDocuments()
      if (failed.length === 0) {
        setNotice(`已删除 ${succeeded.length} 个文档，真实列表已刷新；文档已进入回收站。`)
      } else {
        const firstFailure = failed[0]
        setNotice(`批量删除完成：成功 ${succeeded.length} 个，失败 ${failed.length} 个。失败文档保留在真实列表中。`)
        setNoticeError({
          ...firstFailure.error,
          message: `失败项「${firstFailure.document.title}」：${firstFailure.error.message}`,
        })
      }
    } finally {
      setBusy(false)
    }
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
        <div className="v2-m3-heading-actions"><button type="button" className="v2-m3-secondary-button" onClick={handleBulkDelete} disabled={busy || selectedDocumentKeys.size === 0} title={selectedDocumentKeys.size === 0 ? '先选择要删除的真实文档' : '逐条调用服务端删除接口'}><FunnelSimple size={15} aria-hidden="true" />批量删除{selectedDocumentKeys.size > 0 ? ` (${selectedDocumentKeys.size})` : ''}</button><button type="button" className="v2-m3-secondary-button" onClick={() => setBatchUploadOpen(true)} disabled={busy} title={!selectedSearchKbId ? '打开弹窗后选择目标知识库' : '批量/目录上传到已选知识库'}><FolderOpen size={15} aria-hidden="true" />批量/目录上传</button><button type="button" className="v2-m3-primary-button" onClick={() => uploadInputRef.current?.click()} disabled={!selectedSearchKbId || busy}><UploadSimple size={15} aria-hidden="true" />上传文档</button><input ref={uploadInputRef} className="v2-m3-hidden-input" type="file" accept=".txt,.md,.pdf,.doc,.docx,.ppt,.pptx,.xls,.xlsx" onChange={(event) => { const file = event.target.files?.[0]; event.target.value = ''; if (file) void handleUpload(file) }} /></div>
      </section>
      {notice ? <div className="v2-m3-notice v2-m3-notice--success" role="status">{notice}</div> : null}
      {noticeError ? <div className="v2-m3-notice v2-m3-notice--error" role="alert"><span>{noticeError.message}<small>{formatErrorMeta(noticeError)}</small></span></div> : null}
      <UploadCenter
        state={uploadCenterState}
        batches={uploadBatches}
        errors={uploadErrors}
        actionKey={uploadActionKey}
        onAction={(item, action) => void handleUploadAction(item, action)}
        onRefresh={() => void loadUploadCenter()}
      />
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
        <DocumentTable documents={visibleDocuments} loading={documentsState === 'loading'} busy={busy} selectedKeys={selectedDocumentKeys} onToggle={toggleDocumentSelection} onToggleAll={toggleAllVisibleDocuments} onDetail={(document) => void handleDetail(document)} onDelete={(document) => void handleDelete(document)} onRetry={(document) => void handleRetry(document)} onVersions={(document) => void handleVersions(document)} />
        <nav className="v2-m3-pagination" aria-label="文档分页"><span>共 {filteredDocuments.length} 条真实结果</span><div>{Array.from({ length: totalPages }, (_, index) => index + 1).slice(0, 7).map((page) => <button type="button" key={page} className={page === currentPage ? 'v2-m3-page-button v2-m3-page-button--active' : 'v2-m3-page-button'} onClick={() => setCurrentPage(page)} aria-current={page === currentPage ? 'page' : undefined}>{page}</button>)}</div></nav>
      </section>
      {detailDocument ? <Modal title="文档详情" onClose={() => setDetailDocument(null)}><DocumentDetail state={detailState} document={detailDocument} /></Modal> : detailState === 'loading' ? <Modal title="文档详情" onClose={() => setDetailState('empty')}><StatePanel state="loading" message="正在获取文档详情。" /></Modal> : null}
      {versionDocument ? <Modal title={`版本与 diff · ${versionDocument.title}`} onClose={() => setVersionDocument(null)}><VersionPanel state={versionState} versions={versions} diffState={diffState} diff={diff} fromVersion={fromVersion} toVersion={toVersion} onFromChange={setFromVersion} onToChange={setToVersion} onDiff={() => void handleDiff()} /></Modal> : null}
      <BatchUploadModal open={batchUploadOpen} kbId={selectedSearchKbId || null} services={services} onClose={() => setBatchUploadOpen(false)} onSuccess={() => { void loadDocuments(); void loadUploadCenter() }} />
    </div>
  )
}

function documentSelectionKey(document: DocumentTableRow): string {
  return `${document.kbId}:${document.id}`
}

function UploadCenter({
  state,
  batches,
  errors,
  actionKey,
  onAction,
  onRefresh,
}: {
  readonly state: PageState
  readonly batches: readonly UploadBatchView[]
  readonly errors: readonly AdapterError[]
  readonly actionKey: string | null
  readonly onAction: (item: UploadBatchItemView, action: 'retry' | 'cancel') => void
  readonly onRefresh: () => void
}) {
  return (
    <section className="v2-m3-upload-center-card" aria-label="上传中心">
      <div className="v2-m3-upload-center-heading">
        <div>
          <p className="v2-eyebrow">UPLOAD CENTER / SERVER PROJECTION</p>
          <h2>上传中心</h2>
          <p>刷新后从服务端重新读取批次；状态、进度、错误、attempt 和 job id 均不使用前端临时成功状态。</p>
        </div>
        <button type="button" className="v2-m3-secondary-button" onClick={onRefresh} disabled={state === 'loading'}>
          <ArrowClockwise size={14} aria-hidden="true" />刷新状态
        </button>
      </div>
      {errors.length > 0 ? <div className="v2-m3-upload-center-errors" role="alert">{errors.map((error, index) => <div key={`${error.code}-${index}`}><strong>{error.code}</strong><span>{error.message}</span></div>)}</div> : null}
      {state === 'loading' ? <StatePanel state="loading" message="正在从服务端恢复上传批次。" /> : null}
      {state === 'empty' ? <StatePanel state="empty" message="没有可恢复的上传批次。开始批量上传后，批次会进入这里。" /> : null}
      {state === 'error' && batches.length === 0 ? <StatePanel state="error" message="上传中心无法读取真实批次，未用本地样本替代。" /> : null}
      {batches.map((batch) => <UploadBatchCard key={batch.id} batch={batch} actionKey={actionKey} onAction={onAction} />)}
    </section>
  )
}

function UploadBatchCard({
  batch,
  actionKey,
  onAction,
}: {
  readonly batch: UploadBatchView
  readonly actionKey: string | null
  readonly onAction: (item: UploadBatchItemView, action: 'retry' | 'cancel') => void
}) {
  const counts = batch.counts
  const countText = ['ready', 'processing', 'indexing', 'uploading', 'verifying', 'queued', 'failed', 'cancelled']
    .filter((status) => Number(counts[status] ?? 0) > 0)
    .map((status) => `${UPLOAD_STATUS_LABEL[status] ?? status} ${counts[status]}`)
    .join(' · ')
  return (
    <article className="v2-m3-upload-batch" data-batch-id={batch.id}>
      <header className="v2-m3-upload-batch-heading">
        <div><strong>批次 {batch.id}</strong><span>知识库 {batch.kbId} · {batch.mode} · {formatBytes(batch.totalBytes)}</span></div>
        <span className={`v2-m3-upload-status v2-m3-upload-status--${batch.status}`}>{UPLOAD_STATUS_LABEL[batch.status] ?? batch.status}</span>
      </header>
      <div className="v2-m3-upload-batch-meta"><span>{countText || '暂无计数'}</span><time dateTime={batch.updatedAt}>更新 {formatDateTime(batch.updatedAt)}</time></div>
      <ul className="v2-m3-upload-items">
        {batch.items.map((item) => <UploadItemRow key={item.id} item={item} actionKey={actionKey} onAction={onAction} />)}
      </ul>
    </article>
  )
}

function UploadItemRow({
  item,
  actionKey,
  onAction,
}: {
  readonly item: UploadBatchItemView
  readonly actionKey: string | null
  readonly onAction: (item: UploadBatchItemView, action: 'retry' | 'cancel') => void
}) {
  const progress = item.progress
  const current = typeof progress?.current === 'number' ? progress.current : item.uploadedBytes ?? 0
  const total = typeof progress?.total === 'number' ? progress.total : item.byteSize ?? 0
  const percent = total > 0 ? Math.max(0, Math.min(100, Math.round((current / total) * 100))) : item.status === 'ready' ? 100 : 0
  const retryKey = `retry:${item.jobId ?? item.id}`
  const cancelKey = `cancel:${item.jobId ?? item.id}`
  return (
    <li className="v2-m3-upload-item" data-item-status={item.status}>
      <div className="v2-m3-upload-item-main"><strong title={item.relativePath}>{item.relativePath}</strong><span>{UPLOAD_STATUS_LABEL[item.status] ?? item.status} · {item.stage ?? '等待服务端阶段'} · {percent}%</span></div>
      <div className="v2-m3-upload-item-progress" role="progressbar" aria-valuenow={percent} aria-valuemin={0} aria-valuemax={100}><span style={{ width: `${percent}%` }} /></div>
      <div className="v2-m3-upload-item-meta"><span>attempt {item.attemptNo ?? '—'} / {item.attempts} · job {item.jobId ?? '—'}</span><span>item {item.id}</span></div>
      {item.error ? <div className="v2-m3-upload-item-error"><strong>{item.error.code}</strong><span>{item.error.message}</span></div> : null}
      <div className="v2-m3-upload-item-actions">
        {item.status === 'failed' && item.error?.retryable && item.jobId ? <button type="button" className="v2-m3-secondary-button" onClick={() => onAction(item, 'retry')} disabled={actionKey === retryKey}><ArrowClockwise size={13} aria-hidden="true" />{actionKey === retryKey ? '重试中…' : '重试任务'}</button> : null}
        {(item.status === 'processing' || item.status === 'indexing') && item.jobId ? <button type="button" className="v2-m3-secondary-button" onClick={() => onAction(item, 'cancel')} disabled={actionKey === cancelKey}><Stop size={13} aria-hidden="true" />{actionKey === cancelKey ? '取消中…' : '取消任务'}</button> : null}
        {item.status === 'uploading' ? <button type="button" className="v2-m3-secondary-button" onClick={() => onAction(item, 'cancel')} disabled={actionKey === cancelKey}><Stop size={13} aria-hidden="true" />{actionKey === cancelKey ? '取消中…' : '取消上传'}</button> : null}
      </div>
    </li>
  )
}

const UPLOAD_STATUS_LABEL: Record<string, string> = {
  queued: '排队中',
  uploading: '上传中',
  verifying: '校验中',
  processing: '处理中',
  indexing: '索引中',
  ready: '已就绪',
  failed: '失败',
  cancelled: '已取消',
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
