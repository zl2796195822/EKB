import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ChangeEvent,
  type FormEvent,
  type ReactElement,
} from 'react'
import { ChatPanel, type ChatMessage } from './components/ChatPanel'
import { ConversationHistory } from './components/ConversationHistory'
import { ErrorBoundary } from './components/ErrorBoundary'
import { KnowledgeSidebar } from './components/KnowledgeSidebar'
import { ApiClient } from './lib/api'
import type {
  ConversationMessage,
  ConversationSummary,
  DocumentRecord,
  FeedbackRating,
  KnowledgeBase,
  LoginResponse,
  SearchResult,
} from './types/api'
import './styles.css'

const defaultApiBaseUrl = import.meta.env.VITE_API_BASE_URL ?? 'http://127.0.0.1:8023/api/v1'

export function App(): ReactElement {
  const client = useMemo(() => new ApiClient(defaultApiBaseUrl), [])
  const [session, setSession] = useState<LoginResponse | null>(null)
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [knowledgeBases, setKnowledgeBases] = useState<KnowledgeBase[]>([])
  const [selectedKbId, setSelectedKbId] = useState('')
  const [documents, setDocuments] = useState<DocumentRecord[]>([])
  const [messages, setMessages] = useState<ChatMessage[]>([])
  const [question, setQuestion] = useState('')
  const [searchResults, setSearchResults] = useState<SearchResult[]>([])
  const [conversations, setConversations] = useState<ConversationSummary[]>([])
  const [activeConversationId, setActiveConversationId] = useState<string | undefined>(undefined)
  const [error, setError] = useState('')
  const [isLoading, setIsLoading] = useState(false)
  const [isUploading, setIsUploading] = useState(false)
  const [isAsking, setIsAsking] = useState(false)
  const abortController = useRef<AbortController | null>(null)
  // 当前会话 ID：同一会话内续接问答，切换知识库或新建对话时重置。
  // 同步到 ref 以便 ask 闭包读取最新值，state 用于驱动历史面板高亮。
  const conversationIdRef = useRef<string | undefined>(undefined)

  const syncConversation = useCallback((id: string | undefined) => {
    conversationIdRef.current = id
    setActiveConversationId(id)
  }, [])

  useEffect(() => {
    client.setOnAuthFailure(() => {
      setSession(null)
      setMessages([])
      setError('登录已过期，请重新登录')
    })
  }, [client])

  useEffect(() => {
    if (!session) return
    client.setToken(session.access_token)
    client.setRefreshToken(session.refresh_token)
    void loadKnowledgeBases()
    void loadConversations()
  }, [client, session])

  useEffect(() => {
    if (!selectedKbId) return
    void loadDocuments(selectedKbId)
    // 切换知识库时开始新会话，避免跨库追问造成上下文错位。
    syncConversation(undefined)
    setMessages([])
    setSearchResults([])
  }, [selectedKbId, syncConversation])

  const loadKnowledgeBases = useCallback(async () => {
    setIsLoading(true)
    setError('')
    try {
      const nextKnowledgeBases = await client.listKnowledgeBases()
      setKnowledgeBases(nextKnowledgeBases)
      setSelectedKbId((current) => current || nextKnowledgeBases[0]?.id || '')
    } catch (loadError) {
      setError(loadError instanceof Error ? loadError.message : '知识库加载失败')
    } finally {
      setIsLoading(false)
    }
  }, [client])

  const loadDocuments = useCallback(async (kbId: string) => {
    try {
      setDocuments(await client.listDocuments(kbId))
    } catch (loadError) {
      setError(loadError instanceof Error ? loadError.message : '文档加载失败')
    }
  }, [client])

  const loadConversations = useCallback(async () => {
    try {
      setConversations(await client.listConversations())
    } catch (loadError) {
      // 历史会话加载失败不阻塞主流程，仅在控制台留痕。
      console.warn('会话历史加载失败', loadError)
    }
  }, [client])

  const handleLogin = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    setIsLoading(true)
    setError('')
    try {
      const nextSession = await client.login(email, password)
      client.setToken(nextSession.access_token)
      setSession(nextSession)
    } catch (loginError) {
      setError(loginError instanceof Error ? loginError.message : '登录失败')
    } finally {
      setIsLoading(false)
    }
  }

  const handleCreateKb = async (name: string) => {
    setError('')
    try {
      await client.createKnowledgeBase(name)
      await loadKnowledgeBases()
    } catch (createError) {
      setError(createError instanceof Error ? createError.message : '创建知识库失败')
    }
  }

  const handleDeleteKb = async (kbId: string) => {
    setError('')
    try {
      await client.deleteKnowledgeBase(kbId)
      if (selectedKbId === kbId) setSelectedKbId('')
      await loadKnowledgeBases()
    } catch (deleteError) {
      setError(deleteError instanceof Error ? deleteError.message : '删除知识库失败')
    }
  }

  const handleDeleteDoc = async (docId: string) => {
    if (!selectedKbId) return
    setError('')
    try {
      await client.deleteDocument(selectedKbId, docId)
      await Promise.all([loadDocuments(selectedKbId), loadKnowledgeBases()])
    } catch (deleteError) {
      setError(deleteError instanceof Error ? deleteError.message : '删除文档失败')
    }
  }

  const handleLogout = async () => {
    if (session) {
      try {
        await client.logout(session.refresh_token)
      } catch {
        // 登出失败不阻塞前端清理，本地凭据仍需清除。
      }
    }
    client.setToken(null)
    client.setRefreshToken(null)
    setSession(null)
    setMessages([])
    setKnowledgeBases([])
    setDocuments([])
    setSelectedKbId('')
    setSearchResults([])
    setConversations([])
    syncConversation(undefined)
  }

  const handleUpload = async (event: ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0]
    event.target.value = ''
    if (!file || !selectedKbId) return
    setIsUploading(true)
    setError('')
    try {
      await client.uploadDocument(selectedKbId, file)
      await Promise.all([loadDocuments(selectedKbId), loadKnowledgeBases()])
    } catch (uploadError) {
      setError(uploadError instanceof Error ? uploadError.message : '上传失败')
    } finally {
      setIsUploading(false)
    }
  }

  const handleAsk = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    const trimmedQuestion = question.trim()
    if (!trimmedQuestion || !selectedKbId || isAsking) return

    abortController.current?.abort()
    abortController.current = new AbortController()
    setIsAsking(true)
    setError('')
    setSearchResults([])
    setQuestion('')

    const userMessage: ChatMessage = {
      id: `user-${Date.now()}`,
      role: 'user',
      content: trimmedQuestion,
      citations: [],
    }
    const assistantMessageId = `assistant-${Date.now()}`
    const assistantMessage: ChatMessage = {
      id: assistantMessageId,
      role: 'assistant',
      content: '',
      citations: [],
      isStreaming: true,
    }
    setMessages((current) => [...current, userMessage, assistantMessage])

    try {
      const preview = await client.search(trimmedQuestion, selectedKbId)
      setSearchResults(preview)
      await client.ask(
        trimmedQuestion,
        selectedKbId,
        (eventData) => {
          if (eventData.event === 'request') {
            // 捕获服务端分配的 conversation_id 和 message_id，用于续接和反馈。
            const nextConversationId = String(eventData.data.conversation_id ?? '')
            if (nextConversationId) syncConversation(nextConversationId)
            const serverMessageId = String(eventData.data.message_id ?? '')
            if (serverMessageId) {
              setMessages((current) =>
                current.map((message) =>
                  message.id === assistantMessageId
                    ? { ...message, messageId: serverMessageId }
                    : message,
                ),
              )
            }
            return
          }
          if (eventData.event === 'token') {
            const text = String(eventData.data.text ?? '')
            setMessages((current) =>
              current.map((message) =>
                message.id === assistantMessageId
                  ? { ...message, content: message.content + text }
                  : message,
              ),
            )
            return
          }
          if (eventData.event === 'citation') {
            const citation = preview.find((item) => item.chunk_id === eventData.data.chunk_id)
            if (!citation) return
            // 合并 citation 事件补充的 version 和 updated_at（搜索结果中无 version）。
            const enrichedCitation = {
              ...citation,
              doc_version: Number(eventData.data.version ?? 1),
              updated_at: String(eventData.data.updated_at ?? citation.updated_at),
            }
            setMessages((current) =>
              current.map((message) =>
                message.id === assistantMessageId
                  ? { ...message, citations: [...message.citations, enrichedCitation] }
                  : message,
              ),
            )
            return
          }
          if (eventData.event === 'error') {
            const errorMessage = String(eventData.data.message ?? '问答处理失败')
            setMessages((current) =>
              current.map((message) =>
                message.id === assistantMessageId
                  ? { ...message, error: errorMessage }
                  : message,
              ),
            )
            return
          }
          if (eventData.event === 'done') {
            const finishReason = String(eventData.data.finish_reason ?? 'stop')
            setMessages((current) =>
              current.map((message) =>
                message.id === assistantMessageId
                  ? { ...message, isStreaming: false, finishReason }
                  : message,
              ),
            )
          }
        },
        abortController.current.signal,
        conversationIdRef.current,
      )
    } catch (askError) {
      if (askError instanceof DOMException && askError.name === 'AbortError') {
        setMessages((current) =>
          current.map((message) =>
            message.id === assistantMessageId
              ? { ...message, isStreaming: false, finishReason: 'cancelled' }
              : message,
          ),
        )
        return
      }
      setError(askError instanceof Error ? askError.message : '问答失败')
      setMessages((current) =>
        current.map((message) =>
          message.id === assistantMessageId
            ? { ...message, isStreaming: false, finishReason: 'error' }
            : message,
        ),
      )
    } finally {
      setIsAsking(false)
      // 刷新历史会话列表，使新建/续接的会话立即出现在历史面板顶部。
      void loadConversations()
    }
  }

  const handleCancelAsk = () => {
    abortController.current?.abort()
  }

  const handleSelectConversation = async (conversationId: string) => {
    setError('')
    try {
      const history = await client.getConversationMessages(conversationId)
      // 历史消息还原为 ChatMessage；assistant 消息保留 id 作为 messageId 以支持反馈。
      setMessages(
        history.map((message) => toChatMessage(message)),
      )
      setSearchResults([])
      syncConversation(conversationId)
    } catch (loadError) {
      setError(loadError instanceof Error ? loadError.message : '会话加载失败')
    }
  }

  const handleDeleteConversation = async (conversationId: string) => {
    setError('')
    try {
      await client.deleteConversation(conversationId)
      if (activeConversationId === conversationId) {
        syncConversation(undefined)
        setMessages([])
        setSearchResults([])
      }
      await loadConversations()
    } catch (deleteError) {
      setError(deleteError instanceof Error ? deleteError.message : '会话删除失败')
    }
  }

  const handleFeedback = async (messageId: string, rating: FeedbackRating) => {
    setError('')
    try {
      await client.sendFeedback(messageId, {
        rating,
        reason: rating === 'UP' ? '回答准确、引用可靠' : '引用不支持结论或回答有误',
      })
      setMessages((current) =>
        current.map((message) =>
          message.messageId === messageId ? { ...message, feedback: rating } : message,
        ),
      )
    } catch (feedbackError) {
      setError(feedbackError instanceof Error ? feedbackError.message : '反馈提交失败')
    }
  }

  if (!session) {
    return (
      <ErrorBoundary>
        <main className="auth-screen">
          <div className="auth-panel">
            <div className="brand-lockup">
              <span className="brand-mark">E</span>
              <div>
                <strong>EKB</strong>
                <span>企业知识库</span>
              </div>
            </div>
            <p className="eyebrow">M1 tracer bullet</p>
            <h1>让每个答案都能回到证据</h1>
            <p className="auth-copy">
              登录后访问授权知识库。当前版本用于验证租户上下文、检索过滤、SSE 问答和引用链路。
            </p>
            <form className="auth-form" onSubmit={handleLogin}>
              <label>
                邮箱
                <input
                  autoComplete="username"
                  value={email}
                  onChange={(event) => setEmail(event.target.value)}
                  placeholder="admin@example.com"
                  required
                />
              </label>
              <label>
                密码
                <input
                  autoComplete="current-password"
                  type="password"
                  value={password}
                  onChange={(event) => setPassword(event.target.value)}
                  placeholder="输入本地开发密码"
                  required
                />
              </label>
              {error && <p className="form-error" role="alert">{error}</p>}
              <button className="primary-button full-width" type="submit" disabled={isLoading}>
                {isLoading ? '登录中…' : '进入知识库'}
              </button>
            </form>
            <p className="auth-footnote">开发环境凭据由 `EKB_DEV_USER_EMAIL` 和 `EKB_DEV_PASSWORD` 注入。</p>
          </div>
        </main>
      </ErrorBoundary>
    )
  }

  return (
    <ErrorBoundary>
      <div className="app-shell">
        <KnowledgeSidebar
          knowledgeBases={knowledgeBases}
          selectedKbId={selectedKbId}
          documents={documents}
          isUploading={isUploading}
          onSelect={setSelectedKbId}
          onUpload={handleUpload}
          onCreateKb={handleCreateKb}
          onDeleteKb={handleDeleteKb}
          onDeleteDoc={handleDeleteDoc}
        />
        <main className="main-content">
          <div className="topbar">
            <div>
              <span className="topbar-label">当前租户</span>
              <strong>{session.tenants[0]?.name ?? '未命名租户'}</strong>
            </div>
            <ConversationHistory
              conversations={conversations}
              activeConversationId={activeConversationId}
              onSelect={handleSelectConversation}
              onDelete={handleDeleteConversation}
            />
            <div className="topbar-user">
              <span>{session.user.name}</span>
              <span className="avatar" aria-hidden="true">{session.user.name.slice(0, 1)}</span>
              <button className="logout-button" type="button" onClick={handleLogout}>
                退出
              </button>
            </div>
          </div>
          {error && <div className="global-error" role="alert">{error}</div>}
          {isLoading && knowledgeBases.length === 0 ? (
            <div className="loading-state">正在加载授权知识库…</div>
          ) : (
            <ChatPanel
              messages={messages}
              question={question}
              isAsking={isAsking}
              searchResults={searchResults}
              onQuestionChange={setQuestion}
              onSubmit={handleAsk}
              onCancel={handleCancelAsk}
              onFeedback={handleFeedback}
            />
          )}
        </main>
      </div>
    </ErrorBoundary>
  )
}

function toChatMessage(message: ConversationMessage): ChatMessage {
  const isAssistant = message.role === 'ASSISTANT'
  return {
    id: message.id,
    role: isAssistant ? 'assistant' : 'user',
    content: message.content,
    citations: [],
    // assistant 消息的数据库 id 即 messageId，可用于提交反馈。
    messageId: isAssistant ? message.id : undefined,
  }
}
