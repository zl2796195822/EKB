import {
  useState,
  type FC,
} from 'react'
import {
  ArrowsLeftRight,
  ClockCounterClockwise,
  Plus,
  Minus,
  ArrowsHorizontal,
  FileText,
} from '@phosphor-icons/react'
import type { DocumentRecord, DocumentVersionRecord, DocumentDiff } from '../types/api'

export interface DocVersionPanelProps {
  kbId: string
  doc: DocumentRecord
  onLoadVersions: (kbId: string, docId: string) => Promise<DocumentVersionRecord[]>
  onLoadDiff: (kbId: string, docId: string, from: number, to: number) => Promise<DocumentDiff>
}

const DocVersionPanel: FC<DocVersionPanelProps> = ({ kbId, doc, onLoadVersions, onLoadDiff }) => {
  const [versions, setVersions] = useState<DocumentVersionRecord[]>([])
  const [diff, setDiff] = useState<DocumentDiff | null>(null)
  const [fromVer, setFromVer] = useState(1)
  const [toVer, setToVer] = useState(2)
  const [isLoadingVersions, setIsLoadingVersions] = useState(false)
  const [isLoadingDiff, setIsLoadingDiff] = useState(false)
  const [showVersions, setShowVersions] = useState(false)
  const [error, setError] = useState('')

  const handleToggleVersions = async () => {
    if (showVersions) {
      setShowVersions(false)
      return
    }
    setShowVersions(true)
    if (versions.length > 0) return
    setIsLoadingVersions(true)
    setError('')
    try {
      const list = await onLoadVersions(kbId, doc.id)
      setVersions(list)
      if (list.length >= 2) {
        setFromVer(list[list.length - 1].version)
        setToVer(list[0].version)
      }
    } catch {
      setError('版本历史加载失败')
    } finally {
      setIsLoadingVersions(false)
    }
  }

  const handleLoadDiff = async () => {
    setIsLoadingDiff(true)
    setError('')
    setDiff(null)
    try {
      const result = await onLoadDiff(kbId, doc.id, fromVer, toVer)
      setDiff(result)
    } catch {
      setError('Diff 加载失败')
    } finally {
      setIsLoadingDiff(false)
    }
  }

  const totalChanges = diff ? diff.added.length + diff.removed.length + diff.changed.length : 0

  return (
    <div className="doc-version-panel">
      <button
        type="button"
        className="doc-version-toggle"
        onClick={handleToggleVersions}
        aria-expanded={showVersions}
      >
        <ClockCounterClockwise size={13} weight="fill" aria-hidden="true" />
        <span>版本历史</span>
        <span className="doc-version-badge">v{doc.version}</span>
      </button>

      {showVersions && (
        <div className="doc-version-drawer">
          {error && (
            <p className="doc-version-error" role="alert">{error}</p>
          )}

          {isLoadingVersions ? (
            <p className="doc-version-loading">加载中…</p>
          ) : versions.length === 0 ? (
            <p className="doc-version-empty">
              <FileText size={12} weight="fill" aria-hidden="true" />
              暂无版本快照
            </p>
          ) : (
            <>
              <div className="doc-version-list">
                {versions.map((v) => (
                  <div key={v.id} className="doc-version-item">
                    <span className="doc-version-tag">v{v.version}</span>
                    <span className="doc-version-meta">{v.chunk_count} chunks</span>
                    <time className="doc-version-time">
                      {new Date(v.created_at).toLocaleString('zh-CN', {
                        month: 'numeric',
                        day: 'numeric',
                        hour: '2-digit',
                        minute: '2-digit',
                      })}
                    </time>
                  </div>
                ))}
              </div>

              {versions.length >= 2 && (
                <div className="doc-version-diff-controls">
                  <div className="doc-version-diff-selectors">
                    <select
                      value={fromVer}
                      onChange={(e) => setFromVer(Number(e.target.value))}
                      className="doc-version-select"
                      aria-label="对比起始版本"
                    >
                      {versions.map((v) => (
                        <option key={v.id} value={v.version}>v{v.version}</option>
                      ))}
                    </select>
                    <ArrowsHorizontal size={14} aria-hidden="true" />
                    <select
                      value={toVer}
                      onChange={(e) => setToVer(Number(e.target.value))}
                      className="doc-version-select"
                      aria-label="对比目标版本"
                    >
                      {versions.map((v) => (
                        <option key={v.id} value={v.version}>v{v.version}</option>
                      ))}
                    </select>
                    <button
                      type="button"
                      className="doc-version-diff-btn"
                      onClick={handleLoadDiff}
                      disabled={isLoadingDiff || fromVer === toVer}
                    >
                      <ArrowsLeftRight size={13} weight="bold" aria-hidden="true" />
                      {isLoadingDiff ? '对比中…' : '对比'}
                    </button>
                  </div>
                </div>
              )}
            </>
          )}

          {diff && (
            <div className="doc-diff-view" aria-label="版本差异">
              <div className="doc-diff-summary">
                <span className="doc-diff-stat doc-diff-added">
                  <Plus size={11} weight="bold" aria-hidden="true" />
                  {diff.added.length} 新增
                </span>
                <span className="doc-diff-stat doc-diff-removed">
                  <Minus size={11} weight="bold" aria-hidden="true" />
                  {diff.removed.length} 删除
                </span>
                <span className="doc-diff-stat doc-diff-changed">
                  <ArrowsLeftRight size={11} weight="bold" aria-hidden="true" />
                  {diff.changed.length} 变更
                </span>
                {totalChanges === 0 && (
                  <span className="doc-diff-stat doc-diff-none">两版本内容相同</span>
                )}
              </div>

              {diff.added.length > 0 && (
                <div className="doc-diff-section">
                  <h4 className="doc-diff-section-title doc-diff-section-added">
                    <Plus size={12} weight="bold" aria-hidden="true" />
                    新增内容
                  </h4>
                  {diff.added.map((chunk, i) => (
                    <div key={i} className="doc-diff-chunk doc-diff-chunk-added">
                      {chunk.section_path.length > 0 && (
                        <div className="doc-diff-path">{chunk.section_path.join(' › ')}</div>
                      )}
                      <pre className="doc-diff-content">{chunk.content}</pre>
                    </div>
                  ))}
                </div>
              )}

              {diff.removed.length > 0 && (
                <div className="doc-diff-section">
                  <h4 className="doc-diff-section-title doc-diff-section-removed">
                    <Minus size={12} weight="bold" aria-hidden="true" />
                    删除内容
                  </h4>
                  {diff.removed.map((chunk, i) => (
                    <div key={i} className="doc-diff-chunk doc-diff-chunk-removed">
                      {chunk.section_path.length > 0 && (
                        <div className="doc-diff-path">{chunk.section_path.join(' › ')}</div>
                      )}
                      <pre className="doc-diff-content">{chunk.content}</pre>
                    </div>
                  ))}
                </div>
              )}

              {diff.changed.length > 0 && (
                <div className="doc-diff-section">
                  <h4 className="doc-diff-section-title doc-diff-section-changed">
                    <ArrowsLeftRight size={12} weight="bold" aria-hidden="true" />
                    变更内容
                  </h4>
                  {diff.changed.map((chunk, i) => (
                    <div key={i} className="doc-diff-chunk doc-diff-chunk-changed">
                      {chunk.section_path.length > 0 && (
                        <div className="doc-diff-path">{chunk.section_path.join(' › ')}</div>
                      )}
                      <div className="doc-diff-changed-pair">
                        <pre className="doc-diff-content doc-diff-before">{chunk.before}</pre>
                        <pre className="doc-diff-content doc-diff-after">{chunk.after}</pre>
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </div>
          )}
        </div>
      )}
    </div>
  )
}

export default DocVersionPanel
