import { useState, type ReactElement } from 'react'
import type { ConversationSummary } from '../types/api'

interface ConversationHistoryProps {
  conversations: ConversationSummary[]
  activeConversationId: string | undefined
  onSelect: (conversationId: string) => void
  onDelete: (conversationId: string) => Promise<void>
}

export function ConversationHistory({
  conversations,
  activeConversationId,
  onSelect,
  onDelete,
}: ConversationHistoryProps): ReactElement {
  const [isOpen, setIsOpen] = useState(false)

  const handleDelete = async (event: React.MouseEvent, conversationId: string, title: string) => {
    event.stopPropagation()
    if (window.confirm(`确认删除会话「${title}」？`)) {
      await onDelete(conversationId)
    }
  }

  return (
    <div className="conversation-history">
      <button
        type="button"
        className={`ghost-button history-toggle ${isOpen ? 'is-active' : ''}`}
        onClick={() => setIsOpen((current) => !current)}
      >
        历史会话{conversations.length > 0 ? ` (${conversations.length})` : ''}
      </button>
      {isOpen && (
        <div className="history-panel">
          {conversations.length === 0 ? (
            <p className="muted">暂无历史会话</p>
          ) : (
            <ul className="history-list">
              {conversations.map((conversation) => (
                <li
                  key={conversation.id}
                  className={`history-item ${activeConversationId === conversation.id ? 'is-active' : ''}`}
                >
                  <button
                    type="button"
                    className="history-select"
                    onClick={() => {
                      onSelect(conversation.id)
                      setIsOpen(false)
                    }}
                  >
                    <strong>{conversation.title || '未命名会话'}</strong>
                    <small>{formatTime(conversation.updated_at)}</small>
                  </button>
                  <button
                    type="button"
                    className="icon-delete"
                    aria-label={`删除会话 ${conversation.title}`}
                    title="删除会话"
                    onClick={(event) => handleDelete(event, conversation.id, conversation.title)}
                  >
                    ×
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </div>
  )
}

function formatTime(iso: string): string {
  const date = new Date(iso)
  if (Number.isNaN(date.getTime())) return iso
  const now = new Date()
  const diffMs = now.getTime() - date.getTime()
  const diffMin = Math.floor(diffMs / 60000)
  if (diffMin < 1) return '刚刚'
  if (diffMin < 60) return `${diffMin} 分钟前`
  const diffHour = Math.floor(diffMin / 60)
  if (diffHour < 24) return `${diffHour} 小时前`
  const diffDay = Math.floor(diffHour / 24)
  if (diffDay < 7) return `${diffDay} 天前`
  return date.toLocaleDateString('zh-CN')
}
