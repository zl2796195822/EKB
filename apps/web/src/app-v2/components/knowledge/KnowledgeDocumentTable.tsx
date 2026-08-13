import { ArrowClockwise, ClockCounterClockwise, Eye, FileText, Trash } from '@phosphor-icons/react'
import { StatusPill } from '../ui'
import type { DocumentView } from '../../types'

interface KnowledgeDocumentTableProps {
  readonly documents: readonly DocumentView[]
  readonly loading: boolean
  readonly onDetail: (document: DocumentView) => void
  readonly onDelete: (document: DocumentView) => void
  readonly onRetry: (document: DocumentView) => void
  readonly onVersions: (document: DocumentView) => void
}

export function KnowledgeDocumentTable({ documents, loading, onDetail, onDelete, onRetry, onVersions }: KnowledgeDocumentTableProps) {
  return (
    <div className="v2-m3-table-wrap">
      <table className="v2-m3-table">
        <thead>
          <tr>
            <th scope="col">文档名称</th>
            <th scope="col">类型</th>
            <th scope="col">版本</th>
            <th scope="col">更新时间</th>
            <th scope="col">状态</th>
            <th scope="col"><span className="v2-sr-only">操作</span></th>
          </tr>
        </thead>
        <tbody>
          {loading ? (
            <tr><td colSpan={6} className="v2-m3-table-message">正在加载真实文档…</td></tr>
          ) : documents.length === 0 ? (
            <tr><td colSpan={6} className="v2-m3-table-message">当前知识库没有已加载的文档。</td></tr>
          ) : (
            documents.map((document) => (
              <tr key={document.id}>
                <td>
                  <button type="button" className="v2-m3-document-link" onClick={() => onDetail(document)}>
                    <FileText size={16} aria-hidden="true" />
                    <span>{document.title}</span>
                  </button>
                  {document.failureReason ? <small className="v2-m3-row-error">{document.failureReason}</small> : null}
                </td>
                <td>{document.mimeType || '—'}</td>
                <td>v{document.version}</td>
                <td><time dateTime={document.updatedAt}>{formatDate(document.updatedAt)}</time></td>
                <td><StatusPill status={document.status} /></td>
                <td>
                  <div className="v2-m3-row-actions">
                    <button type="button" className="v2-m3-icon-button" onClick={() => onDetail(document)} aria-label={`查看 ${document.title} 详情`} title="查看详情">
                      <Eye size={15} aria-hidden="true" />
                    </button>
                    <button type="button" className="v2-m3-icon-button" onClick={() => onVersions(document)} aria-label={`查看 ${document.title} 版本`} title="版本与 diff">
                      <ClockCounterClockwise size={15} aria-hidden="true" />
                    </button>
                    {document.status === 'FAILED' ? (
                      <button type="button" className="v2-m3-icon-button" onClick={() => onRetry(document)} aria-label={`重试 ${document.title}`} title="失败重试">
                        <ArrowClockwise size={15} aria-hidden="true" />
                      </button>
                    ) : null}
                    <button type="button" className="v2-m3-icon-button v2-m3-icon-button--danger" onClick={() => onDelete(document)} aria-label={`删除 ${document.title}`} title="删除文档">
                      <Trash size={15} aria-hidden="true" />
                    </button>
                  </div>
                </td>
              </tr>
            ))
          )}
        </tbody>
      </table>
    </div>
  )
}

function formatDate(value: string): string {
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? value : date.toLocaleDateString('zh-CN')
}
