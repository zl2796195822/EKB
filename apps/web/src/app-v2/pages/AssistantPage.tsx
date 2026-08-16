import { Archive, Export, Funnel, Pencil, SidebarSimple, ShareNetwork, Star, Trash } from '@phosphor-icons/react'
import { useCallback, useEffect, useMemo, useRef, useState, type FormEvent } from 'react'
import {
  AssistantComposer,
  AssistantContextPanel,
  AssistantMessageList,
  AssistantSidebar,
  type AssistantDisplayMessage,
  type ComposerSelectionState,
  type ConversationGroup,
} from '../components/assistant'
import { StatePanel } from '../components/StatePanel'
import { APP_V2_SESSION_STORAGE_KEY } from '../adapters/auth'
import { buildConversationMarkdown, canExportConversation } from './conversationExport'
import type {
  AdapterError,
  CitationView,
  ComposerAskOptions,
  ComposerCapabilitiesInfo,
  ComposerCapabilitiesResult,
  ComposerModelInfo,
  ConversationMessageView,
  ConversationView,
  FeedbackRating,
  KnowledgeBaseView,
  PageState,
  QaFinishReason,
  QaStreamEvent,
  QaStreamHandle,
  SearchHitView,
  V2PageProps,
} from '../types'
import type { ConversationBranchView } from '../types/conversations'
import type { FavoriteItemView } from '../types/favorites'

const CHAT_API_BASE = (import.meta as ImportMeta & { readonly env?: { readonly VITE_API_BASE_URL?: string } }).env?.VITE_API_BASE_URL ?? '/api/v1'

/**
 * 读取当前登录 access token（与 ApiClient/auth 适配器共用 sessionStorage key）。
 * 用于 Stop/Retry/Regenerate 中需要绕过 services 的原生 fetch 调用（spec 02 §6/§7）。
 */
function readAuthToken(): string {
  try {
    const raw = window.sessionStorage.getItem(APP_V2_SESSION_STORAGE_KEY)
    if (!raw) return ''
    const parsed = JSON.parse(raw) as { accessToken?: unknown }
    return typeof parsed.accessToken === 'string' ? parsed.accessToken : ''
  } catch {
    return ''
  }
}

type AssistantTab = 'recent' | 'favorites' | 'projects'
type StreamState = 'idle' | 'starting' | 'retrieval' | 'generation' | 'done' | 'cancelled' | 'error'
type FeedbackState = 'idle' | 'submitting' | 'submitted' | 'error'
type CapabilitiesState = 'idle' | 'loading' | 'ready' | 'error'

interface FeedbackView {
  readonly rating?: FeedbackRating
  readonly state: FeedbackState
  readonly message?: string
}

const EMPTY_FEEDBACK: FeedbackView = { state: 'idle' }
const DEFAULT_COMPOSER_SELECTION: ComposerSelectionState = {
  attachmentDocIds: [],
  attachmentLabels: {},
  // 深度思考默认开启「标准档 standard」；后端 capabilities 返回的 defaults 会覆盖。
  deepThinking: true,
  thinkingLevel: 'standard',
  selectedModelId: '',
}

function startOfDay(value: Date): number {
  return new Date(value.getFullYear(), value.getMonth(), value.getDate()).getTime()
}

function conversationGroupLabel(updatedAt: string, now: Date): string {
  const date = new Date(updatedAt)
  if (Number.isNaN(date.getTime())) return '更早'
  const dayDiff = Math.floor((startOfDay(now) - startOfDay(date)) / 86_400_000)
  if (dayDiff <= 0) return '今天'
  if (dayDiff === 1) return '昨天'
  if (dayDiff <= 7) return '过去 7 天'
  return '更早'
}

function createConversationGroups(conversations: readonly ConversationView[], query: string): readonly ConversationGroup[] {
  const normalizedQuery = query.trim().toLocaleLowerCase()
  const filtered = normalizedQuery
    ? conversations.filter((conversation) => conversation.title.toLocaleLowerCase().includes(normalizedQuery))
    : conversations
  const now = new Date()
  const labels = ['今天', '昨天', '过去 7 天', '更早']
  return labels
    .map((label) => ({ label, items: filtered.filter((conversation) => conversationGroupLabel(conversation.updatedAt, now) === label) }))
    .filter((group) => group.items.length > 0)
}

function makeLocalMessageId(prefix: string, generation: number): string {
  return `app-v2-${prefix}-${generation}`
}

function mapMessage(message: ConversationMessageView): AssistantDisplayMessage {
  return { ...message }
}

function formatOperationError(error: AdapterError | undefined, fallbackCode: string, fallbackMessage: string): string {
  return `${error?.code ?? fallbackCode}：${error?.message ?? fallbackMessage}${error?.requestId ? `（request_id：${error.requestId}）` : ''}`
}

function branchOptionLabel(branch: ConversationBranchView, index: number): string {
  const label = branch.label?.trim()
  if (label) return label
  return branch.parentBranchId ? `分支 ${index + 1}` : 'root'
}

export function AssistantPage({ services }: V2PageProps) {
  const [knowledgeState, setKnowledgeState] = useState<PageState>('loading')
  const [knowledgeBases, setKnowledgeBases] = useState<readonly KnowledgeBaseView[]>([])
  const [selectedKbId, setSelectedKbId] = useState('')
  const [conversationsState, setConversationsState] = useState<PageState>('loading')
  const [conversations, setConversations] = useState<readonly ConversationView[]>([])
  const [selectedConversationId, setSelectedConversationId] = useState('')
  const [branchesState, setBranchesState] = useState<PageState>('empty')
  const [branches, setBranches] = useState<readonly ConversationBranchView[]>([])
  const [activeBranchId, setActiveBranchId] = useState<string | null>(null)
  const [conversationSearch, setConversationSearch] = useState('')
  const [conversationTab, setConversationTab] = useState<AssistantTab>('recent')
  const [messagesState, setMessagesState] = useState<PageState>('empty')
  const [messagesError, setMessagesError] = useState<AdapterError | null>(null)
  const [messages, setMessages] = useState<readonly AssistantDisplayMessage[]>([])
  const [composerValue, setComposerValue] = useState('')
  const [lastQuestion, setLastQuestion] = useState('')
  const [streamState, setStreamState] = useState<StreamState>('idle')
  const [streamPhase, setStreamPhase] = useState<'retrieval' | 'generation' | 'done' | null>(null)
  const [streamContent, setStreamContent] = useState('')
  const [streamCitations, setStreamCitations] = useState<readonly CitationView[]>([])
  const [streamTurnId, setStreamTurnId] = useState('')
  const [streamMessageId, setStreamMessageId] = useState('')
  const [streamConversationId, setStreamConversationId] = useState('')
  const [streamFinishReason, setStreamFinishReason] = useState<QaFinishReason | undefined>(undefined)
  const [streamError, setStreamError] = useState<{ code: string; message: string; requestId?: string } | null>(null)
  const [streamRequestId, setStreamRequestId] = useState('')
  const [latestRealSequence, setLatestRealSequence] = useState<number | null>(null)
  const [protocolErrors, setProtocolErrors] = useState<readonly string[]>([])
  const [operationNotice, setOperationNotice] = useState<string | null>(null)
  const [renameOpen, setRenameOpen] = useState(false)
  const [renameValue, setRenameValue] = useState('')
  const [renameBusy, setRenameBusy] = useState(false)
  const [feedbackByMessage, setFeedbackByMessage] = useState<Readonly<Record<string, FeedbackView>>>({})
  const [searchQuery, setSearchQuery] = useState('')
  const [searchState, setSearchState] = useState<PageState>('empty')
  const [searchHits, setSearchHits] = useState<readonly SearchHitView[]>([])
  const [previewState, setPreviewState] = useState<PageState>('empty')
  const [previewDocument, setPreviewDocument] = useState<import('../types').DocumentView | null>(null)
  const [leftRailOpen, setLeftRailOpen] = useState(false)
  const [contextRailOpen, setContextRailOpen] = useState(false)
  const [favoritesState, setFavoritesState] = useState<PageState>('loading')
  const [favoriteConversations, setFavoriteConversations] = useState<readonly FavoriteItemView[]>([])
  const [favoritesTotal, setFavoritesTotal] = useState(0)
  const [currentConvFavorited, setCurrentConvFavorited] = useState<boolean | null>(null)
  const [currentConvFavoriting, setCurrentConvFavoriting] = useState(false)
  // Phase 2：Composer 功能按钮能力与用户选择
  const [composerCapabilities, setComposerCapabilities] = useState<ComposerCapabilitiesInfo | undefined>()
  const [composerModels, setComposerModels] = useState<readonly ComposerModelInfo[]>([])
  const [composerSelection, setComposerSelection] = useState<ComposerSelectionState>(DEFAULT_COMPOSER_SELECTION)
  const [capabilitiesState, setCapabilitiesState] = useState<CapabilitiesState>('idle')

  const streamGenerationRef = useRef(0)
  const streamHandleRef = useRef<QaStreamHandle | null>(null)
  const streamTurnIdRef = useRef('')
  const streamMessageIdRef = useRef('')
  const streamConversationIdRef = useRef('')
  const cancelRequestedRef = useRef(false)
  const messageLoadGenerationRef = useRef(0)
  const branchLoadGenerationRef = useRef(0)

  const selectedKnowledgeBase = useMemo(
    () => knowledgeBases.find((knowledgeBase) => knowledgeBase.id === selectedKbId) ?? null,
    [knowledgeBases, selectedKbId],
  )
  const conversationGroups = useMemo(
    () => createConversationGroups(conversations, conversationSearch),
    [conversations, conversationSearch],
  )
  const selectedConversation = useMemo(
    () => conversations.find((conversation) => conversation.id === selectedConversationId) ?? null,
    [conversations, selectedConversationId],
  )
  const isStreaming = streamState === 'starting' || streamState === 'retrieval' || streamState === 'generation'

  const handlePickAttachments = useCallback(async () => {
    if (isStreaming) return []
    const files = await new Promise<File[]>((resolve) => {
      const input = document.createElement('input')
      input.type = 'file'
      input.multiple = true
      input.accept = '.pdf,.doc,.docx,.txt,.md,.csv,.xlsx,.xls,image/*'
      input.onchange = () => resolve(Array.from(input.files ?? []))
      input.click()
    })
    const picked: Array<{ docId: string; title: string }> = []
    for (const file of files.slice(0, 10)) {
      const result = await services.attachments.upload(file, selectedConversationId || undefined)
      if (result.state === 'ready' && result.data) {
        picked.push({ docId: result.data.id, title: result.data.title })
      } else {
        setOperationNotice(result.error?.message ?? `附件「${file.name}」处理失败。`)
      }
    }
    return picked
  }, [isStreaming, selectedConversationId, services.attachments])

  const handlePromoteAttachment = useCallback(
    (attachmentId: string, input: import('../types').AttachmentPromotionInput) =>
      services.attachments.promote(attachmentId, input),
    [services.attachments],
  )

  const loadKnowledgeBases = useCallback(async () => {
    setKnowledgeState('loading')
    const result = await services.knowledge.list()
    setKnowledgeState(result.state)
    setKnowledgeBases(result.data ?? [])
    setSelectedKbId((current) => {
      if (current && result.data?.some((knowledgeBase) => knowledgeBase.id === current)) return current
      // spec 02 §1：知识库 0..N 可选，默认不选中（通用模型知识模式）
      return ''
    })
  }, [services.knowledge])

  const loadConversations = useCallback(async (preferredId?: string) => {
    setConversationsState('loading')
    const result = await services.conversations.list()
    setConversationsState(result.state)
    setConversations(result.data ?? [])
    if (!result.data) {
      setSelectedConversationId('')
      return
    }
    setSelectedConversationId((current) => {
      if (preferredId && result.data?.some((conversation) => conversation.id === preferredId)) return preferredId
      if (current && result.data?.some((conversation) => conversation.id === current)) return current
      return result.data?.[0]?.id ?? ''
    })
  }, [services.conversations])

  const loadBranches = useCallback(async (conversationId: string) => {
    const generation = branchLoadGenerationRef.current + 1
    branchLoadGenerationRef.current = generation
    if (!conversationId) {
      setBranchesState('empty')
      setBranches([])
      setActiveBranchId(null)
      branchLoadGenerationRef.current = 0
      return
    }
    setBranchesState('loading')
    setBranches([])
    setActiveBranchId(null)
    const result = await services.conversations.listBranches(conversationId)
    if (generation !== branchLoadGenerationRef.current) return
    branchLoadGenerationRef.current = 0
    setBranchesState(result.state)
    if (result.data) {
      setBranches(result.data.branches)
      setActiveBranchId(result.data.activeBranchId)
      return
    }
    setBranches([])
    setActiveBranchId(null)
    setOperationNotice(formatOperationError(result.error, 'CONVERSATION_BRANCHES_LOAD_FAILED', '会话分支加载失败。'))
  }, [services.conversations])

  const loadFavorites = useCallback(async () => {
    setFavoritesState('loading')
    const result = await services.favorites.list({ resourceType: 'CONVERSATION' })
    setFavoritesState(result.state)
    const items = result.data?.items ?? []
    setFavoriteConversations(items)
    setFavoritesTotal(result.data?.counts?.CONVERSATION ?? result.data?.total ?? items.length)
  }, [services.favorites])

  const loadCapabilities = useCallback(async () => {
    setCapabilitiesState('loading')
    const result: ComposerCapabilitiesResult = await services.qaStream.getCapabilities()
    if (result.state === 'ready' && result.data) {
      setComposerCapabilities(result.data.capabilities)
      setComposerModels(result.data.models)
      setComposerSelection((prev: ComposerSelectionState) => ({
        ...prev,
        deepThinking: result.data!.defaults.deepThinking,
        thinkingLevel: result.data!.defaults.thinkingLevel ?? prev.thinkingLevel,
        selectedModelId: result.data!.defaults.model ?? '',
        attachmentDocIds: result.data!.defaults.attachmentDocIds ?? prev.attachmentDocIds,
      }))
      setCapabilitiesState('ready')
    } else {
      setCapabilitiesState('error')
      setOperationNotice('功能按钮能力加载失败，部分功能可能不可用。')
    }
  }, [services.qaStream])

  const checkCurrentFavorite = useCallback(async (conversationId: string) => {
    if (!conversationId) {
      setCurrentConvFavorited(null)
      return
    }
    const result = await services.favorites.check('CONVERSATION', conversationId)
    if (result.state === 'ready') {
      setCurrentConvFavorited(Boolean(result.favorited))
    } else {
      setCurrentConvFavorited(false)
    }
  }, [services.favorites])

  const handleToggleCurrentFavorite = useCallback(async () => {
    if (!selectedConversationId || isStreaming || currentConvFavoriting) return
    setCurrentConvFavoriting(true)
    const prev = currentConvFavorited
    try {
      setCurrentConvFavorited(prev === null ? true : !prev)
      const result = await services.favorites.toggle('CONVERSATION', selectedConversationId)
      if (result.state === 'ready') {
        setCurrentConvFavorited(Boolean(result.favorited))
        setOperationNotice(
          result.favorited
            ? `已收藏会话「${selectedConversation?.title || '未命名会话'}」；星标仅你自己可见。`
            : `已取消收藏会话「${selectedConversation?.title || '未命名会话'}」。`,
        )
        await loadFavorites()
      } else {
        setCurrentConvFavorited(prev)
        setOperationNotice(result.error?.message ?? '收藏切换失败。')
      }
    } finally {
      setCurrentConvFavoriting(false)
    }
  }, [currentConvFavorited, currentConvFavoriting, isStreaming, loadFavorites, selectedConversation, selectedConversationId, services.favorites])

  const loadMessages = useCallback(async (conversationId: string, branchId?: string) => {
    const generation = messageLoadGenerationRef.current + 1
    messageLoadGenerationRef.current = generation
    if (!conversationId) {
      setMessagesState('empty')
      setMessagesError(null)
      setMessages([])
      return
    }
    setMessagesState('loading')
    setMessagesError(null)
    const result = await services.conversations.messages(conversationId, branchId)
    if (generation !== messageLoadGenerationRef.current) return
    setMessagesState(result.state)
    setMessagesError(result.error ?? null)
    setMessages((result.data ?? []).map(mapMessage))
  }, [services.conversations])

  useEffect(() => {
    const [, query] = window.location.hash.split('?')
    if (query) {
      const conv = new URLSearchParams(query).get('conv')?.trim()
      if (conv) {
        void loadConversations(conv)
      } else {
        void loadConversations()
      }
    } else {
      void loadConversations()
    }
    void loadKnowledgeBases()
    void loadFavorites()
    void loadCapabilities()
  }, [loadCapabilities, loadConversations, loadFavorites, loadKnowledgeBases])

  useEffect(() => {
    void loadBranches(selectedConversationId)
  }, [loadBranches, selectedConversationId])

  useEffect(() => {
    if (isStreaming) return
    void checkCurrentFavorite(selectedConversationId)
  }, [checkCurrentFavorite, isStreaming, selectedConversationId])

  useEffect(() => {
    setSearchState('empty')
    setSearchHits([])
    setPreviewState('empty')
    setPreviewDocument(null)
  }, [selectedKbId])

  useEffect(() => {
    if (isStreaming) return
    if (selectedConversationId && branchesState === 'loading') return
    void loadMessages(selectedConversationId, activeBranchId ?? undefined)
  }, [activeBranchId, branchesState, isStreaming, loadMessages, selectedConversationId])

  const updateTransientAssistant = useCallback((generation: number, update: (message: AssistantDisplayMessage) => AssistantDisplayMessage | null) => {
    if (generation !== streamGenerationRef.current) return
    setMessages((current) => current.flatMap((message) => {
      if (!message.transient || message.role !== 'assistant') return [message]
      const updated = update(message)
      return updated ? [updated] : []
    }))
  }, [])

  // Regenerate 的事件流需要按真实 assistant_message_id 增量更新（非 transient 消息）
  const updateAssistantMessageById = useCallback((messageId: string, update: (message: AssistantDisplayMessage) => AssistantDisplayMessage) => {
    setMessages((current) => current.map((message) => (message.id === messageId ? update(message) : message)))
  }, [])

  const handleStreamEvent = useCallback((generation: number, event: QaStreamEvent) => {
    if (generation !== streamGenerationRef.current) return
    if (event.sequence !== undefined) setLatestRealSequence(event.sequence)

    switch (event.kind) {
      case 'request':
        streamTurnIdRef.current = event.turnId
        streamMessageIdRef.current = event.messageId
        streamConversationIdRef.current = event.conversationId
        setStreamTurnId(event.turnId)
        setStreamMessageId(event.messageId)
        setStreamConversationId(event.conversationId)
        setStreamRequestId(event.requestId)
        setLatestRealSequence(event.sequence ?? null)
        updateTransientAssistant(generation, (message) => ({ ...message, id: event.messageId, streaming: true }))
        break
      case 'phase':
        if (event.phase === 'retrieval_started' || event.phase === 'retrieval_completed') {
          setStreamState('retrieval')
          setStreamPhase('retrieval')
        } else {
          setStreamState('generation')
          setStreamPhase('generation')
        }
        break
      case 'content_delta':
        setStreamState('generation')
        setStreamPhase('generation')
        setStreamContent((current) => current + event.delta)
        updateTransientAssistant(generation, (message) => ({ ...message, streaming: true, content: message.content + event.delta }))
        break
      case 'citations':
        setStreamCitations(event.items)
        updateTransientAssistant(generation, (message) => ({ ...message, citations: event.items }))
        break
      case 'done': {
        const finishReason = event.finishReason
        const terminalState: StreamState = finishReason === 'cancelled' ? 'cancelled' : finishReason === 'error' || finishReason === 'timeout' ? 'error' : 'done'
        setStreamState(terminalState)
        setStreamPhase('done')
        setStreamFinishReason(finishReason)
        setLatestRealSequence(event.lastSequence)
        if (event.messageId) {
          streamMessageIdRef.current = event.messageId
          setStreamMessageId(event.messageId)
        }
        const actualMessageId = event.messageId ?? streamMessageIdRef.current
        const canUseRealAssistantMessage = Boolean(actualMessageId) && finishReason !== 'cancelled' && finishReason !== 'error'
        updateTransientAssistant(generation, (message) => ({
          ...message,
          id: actualMessageId || message.id,
          transient: !canUseRealAssistantMessage,
          streaming: false,
          finishReason,
        }))
        const conversationId = streamConversationIdRef.current
        streamHandleRef.current = null
        if (conversationId) {
          setSelectedConversationId(conversationId)
          void loadConversations(conversationId)
          void loadMessages(conversationId, activeBranchId ?? undefined)
        }
        break
      }
      case 'error':
        setStreamState('error')
        setStreamPhase('done')
        setStreamFinishReason('error')
        setStreamError({ code: event.code, message: event.message, requestId: event.requestId })
        updateTransientAssistant(generation, (message) => ({ ...message, streaming: false, finishReason: 'error' }))
        // Phase 3 Step3.1：出错时自动把问题恢复到输入框，避免用户重新输入
        setComposerValue((current) => (current.trim() ? current : lastQuestion))
        setOperationNotice('问答处理失败，问题已自动恢复到输入框；可修改后重试，或在下方回答卡片点击「重试」用新 turn 重新发起。')
        break
      case 'protocol_error':
        setProtocolErrors((current) => [...current, event.code].slice(-5))
        break
    }
  }, [activeBranchId, lastQuestion, loadConversations, loadMessages, updateTransientAssistant])

  const handleCancel = useCallback(async () => {
    if (!isStreaming && !cancelRequestedRef.current) return
    cancelRequestedRef.current = true
    const handle = streamHandleRef.current
    const turnId = streamTurnIdRef.current || handle?.turnId || ''
    if (!handle) {
      setStreamState('cancelled')
      setStreamFinishReason('cancelled')
      setStreamError({ code: 'LOCAL_ABORT_ONLY', message: '服务端尚未返回 turn_id，未能调用服务端 cancel；仅等待本地流句柄后终止。' })
      return
    }

    if (!turnId) {
      handle.abort()
      streamHandleRef.current = null
      setStreamState('cancelled')
      setStreamFinishReason('cancelled')
      setStreamError({ code: 'LOCAL_ABORT_ONLY', message: '服务端尚未返回 turn_id，未能调用服务端 cancel；已仅终止本地流。' })
      updateTransientAssistant(streamGenerationRef.current, (message) => ({ ...message, streaming: false, finishReason: 'cancelled' }))
      cancelRequestedRef.current = false
      return
    }

    const result = await services.qaStream.cancel(turnId)
    handle.abort()
    streamHandleRef.current = null
    cancelRequestedRef.current = false
    if (result.state !== 'ready' || !result.data) {
      setStreamState('error')
      setStreamFinishReason('error')
      setStreamError({ code: result.error?.code ?? 'CANCEL_FAILED', message: `服务端 cancel 未确认：${result.error?.message ?? '请求失败'}；本地流已终止。` })
      updateTransientAssistant(streamGenerationRef.current, (message) => ({ ...message, streaming: false, finishReason: 'error' }))
      return
    }
    if (result.data.status === 'cancelled') {
      setStreamState('cancelled')
      setStreamFinishReason('cancelled')
      setStreamError(null)
      updateTransientAssistant(streamGenerationRef.current, (message) => ({ ...message, streaming: false, finishReason: 'cancelled' }))
      return
    }
    if (result.data.status === 'already_completed') {
      setStreamState('done')
      setStreamPhase('done')
      setStreamError({ code: 'ALREADY_COMPLETED', message: '服务端已完成该回答，本地流已终止。' })
      return
    }
    setStreamState('error')
    setStreamFinishReason('error')
    setStreamError({ code: 'CANCEL_NOT_FOUND', message: '服务端返回 not_found，未确认取消；本地流已终止。' })
    updateTransientAssistant(streamGenerationRef.current, (message) => ({ ...message, streaming: false, finishReason: 'error' }))
  }, [isStreaming, services.qaStream, updateTransientAssistant])

  // 抽取问答发起核心，供 handleSend（表单提交）与 handleRetryTurn（重试失败 turn）复用
  const runAsk = useCallback(async (question: string) => {
    if (!question || isStreaming) return
    const generation = streamGenerationRef.current + 1
    streamGenerationRef.current = generation
    streamHandleRef.current = null
    cancelRequestedRef.current = false
    streamTurnIdRef.current = ''
    streamMessageIdRef.current = ''
    streamConversationIdRef.current = selectedConversationId
    setStreamState('starting')
    setStreamPhase(null)
    setStreamContent('')
    setStreamCitations([])
    setStreamTurnId('')
    setStreamMessageId('')
    setStreamConversationId(selectedConversationId)
    setStreamFinishReason(undefined)
    setStreamError(null)
    setStreamRequestId('')
    setLatestRealSequence(null)
    setProtocolErrors([])
    setOperationNotice(null)
    setComposerValue('')
    setLastQuestion(question)
    const userMessage: AssistantDisplayMessage = {
      id: makeLocalMessageId('user', generation),
      role: 'user',
      content: question,
      createdAt: new Date().toISOString(),
      transient: true,
    }
    const assistantMessage: AssistantDisplayMessage = {
      id: makeLocalMessageId('assistant', generation),
      role: 'assistant',
      content: '',
      createdAt: new Date().toISOString(),
      transient: true,
      streaming: true,
    }
    setMessages((current) => [...current, userMessage, assistantMessage])
    // Phase 2：透传 Composer 功能按钮选项
    const composerOptions: ComposerAskOptions = {
      deepThinking: composerSelection.deepThinking,
      thinkingLevel: composerSelection.thinkingLevel,
      model: composerSelection.selectedModelId || undefined,
      attachmentDocIds: composerSelection.attachmentDocIds,
    }
    const result = await services.qaStream.ask(
      {
        question,
        knowledgeBaseId: selectedKbId,
        ...(selectedConversationId ? { conversationId: selectedConversationId } : {}),
        options: composerOptions,
      },
      (streamEvent) => handleStreamEvent(generation, streamEvent),
    )
    if (generation !== streamGenerationRef.current) return
    if (!result.data) {
      setStreamState(result.state === 'permission-denied' ? 'error' : 'error')
      setStreamFinishReason('error')
      setStreamError({ code: result.error?.code ?? 'STREAM_START_FAILED', message: result.error?.message ?? '问答流启动失败。', requestId: result.error?.requestId })
      updateTransientAssistant(generation, (message) => ({ ...message, streaming: false, finishReason: 'error' }))
      // Phase 3 Step3.1：流启动失败时同样自动恢复问题
      setComposerValue(question)
      setOperationNotice('问答流启动失败，问题已恢复到输入框；可修改后重试，或在下方回答卡片点击「重试」。')
      return
    }
    streamHandleRef.current = result.data
    if (!streamTurnIdRef.current && result.data.turnId) {
      streamTurnIdRef.current = result.data.turnId
      setStreamTurnId(result.data.turnId)
    }
    if (!streamMessageIdRef.current && result.data.messageId) {
      streamMessageIdRef.current = result.data.messageId
      setStreamMessageId(result.data.messageId)
    }
    if (!streamConversationIdRef.current && result.data.conversationId) {
      streamConversationIdRef.current = result.data.conversationId
      setStreamConversationId(result.data.conversationId)
    }
    if (cancelRequestedRef.current) void handleCancel()
  }, [composerSelection, handleCancel, handleStreamEvent, isStreaming, selectedConversationId, selectedKbId, services.qaStream, updateTransientAssistant])

  const handleSend = useCallback(async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    await runAsk(composerValue.trim())
  }, [composerValue, runAsk])

  // Stop：取消正在运行的 turn（spec 02 §6）。复用已接入真实 /qa/turns/{id}/cancel 的 handleCancel：
  // 它会调用服务端 cancel + 中断本地流句柄 + 同步流状态；regenerate 的事件流同样通过
  // streamHandleRef.current.abort() 被中断。
  const handleStopTurn = useCallback(() => {
    void handleCancel()
  }, [handleCancel])

  // Retry：重试失败的 turn（spec 02 §7）。使用新 client_turn_id 重新发起同一问题，
  // 走标准 /qa/ask 流式通道，无需单独订阅事件流。
  const handleRetryTurn = useCallback((_failedTurnId: string, lastQuestion: string) => {
    void runAsk(lastQuestion)
  }, [runAsk])

  // Regenerate 事件流订阅：监听 GET /chat/turns/{turn_id}/events（spec 03 §3），
  // 将 worker 事件（turn.stage / message.delta / turn.completed|stopped|failed）增量更新到
  // 真实 assistant 消息。与 /qa/ask 的 SSE v2 envelope 不同，这里 envelope 把 payload 展开
  // 到顶层、事件名也不同（spec 03 §3.1 v3 envelope），因此单独解析而非复用 SseStreamParser。
  const subscribeRegenerateStream = useCallback(
    async (generation: number, turnId: string, assistantMessageId: string, conversationId: string, branchId: string, lastSeq?: number) => {
      const token = readAuthToken()
      if (!token) {
        setStreamState('error')
        setStreamFinishReason('error')
        setStreamError({ code: 'NO_AUTH_TOKEN', message: '未找到登录凭证，无法监听重新生成事件流，请重新登录。' })
        return
      }
      const controller = new AbortController()
      streamHandleRef.current = {
        turnId,
        messageId: assistantMessageId,
        conversationId,
        abort: () => controller.abort(),
      }
      let buffer = ''
      const finish = (state: StreamState, finishReason: QaFinishReason) => {
        if (generation !== streamGenerationRef.current) return
        streamHandleRef.current = null
        setStreamState(state)
        setStreamPhase('done')
        setStreamFinishReason(finishReason)
        updateAssistantMessageById(assistantMessageId, (message) => ({ ...message, streaming: false, finishReason }))
        void loadBranches(conversationId)
        void loadMessages(conversationId, branchId)
      }
      try {
        const headers: Record<string, string> = { Authorization: `Bearer ${token}`, Accept: 'text/event-stream' }
        // PH4-3 刷新恢复：携带 Last-Event-ID {turnId}:{lastSeq}，服务端从该 seq 之后重放并继续 live follow。
        if (lastSeq !== undefined) headers['Last-Event-ID'] = `${turnId}:${lastSeq}`
        const resp = await fetch(`${CHAT_API_BASE}/chat/turns/${encodeURIComponent(turnId)}/events`, {
          method: 'GET',
          headers,
          signal: controller.signal,
        })
        if (!resp.ok || !resp.body) throw new Error(`${resp.status} ${resp.statusText}`)
        setStreamState('retrieval')
        const reader = resp.body.getReader()
        const decoder = new TextDecoder()
        const handleBlock = (block: string) => {
          const trimmed = block.trim()
          if (!trimmed || trimmed.startsWith(':')) return
          const eventName = block.match(/^event: (.+)$/m)?.[1]?.trim() ?? ''
          const dataLine = block.match(/^data: (.+)$/m)?.[1]?.trim() ?? ''
          if (!eventName || !dataLine) return
          let envelope: Record<string, unknown>
          try {
            envelope = JSON.parse(dataLine) as Record<string, unknown>
          } catch {
            return
          }
          if (generation !== streamGenerationRef.current) return
          if (typeof envelope.seq === 'number') setLatestRealSequence(envelope.seq)
          switch (eventName) {
            case 'turn.stage': {
              const phaseState = String(envelope.state ?? '')
              if (phaseState === 'RETRIEVING') { setStreamState('retrieval'); setStreamPhase('retrieval') }
              else if (phaseState === 'STREAMING') { setStreamState('generation'); setStreamPhase('generation') }
              updateAssistantMessageById(assistantMessageId, (message) => ({ ...message, streaming: true }))
              break
            }
            case 'message.delta': {
              const delta = typeof envelope.delta === 'string' ? envelope.delta : ''
              if (!delta) break
              setStreamState('generation')
              setStreamPhase('generation')
              setStreamContent((current) => current + delta)
              updateAssistantMessageById(assistantMessageId, (message) => ({ ...message, streaming: true, content: message.content + delta }))
              break
            }
            case 'turn.completed':
              finish('done', 'stop')
              break
            case 'turn.stopped':
              finish('cancelled', 'cancelled')
              break
            case 'turn.failed': {
              const code = typeof envelope.error_code === 'string' ? envelope.error_code : 'GENERATION_ERROR'
              const failMessage = typeof envelope.message === 'string' ? envelope.message : '重新生成失败。'
              setStreamError({ code, message: failMessage })
              finish('error', 'error')
              break
            }
            default:
              break
          }
        }
        while (true) {
          const { done, value } = await reader.read()
          const chunk = decoder.decode(value ?? new Uint8Array(), { stream: !done })
          buffer += chunk
          const blocks = buffer.split('\n\n')
          buffer = blocks.pop() ?? ''
          for (const block of blocks) handleBlock(block)
          if (done) break
        }
        if (buffer.trim()) handleBlock(buffer)
        // 流自然结束但未收到终态事件：以当前真实状态收尾
        if (generation === streamGenerationRef.current && streamHandleRef.current) {
          streamHandleRef.current = null
          setStreamState('done')
          setStreamPhase('done')
          void loadBranches(conversationId)
          void loadMessages(conversationId, branchId)
        }
      } catch (err) {
        if (generation !== streamGenerationRef.current) return
        if ((err as Error).name === 'AbortError') {
          // 用户主动停止（handleCancel 已调用服务端 cancel 并同步流状态）
          streamHandleRef.current = null
          updateAssistantMessageById(assistantMessageId, (message) => ({ ...message, streaming: false, finishReason: 'cancelled' }))
          void loadMessages(conversationId, branchId)
          return
        }
        streamHandleRef.current = null
        setStreamState('error')
        setStreamPhase('done')
        setStreamFinishReason('error')
        setStreamError({ code: 'REGENERATE_STREAM_ERROR', message: (err as Error).message || '重新生成事件流读取失败。' })
        updateAssistantMessageById(assistantMessageId, (message) => ({ ...message, streaming: false, finishReason: 'error' }))
        void loadBranches(conversationId)
        void loadMessages(conversationId, branchId)
      }
    },
    [loadBranches, loadMessages, updateAssistantMessageById],
  )

  // PH4-6：context 诊断面板使用的 turn_id（最后一条带 turn_id 的 assistant 消息，回退到当前流 turn）。
  const diagnosticTurnId = useMemo(() => {
    for (let i = messages.length - 1; i >= 0; i--) {
      const message = messages[i]
      if (message.role === 'assistant' && message.turnId) return message.turnId
    }
    return streamTurnId || ''
  }, [messages, streamTurnId])

  // PH4-3 刷新恢复：会话消息加载后，若最后一条 assistant 消息为非终态（内容为空且
  // 携带 turn_id），查询 turn 状态；非终态则用 Last-Event-ID 重连 SSE，从断点继续增量更新。
  // 切换会话不会取消后台 turn；此处仅在非终态时重连事件流（spec 02 §11.1）。
  // 注意：RUNNING 期间 assistant 消息内容为空（仅在 turn 终态化时持久化），
  // 因此用 last_seq 作为游标重放事件不会造成内容重复。
  useEffect(() => {
    if (isStreaming) return
    if (messagesState !== 'ready') return
    if (messages.length === 0) return
    const lastMsg = messages[messages.length - 1]
    if (!lastMsg || lastMsg.role !== 'assistant' || !lastMsg.turnId || lastMsg.content) return
    const turnId = lastMsg.turnId
    const assistantMessageId = lastMsg.id
    const conversationId = selectedConversationId
    const branchId = activeBranchId ?? ''
    let cancelled = false
    void (async () => {
      const token = readAuthToken()
      if (!token || cancelled) return
      let turnState = ''
      let lastSeq = 0
      try {
        const resp = await fetch(`${CHAT_API_BASE}/chat/turns/${encodeURIComponent(turnId)}`, {
          headers: { Authorization: `Bearer ${token}` },
        })
        if (!resp.ok || cancelled) return
        const data = (await resp.json()) as { turn?: { state?: string; last_seq?: number; last_event_seq?: number } }
        turnState = data.turn?.state ?? ''
        lastSeq = (data.turn?.last_event_seq ?? data.turn?.last_seq) ?? 0
      } catch {
        return
      }
      if (cancelled) return
      // 终态：canonical 消息已是最终状态，无需重连
      if (['COMPLETED', 'STOPPED', 'FAILED'].includes(turnState)) return
      // 非终态：重连 SSE，携带 Last-Event-ID 续传
      const generation = streamGenerationRef.current + 1
      streamGenerationRef.current = generation
      streamHandleRef.current = null
      cancelRequestedRef.current = false
      streamTurnIdRef.current = turnId
      streamMessageIdRef.current = assistantMessageId
      streamConversationIdRef.current = conversationId
      setStreamState('starting')
      setStreamPhase(null)
      setStreamContent('')
      setStreamCitations([])
      setStreamTurnId(turnId)
      setStreamMessageId(assistantMessageId)
      setStreamConversationId(conversationId)
      setStreamFinishReason(undefined)
      setStreamError(null)
      setStreamRequestId('')
      setLatestRealSequence(null)
      setProtocolErrors([])
      updateAssistantMessageById(assistantMessageId, (message) => ({ ...message, streaming: true }))
      void subscribeRegenerateStream(generation, turnId, assistantMessageId, conversationId, branchId, lastSeq)
    })()
    return () => { cancelled = true }
  }, [activeBranchId, isStreaming, messages, messagesState, selectedConversationId, subscribeRegenerateStream, updateAssistantMessageById])

  // Regenerate：fork 新分支重新生成回答（spec 02 §7）。
  // POST /chat/conversations/{id}/messages/{assistant_message_id}/regenerate 创建新 turn，
  // 随后订阅 /chat/turns/{turn_id}/events 实时展示生成内容。
  const handleRegenerate = useCallback(
    async (conversationId: string, assistantMessageId: string) => {
      if (isStreaming) {
        setOperationNotice('当前回答仍在流式生成，请先停止后再重新生成。')
        return
      }
      if (!conversationId || !assistantMessageId) return
      const token = readAuthToken()
      if (!token) {
        setOperationNotice('未找到登录凭证，请重新登录后再试。')
        return
      }
      setOperationNotice('正在发起新分支重新生成回答…')
      try {
        const resp = await fetch(
          `${CHAT_API_BASE}/chat/conversations/${encodeURIComponent(conversationId)}/messages/${encodeURIComponent(assistantMessageId)}/regenerate`,
          {
            method: 'POST',
            headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' },
            body: JSON.stringify({ client_turn_id: crypto.randomUUID(), activate: true }),
          },
        )
        if (!resp.ok) {
          const text = await resp.text().catch(() => '')
          throw new Error(`${resp.status} ${resp.statusText}${text ? `：${text}` : ''}`)
        }
        const data = (await resp.json()) as {
          turn_id: string
          conversation_id: string
          branch_id: string
          user_message_id: string
          assistant_message_id: string
          state: string
          reused?: boolean
        }
        const generation = streamGenerationRef.current + 1
        streamGenerationRef.current = generation
        streamHandleRef.current = null
        cancelRequestedRef.current = false
        streamTurnIdRef.current = data.turn_id
        streamMessageIdRef.current = data.assistant_message_id
        streamConversationIdRef.current = data.conversation_id
        setStreamState('starting')
        setStreamPhase(null)
        setStreamContent('')
        setStreamCitations([])
        setStreamTurnId(data.turn_id)
        setStreamMessageId(data.assistant_message_id)
        setStreamConversationId(data.conversation_id)
        setStreamFinishReason(undefined)
        setStreamError(null)
        setStreamRequestId('')
        setLatestRealSequence(null)
        setProtocolErrors([])
        setSelectedConversationId(data.conversation_id)
        // fork 已 activate 新分支：加载分支结构与消息（含 pending assistant 消息）
        void loadBranches(data.conversation_id)
        await loadMessages(data.conversation_id, data.branch_id)
        setOperationNotice('已 fork 新分支，正在重新生成回答…')
        void subscribeRegenerateStream(generation, data.turn_id, data.assistant_message_id, data.conversation_id, data.branch_id)
      } catch (e) {
        setStreamState('error')
        setStreamFinishReason('error')
        setStreamError({ code: 'REGENERATE_FAILED', message: (e as Error).message || '重新生成请求失败。' })
        setOperationNotice(`重新生成失败：${(e as Error).message || '请求失败'}`)
      }
    },
    [isStreaming, loadBranches, loadMessages, subscribeRegenerateStream],
  )

  const handleNewConversation = useCallback(() => {
    if (isStreaming) {
      setOperationNotice('当前回答仍在流式生成，请先停止后再新建会话。')
      return
    }
    setSelectedConversationId('')
    setBranchesState('empty')
    setBranches([])
    setActiveBranchId(null)
    setMessages([])
    setMessagesState('empty')
    setMessagesError(null)
    setStreamState('idle')
    setStreamPhase(null)
    setStreamCitations([])
    setPreviewState('empty')
    setPreviewDocument(null)
    setOperationNotice('已清空当前工作区；首次提问将由服务端创建新会话。')
  }, [isStreaming])

  const handleSelectConversation = useCallback((conversationId: string) => {
    if (isStreaming) {
      setOperationNotice('当前回答仍在流式生成，停止后才能切换会话。')
      return
    }
    setSelectedConversationId(conversationId)
    setBranchesState('empty')
    setBranches([])
    setActiveBranchId(null)
    setMessages([])
    setMessagesState('loading')
    setStreamState('idle')
    setStreamCitations([])
    setPreviewState('empty')
    setPreviewDocument(null)
    setOperationNotice(null)
  }, [isStreaming])

  const handleSelectBranch = useCallback(async (branchId: string) => {
    if (isStreaming) {
      setOperationNotice('当前回答仍在流式生成，停止后才能切换分支。')
      return
    }
    if (!selectedConversationId || !branchId || branchId === activeBranchId) return
    const result = await services.conversations.setActiveBranch(selectedConversationId, branchId)
    if (result.state !== 'ready') {
      setOperationNotice(formatOperationError(result.error, 'CONVERSATION_BRANCH_SWITCH_FAILED', '会话分支切换失败。'))
      return
    }
    setActiveBranchId(branchId)
    setMessages([])
    setMessagesState('loading')
    setMessagesError(null)
    setOperationNotice('会话分支已切换，正在加载当前分支消息。')
    await loadMessages(selectedConversationId, branchId)
  }, [activeBranchId, isStreaming, loadMessages, selectedConversationId, services.conversations])

  const handleDeleteConversation = useCallback(async () => {
    if (!selectedConversation || isStreaming || !window.confirm(`确认删除会话「${selectedConversation.title || '未命名会话'}」？`)) return
    const result = await services.conversations.remove(selectedConversation.id)
    if (result.state !== 'ready') {
      setOperationNotice(result.error?.message ?? '会话删除失败。')
      return
    }
    setMessages([])
    setSelectedConversationId('')
    setBranchesState('empty')
    setBranches([])
    setActiveBranchId(null)
    setOperationNotice('会话已由服务端删除，正在刷新真实会话列表。')
    await loadConversations()
  }, [isStreaming, loadConversations, selectedConversation, services.conversations])

  const handleOpenRename = useCallback(() => {
    if (!selectedConversation || isStreaming || renameBusy) return
    setRenameValue(selectedConversation.title)
    setRenameOpen(true)
    setOperationNotice(null)
  }, [isStreaming, renameBusy, selectedConversation])

  const handleCancelRename = useCallback(() => {
    if (renameBusy) return
    setRenameOpen(false)
    setRenameValue('')
  }, [renameBusy])

  const handleRename = useCallback(async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    if (!selectedConversationId || isStreaming || renameBusy) return
    const title = renameValue.trim()
    if (!title) {
      setOperationNotice('会话标题不能为空。')
      return
    }
    setRenameBusy(true)
    try {
      const result = await services.conversations.rename(selectedConversationId, title)
      if (result.state !== 'ready') {
        const error = result.error
        setOperationNotice(`${error?.code ?? 'CONVERSATION_RENAME_FAILED'}：${error?.message ?? '会话重命名失败。'}${error?.requestId ? `（request_id：${error.requestId}）` : ''}`)
        return
      }
      setRenameOpen(false)
      setRenameValue(title)
      await loadConversations(selectedConversationId)
      setOperationNotice(`会话已重命名为「${title}」。`)
    } catch {
      setOperationNotice('CONVERSATION_RENAME_FAILED：会话重命名失败。')
    } finally {
      setRenameBusy(false)
    }
  }, [isStreaming, loadConversations, renameBusy, renameValue, selectedConversationId, services.conversations])

  const handleExportConversation = useCallback(() => {
    if (!selectedConversationId || !canExportConversation(messagesState, messages, isStreaming)) return
    const markdown = buildConversationMarkdown(messages, selectedConversation?.title ?? '')
    const blob = new Blob([markdown], { type: 'text/markdown;charset=utf-8' })
    const url = URL.createObjectURL(blob)
    const anchor = document.createElement('a')
    anchor.href = url
    anchor.download = 'conversation.md'
    document.body.appendChild(anchor)
    anchor.click()
    anchor.remove()
    window.setTimeout(() => URL.revokeObjectURL(url), 0)
  }, [isStreaming, messages, messagesState, selectedConversation?.title, selectedConversationId])

  const handleSearch = useCallback(async () => {
    if (!selectedKbId || !searchQuery.trim()) return
    setSearchState('loading')
    setSearchHits([])
    const result = await services.search.search(searchQuery.trim(), selectedKbId)
    setSearchState(result.state)
    setSearchHits(result.data ?? [])
  }, [searchQuery, selectedKbId, services.search])

  const handleOpenDocument = useCallback(async (documentId: string) => {
    if (!selectedKbId) return
    setPreviewState('loading')
    setPreviewDocument(null)
    const result = await services.documents.get(selectedKbId, documentId)
    setPreviewState(result.state)
    setPreviewDocument(result.data ?? null)
  }, [selectedKbId, services.documents])

  const handleFeedback = useCallback(async (messageId: string, rating: FeedbackRating) => {
    if (!messageId || messageId.startsWith('app-v2-')) return
    const reason = rating === 'UP' ? '回答解决了我的问题' : '回答缺少足够依据'
    setFeedbackByMessage((current) => ({ ...current, [messageId]: { rating, state: 'submitting' } }))
    const result = await services.feedback.submit(messageId, { rating, reason })
    if (result.state === 'ready') {
      setFeedbackByMessage((current) => ({ ...current, [messageId]: { rating, state: 'submitted' } }))
    } else {
      setFeedbackByMessage((current) => ({ ...current, [messageId]: { rating, state: 'error', message: result.error?.message ?? '提交失败，可重试。' } }))
    }
  }, [services.feedback])

  const displayMessages = useMemo(() => {
    if (!streamContent && streamState === 'idle') return messages
    return messages
  }, [messages, streamContent, streamState])

  const assistantStateMessage = streamState === 'starting' ? '正在建立 SSE v2 连接…' : streamState === 'retrieval' ? '正在检索当前授权知识库…' : streamState === 'generation' ? '正在生成回答…' : streamState === 'cancelled' ? '回答已取消。' : streamState === 'error' ? '回答流发生错误。' : streamState === 'done' && streamFinishReason === 'refusal' ? '当前授权知识库没有足够证据，服务端已拒答。' : null

  return (
    <div className="v2-assistant-columns">
      <AssistantSidebar
        open={leftRailOpen}
        conversationsState={conversationsState}
        groups={conversationGroups}
        selectedConversationId={selectedConversationId}
        selectedKnowledgeBase={selectedKnowledgeBase}
        searchValue={conversationSearch}
        activeTab={conversationTab}
        onSearchChange={setConversationSearch}
        onTabChange={setConversationTab}
        onSelectConversation={handleSelectConversation}
        onNewConversation={handleNewConversation}
        errorMessage="会话列表没有加载成功，请稍后重试。"
        favoritesState={favoritesState}
        favoriteConversations={favoriteConversations}
        favoritesTotal={favoritesTotal}
      />
      <main className="v2-m4-chat" aria-label="AI 助手聊天区">
        <header className="v2-m4-chat-header">
          <div className="v2-m4-mobile-toggles">
            <button type="button" onClick={() => setLeftRailOpen((open) => !open)} aria-expanded={leftRailOpen}><SidebarSimple size={15} />会话</button>
            <button type="button" onClick={() => setContextRailOpen((open) => !open)} aria-expanded={contextRailOpen}><SidebarSimple size={15} />上下文</button>
          </div>
          <div className="v2-m4-chat-title">
            <p className="v2-eyebrow">AI ASSISTANT / STREAM V2</p>
            <h1>{selectedConversation?.title || (streamConversationId ? '正在加载新会话' : '新会话')}</h1>
            <div className="v2-m4-chat-subtitle">
              <label>当前知识库
                <select aria-label="选择当前授权知识库" value={selectedKbId} onChange={(event) => setSelectedKbId(event.target.value)} disabled={knowledgeState === 'loading' || isStreaming}>
                  <option value="">不使用知识库（通用模式）</option>
                  {knowledgeBases.map((knowledgeBase) => <option value={knowledgeBase.id} key={knowledgeBase.id}>{knowledgeBase.name}</option>)}
                </select>
              </label>
              {branches.length > 0 ? (
                <label>分支
                  <select
                    aria-label="选择会话分支"
                    value={activeBranchId ?? ''}
                    onChange={(event) => void handleSelectBranch(event.target.value)}
                    disabled={branchesState === 'loading' || isStreaming}
                  >
                    {branches.map((branch, index) => <option value={branch.id} key={branch.id}>{branchOptionLabel(branch, index)}</option>)}
                  </select>
                </label>
              ) : null}
              {selectedKnowledgeBase ? (
                <span className="v2-m4-turn-badge" title={`知识库增强模式：${selectedKnowledgeBase.name}`}>
                  知识库增强：{selectedKnowledgeBase.name}
                </span>
              ) : (
                <span className="v2-m4-turn-badge" title="未选中知识库，将使用通用模型知识作答">
                  通用模型知识模式
                </span>
              )}
              {streamTurnId ? <span className="v2-m4-turn-badge">turn_id 已绑定</span> : null}
            </div>
          </div>
          <div className="v2-m4-chat-actions">
            <button type="button" disabled title="v4 P2 内容治理：分享端点（signed share link + ACL 快照）规划中，当前不伪造分享成功。">
              <ShareNetwork size={15} />分享
              <small aria-hidden="true" style={{ marginLeft: 6, padding: '1px 6px', borderRadius: 999, fontSize: 10, fontWeight: 500, background: '#FEF3C7', color: '#92400E' }}>v4 P2</small>
            </button>
            <button type="button" onClick={handleOpenRename} disabled={!selectedConversationId || isStreaming || renameBusy} title="重命名当前会话">
              <Pencil size={15} />重命名
            </button>
            <button
              type="button"
              onClick={() => void handleToggleCurrentFavorite()}
              disabled={!selectedConversationId || isStreaming || currentConvFavoriting}
              title={
                !selectedConversationId
                  ? '先选择或创建一个会话后才能收藏'
                  : isStreaming
                    ? '回答仍在流式生成，收藏操作稍后可用'
                    : currentConvFavorited
                      ? '取消收藏当前会话（仅你自己可见）'
                      : '收藏当前会话到星标列表（仅你自己可见）'
              }
              style={currentConvFavorited ? { color: '#059669' } : undefined}
            >
              <Star size={15} weight={currentConvFavorited ? 'fill' : 'regular'} />
              {currentConvFavorited ? '已收藏' : '收藏'}
            </button>
            <button
              type="button"
              onClick={handleExportConversation}
              disabled={!selectedConversationId || !canExportConversation(messagesState, messages, isStreaming)}
              title="导出当前服务端已加载的持久消息"
            >
              <Export size={15} />导出
            </button>
            <button type="button" className="v2-m4-delete-button" disabled={!selectedConversationId || isStreaming} onClick={() => void handleDeleteConversation()} title="删除当前会话"><Trash size={15} /></button>
          </div>
        </header>
        {renameOpen ? (
          <form className="v2-m4-rename-form" onSubmit={(event) => void handleRename(event)} aria-label="重命名当前会话">
            <label htmlFor="v2-m4-rename-input">会话名称</label>
            <input id="v2-m4-rename-input" aria-label="会话名称" value={renameValue} onChange={(event) => setRenameValue(event.target.value)} disabled={renameBusy} autoFocus />
            <button type="button" onClick={handleCancelRename} disabled={renameBusy} aria-label="取消重命名">取消</button>
            <button type="submit" disabled={renameBusy} aria-label={renameBusy ? '保存中' : '保存重命名'}>{renameBusy ? '保存中…' : '保存'}</button>
          </form>
        ) : null}
        <div className="v2-m4-stream-bar" data-state={streamState}>
          <div className="v2-m4-stream-stage"><span className="v2-m4-stage-dot" />{assistantStateMessage ?? '等待你的问题'}</div>
          <div className="v2-m4-stream-meta">
            <span>{streamPhase ? `阶段：${streamPhase === 'retrieval' ? '检索' : streamPhase === 'generation' ? '生成' : '完成'}` : '阶段：—'}</span>
            <span>{latestRealSequence === null ? '最近真实 seq：无可用 seq' : `最近真实 seq：${latestRealSequence}`}</span>
            {streamRequestId ? <span>request_id：{streamRequestId}</span> : null}
          </div>
        </div>
        {knowledgeState === 'loading' ? <StatePanel state="loading" message="正在加载当前主体有权使用的知识库。" /> : null}
        {knowledgeState === 'permission-denied' || knowledgeState === 'error' ? <StatePanel state={knowledgeState} message="授权知识库加载失败，无法开始问答。" /> : null}
        {knowledgeState === 'empty' ? <StatePanel state="empty" message="当前主体没有可用知识库，可不选知识库直接提问（通用模型知识模式）。" reason="spec 02 §1：知识库 0..N 可选；不选时服务端以通用模型知识作答。" /> : null}
        {messagesState === 'loading' && messages.length === 0 ? <StatePanel state="loading" message="正在加载当前会话消息。" /> : null}
        {messagesState === 'error' || messagesState === 'permission-denied' ? <div className="v2-m4-message-state"><StatePanel state={messagesState} message={messagesError?.message ?? '会话消息加载失败。'} /><button type="button" onClick={() => void loadMessages(selectedConversationId, activeBranchId ?? undefined)}>重试</button></div> : null}
        {operationNotice ? <div className="v2-m4-operation-notice" role="status">{operationNotice}</div> : null}
        {streamError ? <div className="v2-m4-operation-notice v2-m4-operation-notice--error" role="alert"><strong>{streamError.code}</strong> {streamError.message}{streamError.requestId ? `（request_id：${streamError.requestId}）` : ''}</div> : null}
        {protocolErrors.length > 0 ? <div className="v2-m4-protocol-notice" role="status"><Funnel size={14} />已忽略 {protocolErrors.length} 个异常流事件（{protocolErrors.join('、')}），未更新回答内容；详情已隐藏。</div> : null}
        <section className="v2-m4-chat-scroll">
          <AssistantMessageList
            messages={displayMessages}
            streaming={isStreaming}
            feedbackByMessage={{ ...feedbackByMessage }}
            onFeedback={(messageId, rating) => void handleFeedback(messageId, rating)}
            onStopTurn={handleStopTurn}
            onRetryTurn={handleRetryTurn}
            onRegenerate={handleRegenerate}
            conversationId={selectedConversationId || streamConversationId}
            lastQuestion={lastQuestion}
          />
          {streamState === 'done' && streamFinishReason !== 'refusal' && streamCitations.length === 0 ? <div className="v2-m4-no-citations"><Archive size={14} />当前回答未返回引用，右侧仅显示你主动检索的真实结果。</div> : null}
        </section>
        <AssistantComposer
          value={composerValue}
          disabled={knowledgeState !== 'ready' || isStreaming}
          streaming={isStreaming}
          capabilities={composerCapabilities}
          models={composerModels}
          selection={composerSelection}
          onChange={setComposerValue}
          onSelectionChange={setComposerSelection}
          onSubmit={handleSend}
          onCancel={() => void handleCancel()}
          onPickAttachments={handlePickAttachments}
          knowledgeBases={knowledgeBases}
          selectedKnowledgeBaseId={selectedKbId}
          onPromoteAttachment={handlePromoteAttachment}
        />
      </main>
      <AssistantContextPanel
        open={contextRailOpen}
        selectedKnowledgeBase={selectedKnowledgeBase}
        citations={streamCitations}
        searchQuery={searchQuery}
        searchState={searchState}
        searchHits={searchHits}
        previewState={previewState}
        previewDocument={previewDocument}
        turnId={diagnosticTurnId}
        onSearchQueryChange={setSearchQuery}
        onSearch={() => void handleSearch()}
        onOpenCitation={(documentId) => void handleOpenDocument(documentId)}
        onOpenSearchHit={(documentId) => void handleOpenDocument(documentId)}
      />
    </div>
  )
}
