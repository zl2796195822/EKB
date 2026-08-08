import { useState, type FormEvent, type ReactElement } from 'react'
import type { FeedbackRating, SearchResult } from '../types/api'

export interface ChatMessage {
  id: string
  role: 'user' | 'assistant'
  content: string
  citations: SearchResult[]
  isStreaming?: boolean
  messageId?: string
  finishReason?: string
  error?: string
  feedback?: FeedbackRating
}

interface ChatPanelProps {
  messages: ChatMessage[]
  question: string
  isAsking: boolean
  searchResults: SearchResult[]
  onQuestionChange: (value: string) => void
  onSubmit: (event: FormEvent<HTMLFormElement>) => void
  onCancel?: () => void
  onFeedback?: (messageId: string, rating: FeedbackRating) => void
}

const FINISH_REASON_LABEL: Record<string, string> = {
  refusal: '证据不足，已拒答',
  timeout: '响应超时，已停止',
  cancelled: '已取消',
  error: '处理失败',
}

const FEEDBACK_REASONS: Record<FeedbackRating, string> = {
  UP: '回答准确、引用可靠',
  DOWN: '引用不支持结论或回答有误',
}

export function ChatPanel({
  messages,
  question,
  isAsking,
  searchResults,
  onQuestionChange,
  onSubmit,
  onCancel,
  onFeedback,
}: ChatPanelProps): ReactElement {
  const [feedbackTarget, setFeedbackTarget] = useState<string | null>(null)
  const [feedbackRating, setFeedbackRating] = useState<FeedbackRating | null>(null)
  const [feedbackComment, setFeedbackComment] = useState('')
  return (
    <section className="chat-panel" aria-label="知识问答">
      <header className="content-header">
        <div>
          <p className="eyebrow">授权问答</p>
          <h1>把问题交给知识库</h1>
          <p className="header-copy">答案只基于当前选中的知识库，并展示可回溯的引用。</p>
        </div>
        <span className="secure-chip">
          <span className="secure-dot" aria-hidden="true" />
          检索期权限已启用
        </span>
      </header>

      <div className="chat-body">
        {messages.length === 0 ? (
          <div className="empty-chat">
            <div className="empty-icon" aria-hidden="true">
              ?
            </div>
            <h2>从一个具体问题开始</h2>
            <p>例如：数据库连接池耗尽时，应该先检查哪些指标？</p>
          </div>
        ) : (
          <div className="message-list">
            {messages.map((message) => (
              <article className={`message message-${message.role}`} key={message.id}>
                <div className="message-meta">{message.role === 'user' ? '你' : 'EKB'}</div>
                <div className="message-content">
                  <p>{message.content || (message.isStreaming ? '正在检索并生成回答…' : '')}</p>
                  {message.isStreaming && <span className="stream-cursor" aria-label="生成中" />}
                  {message.error && <p className="message-error" role="alert">{message.error}</p>}
                  {message.finishReason && message.finishReason !== 'stop' && (
                    <p className="message-finish">{FINISH_REASON_LABEL[message.finishReason] ?? message.finishReason}</p>
                  )}
                  {message.citations.length > 0 && (
                    <div className="citation-list">
                      {message.citations.map((citation) => (
                        <div className="citation" key={citation.chunk_id}>
                          <span className="citation-index">引</span>
                          <div>
                            <strong>{citation.title}</strong>
                            <small>{citation.section_path.join(' / ')}</small>
                            <small className="citation-meta">
                              v{citation.doc_version ?? 1} · {formatCitationTime(citation.updated_at)}
                            </small>
                          </div>
                        </div>
                      ))}
                    </div>
                  )}
                  {message.role === 'assistant' && message.messageId && !message.isStreaming && !message.error && (
                    <div className="feedback-bar">
                      {message.feedback ? (
                        <span className="feedback-sent">
                          已反馈：{message.feedback === 'UP' ? '有帮助' : '需改进'}
                        </span>
                      ) : feedbackTarget === message.id ? (
                        <form
                          className="feedback-form"
                          onSubmit={(event) => {
                            event.preventDefault()
                            if (feedbackRating && message.messageId && onFeedback) {
                              onFeedback(message.messageId, feedbackRating)
                              setFeedbackTarget(null)
                              setFeedbackRating(null)
                              setFeedbackComment('')
                            }
                          }}
                        >
                          <div className="feedback-rating">
                            <button
                              type="button"
                              className={`feedback-option ${feedbackRating === 'UP' ? 'is-active' : ''}`}
                              onClick={() => setFeedbackRating('UP')}
                            >
                              有帮助
                            </button>
                            <button
                              type="button"
                              className={`feedback-option ${feedbackRating === 'DOWN' ? 'is-active' : ''}`}
                              onClick={() => setFeedbackRating('DOWN')}
                            >
                              需改进
                            </button>
                          </div>
                          {feedbackRating && (
                            <>
                              <p className="feedback-reason">{FEEDBACK_REASONS[feedbackRating]}</p>
                              <textarea
                                className="feedback-comment"
                                placeholder="补充说明（可选）"
                                value={feedbackComment}
                                onChange={(event) => setFeedbackComment(event.target.value)}
                                rows={2}
                                maxLength={500}
                              />
                              <div className="feedback-actions">
                                <button type="submit" className="primary-button small">
                                  提交
                                </button>
                                <button
                                  type="button"
                                  className="ghost-button"
                                  onClick={() => {
                                    setFeedbackTarget(null)
                                    setFeedbackRating(null)
                                    setFeedbackComment('')
                                  }}
                                >
                                  取消
                                </button>
                              </div>
                            </>
                          )}
                        </form>
                      ) : (
                        <button
                          type="button"
                          className="ghost-button feedback-trigger"
                          onClick={() => setFeedbackTarget(message.id)}
                        >
                          反馈
                        </button>
                      )}
                    </div>
                  )}
                </div>
              </article>
            ))}
          </div>
        )}

        {searchResults.length > 0 && (
          <section className="search-preview" aria-label="检索预览">
            <div className="section-heading">
              <div>
                <div className="section-label">检索预览</div>
                <p className="muted">回答前的授权证据</p>
              </div>
              <span className="result-count">{searchResults.length} 条</span>
            </div>
            {searchResults.map((result) => (
              <div className="search-result" key={result.chunk_id}>
                <strong>{result.title}</strong>
                <span>{result.snippet}</span>
              </div>
            ))}
          </section>
        )}
      </div>

      <form className="composer" onSubmit={onSubmit}>
        <label htmlFor="question" className="sr-only">
          输入问题
        </label>
        <textarea
          id="question"
          value={question}
          onChange={(event) => onQuestionChange(event.target.value)}
          placeholder="询问当前知识库中的制度、SOP 或故障处理方法…"
          rows={3}
          maxLength={2000}
          disabled={isAsking}
        />
        <div className="composer-footer">
          <span className="muted">Enter 发送 · Shift + Enter 换行</span>
          <div className="composer-actions">
            {isAsking && onCancel && (
              <button type="button" className="ghost-button" onClick={onCancel}>
                取消
              </button>
            )}
            <button className="primary-button" type="submit" disabled={isAsking || !question.trim()}>
              {isAsking ? '生成中…' : '发送问题'}
            </button>
          </div>
        </div>
      </form>
    </section>
  )
}

function formatCitationTime(iso: string): string {
  if (!iso) return ''
  const date = new Date(iso)
  if (Number.isNaN(date.getTime())) return iso
  return date.toLocaleDateString('zh-CN', { year: 'numeric', month: '2-digit', day: '2-digit' })
}
