import {
  useEffect,
  useRef,
  useState,
  type ChangeEvent,
  type FC,
  type FormEvent,
  type KeyboardEvent,
} from 'react'
import {
  Plus,
  Trash,
  PaperPlaneTilt,
  StopCircle,
  ThumbsUp,
  ThumbsDown,
  ShieldCheck,
  Sparkle,
  MagnifyingGlass,
  ArrowSquareOut,
  BookOpen,
  ChatCircle,
} from '@phosphor-icons/react'
import { GalaxyCard, GalaxyHero, GalaxyFormField } from '../components/ui'
import type {
  ConversationSummary,
  DocumentRecord,
  DocumentStatus,
  FeedbackRating,
  KnowledgeBase,
  SearchResult,
} from '../types/api'

export interface QAPageProps {
  knowledgeBases: KnowledgeBase[]
  selectedKbId: string
  documents: DocumentRecord[]
  messages: Array<{
    id: string
    role: 'user' | 'assistant'
    content: string
    citations: SearchResult[]
    messageId?: string
    isStreaming?: boolean
    error?: string
    finishReason?: string
    feedback?: FeedbackRating
  }>
  question: string
  searchResults: SearchResult[]
  conversations: ConversationSummary[]
  activeConversationId: string | undefined
  isAsking: boolean
  isLoading: boolean
  isUploading: boolean
  userEmail?: string
  goToPage: (page: 'qa' | 'kb' | 'search' | 'ops') => void
  onChangeQuestion: (value: string) => void
  onSubmitAsk: (event: FormEvent<HTMLFormElement>) => void
  onCancelAsk: () => void
  onSelectConversation: (conversationId: string) => void
  onDeleteConversation: (conversationId: string) => Promise<void>
  onFeedback: (messageId: string, rating: FeedbackRating) => void
  onCreateKb: (name: string) => Promise<void>
  onDeleteKb: (kbId: string) => Promise<void>
  onDeleteDoc: (docId: string) => Promise<void>
  onUpload: (event: ChangeEvent<HTMLInputElement>) => void
  onSelectKb: (kbId: string) => void
  onClearMessages: () => void
}

const QAPage: FC<QAPageProps> = (props) => {
  const {
    knowledgeBases,
    selectedKbId,
    documents,
    messages,
    question,
    conversations,
    activeConversationId,
    isAsking,
    isUploading,
    userEmail,
    goToPage,
    onChangeQuestion,
    onSubmitAsk,
    onCancelAsk,
    onSelectConversation,
    onDeleteConversation,
    onFeedback,
    onCreateKb,
    onDeleteKb,
    onDeleteDoc,
    onUpload,
    onSelectKb,
    onClearMessages,
  } = props

  const scrollRef = useRef<HTMLDivElement | null>(null)
  const textareaRef = useRef<HTMLTextAreaElement | null>(null)
  const [isCreatingKb, setIsCreatingKb] = useState(false)
  const [newKbName, setNewKbName] = useState('')
  const prefersReduced =
    typeof window !== 'undefined' &&
    window.matchMedia('(prefers-reduced-motion: reduce)').matches

  useEffect(() => {
    if (!scrollRef.current) return
    const el = scrollRef.current
    el.scrollTo({
      top: el.scrollHeight,
      behavior: prefersReduced ? 'auto' : 'smooth',
    })
  }, [messages.length, prefersReduced])

  const focusInput = () => {
    setTimeout(() => textareaRef.current?.focus(), 0)
  }

  const handleNewConversation = () => {
    onClearMessages()
    focusInput()
  }

  const handleKbCreateSubmit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    const trimmed = newKbName.trim()
    if (!trimmed) return
    await onCreateKb(trimmed)
    setNewKbName('')
    setIsCreatingKb(false)
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

  const handleDeleteConvClick = (
    event: React.MouseEvent,
    conversationId: string,
    title: string,
  ) => {
    event.stopPropagation()
    if (window.confirm(`确认删除会话「${title}」？`)) {
      void onDeleteConversation(conversationId)
    }
  }

  const handleTextareaKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    const isMac = /Mac|iPhone|iPad/i.test(navigator.userAgent)
    const ctrlOrCmd = isMac ? event.metaKey : event.ctrlKey
    if (ctrlOrCmd && event.key === 'Enter') {
      event.preventDefault()
      const form = (event.target as HTMLTextAreaElement).closest('form')
      if (form) form.requestSubmit()
      return
    }
    if (event.key === 'Enter' && !event.shiftKey && !ctrlOrCmd) {
      event.preventDefault()
      const form = (event.target as HTMLTextAreaElement).closest('form')
      if (form) form.requestSubmit()
    }
  }

  const statusLabel: Record<DocumentStatus, string> = {
    PROCESSING: '处理中',
    READY: '就绪',
    FAILED: '失败',
    DELETED: '已删除',
  }

  const statusClass: Record<DocumentStatus, string> = {
    PROCESSING: 'status-processing',
    READY: 'status-ready',
    FAILED: 'status-failed',
    DELETED: 'status-failed',
  }

  const formatTime = (iso: string): string => {
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

  const secureChip = (
    <span className="qa-secure-chip">
      <ShieldCheck size={14} weight="fill" aria-hidden="true" />
      已鉴权
    </span>
  )

  return (
    <section className="content-rail" style={{ paddingTop: 20 }}>
      <div className="qa-hero-layout">
        <div className="qa-hero-secure-wrap">{secureChip}</div>
        <GalaxyHero
          eyebrow={userEmail ? `当前账户 · ${userEmail}` : 'Galaxy Motion v4'}
          title="让每个答案回到证据"
          subtitle="企业级 RAG 问答助手，每条回答都能回到证据原文"
          primaryLabel="新建会话"
          secondaryLabel="跳转检索中心"
          primaryIcon={Sparkle}
          secondaryIcon={MagnifyingGlass}
          onPrimary={handleNewConversation}
          onSecondary={() => goToPage('search')}
        />
      </div>

      <div style={{ height: 24 }} />

      <div className="galaxy-qa-grid">
        <div className="galaxy-qa-left">
          <GalaxyCard variant="kb" leftAccent>
            <div className="qa-left-section">
              <div className="qa-top-row">
                <h4 className="qa-left-section-title">
                  <BookOpen size={14} weight="fill" aria-hidden="true" />
                  知识库列表
                </h4>
                {!isCreatingKb && (
                  <button
                    type="button"
                    className="qa-ghost-sm"
                    onClick={() => setIsCreatingKb(true)}
                  >
                    <Plus size={12} weight="bold" style={{ marginRight: 4 }} />
                    新建
                  </button>
                )}
              </div>

              {isCreatingKb && (
                <form className="qa-create-form" onSubmit={handleKbCreateSubmit}>
                  <input
                    autoFocus
                    placeholder="知识库名称"
                    value={newKbName}
                    onChange={(event) => setNewKbName(event.target.value)}
                    maxLength={120}
                  />
                  <div className="qa-create-actions">
                    <button type="submit" className="qa-primary-sm">
                      创建
                    </button>
                    <button
                      type="button"
                      className="qa-ghost-sm"
                      onClick={() => {
                        setIsCreatingKb(false)
                        setNewKbName('')
                      }}
                    >
                      取消
                    </button>
                  </div>
                </form>
              )}

              {knowledgeBases.length === 0 && !isCreatingKb ? (
                <p className="qa-mute-text">暂无授权知识库</p>
              ) : (
                <div style={{ display: 'grid', gap: 2 }}>
                  {knowledgeBases.map((kb) => (
                    <div
                      className={`qa-kb-item ${selectedKbId === kb.id ? 'active' : ''}`}
                      key={kb.id}
                    >
                      <button
                        type="button"
                        style={{
                          display: 'flex',
                          alignItems: 'center',
                          gap: 10,
                          flex: '1 1 auto',
                          minWidth: 0,
                          padding: 0,
                          border: 0,
                          background: 'transparent',
                          textAlign: 'left',
                          cursor: 'pointer',
                        }}
                        onClick={() => onSelectKb(kb.id)}
                      >
                        <span className="qa-kb-dot" aria-hidden="true" />
                        <div className="qa-kb-info">
                          <strong>{kb.name}</strong>
                          <small>{kb.document_count} 份文档</small>
                        </div>
                      </button>
                      <button
                        type="button"
                        className="qa-icon-btn"
                        aria-label={`删除知识库 ${kb.name}`}
                        title="删除知识库"
                        onClick={() => handleDeleteKbClick(kb.id, kb.name)}
                      >
                        <Trash size={14} weight="bold" aria-hidden="true" />
                      </button>
                    </div>
                  ))}
                </div>
              )}

              <div className="qa-left-divider" />

              <div className="qa-top-row">
                <h4 className="qa-left-section-title">文档</h4>
                <label className="qa-upload-label">
                  <Plus size={11} weight="bold" aria-hidden="true" />
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
                <p className="qa-mute-text">选择知识库后查看文档</p>
              ) : (
                <div>
                  {documents.map((doc) => (
                    <div className="qa-doc-item" key={doc.id}>
                      <div className="qa-doc-info">
                        <strong>{doc.title}</strong>
                        <small>
                          v{doc.version} ·{' '}
                          <span
                            className={`status ${statusClass[doc.status]}`}
                            style={{ marginLeft: 2 }}
                          >
                            {statusLabel[doc.status]}
                          </span>
                        </small>
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
                  ))}
                </div>
              )}
            </div>
          </GalaxyCard>

          <GalaxyCard variant="conversation" leftAccent>
            <div className="qa-left-section">
              <div className="qa-top-row">
                <h4 className="qa-left-section-title">
                  <ChatCircle size={14} weight="fill" aria-hidden="true" />
                  会话历史
                  {conversations.length > 0 ? ` (${conversations.length})` : ''}
                </h4>
              </div>
              {conversations.length === 0 ? (
                <p className="qa-mute-text">暂无历史会话</p>
              ) : (
                <div style={{ display: 'grid', gap: 2 }}>
                  {conversations.map((conv) => (
                    <div
                      className={`qa-conv-item ${activeConversationId === conv.id ? 'active' : ''}`}
                      key={conv.id}
                    >
                      <button
                        type="button"
                        className="qa-conv-select"
                        onClick={() => onSelectConversation(conv.id)}
                      >
                        <strong>{conv.title || '未命名会话'}</strong>
                        <small>{formatTime(conv.updated_at)}</small>
                      </button>
                      <button
                        type="button"
                        className="qa-icon-btn"
                        aria-label={`删除会话 ${conv.title}`}
                        title="删除会话"
                        onClick={(event) =>
                          handleDeleteConvClick(event, conv.id, conv.title)
                        }
                      >
                        <Trash size={13} weight="bold" aria-hidden="true" />
                      </button>
                    </div>
                  ))}
                </div>
              )}
            </div>
          </GalaxyCard>
        </div>

        <div className="galaxy-qa-right">
          <GalaxyCard variant="default" className="chat-pan">
            <div className="chat-body-scroll" ref={scrollRef} aria-label="消息列表">
              {messages.length === 0 ? (
                <div
                  style={{
                    display: 'grid',
                    placeContent: 'center',
                    minHeight: 300,
                    textAlign: 'center',
                    gap: 12,
                    padding: 24,
                  }}
                >
                  <div
                    style={{
                      width: 56,
                      height: 56,
                      margin: '0 auto',
                      display: 'grid',
                      placeItems: 'center',
                      borderRadius: 16,
                      background:
                        'linear-gradient(135deg, rgba(37,99,235,0.12), rgba(13,148,136,0.1))',
                      color: 'var(--c-primary)',
                    }}
                  >
                    <Sparkle size={28} weight="fill" />
                  </div>
                  <h3
                    style={{
                      margin: 0,
                      fontSize: 20,
                      color: 'var(--c-ink-1)',
                      fontWeight: 700,
                    }}
                  >
                    从一个具体问题开始
                  </h3>
                  <p
                    style={{
                      margin: 0,
                      maxWidth: 420,
                      color: 'var(--c-ink-3)',
                      fontSize: 14,
                      lineHeight: 1.7,
                    }}
                  >
                    例如：数据库连接池耗尽时，应该先检查哪些指标？
                  </p>
                </div>
              ) : (
                messages.map((message) => (
                  <div
                    className={`message-row ${message.role}`}
                    key={message.id}
                  >
                    <span className="message-role-label">
                      {message.role === 'user' ? '你' : 'EKB 助手'}
                    </span>

                    <div
                      className={
                        message.role === 'user' ? 'bubble-user' : 'bubble-assistant'
                      }
                    >
                      {message.role === 'assistant' && !message.content && message.isStreaming
                        ? '正在检索并生成回答…'
                        : message.content}
                      {message.role === 'assistant' && message.isStreaming && (
                        <span className="typewriter-caret" aria-hidden="true">
                          ▍
                        </span>
                      )}
                      {message.error && (
                        <div className="message-error-bubble" role="alert">
                          {message.error}
                        </div>
                      )}
                    </div>

                    {message.role === 'assistant' && message.citations.length > 0 && (
                      <div className="cit-strip" style={{ width: '100%' }}>
                        {message.citations.map((cit) => (
                          <GalaxyCard variant="document" key={cit.chunk_id}>
                            <p className="cit-doc-title">{cit.title}</p>
                            <p className="cit-doc-snippet">{cit.snippet}</p>
                            <button
                              type="button"
                              className="cit-doc-open-btn"
                              onClick={() => {
                                const maybeUrl =
                                  (cit as unknown as { source_url?: string }).source_url || ''
                                if (maybeUrl) window.open(maybeUrl, '_blank', 'noopener')
                              }}
                            >
                              <ArrowSquareOut size={11} weight="bold" aria-hidden="true" />
                              打开原文
                            </button>
                          </GalaxyCard>
                        ))}
                      </div>
                    )}

                    {message.role === 'assistant' &&
                      message.messageId &&
                      !message.isStreaming &&
                      !message.error && (
                        <div className="message-actions">
                          <button
                            type="button"
                            className={`feedback-btn up ${message.feedback === 'UP' ? 'active' : ''}`}
                            disabled={!!message.feedback}
                            onClick={() => onFeedback(message.messageId as string, 'UP')}
                            aria-label="赞"
                          >
                            <ThumbsUp size={12} weight={message.feedback === 'UP' ? 'fill' : 'regular'} aria-hidden="true" />
                            赞
                          </button>
                          <button
                            type="button"
                            className={`feedback-btn down ${message.feedback === 'DOWN' ? 'active' : ''}`}
                            disabled={!!message.feedback}
                            onClick={() => onFeedback(message.messageId as string, 'DOWN')}
                            aria-label="踩"
                          >
                            <ThumbsDown size={12} weight={message.feedback === 'DOWN' ? 'fill' : 'regular'} aria-hidden="true" />
                            踩
                          </button>
                        </div>
                      )}
                  </div>
                ))
              )}
            </div>

            <form className="qa-composer" onSubmit={onSubmitAsk}>
              <GalaxyFormField
                label="问题"
                helperText={
                  !selectedKbId
                    ? '请先从左侧选择一个知识库'
                    : 'Enter 发送，⇧ Enter 换行，⌘/Ctrl+Enter 发送'
                }
                isError={!selectedKbId}
              >
                <div className="qa-composer-inner">
                  <textarea
                    ref={textareaRef}
                    id="qa-question"
                    className="qa-composer-textarea"
                    rows={2}
                    value={question}
                    onChange={(event) => onChangeQuestion(event.target.value)}
                    onKeyDown={handleTextareaKeyDown}
                    placeholder={
                      selectedKbId
                        ? '询问当前知识库中的制度、SOP 或故障处理方法…'
                        : '请先从左侧选择一个知识库'
                    }
                    maxLength={2000}
                    disabled={isAsking || !selectedKbId}
                  />
                  <div className="qa-composer-actions">
                    {isAsking ? (
                      <button
                        type="button"
                        className="qa-stop-btn"
                        onClick={onCancelAsk}
                      >
                        <StopCircle size={16} weight="fill" aria-hidden="true" />
                        停止
                      </button>
                    ) : (
                      <button
                        type="submit"
                        className="qa-send-btn"
                        disabled={!question.trim() || isAsking || !selectedKbId}
                      >
                        <PaperPlaneTilt size={16} weight="fill" aria-hidden="true" />
                        发送
                      </button>
                    )}
                  </div>
                </div>
              </GalaxyFormField>
            </form>
          </GalaxyCard>
        </div>
      </div>
    </section>
  )
}

export default QAPage
