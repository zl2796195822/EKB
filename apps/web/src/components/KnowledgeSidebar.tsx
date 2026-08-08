import { useState, type ChangeEvent, type FormEvent, type ReactElement } from 'react'
import type { DocumentRecord, KnowledgeBase } from '../types/api'
import { StatusBadge } from './StatusBadge'

interface KnowledgeSidebarProps {
  knowledgeBases: KnowledgeBase[]
  selectedKbId: string
  documents: DocumentRecord[]
  isUploading: boolean
  onSelect: (kbId: string) => void
  onUpload: (event: ChangeEvent<HTMLInputElement>) => void
  onCreateKb: (name: string) => Promise<void>
  onDeleteKb: (kbId: string) => Promise<void>
  onDeleteDoc: (docId: string) => Promise<void>
}

export function KnowledgeSidebar({
  knowledgeBases,
  selectedKbId,
  documents,
  isUploading,
  onSelect,
  onUpload,
  onCreateKb,
  onDeleteKb,
  onDeleteDoc,
}: KnowledgeSidebarProps): ReactElement {
  const [isCreating, setIsCreating] = useState(false)
  const [newKbName, setNewKbName] = useState('')

  const handleCreateSubmit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    const trimmed = newKbName.trim()
    if (!trimmed) return
    await onCreateKb(trimmed)
    setNewKbName('')
    setIsCreating(false)
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

  return (
    <aside className="sidebar" aria-label="知识库导航">
      <div className="brand-lockup">
        <span className="brand-mark">E</span>
        <div>
          <strong>EKB</strong>
          <span>企业知识库</span>
        </div>
      </div>

      <div className="sidebar-section">
        <div className="section-heading">
          <div className="section-label">知识库</div>
          {!isCreating && (
            <button
              type="button"
              className="ghost-button"
              onClick={() => setIsCreating(true)}
            >
              + 新建
            </button>
          )}
        </div>
        {isCreating && (
          <form className="kb-create-form" onSubmit={handleCreateSubmit}>
            <input
              autoFocus
              placeholder="知识库名称"
              value={newKbName}
              onChange={(event) => setNewKbName(event.target.value)}
              maxLength={120}
            />
            <div className="kb-create-actions">
              <button type="submit" className="primary-button small">
                创建
              </button>
              <button
                type="button"
                className="ghost-button"
                onClick={() => {
                  setIsCreating(false)
                  setNewKbName('')
                }}
              >
                取消
              </button>
            </div>
          </form>
        )}
        <div className="kb-list">
          {knowledgeBases.length === 0 && !isCreating ? (
            <p className="muted">暂无授权知识库</p>
          ) : (
            knowledgeBases.map((knowledgeBase) => (
              <div
                className={`kb-item ${selectedKbId === knowledgeBase.id ? 'is-active' : ''}`}
                key={knowledgeBase.id}
              >
                <button
                  type="button"
                  className="kb-select"
                  onClick={() => onSelect(knowledgeBase.id)}
                >
                  <span className="kb-dot" aria-hidden="true" />
                  <span>
                    <strong>{knowledgeBase.name}</strong>
                    <small>{knowledgeBase.document_count} 份文档</small>
                  </span>
                </button>
                <button
                  type="button"
                  className="icon-delete"
                  aria-label={`删除知识库 ${knowledgeBase.name}`}
                  title="删除知识库"
                  onClick={() => handleDeleteKbClick(knowledgeBase.id, knowledgeBase.name)}
                >
                  ×
                </button>
              </div>
            ))
          )}
        </div>
      </div>

      <div className="sidebar-section sidebar-documents">
        <div className="section-heading">
          <div className="section-label">文档</div>
          <label className="upload-button">
            <span>{isUploading ? '上传中…' : '上传'}</span>
            <input
              type="file"
              accept=".txt,.md,.pdf,.doc,.docx,.ppt,.pptx,.xls,.xlsx"
              onChange={onUpload}
              disabled={isUploading || !selectedKbId}
            />
          </label>
        </div>
        {documents.length === 0 ? (
          <p className="muted">选择知识库后查看文档</p>
        ) : (
          <ul className="document-list">
            {documents.map((document) => (
              <li key={document.id}>
                <div className="doc-info">
                  <strong>{document.title}</strong>
                  <small>v{document.version}</small>
                </div>
                <StatusBadge status={document.status} />
                <button
                  type="button"
                  className="icon-delete"
                  aria-label={`删除文档 ${document.title}`}
                  title="删除文档"
                  onClick={() => handleDeleteDocClick(document.id, document.title)}
                >
                  ×
                </button>
              </li>
            ))}
          </ul>
        )}
      </div>
    </aside>
  )
}
