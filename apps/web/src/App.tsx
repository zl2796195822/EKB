import {
  Suspense,
  lazy,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ChangeEvent,
  type FormEvent,
  type ReactElement,
} from 'react'
import { BlurReveal } from './components/ui/animated/BlurReveal'
import { DockBar } from './components/ui/DockBar'
import { PillNavTabs } from './components/ui/PillNavTabs'
import { ErrorBoundary } from './components/ErrorBoundary'
import { useHashPage, type GalaxyPageId } from './hooks/useHashPage'
import { ApiClient } from './lib/api'
import type {
  ConversationMessage,
  ConversationSummary,
  DocumentDiff,
  DocumentRecord,
  DocumentVersionRecord,
  FeedbackRating,
  KnowledgeBase,
  LoginResponse,
  SearchResult,
} from './types/api'
import type { QAPageProps } from './pages/QAPage'
import type { SearchPageProps } from './pages/SearchPage'
import type { KbPageProps } from './pages/KbPage'
import type { OpsPageProps } from './pages/OpsPage'
import './styles.css'

const QAPage = lazy(() => import('./pages/QAPage'))
const KbPage = lazy(() => import('./pages/KbPage'))
const SearchPage = lazy(() => import('./pages/SearchPage'))
const OpsPage = lazy(() => import('./pages/OpsPage'))

type ChatMessage = {
  id: string
  role: 'user' | 'assistant'
  content: string
  citations: SearchResult[]
  messageId?: string
  isStreaming?: boolean
  error?: string
  finishReason?: string
  feedback?: FeedbackRating
}

// 生产环境通过 nginx 反代 /api/v1（相对路径）；开发环境通过 VITE_API_BASE_URL 覆盖。
const defaultApiBaseUrl = import.meta.env.VITE_API_BASE_URL ?? '/api/v1'

const PAGE_IDS: GalaxyPageId[] = ['qa', 'kb', 'search', 'ops']

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
  const [searchQuery, setSearchQuery] = useState('')
  const [searchPageNum, setSearchPageNum] = useState(1)
  const [searchPageSize] = useState(10)
  const [conversations, setConversations] = useState<ConversationSummary[]>([])
  const [activeConversationId, setActiveConversationId] = useState<string | undefined>(undefined)
  const [error, setError] = useState('')
  const [isLoading, setIsLoading] = useState(false)
  const [isUploading, setIsUploading] = useState(false)
  const [isAsking, setIsAsking] = useState(false)
  const abortController = useRef<AbortController | null>(null)
  const conversationIdRef = useRef<string | undefined>(undefined)
  const [activePage, goToPage] = useHashPage()

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

  const handleLoadDocVersions = useCallback(
    async (kbId: string, docId: string): Promise<DocumentVersionRecord[]> => {
      return client.listDocumentVersions(kbId, docId)
    },
    [client],
  )

  const handleLoadDocDiff = useCallback(
    async (kbId: string, docId: string, from: number, to: number): Promise<DocumentDiff> => {
      return client.getDocumentDiff(kbId, docId, from, to)
    },
    [client],
  )

  const handleLogout = async () => {
    if (session) {
      try {
        await client.logout(session.refresh_token)
      } catch {
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

  const handleSearchSubmit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    const trimmed = searchQuery.trim()
    if (!trimmed || !selectedKbId) return
    setIsLoading(true)
    setError('')
    try {
      const results = await client.search(trimmed, selectedKbId)
      setSearchResults(results)
      setSearchPageNum(1)
    } catch (searchError) {
      setError(searchError instanceof Error ? searchError.message : '检索失败')
    } finally {
      setIsLoading(false)
    }
  }

  useEffect(() => {
    if (!selectedKbId || !searchQuery.trim()) return
    let cancelled = false
    setIsLoading(true)
    setError('')
    void (async () => {
      try {
        const results = await client.search(searchQuery.trim(), selectedKbId)
        if (!cancelled) {
          setSearchResults(results)
          setSearchPageNum(1)
        }
      } catch (searchError) {
        if (!cancelled) {
          setError(searchError instanceof Error ? searchError.message : '检索失败')
        }
      } finally {
        if (!cancelled) setIsLoading(false)
      }
    })()
    return () => {
      cancelled = true
    }
  }, [client, selectedKbId, searchQuery])

  if (!session) {
    return (
      <ErrorBoundary>
        <main className="auth-screen bg-galaxy">
          {/* 极光第三层（CSS 动画 Layer 3） */}
          <div className="aurora-layer3" aria-hidden="true" />
          <div className="auth-panel">
            <div className="brand-lockup">
              <span className="brand-mark">E</span>
              <div>
                <strong>EKB</strong>
                <span>企业知识库</span>
              </div>
            </div>
            <p className="eyebrow">Galaxy Motion v4</p>
            <BlurReveal
              text="让每个答案都能回到证据"
              as="h1"
              animateBy="words"
              staggerMs={70}
              duration={0.6}
              delay={0.15}
              style={{ margin: '16px 0 10px', color: 'var(--c-ink-1)', fontSize: 'var(--fs-36)', lineHeight: 1.1, letterSpacing: '-0.03em' }}
            />
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

  const qaProps: Omit<QAPageProps, 'userEmail' | 'goToPage'> = {
    knowledgeBases,
    selectedKbId,
    documents,
    messages,
    question,
    searchResults,
    conversations,
    activeConversationId,
    isAsking,
    isLoading,
    isUploading,
    onChangeQuestion: setQuestion,
    onSubmitAsk: handleAsk,
    onCancelAsk: handleCancelAsk,
    onSelectConversation: handleSelectConversation,
    onDeleteConversation: handleDeleteConversation,
    onFeedback: handleFeedback,
    onCreateKb: handleCreateKb,
    onDeleteKb: handleDeleteKb,
    onDeleteDoc: handleDeleteDoc,
    onUpload: handleUpload,
    onSelectKb: (kbId: string) => setSelectedKbId(kbId),
    onClearMessages: () => {
      syncConversation(undefined)
      setMessages([])
      setSearchResults([])
    },
  }

  const searchProps: SearchPageProps = {
    searchResults,
    knowledgeBases,
    selectedKbId,
    searchQuery,
    searchPageNum,
    searchPageSize,
    isLoading,
    onChangeQuery: setSearchQuery,
    onSubmitSearch: handleSearchSubmit,
    onChangeKb: (kbId: string) => setSelectedKbId(kbId),
    onChangePage: (n: number) => setSearchPageNum(n),
  }

  const kbProps: Omit<KbPageProps, 'userEmail' | 'goToPage'> = {
    knowledgeBases,
    selectedKbId,
    documents,
    conversationsCount: conversations.length,
    isLoading,
    isUploading,
    onSelectKb: (kbId: string) => setSelectedKbId(kbId),
    onCreateKb: handleCreateKb,
    onDeleteKb: handleDeleteKb,
    onDeleteDoc: handleDeleteDoc,
    onUpload: handleUpload,
    onLoadDocVersions: handleLoadDocVersions,
    onLoadDocDiff: handleLoadDocDiff,
  }

  const opsProps: Omit<OpsPageProps, 'goToPage'> = {}

  return (
    <ErrorBoundary>
      <div className="galaxy-shell">
        <div className="bg-galaxy" aria-hidden="true" />
        <div className="shell-content">
          <PillNavTabs activePage={activePage} onChange={goToPage} />
          {error && <div className="global-error" role="alert">{error}</div>}
          <Suspense fallback={null}>
            {PAGE_IDS.map((pageId) => {
              const isActive = activePage === pageId
              return (
                <div
                  key={pageId}
                  id={`page-${pageId}`}
                  className={`page${isActive ? ' active' : ''}`}
                  role="tabpanel"
                  aria-hidden={!isActive}
                >
                  {pageId === 'qa' && isActive && <QAPage userEmail={session.user.email} {...qaProps} goToPage={goToPage} />}
                  {pageId === 'kb' && isActive && <KbPage userEmail={session.user.email} {...kbProps} goToPage={goToPage} />}
                  {pageId === 'search' && isActive && <SearchPage {...searchProps} />}
                  {pageId === 'ops' && isActive && <OpsPage goToPage={goToPage} />}
                </div>
              )
            })}
          </Suspense>
          <DockBar activePage={activePage} onChange={goToPage} />
        </div>
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
    messageId: isAssistant ? message.id : undefined,
  }
}
