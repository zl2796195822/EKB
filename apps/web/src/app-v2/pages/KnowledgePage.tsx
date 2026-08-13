import {
  ArrowsLeftRight,
  ArrowsHorizontal,
  Check,
  FileText,
  FolderOpen,
  FunnelSimple,
  MagnifyingGlass,
  PencilSimple,
  Plus,
  Trash,
  UploadSimple,
  UsersThree,
  X,
} from '@phosphor-icons/react'
import { useCallback, useEffect, useMemo, useRef, useState, type FormEvent } from 'react'
import { FolderTree, KnowledgeDocumentTable, KnowledgeSpaceRail } from '../components/knowledge'
import { BatchUploadModal } from '../components/documents/BatchUploadModal'
import { StatePanel } from '../components/StatePanel'
import { StatusPill } from '../components/ui'
import type {
  AdapterError,
  DocumentDiffResult,
  DocumentDiffView,
  DocumentVersionView,
  DocumentView,
  KbMemberView,
  KnowledgeBaseView,
  PageState,
  V2PageProps,
} from '../types'
import type { FavoriteItemView, FavoritesResourceKind } from '../types/favorites'

const PAGE_SIZE = 8

function readKbParam(): string {
  if (typeof window === 'undefined') return ''
  const [, query] = window.location.hash.split('?')
  if (!query) return ''
  return new URLSearchParams(query).get('kb')?.trim() ?? ''
}

export function KnowledgePage({ services, session }: V2PageProps) {
  const [requestedKbId, setRequestedKbId] = useState(() => readKbParam())
  const [knowledgeState, setKnowledgeState] = useState<PageState>('loading')
  const [knowledgeBases, setKnowledgeBases] = useState<readonly KnowledgeBaseView[]>([])
  const [selectedKbId, setSelectedKbId] = useState('')
  const [documentsState, setDocumentsState] = useState<PageState>('empty')
  const [documents, setDocuments] = useState<readonly DocumentView[]>([])
  const [membersState, setMembersState] = useState<PageState>('empty')
  const [members, setMembers] = useState<readonly KbMemberView[]>([])
  const [notice, setNotice] = useState<string | null>(null)
  const [noticeError, setNoticeError] = useState<AdapterError | null>(null)
  const [isCreateOpen, setIsCreateOpen] = useState(false)
  const [createName, setCreateName] = useState('')
  const [createDescription, setCreateDescription] = useState('')
  const [createVisibility, setCreateVisibility] = useState<KnowledgeBaseView['visibility']>('PRIVATE')
  const [isEditOpen, setIsEditOpen] = useState(false)
  const [editName, setEditName] = useState('')
  const [editDescription, setEditDescription] = useState('')
  const [editVisibility, setEditVisibility] = useState<KnowledgeBaseView['visibility']>('PRIVATE')
  const [isBusy, setIsBusy] = useState(false)
  const [documentFilter, setDocumentFilter] = useState('')
  const [currentPage, setCurrentPage] = useState(1)
  const [searchQuery, setSearchQuery] = useState('')
  const [searchState, setSearchState] = useState<PageState>('empty')
  const [searchHits, setSearchHits] = useState<readonly import('../types').SearchHitView[]>([])
  const [memberEmail, setMemberEmail] = useState('')
  const [memberRole, setMemberRole] = useState('VIEWER')
  const [detailState, setDetailState] = useState<PageState>('empty')
  const [detailDocument, setDetailDocument] = useState<DocumentView | null>(null)
  const [versionDocument, setVersionDocument] = useState<DocumentView | null>(null)
  const [versionState, setVersionState] = useState<PageState>('empty')
  const [versions, setVersions] = useState<readonly DocumentVersionView[]>([])
  const [diffState, setDiffState] = useState<PageState>('empty')
  const [diff, setDiff] = useState<DocumentDiffView | null>(null)
  const [fromVersion, setFromVersion] = useState<number | null>(null)
  const [toVersion, setToVersion] = useState<number | null>(null)
  const [favoritesState, setFavoritesState] = useState<PageState>('loading')
  const [favoritesItems, setFavoritesItems] = useState<readonly FavoriteItemView[]>([])
  const [favoritesTotal, setFavoritesTotal] = useState(0)
  const [favoritesCounts, setFavoritesCounts] = useState<Readonly<Record<string, number>>>({})
  const [favoritesOpen, setFavoritesOpen] = useState(false)
  const [batchUploadOpen, setBatchUploadOpen] = useState(false)
  const uploadInputRef = useRef<HTMLInputElement | null>(null)

  const selectedKb = useMemo(
    () => knowledgeBases.find((knowledgeBase) => knowledgeBase.id === selectedKbId) ?? null,
    [knowledgeBases, selectedKbId],
  )
  const canWrite = Boolean(selectedKb && ['OWNER', 'ADMIN', 'EDITOR'].includes(selectedKb.role))
  const canManageMembers = Boolean(selectedKb && ['OWNER', 'ADMIN'].includes(selectedKb.role))

  const loadKnowledgeBases = useCallback(async (preferredId?: string) => {
    setKnowledgeState('loading')
    const result = await services.knowledge.list()
    setKnowledgeState(result.state)
    if (!result.data) {
      setKnowledgeBases([])
      setSelectedKbId('')
      return
    }
    setKnowledgeBases(result.data)
    setSelectedKbId((current) => {
      if (preferredId && result.data?.some((knowledgeBase) => knowledgeBase.id === preferredId)) return preferredId
      if (current && result.data?.some((knowledgeBase) => knowledgeBase.id === current)) return current
      return result.data?.[0]?.id ?? ''
    })
  }, [services.knowledge])

  const loadFavorites = useCallback(async () => {
    setFavoritesState('loading')
    const result = await services.favorites.list()
    setFavoritesState(result.state)
    setFavoritesItems(result.data?.items ?? [])
    setFavoritesTotal(result.data?.total ?? 0)
    setFavoritesCounts(result.data?.counts ?? {})
  }, [services.favorites])

  const loadSelectedData = useCallback(async (kbId: string) => {
    setDocumentsState('loading')
    setMembersState('loading')
    const [documentsResult, membersResult] = await Promise.all([
      services.documents.list(kbId),
      services.knowledge.listMembers(kbId),
    ])
    setDocumentsState(documentsResult.state)
    setDocuments(documentsResult.data ?? [])
    setMembersState(membersResult.state)
    setMembers(membersResult.data ?? [])
  }, [services.documents, services.knowledge])

  useEffect(() => {
    const handleHashChange = () => setRequestedKbId(readKbParam())
    window.addEventListener('hashchange', handleHashChange)
    return () => window.removeEventListener('hashchange', handleHashChange)
  }, [])

  useEffect(() => {
    void loadKnowledgeBases(readKbParam() || undefined)
    void loadFavorites()
  }, [loadFavorites, loadKnowledgeBases])

  useEffect(() => {
    if (!requestedKbId) return
    if (!knowledgeBases.some((knowledgeBase) => knowledgeBase.id === requestedKbId)) return
    setSelectedKbId(requestedKbId)
  }, [knowledgeBases, requestedKbId])

  useEffect(() => {
    setSearchHits([])
    setSearchState('empty')
    setCurrentPage(1)
    setDetailDocument(null)
    setVersionDocument(null)
    setVersions([])
    setDiff(null)
    if (!selectedKbId) {
      setDocuments([])
      setDocumentsState('empty')
      setMembers([])
      setMembersState('empty')
      return
    }
    void loadSelectedData(selectedKbId)
  }, [knowledgeState, loadSelectedData, selectedKbId])

  useEffect(() => {
    setEditName(selectedKb?.name ?? '')
    setEditDescription(selectedKb?.description ?? '')
    setEditVisibility(selectedKb?.visibility ?? 'PRIVATE')
  }, [selectedKb])

  const handleSelectKb = (kbId: string) => {
    setSelectedKbId(kbId)
    setRequestedKbId(kbId)
    if (typeof window !== 'undefined' && readKbParam() !== kbId) {
      window.location.hash = `#/knowledge?kb=${encodeURIComponent(kbId)}`
    }
  }

  const handleOpenFavorite = useCallback((resourceType: FavoritesResourceKind, resourceId: string, parentId: string | null) => {
    if (resourceType === 'KB') {
      handleSelectKb(resourceId)
      return
    }
    if (resourceType === 'DOCUMENT') {
      if (parentId) handleSelectKb(parentId)
      setNotice(`已定位到收藏文档所属知识库；详情面板后续可通过 favorites.detail 端点一键打开。`)
      return
    }
    if (resourceType === 'CONVERSATION') {
      if (typeof window !== 'undefined') {
        window.location.hash = `#/assistant?conv=${encodeURIComponent(resourceId)}`
      }
      return
    }
  }, [])

  const handleToggleFavoritesOpen = useCallback(() => {
    setFavoritesOpen((open) => !open)
    if (!favoritesOpen && favoritesState === 'empty') {
      void loadFavorites()
    }
  }, [favoritesOpen, favoritesState, loadFavorites])

  const clearNotice = () => {
    setNotice(null)
    setNoticeError(null)
  }

  const showError = (error: AdapterError | undefined, fallback: string) => {
    setNotice(null)
    setNoticeError(error ?? { code: 'CLIENT_ERROR', message: fallback })
  }

  const handleCreate = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    const name = createName.trim()
    if (!name) return
    setIsBusy(true)
    clearNotice()
    const result = await services.knowledge.create({ name, description: createDescription.trim(), visibility: createVisibility })
    setIsBusy(false)
    if (!result.data || result.state !== 'ready') {
      showError(result.error, '知识库创建失败')
      return
    }
    setNotice('知识库已由服务端创建，正在重新获取真实列表。')
    setCreateName('')
    setCreateDescription('')
    setCreateVisibility('PRIVATE')
    setIsCreateOpen(false)
    await loadKnowledgeBases(result.data.id)
  }

  const handleUpdate = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    if (!selectedKbId || !editName.trim()) return
    setIsBusy(true)
    clearNotice()
    const result = await services.knowledge.update(selectedKbId, {
      name: editName.trim(),
      description: editDescription.trim(),
      visibility: editVisibility,
    })
    setIsBusy(false)
    if (!result.data || result.state !== 'ready') {
      showError(result.error, '知识库更新失败')
      return
    }
    setNotice('知识库已更新，页面正在重新获取真实详情。')
    setIsEditOpen(false)
    await loadKnowledgeBases(selectedKbId)
  }

  const handleDeleteKb = async () => {
    if (!selectedKb || !window.confirm(`确认删除知识库「${selectedKb.name}」？`)) return
    setIsBusy(true)
    clearNotice()
    const result = await services.knowledge.remove(selectedKb.id)
    setIsBusy(false)
    if (result.state !== 'ready') {
      showError(result.error, '知识库删除失败')
      return
    }
    setNotice('删除请求已完成，正在重新获取授权范围。')
    setSearchState('empty')
    setSearchHits([])
    await loadKnowledgeBases()
  }

  const handleUpload = async (file: File) => {
    if (!selectedKbId) return
    setIsBusy(true)
    clearNotice()
    const result = await services.documents.upload(selectedKbId, file)
    setIsBusy(false)
    if (!result.data || result.state !== 'ready') {
      showError(result.error, '文档上传失败')
      return
    }
    setNotice(`上传已接受：${result.data.status}，文档 ${result.data.docId}，任务 ${result.data.jobId}，trace ${result.data.traceId}。`)
    await Promise.all([loadSelectedData(selectedKbId), loadKnowledgeBases(selectedKbId)])
  }

  const handleDocumentDelete = async (document: DocumentView) => {
    if (!selectedKbId || !window.confirm(`确认删除文档「${document.title}」？`)) return
    setIsBusy(true)
    clearNotice()
    const result = await services.documents.remove(selectedKbId, document.id)
    setIsBusy(false)
    if (result.state !== 'ready') {
      showError(result.error, '文档删除失败')
      return
    }
    setNotice('文档删除请求已完成，正在重新获取真实列表。')
    setSearchState('empty')
    setSearchHits([])
    await Promise.all([loadSelectedData(selectedKbId), loadKnowledgeBases(selectedKbId)])
  }

  const handleDocumentRetry = async (document: DocumentView) => {
    if (!selectedKbId) return
    setIsBusy(true)
    clearNotice()
    const result = await services.documents.retry(selectedKbId, document.id)
    setIsBusy(false)
    if (result.state !== 'ready') {
      showError(result.error, '文档重试失败')
      return
    }
    setNotice(`重试已接受：${result.data?.status ?? 'accepted'}，trace ${result.data?.traceId ?? '—'}。`)
    await loadSelectedData(selectedKbId)
  }

  const handleDocumentDetail = async (document: DocumentView) => {
    setDetailDocument(null)
    setDetailState('loading')
    const result = await services.documents.get(document.kbId, document.id)
    setDetailState(result.state)
    setDetailDocument(result.data ?? null)
    if (result.error) showError(result.error, '文档详情加载失败')
  }

  const handleVersions = async (document: DocumentView) => {
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
    const result: DocumentDiffResult = await services.documents.diff(versionDocument.kbId, versionDocument.id, fromVersion, toVersion)
    setDiffState(result.state)
    setDiff(result.data ?? null)
    if (result.error) showError(result.error, '版本差异加载失败')
  }

  const handleSearch = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    const query = searchQuery.trim()
    if (!query || !selectedKbId) return
    setSearchState('loading')
    setSearchHits([])
    const result = await services.search.search(query, selectedKbId)
    setSearchState(result.state)
    setSearchHits(result.data ?? [])
    if (result.error) showError(result.error, '检索失败')
  }

  const handleAddMember = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    if (!selectedKbId || !memberEmail.trim() || !canManageMembers) return
    setIsBusy(true)
    clearNotice()
    const result = await services.knowledge.addMember(selectedKbId, { email: memberEmail.trim(), role: memberRole })
    setIsBusy(false)
    if (result.state !== 'ready') {
      showError(result.error, '成员添加失败')
      return
    }
    setMemberEmail('')
    setNotice('成员已由服务端添加，正在重新获取成员列表。')
    const membersResult = await services.knowledge.listMembers(selectedKbId)
    setMembersState(membersResult.state)
    setMembers(membersResult.data ?? [])
  }

  const handleRemoveMember = async (member: KbMemberView) => {
    if (!selectedKbId || !canManageMembers || !window.confirm('确认移除该知识库成员？')) return
    setIsBusy(true)
    clearNotice()
    const result = await services.knowledge.removeMember(selectedKbId, member.userId)
    setIsBusy(false)
    if (result.state !== 'ready') {
      showError(result.error, '成员移除失败')
      return
    }
    setNotice('成员移除请求已完成，正在重新获取成员列表。')
    const membersResult = await services.knowledge.listMembers(selectedKbId)
    setMembersState(membersResult.state)
    setMembers(membersResult.data ?? [])
  }

  const filteredDocuments = useMemo(() => {
    const query = documentFilter.trim().toLowerCase()
    if (!query) return documents
    return documents.filter((document) => `${document.title} ${document.mimeType} ${document.status}`.toLowerCase().includes(query))
  }, [documentFilter, documents])
  const totalPages = Math.max(1, Math.ceil(filteredDocuments.length / PAGE_SIZE))
  const visibleDocuments = filteredDocuments.slice((currentPage - 1) * PAGE_SIZE, currentPage * PAGE_SIZE)

  useEffect(() => {
    setCurrentPage((page) => Math.min(page, totalPages))
  }, [totalPages])

  return (
    <div className="v2-knowledge-columns v2-m3-knowledge-columns">
      <KnowledgeSpaceRail
        state={knowledgeState}
        spaces={knowledgeBases}
        selectedId={selectedKbId}
        onSelect={handleSelectKb}
        onCreate={() => setIsCreateOpen(true)}
        favoritesState={favoritesState}
        favoritesItems={favoritesItems}
        favoritesTotal={favoritesTotal}
        favoritesCounts={favoritesCounts}
        onToggleFavorites={handleToggleFavoritesOpen}
        favoritesOpen={favoritesOpen}
        onOpenFavorite={handleOpenFavorite}
      />
      <section className="v2-knowledge-main v2-m3-knowledge-main" aria-labelledby="knowledge-page-title">
        <div className="v2-m3-page-heading">
          <div>
            <p className="v2-eyebrow">KNOWLEDGE BASE / WORKSPACE</p>
            <h1 id="knowledge-page-title">知识库</h1>
            <p>管理已授权知识空间中的真实文档、版本和成员访问。</p>
          </div>
          <div className="v2-m3-heading-actions">
            <button type="button" className="v2-m3-secondary-button" onClick={() => setIsCreateOpen(true)} disabled={!session.capabilities.includes('kb:write')}>
              <Plus size={15} aria-hidden="true" />新建知识库
            </button>
            <button type="button" className="v2-m3-secondary-button" onClick={() => setBatchUploadOpen(true)} disabled={isBusy} title={!selectedKbId ? '打开弹窗后选择目标知识库' : '批量/目录上传到已选知识库'}>
              <FolderOpen size={15} aria-hidden="true" />批量/目录上传
            </button>
            <button type="button" className="v2-m3-primary-button" onClick={() => uploadInputRef.current?.click()} disabled={!selectedKbId || !canWrite || isBusy}>
              <UploadSimple size={15} aria-hidden="true" />上传知识
            </button>
            <input ref={uploadInputRef} className="v2-m3-hidden-input" type="file" accept=".txt,.md,.pdf,.doc,.docx,.ppt,.pptx,.xls,.xlsx" onChange={(event) => { const file = event.target.files?.[0]; event.target.value = ''; if (file) void handleUpload(file) }} />
          </div>
        </div>

        {notice ? <div className="v2-m3-notice v2-m3-notice--success" role="status"><Check size={15} aria-hidden="true" />{notice}</div> : null}
        {noticeError ? <div className="v2-m3-notice v2-m3-notice--error" role="alert"><X size={15} aria-hidden="true" /><span>{noticeError.message}<small>{formatErrorMeta(noticeError)}</small></span></div> : null}

        {!selectedKb ? (
          <StatePanel state={knowledgeState === 'error' || knowledgeState === 'permission-denied' ? knowledgeState : knowledgeState === 'empty' ? 'empty' : 'loading'} message="选择一个真实知识库后，文档与成员操作会显示在此处。" />
        ) : (
          <>
            <div className="v2-m3-selected-kb-card">
              <div>
                <div className="v2-m3-title-row"><FileText size={18} aria-hidden="true" /><h2>{selectedKb.name}</h2><span className="v2-m3-role-chip">{selectedKb.role}</span></div>
                <p>{selectedKb.description || '该知识库没有描述。'}</p>
              </div>
              <div className="v2-m3-card-actions">
                <button type="button" className="v2-m3-text-button" onClick={() => setIsEditOpen(true)} disabled={!canWrite}><PencilSimple size={14} aria-hidden="true" />编辑</button>
                <button type="button" className="v2-m3-text-button v2-m3-text-button--danger" onClick={() => void handleDeleteKb()} disabled={!canWrite || isBusy}><Trash size={14} aria-hidden="true" />删除</button>
              </div>
            </div>

            <form className="v2-m3-document-toolbar" onSubmit={handleSearch}>
              <label className="v2-m3-search-input"><MagnifyingGlass size={15} aria-hidden="true" /><input value={searchQuery} onChange={(event) => setSearchQuery(event.target.value)} placeholder="语义搜索当前知识库…" aria-label="语义搜索当前知识库" /><button type="submit" disabled={!searchQuery.trim() || searchState === 'loading'}>检索</button></label>
              <label className="v2-m3-filter-input"><FunnelSimple size={14} aria-hidden="true" /><input value={documentFilter} onChange={(event) => { setDocumentFilter(event.target.value); setCurrentPage(1) }} placeholder="筛选已加载文档" aria-label="筛选已加载文档" /></label>
            </form>

            {searchState !== 'empty' ? <SearchPanel state={searchState} query={searchQuery} hits={searchHits} /> : null}
            <div className="v2-m3-section-heading"><div><h2>文件</h2><span>{filteredDocuments.length} 条真实结果 · 仅作用于当前授权知识库</span></div><button type="button" className="v2-m3-secondary-button" onClick={() => setBatchUploadOpen(true)} disabled={isBusy}><FolderOpen size={14} aria-hidden="true" />批量/目录上传</button><button type="button" className="v2-m3-secondary-button" onClick={() => uploadInputRef.current?.click()} disabled={!canWrite || isBusy}><UploadSimple size={14} aria-hidden="true" />上传</button></div>
            <KnowledgeDocumentTable documents={visibleDocuments} loading={documentsState === 'loading'} onDetail={(document) => void handleDocumentDetail(document)} onDelete={(document) => void handleDocumentDelete(document)} onRetry={(document) => void handleDocumentRetry(document)} onVersions={(document) => void handleVersions(document)} />
            {documentsState === 'error' || documentsState === 'permission-denied' ? <StatePanel state={documentsState} message="文档列表未能加载，未使用本地数据替代。" /> : null}
            <Pagination currentPage={currentPage} totalPages={totalPages} total={filteredDocuments.length} onChange={setCurrentPage} />
          </>
        )}
      </section>
      <KnowledgeOverview selectedKb={selectedKb} documents={documents} membersState={membersState} members={members} canManageMembers={canManageMembers} memberEmail={memberEmail} memberRole={memberRole} isBusy={isBusy} canWrite={canWrite} services={services.knowledge} onMemberEmailChange={setMemberEmail} onMemberRoleChange={setMemberRole} onAddMember={handleAddMember} onRemoveMember={(member) => void handleRemoveMember(member)} />
      {isCreateOpen ? <Modal title="新建知识库" onClose={() => setIsCreateOpen(false)}><form className="v2-m3-form" onSubmit={handleCreate}><label>名称<input value={createName} onChange={(event) => setCreateName(event.target.value)} required autoFocus /></label><label>描述<textarea value={createDescription} onChange={(event) => setCreateDescription(event.target.value)} rows={3} /></label><label>可见性<select value={createVisibility} onChange={(event) => setCreateVisibility(event.target.value as KnowledgeBaseView['visibility'])}><option value="PRIVATE">PRIVATE · 仅授权成员</option><option value="TEAM">TEAM · 团队可见</option><option value="PUBLIC">PUBLIC · 公开</option></select></label><div className="v2-m3-modal-actions"><button type="button" className="v2-m3-secondary-button" onClick={() => setIsCreateOpen(false)}>取消</button><button type="submit" className="v2-m3-primary-button" disabled={isBusy || !createName.trim()}>创建</button></div></form></Modal> : null}
      {isEditOpen && selectedKb ? <Modal title="编辑知识库" onClose={() => setIsEditOpen(false)}><form className="v2-m3-form" onSubmit={handleUpdate}><label>名称<input value={editName} onChange={(event) => setEditName(event.target.value)} required autoFocus /></label><label>描述<textarea value={editDescription} onChange={(event) => setEditDescription(event.target.value)} rows={3} /></label><label>可见性<select value={editVisibility} onChange={(event) => setEditVisibility(event.target.value as KnowledgeBaseView['visibility'])}><option value="PRIVATE">PRIVATE · 仅授权成员</option><option value="TEAM">TEAM · 团队可见</option><option value="PUBLIC">PUBLIC · 公开</option></select></label><div className="v2-m3-modal-actions"><button type="button" className="v2-m3-secondary-button" onClick={() => setIsEditOpen(false)}>取消</button><button type="submit" className="v2-m3-primary-button" disabled={isBusy || !editName.trim()}>保存</button></div></form></Modal> : null}
      {detailDocument ? <Modal title="文档详情" onClose={() => setDetailDocument(null)}><DocumentDetail state={detailState} document={detailDocument} /></Modal> : detailState === 'loading' ? <Modal title="文档详情" onClose={() => setDetailState('empty')}><StatePanel state="loading" message="正在获取文档详情。" /></Modal> : null}
      {versionDocument ? <Modal title={`版本与 diff · ${versionDocument.title}`} onClose={() => setVersionDocument(null)}><VersionPanel state={versionState} versions={versions} diffState={diffState} diff={diff} fromVersion={fromVersion} toVersion={toVersion} onFromChange={setFromVersion} onToChange={setToVersion} onDiff={() => void handleDiff()} /></Modal> : null}
      <BatchUploadModal open={batchUploadOpen} kbId={selectedKbId || null} services={services} onClose={() => setBatchUploadOpen(false)} onSuccess={() => selectedKbId && void loadSelectedData(selectedKbId)} />
    </div>
  )
}

function KnowledgeOverview({ selectedKb, documents, membersState, members, canManageMembers, memberEmail, memberRole, isBusy, canWrite, services, onMemberEmailChange, onMemberRoleChange, onAddMember, onRemoveMember }: {
  readonly selectedKb: KnowledgeBaseView | null
  readonly documents: readonly DocumentView[]
  readonly membersState: PageState
  readonly members: readonly KbMemberView[]
  readonly canManageMembers: boolean
  readonly memberEmail: string
  readonly memberRole: string
  readonly isBusy: boolean
  readonly canWrite: boolean
  readonly services: import('../types').KnowledgeServices
  readonly onMemberEmailChange: (value: string) => void
  readonly onMemberRoleChange: (value: string) => void
  readonly onAddMember: (event: FormEvent<HTMLFormElement>) => void
  readonly onRemoveMember: (member: KbMemberView) => void
}) {
  return (
    <aside className="v2-knowledge-context v2-m3-knowledge-context" aria-label="知识库概览">
      <div className="v2-context-heading"><strong>知识库概览</strong><FileText size={16} aria-hidden="true" /></div>
      {!selectedKb ? <StatePanel state="empty" message="选择知识库后显示真实概览。" /> : <>
        <div className="v2-m3-overview-stats"><OverviewStat label="文档" value={String(documents.length)} /><OverviewStat label="成员" value={membersState === 'ready' || membersState === 'empty' ? String(members.length) : '—'} /><OverviewStat label="角色" value={selectedKb.role} /></div>
        <div className="v2-context-divider" />
        <div className="v2-m3-context-heading"><strong>最近更新</strong><span>来自当前列表</span></div>
        {documents.length === 0 ? <StatePanel state="empty" message="当前没有已加载的文档。" /> : <div className="v2-m3-recent-list">{documents.slice(0, 4).map((document) => <div key={document.id}><FileText size={14} aria-hidden="true" /><span>{document.title}</span><StatusPill status={document.status} /></div>)}</div>}
        <div className="v2-context-divider" />
        <div className="v2-m3-context-heading"><strong>成员访问</strong><UsersThree size={16} aria-hidden="true" /></div>
        {membersState === 'loading' ? <StatePanel state="loading" message="正在获取成员授权状态。" /> : membersState === 'permission-denied' ? <StatePanel state="permission-denied" message="当前主体无权读取成员列表。" /> : membersState === 'error' ? <StatePanel state="error" message="成员列表加载失败，未显示猜测数据。" /> : members.length === 0 ? <StatePanel state="empty" message="当前知识库没有可显示的成员记录。" /> : <div className="v2-m3-member-list">{members.map((member) => <div key={member.id}><span><strong>{member.userId}</strong><small>{member.role}</small></span><button type="button" className="v2-m3-icon-button v2-m3-icon-button--danger" onClick={() => onRemoveMember(member)} disabled={!canManageMembers || isBusy} aria-label={`移除成员 ${member.userId}`} title={canManageMembers ? '移除成员' : '需要 OWNER/ADMIN 权限'}><Trash size={14} aria-hidden="true" /></button></div>)}</div>}
        <form className="v2-m3-member-form" onSubmit={onAddMember}><label>添加成员<input type="email" value={memberEmail} onChange={(event) => onMemberEmailChange(event.target.value)} placeholder="成员邮箱" disabled={!canManageMembers || isBusy} /></label><select value={memberRole} onChange={(event) => onMemberRoleChange(event.target.value)} disabled={!canManageMembers || isBusy} aria-label="成员角色"><option value="VIEWER">VIEWER</option><option value="EDITOR">EDITOR</option><option value="ADMIN">ADMIN</option></select><button type="submit" className="v2-m3-secondary-button" disabled={!canManageMembers || isBusy || !memberEmail.trim()}>添加</button>{!canManageMembers ? <small>需要该知识库 OWNER/ADMIN 权限。</small> : null}</form>
        <div className="v2-context-divider" />
        <FolderTree kbId={selectedKb.id} services={services} canWrite={canWrite} />
      </>}
      <div className="v2-m3-unavailable-list">
        <span>共享文档</span><small>v4 P2 · 内容治理</small>
        <span>分类占比</span><small>v4 P2 · 治理指标</small>
      </div>
    </aside>
  )
}

function OverviewStat({ label, value }: { readonly label: string; readonly value: string }) {
  return <div><span>{label}</span><strong>{value}</strong></div>
}

function SearchPanel({ state, query, hits }: { readonly state: PageState; readonly query: string; readonly hits: readonly import('../types').SearchHitView[] }) {
  if (state === 'loading') return <StatePanel state="loading" message={`正在检索当前知识库：${query}`} />
  if (state === 'error' || state === 'permission-denied') return <StatePanel state={state} message="检索失败，结果未回退到其他知识库。" />
  if (state === 'empty') return <StatePanel state="empty" message="当前知识库没有匹配的真实命中。" />
  return <div className="v2-m3-search-results"><div className="v2-m3-context-heading"><strong>检索结果</strong><span>{hits.length} 条</span></div>{hits.map((hit) => <article key={hit.chunkId}><strong>{hit.title}</strong><span>{hit.sectionPath.join(' / ') || '未标记章节'} · {Math.round(hit.score * 100)}%</span><p>{hit.snippet}</p></article>)}</div>
}

function Pagination({ currentPage, totalPages, total, onChange }: { readonly currentPage: number; readonly totalPages: number; readonly total: number; readonly onChange: (page: number) => void }) {
  return <nav className="v2-m3-pagination" aria-label="文档分页"><span>共 {total} 条</span><div>{Array.from({ length: totalPages }, (_, index) => index + 1).slice(0, 7).map((page) => <button type="button" key={page} className={page === currentPage ? 'v2-m3-page-button v2-m3-page-button--active' : 'v2-m3-page-button'} onClick={() => onChange(page)} aria-current={page === currentPage ? 'page' : undefined}>{page}</button>)}</div></nav>
}

function Modal({ title, onClose, children }: { readonly title: string; readonly onClose: () => void; readonly children: React.ReactNode }) {
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
