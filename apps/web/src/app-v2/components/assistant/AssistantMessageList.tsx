import { ArrowClockwise, CaretDown, CaretUp, CheckCircle, CircleNotch, Clock, FileText, StopCircle, WarningCircle, XCircle } from '@phosphor-icons/react'
import type { ConversationMessageView, FeedbackRating, QaFinishReason, CitationView } from '../../types'
import { AssistantMarkdown } from './AssistantMarkdown'

export interface AssistantDisplayMessage extends ConversationMessageView {
  readonly transient?: boolean
  readonly streaming?: boolean
  readonly finishReason?: QaFinishReason
  readonly citations?: readonly CitationView[]
}

interface AssistantMessageListProps {
  readonly messages: readonly AssistantDisplayMessage[]
  readonly streaming: boolean
  readonly feedbackByMessage: Readonly<Record<string, { rating?: FeedbackRating; state: 'idle' | 'submitting' | 'submitted' | 'error'; message?: string }>>
  readonly onFeedback: (messageId: string, rating: FeedbackRating) => void
  /** Stop：取消正在运行的 turn（spec 02 §6） */
  readonly onStopTurn?: () => void
  /** Retry：重试失败的 turn（spec 02 §7） */
  readonly onRetryTurn?: (messageId: string, lastQuestion: string) => void
  /** Regenerate：fork 新分支重新生成回答（spec 02 §7） */
  readonly onRegenerate?: (conversationId: string, assistantMessageId: string) => void
  readonly conversationId?: string
  /** 最近一次真实问题，作为 Retry 的兜底（无法从前序消息取到时使用） */
  readonly lastQuestion?: string
}

function formatTime(value: string): string {
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? '—' : new Intl.DateTimeFormat('zh-CN', { hour: '2-digit', minute: '2-digit' }).format(date)
}

function AssistantStatus({ message }: { readonly message: AssistantDisplayMessage }) {
  if (message.streaming) return <span className="v2-m4-message-status is-streaming"><CircleNotch size={13} className="v2-m4-spin" />正在生成回答</span>
  if (message.finishReason === 'cancelled') return <span className="v2-m4-message-status is-cancelled"><WarningCircle size={13} />回答已取消</span>
  if (message.finishReason === 'timeout') return <span className="v2-m4-message-status is-error"><Clock size={13} />回答超时</span>
  if (message.finishReason === 'refusal') return <span className="v2-m4-message-status is-warning"><WarningCircle size={13} />基于当前证据无法确认</span>
  if (message.finishReason === 'error') return <span className="v2-m4-message-status is-error"><XCircle size={13} />回答失败</span>
  return null
}

export function AssistantMessageList({ messages, streaming, feedbackByMessage, onFeedback, onStopTurn, onRetryTurn, onRegenerate, conversationId, lastQuestion }: AssistantMessageListProps) {
  if (messages.length === 0) {
    return <div className="v2-m4-chat-empty"><div className="v2-m4-empty-mark"><FileText size={24} aria-hidden="true" /></div><h2>从一个真实问题开始</h2><p>选择当前授权知识库后，回答会通过 SSE v2 返回，并在右侧展示当前引用。</p></div>
  }

  return (
    <div className="v2-m4-message-list" aria-live="polite">
      {messages.map((message, index) => {
        const feedback = feedbackByMessage[message.id]
        const canFeedback = message.role === 'assistant' && !message.transient && !message.streaming && Boolean(message.content.trim())
        // Stop：仅正在流式生成的 assistant 消息显示（spec 02 §6）
        const canStop = message.role === 'assistant' && Boolean(message.streaming) && Boolean(onStopTurn)
        // Retry：失败的 turn（error / timeout）才允许重试（spec 02 §7）
        const canRetry = message.role === 'assistant' && !message.streaming && (message.finishReason === 'error' || message.finishReason === 'timeout') && Boolean(onRetryTurn)
        // Regenerate：已完成/已停止的 assistant 回答才能 fork 新分支（后端要求 status in completed/stopped）
        const canRegenerate =
          message.role === 'assistant' &&
          !message.streaming &&
          !message.transient &&
          Boolean(message.content.trim()) &&
          message.finishReason !== 'error' &&
          message.finishReason !== 'timeout' &&
          Boolean(conversationId) &&
          Boolean(onRegenerate)
        // Retry 的问题文本：优先取前序最近一条 user 消息内容，兜底用 lastQuestion
        let retryQuestion = lastQuestion ?? ''
        if (canRetry) {
          for (let i = index - 1; i >= 0; i -= 1) {
            if (messages[i].role === 'user' && messages[i].content.trim()) {
              retryQuestion = messages[i].content
              break
            }
          }
        }
        const showActions = message.role === 'assistant' && (canStop || canRetry || canRegenerate)
        return (
          <article className={message.role === 'user' ? 'v2-m4-message v2-m4-message--user' : 'v2-m4-message v2-m4-message--assistant'} key={message.id}>
            <div className="v2-m4-message-meta">
              <strong>{message.role === 'user' ? '你' : 'AI 助手'}</strong>
              <time dateTime={message.createdAt}>{formatTime(message.createdAt)}</time>
              {message.role === 'assistant' ? <AssistantStatus message={message} /> : null}
            </div>
            <div className="v2-m4-message-body">
              {message.content
                ? (message.role === 'assistant'
                    ? <AssistantMarkdown content={message.content} streaming={message.streaming} />
                    : message.content)
                : (message.streaming ? '正在等待首个回答片段…' : '回答没有可显示内容。')}
            </div>
            {message.citations && message.citations.length > 0 ? (
              <div className="v2-m4-inline-citations"><span>引用</span>{message.citations.map((citation) => <span key={citation.citationId}><FileText size={12} />{citation.title}</span>)}</div>
            ) : null}
            {canFeedback ? (
              <div className="v2-m4-feedback" aria-label="回答反馈">
                <span>这条回答有帮助吗？</span>
                <button type="button" disabled={feedback?.state === 'submitting' || feedback?.state === 'submitted'} className={feedback?.rating === 'UP' ? 'is-selected' : ''} onClick={() => onFeedback(message.id, 'UP')}><CaretUp size={14} />有帮助</button>
                <button type="button" disabled={feedback?.state === 'submitting' || feedback?.state === 'submitted'} className={feedback?.rating === 'DOWN' ? 'is-selected' : ''} onClick={() => onFeedback(message.id, 'DOWN')}><CaretDown size={14} />需改进</button>
                {feedback?.state === 'submitting' ? <small>正在提交…</small> : null}
                {feedback?.state === 'submitted' ? <small className="is-success"><CheckCircle size={12} />已提交</small> : null}
                {feedback?.state === 'error' ? <small className="is-error">{feedback.message ?? '提交失败，可重试。'}</small> : null}
              </div>
            ) : null}
            {showActions ? (
              <div className="v2-m4-message-actions" aria-label="回答操作">
                {canStop ? (
                  <button type="button" className="v2-m4-message-action v2-m4-message-action--stop" onClick={() => onStopTurn?.()} title="停止当前生成"><StopCircle size={13} />停止生成</button>
                ) : null}
                {canRetry ? (
                  <button type="button" className="v2-m4-message-action" onClick={() => onRetryTurn?.(message.id, retryQuestion)} title="使用新 turn 重新发起该问题"><ArrowClockwise size={13} />重试</button>
                ) : null}
                {canRegenerate ? (
                  <button type="button" className="v2-m4-message-action" onClick={() => onRegenerate?.(conversationId!, message.id)} title="fork 新分支重新生成此回答"><ArrowClockwise size={13} />重新生成</button>
                ) : null}
              </div>
            ) : null}
          </article>
        )
      })}
      {streaming ? <div className="v2-m4-stream-note"><CircleNotch size={14} className="v2-m4-spin" />流式连接保持活动，心跳由 SSE v2 适配器处理</div> : null}
    </div>
  )
}
