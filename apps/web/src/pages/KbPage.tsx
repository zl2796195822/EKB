import {
  useState,
  type ChangeEvent,
  type FC,
  type FormEvent,
} from 'react'
import {
  Plus,
  Trash,
  Upload,
  ShieldCheck,
  Sparkle,
  MagnifyingGlass,
  BookOpen,
  FileText,
  ChatCircle,
  Question,
} from '@phosphor-icons/react'
import { GalaxyCard, GalaxyHero, GalaxyFormField, MetricCard } from '../components/ui'
import DocVersionPanel from '../components/DocVersionPanel'
import type {
  DocumentRecord,
  DocumentVersionRecord,
  DocumentDiff,
  KnowledgeBase,
} from '../types/api'

export interface KbPageProps {
  knowledgeBases: KnowledgeBase[]
  selectedKbId: string
  documents: DocumentRecord[]
  conversationsCount: number
  isLoading: boolean
  isUploading: boolean
  userEmail?: string
  onSelectKb: (kbId: string) => void
  onCreateKb: (name: string) => Promise<void>
  onDeleteKb: (kbId: string) => Promise<void>
  onDeleteDoc: (docId: string) => Promise<void>
  onUpload: (event: ChangeEvent<HTMLInputElement>) => void
  onLoadDocVersions: (kbId: string, docId: string) => Promise<DocumentVersionRecord[]>
  onLoadDocDiff: (kbId: string, docId: string, from: number, to: number) => Promise<DocumentDiff>
  goToPage: (page: 'qa' | 'kb' | 'search' | 'ops') => void
}

const KbPage: FC<KbPageProps> = (props) => {
  const {
    knowledgeBases,
    selectedKbId,
    documents,
    conversationsCount,
    isUploading,
    userEmail,
    onSelectKb,
    onCreateKb,
    onDeleteKb,
    onDeleteDoc,
    onUpload,
    onLoadDocVersions,
    onLoadDocDiff,
    goToPage,
  } = props

  const [newKbName, setNewKbName] = useState('')

  const handleKbCreateSubmit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    const trimmed = newKbName.trim()
    if (!trimmed) return
    await onCreateKb(trimmed)
    setNewKbName('')
  }

  const handleDeleteKbClick = (kbId: string, name: string) => {
    if (window.confirm(`确认删除知识库「${name}」？关联文档将一并下线。`)) {
      void onDeleteKb(kbId)
    }
  }

  const handleDeleteDocClick = (docId: string, title: string) => {
    if (window.confirm(`确认删除文档「${title}」？删除后将不再被检索。`)) {
      void onDeleteDoc(docId)
    }
  }

  const secureChip = (
    <span className="qa-secure-chip">
      <ShieldCheck size={14} weight="fill" aria-hidden="true" />
      已鉴权
    </span>
  )

  const kbDelta = knowledgeBases.length > 0 ? 0 : 0
  const docDelta = documents.length > 0 ? 0 : 0
  const convDelta = conversationsCount > 0 ? 0 : 0

  return (
    <section className="content-rail" style={{ paddingTop: 20 }}>
      <div className="qa-hero-layout">
        <div className="qa-hero-secure-wrap">{secureChip}</div>
        <GalaxyHero
          eyebrow={userEmail ? `当前账户 · ${userEmail}` : 'Galaxy Motion v4'}
          title="你的知识库就是你的记忆"
          subtitle="创建知识库、上传文档，一切从这里开始组织你的企业记忆"
          primaryLabel="跳转问答页"
          secondaryLabel="跳转检索中心"
          primaryIcon={Sparkle}
          secondaryIcon={MagnifyingGlass}
          onPrimary={() => goToPage('qa')}
          onSecondary={() => goToPage('search')}
        />
      </div>

      <div style={{ height: 24 }} />

      <div className="kb-metrics-grid">
        <MetricCard label="知识基数" value={knowledgeBases.length} delta={kbDelta} />
        <MetricCard label="文档数" value={documents.length} delta={docDelta} />
        <MetricCard label="会话数" value={conversationsCount} delta={convDelta} />
        <MetricCard label="7 日内问答数" value={0} delta={0} />
      </div>

      <div style={{ height: 24 }} />

      <GalaxyCard variant="kb" leftAccent>
        <form className="kb-create-form-wide" onSubmit={handleKbCreateSubmit}>
          <GalaxyFormField label="知识库名称" helperText="建议包含年份或业务域，便于后续检索">
            <input
              placeholder="如 2026 合规知识库"
              value={newKbName}
              onChange={(event) => setNewKbName(event.target.value)}
              maxLength={120}
            />
          </GalaxyFormField>
          <button type="submit" className="kb-create-btn-wide">
            <Plus size={14} weight="bold" aria-hidden="true" />
            创建
          </button>
        </form>
      </GalaxyCard>

      <div style={{ height: 24 }} />

      {knowledgeBases.length === 0 ? (
        <GalaxyCard variant="default">
          <div style={{ textAlign: 'center', padding: '32px 24px' }}>
            <BookOpen size={36} weight="fill" style={{ color: 'var(--c-ink-3)' }} />
            <h3 style={{ margin: '12px 0 6px', color: 'var(--c-ink-1)' }}>暂无知识库</h3>
            <p style={{ margin: 0, color: 'var(--c-ink-3)', fontSize: 'var(--fs-13)' }}>
              使用上方表单创建你的第一个知识库
            </p>
          </div>
        </GalaxyCard>
      ) : (
        <div className="kb-flip-grid">
          {knowledgeBases.map((kb) => {
            const isSelected = selectedKbId === kb.id
            const kbDocs = documents.filter((d) => d.kb_id === kb.id)
            return (
              <div
                className={`kb-flip-card${isSelected ? ' kb-flip-selected' : ''}`}
                key={kb.id}
              >
                <div className="kb-flip-content">
                  <div className="kb-flip-face kb-flip-front">
                    <button
                      type="button"
                      className="kb-flip-select"
                      onClick={() => onSelectKb(kb.id)}
                    >
                      <div className="kb-flip-front-header">
                        <h3 className="kb-flip-title">{kb.name}</h3>
                        <span className="chip kb-doc-chip">
                          <FileText size={11} weight="fill" aria-hidden="true" />
                          {kb.document_count} 份文档
                        </span>
                      </div>
                      {isSelected && (
                        <div className="kb-flip-selected-glow" aria-hidden="true" />
                      )}
                    </button>
                    {isSelected && kbDocs.length > 0 && (
                      <div className="kb-flip-doc-list">
                        <div className="kb-flip-doc-list-title">
                          <FileText size={12} weight="fill" aria-hidden="true" />
                          当前文档 ({kbDocs.length})
                        </div>
                        {kbDocs.map((doc) => (
                          <div className="kb-flip-doc-item" key={doc.id}>
                            <div className="kb-flip-doc-item-header">
                              <div className="qa-doc-info">
                                <strong>{doc.title}</strong>
                                <small>v{doc.version}</small>
                              </div>
                              <button
                                type="button"
                                className="qa-icon-btn"
                                aria-label={`删除文档 ${doc.title}`}
                                title="删除文档"
                                onClick={() => handleDeleteDocClick(doc.id, doc.title)}
                              >
                                <Trash size={13} weight="bold" aria-hidden="true" />
                              </button>
                            </div>
                            <DocVersionPanel
                              kbId={selectedKbId}
                              doc={doc}
                              onLoadVersions={onLoadDocVersions}
                              onLoadDiff={onLoadDocDiff}
                            />
                          </div>
                        ))}
                      </div>
                    )}
                    {isSelected && kbDocs.length === 0 && (
                      <div className="kb-flip-doc-empty">
                        <Question size={12} weight="fill" aria-hidden="true" />
                        该知识库暂无文档，hover 翻卡上传
                      </div>
                    )}
                  </div>

                  <div className="kb-flip-face kb-flip-back">
                    <div className="kb-flip-back-inner">
                      <h3 className="kb-flip-back-title">{kb.name}</h3>
                      <p className="kb-flip-back-sub">操作面板</p>
                      <div className="kb-flip-actions">
                        <label className="kb-flip-action-btn kb-flip-action-upload">
                          <Upload size={16} weight="fill" aria-hidden="true" />
                          <span>{isUploading ? '上传中…' : '上传文档'}</span>
                          <input
                            type="file"
                            accept=".txt,.md,.pdf,.doc,.docx,.ppt,.pptx,.xls,.xlsx"
                            onChange={onUpload}
                            disabled={isUploading || !isSelected}
                            hidden
                          />
                        </label>
                        <button
                          type="button"
                          className="kb-flip-action-btn kb-flip-action-delete"
                          onClick={() => handleDeleteKbClick(kb.id, kb.name)}
                        >
                          <Trash size={16} weight="fill" aria-hidden="true" />
                          删除该库
                        </button>
                        <button
                          type="button"
                          className="kb-flip-action-btn kb-flip-action-qa"
                          onClick={() => goToPage('qa')}
                        >
                          <ChatCircle size={16} weight="fill" aria-hidden="true" />
                          跳到问答页
                        </button>
                      </div>
                    </div>
                  </div>
                </div>
              </div>
            )
          })}
        </div>
      )}
    </section>
  )
}

export default KbPage
