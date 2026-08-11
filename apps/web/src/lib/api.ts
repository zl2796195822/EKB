import type {
  CitationsPayload,
  ContentDeltaPayload,
  ConversationMessage,
  ConversationSummary,
  DonePayload,
  DocumentRetryResponse,
  DocumentDiff,
  DocumentRecord,
  DocumentVersionRecord,
  ErrorPayload,
  FeedbackPayload,
  ApiErrorDetails,
  ApiErrorUploadMetadata,
  AuditLogListResponse,
  AuditLogQuery,
  BackupResponse,
  KbMemberRecord,
  OpsDashboardResponse,
  ReviewItemListResponse,
  ReviewItemQuery,
  ReviewItemRecord,
  ReviewItemUpdate,
  SyncRunResponse,
  SyncSourceCreate,
  SyncSourceRecord,
  KnowledgeBaseUpdate,
  KnowledgeBase,
  LoginResponse,
  MeResponse,
  PhasePayload,
  RefreshResponse,
  RequestPayload,
  SearchResult,
  SseEvent,
  SseV2Envelope,
  TurnCancelResponse,
  TurnPhase,
  UploadAcceptedResponse,
  TenantCreate,
  TenantResponse,
  UserInvite,
  UserInviteResponse,
  ApiKeyCreateResponse,
  ApiKeysResponse,
  ChangePasswordPayload,
  MeProfileResponse,
  NotificationItem,
  NotificationsResponse,
  PatchProfilePayload,
  PreferencesResponse,
  ProfileResponse,
  SessionItem,
  SessionsResponse,
  SessionRevokeResponse,
  TrashClearResponse,
  TrashListQuery,
  TrashListResponse,
  TrashPurgeResponse,
  TrashRestoreResponse,
  AnalyticsActivityResponse,
  AnalyticsDistributionResponse,
  AnalyticsOverviewResponse,
  AnalyticsTrendResponse,
  AppInstallResponse,
  AppsCatalogResponse,
  AppsInstalledResponse,
  AppUninstallResponse,
  AppDetailResponse,
  AppConfigureResponse,
  AppConnectResponse,
  AppCredentialCreateResponse,
  AppCredentialListResponse,
  AppCredentialRevokeResponse,
  AppRunRecordResponse,
  FavoritesListQuery,
  FavoritesListResponse,
  FavoriteCheckResponse,
  FavoriteToggleResponse,
} from '../types/api'
import type {
  V3IdentityListUsersQuery,
  V3IdentityListUsersResponse,
} from '../app-v2/types/v3Identity'

interface ApiErrorBody {
  error?: {
    code?: string
    message?: string
    request_id?: string
    details?: ApiErrorDetails
    upload?: ApiErrorUploadMetadata
  }
}

export class ApiClientError extends Error {
  readonly status: number
  readonly code: string
  readonly requestId?: string
  readonly details: ApiErrorDetails
  readonly upload?: ApiErrorUploadMetadata

  constructor(
    status: number,
    code: string,
    message: string,
    requestId?: string,
    details: ApiErrorDetails = {},
    upload?: ApiErrorUploadMetadata,
  ) {
    super(message)
    this.name = 'ApiClientError'
    this.status = status
    this.code = code
    this.requestId = requestId
    this.details = details
    this.upload = upload
  }
}

type EventHandler = (event: SseEvent) => void

type AuthFailureHandler = () => void

function createIdempotencyKey(): string {
  if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') {
    return crypto.randomUUID()
  }

  if (typeof crypto !== 'undefined' && typeof crypto.getRandomValues === 'function') {
    const bytes = new Uint8Array(16)
    crypto.getRandomValues(bytes)
    return Array.from(bytes, (byte) => byte.toString(16).padStart(2, '0')).join('')
  }

  throw new Error('当前运行环境不支持安全的上传幂等键生成')
}

/** 只序列化已定义的既有查询参数，不引入服务端不认识的字段。 */
function buildQuery<T extends object>(params: T): string {
  const search = new URLSearchParams()
  for (const [key, value] of Object.entries(params)) {
    if (value === undefined || value === '') continue
    search.set(key, String(value))
  }
  const serialized = search.toString()
  return serialized ? `?${serialized}` : ''
}

function extractUploadMetadata(details?: ApiErrorDetails): ApiErrorUploadMetadata | undefined {
  if (!details) return undefined
  const keys: Array<keyof ApiErrorUploadMetadata> = [
    'doc_id',
    'job_id',
    'status',
    'trace_id',
    'max_bytes',
    'max_bytes_human',
    'actual_bytes',
    'actual_bytes_human',
    'allowed_extensions',
    'content_type',
  ]
  const metadata = Object.fromEntries(
    keys.filter((key) => details[key] !== undefined).map((key) => [key, details[key]]),
  ) as ApiErrorUploadMetadata
  return Object.keys(metadata).length > 0 ? metadata : undefined
}

function normalizeDiffChunk(chunk: NonNullable<DocumentDiff['added']>[number]) {
  return {
    ...chunk,
    content: chunk.content ?? chunk.content_preview,
    section_path: chunk.section_path ?? [],
  }
}

// ---- SSE v2 结构化事件 ----
export interface AskStreamHandlers {
  onRequest?: (payload: RequestPayload, raw: SseV2Envelope<RequestPayload>) => void
  onPhase?: (event: 'retrieval_started' | 'retrieval_completed' | 'generation_started', payload: PhasePayload) => void
  onContentDelta?: (payload: ContentDeltaPayload, seq: number) => void
  onCitations?: (payload: CitationsPayload) => void
  onDone?: (payload: DonePayload) => void
  onError?: (payload: ErrorPayload) => void
  onProtocolError?: (type: 'stale_turn' | 'seq_mismatch' | 'bad_envelope' | 'json_decode', detail: string) => void
}

/**
 * ask() 返回的流控制句柄：
 * - turnId: 用于显式 cancelTurn()
 * - abort(): 本地 AbortController（取消 fetch）
 * - conversationId/messageId: 对话/消息定位
 */
export interface AskStreamHandle {
  turnId: string
  messageId: string
  conversationId: string
  abort: () => void
}

/**
 * SSE v2 Conversation Stream 解析器：
 * - 严格校验 envelope.turn_id 匹配，丢弃旧流 / 乱流（stale_turn）
 * - 严格校验 envelope.seq 单调递增，检测 gap（seq_mismatch）
 * - 忽略 SSE 注释行（心跳 :heartbeat ...）
 * - 解析失败（bad_envelope / json_decode）走 onProtocolError 而不是抛异常中断其他连接
 */
export class SseStreamParser {
  private buffer = ''
  private expectedTurnId: string
  private latchTurnId: boolean
  private expectedSeq = 0
  private readonly handlers: AskStreamHandlers
  private closed = false
  /** 从 request 事件回写的业务字段，供 askStream 返回的 handle 读取 */
  messageId = ''
  conversationId = ''

  /**
   * @param expectedTurnId 已知 turn_id（来自 X-Turn-Id 响应头）。留空时进入 latch 模式：
   *   从第一个 request 事件自动锁定 turn_id，避免「预解析丢首块」问题。
   */
  constructor(expectedTurnId: string, handlers: AskStreamHandlers) {
    this.expectedTurnId = expectedTurnId
    this.latchTurnId = !expectedTurnId
    this.handlers = handlers
  }

  push(chunk: string): void {
    if (this.closed) return
    this.buffer += chunk
    const blocks = this.buffer.split('\n\n')
    this.buffer = blocks.pop() ?? ''
    for (const block of blocks) this._handleBlock(block)
  }

  close(): void {
    if (this.closed) return
    this.closed = true
    if (this.buffer.trim()) this._handleBlock(this.buffer)
    this.buffer = ''
  }

  private _handleBlock(block: string): void {
    const trimmed = block.trim()
    if (!trimmed) return
    // SSE v2 心跳注释行：以 `:` 开头，直接忽略
    if (trimmed.startsWith(':')) return

    const eventName = block.match(/^event: (.+)$/m)?.[1]
    const dataLine = block.match(/^data: (.+)$/m)?.[1]
    if (!eventName || !dataLine) return

    let envelope: SseV2Envelope | null = null
    try {
      envelope = JSON.parse(dataLine) as SseV2Envelope
    } catch {
      this.handlers.onProtocolError?.('json_decode', `parse failed on event=${eventName}`)
      return
    }

    if (!envelope || typeof envelope !== 'object' || !envelope.turn_id || typeof envelope.seq !== 'number') {
      this.handlers.onProtocolError?.('bad_envelope', `missing turn_id/seq on event=${eventName}`)
      return
    }

    // turn_id 自动锁定（仅构造时未显式给定；如 CORS 未暴露 X-Turn-Id）
    if (this.latchTurnId) {
      this.expectedTurnId = envelope.turn_id
      this.latchTurnId = false
    }

    // 旧流隔离：event 不属于当前 turn，直接丢弃
    if (envelope.turn_id !== this.expectedTurnId) {
      this.handlers.onProtocolError?.(
        'stale_turn',
        `expected=${this.expectedTurnId} actual=${envelope.turn_id} event=${eventName}`,
      )
      return
    }

    // seq 校验：必须严格单调递增；gap 上报但不丢弃（避免漏数据）
    if (this.expectedSeq === 0) this.expectedSeq = 1
    if (envelope.seq < this.expectedSeq) {
      // 重复或乱序：丢弃（不调用 handler）
      this.handlers.onProtocolError?.('seq_mismatch', `stale seq=${envelope.seq} expected>=${this.expectedSeq}`)
      return
    }
    if (envelope.seq > this.expectedSeq) {
      // gap：上报但仍处理
      this.handlers.onProtocolError?.(
        'seq_mismatch',
        `gap detected: expected=${this.expectedSeq} actual=${envelope.seq}`,
      )
    }
    this.expectedSeq = envelope.seq + 1

    // ---- 分发 v2 事件 ----
    switch (eventName) {
      case 'request':
        this.messageId = (envelope.payload as RequestPayload).message_id ?? this.messageId
        this.conversationId = (envelope.payload as RequestPayload).conversation_id ?? this.conversationId
        this.handlers.onRequest?.(envelope.payload as RequestPayload, envelope as SseV2Envelope<RequestPayload>)
        break
      case 'retrieval_started':
      case 'retrieval_completed':
      case 'generation_started':
        this.handlers.onPhase?.(eventName, envelope.payload as PhasePayload)
        break
      case 'content_delta':
        this.handlers.onContentDelta?.(envelope.payload as ContentDeltaPayload, envelope.seq)
        break
      case 'citations':
        this.handlers.onCitations?.(envelope.payload as CitationsPayload)
        break
      case 'done':
        this.handlers.onDone?.(envelope.payload as DonePayload)
        this.closed = true
        break
      case 'error':
        this.handlers.onError?.(envelope.payload as ErrorPayload)
        break
      case 'token':
      case 'citation':
        // 旧 v1 事件如果出现在 v2 流里（理论不会）：忽略
        break
      default:
        // 未知事件不报错，便于后续协议扩展
        break
    }
  }
}

export class ApiClient {
  private token: string | null = null
  private refreshToken: string | null = null
  private onAuthFailure: AuthFailureHandler | null = null
  private isRefreshing = false
  private refreshWaiters: Array<(token: string | null) => void> = []

  constructor(private readonly baseUrl: string) {}

  setToken(token: string | null): void {
    this.token = token
  }

  setRefreshToken(token: string | null): void {
    this.refreshToken = token
  }

  setOnAuthFailure(handler: AuthFailureHandler | null): void {
    this.onAuthFailure = handler
  }

  async login(email: string, password: string): Promise<LoginResponse> {
    return this.request<LoginResponse>('/auth/login', {
      method: 'POST',
      body: JSON.stringify({ email, password }),
    }, false)
  }

  async refresh(refreshToken: string): Promise<RefreshResponse> {
    return this.request<RefreshResponse>('/auth/refresh', {
      method: 'POST',
      body: JSON.stringify({ refresh_token: refreshToken }),
    }, false)
  }

  async logout(refreshToken: string): Promise<void> {
    await this.request('/auth/logout', {
      method: 'POST',
      body: JSON.stringify({ refresh_token: refreshToken }),
    }, false)
  }

  async getMe(): Promise<MeProfileResponse> {
    return this.request<MeProfileResponse>('/me')
  }

  async inviteUser(payload: UserInvite): Promise<UserInviteResponse> {
    return this.request<UserInviteResponse>('/admin/users', {
      method: 'POST',
      body: JSON.stringify(payload),
    })
  }

  async listTenants(): Promise<TenantResponse[]> {
    return this.request<TenantResponse[]>('/admin/tenants')
  }

  async listTenantUsers(
    tenantId: string,
    params: V3IdentityListUsersQuery = {},
    signal?: AbortSignal,
  ): Promise<V3IdentityListUsersResponse> {
    const searchParams = new URLSearchParams({
      sort: params.sort ?? 'updated_at_desc',
      page_size: String(params.page_size ?? 20),
    })
    if (params.query) searchParams.set('query', params.query)
    if (params.role) searchParams.set('role', params.role)
    if (params.status) searchParams.set('status', params.status)
    if (params.cursor) searchParams.set('cursor', params.cursor)

    return this.request<V3IdentityListUsersResponse>(
      `/tenants/${encodeURIComponent(tenantId)}/users?${searchParams.toString()}`,
      { signal },
    )
  }

  async createTenant(payload: TenantCreate): Promise<TenantResponse> {
    return this.request<TenantResponse>('/admin/tenants', {
      method: 'POST',
      body: JSON.stringify(payload),
    })
  }

  async listKnowledgeBases(): Promise<KnowledgeBase[]> {
    return this.request<KnowledgeBase[]>('/kb')
  }

  async getKnowledgeBase(kbId: string): Promise<KnowledgeBase> {
    return this.request<KnowledgeBase>(`/kb/${encodeURIComponent(kbId)}`)
  }

  async updateKnowledgeBase(kbId: string, payload: KnowledgeBaseUpdate): Promise<KnowledgeBase> {
    return this.request<KnowledgeBase>(`/kb/${encodeURIComponent(kbId)}`, {
      method: 'PATCH',
      body: JSON.stringify(payload),
    })
  }

  async listDocuments(kbId: string): Promise<DocumentRecord[]> {
    return this.request<DocumentRecord[]>(`/kb/${encodeURIComponent(kbId)}/docs`)
  }

  async uploadDocument(
    kbId: string,
    file: File,
    idempotencyKey?: string,
  ): Promise<UploadAcceptedResponse> {
    const formData = new FormData()
    formData.append('file', file)
    formData.append('title', file.name)

    return this.request<UploadAcceptedResponse>(`/kb/${encodeURIComponent(kbId)}/docs`, {
      method: 'POST',
      body: formData,
      headers: { 'Idempotency-Key': idempotencyKey ?? createIdempotencyKey() },
    })
  }

  async createKnowledgeBase(
    name: string,
    description = '',
    visibility: 'PRIVATE' | 'TEAM' | 'PUBLIC' = 'PRIVATE',
  ): Promise<KnowledgeBase> {
    return this.request<KnowledgeBase>('/kb', {
      method: 'POST',
      body: JSON.stringify({ name, description, visibility }),
    })
  }

  async deleteKnowledgeBase(kbId: string): Promise<void> {
    await this.request(`/kb/${encodeURIComponent(kbId)}`, { method: 'DELETE' })
  }

  async deleteDocument(kbId: string, docId: string): Promise<void> {
    await this.request(`/kb/${encodeURIComponent(kbId)}/docs/${encodeURIComponent(docId)}`, {
      method: 'DELETE',
    })
  }

  async getDocument(kbId: string, docId: string): Promise<DocumentRecord> {
    return this.request<DocumentRecord>(
      `/kb/${encodeURIComponent(kbId)}/docs/${encodeURIComponent(docId)}`,
    )
  }

  async retryDocument(kbId: string, docId: string): Promise<DocumentRetryResponse> {
    return this.request<DocumentRetryResponse>(
      `/kb/${encodeURIComponent(kbId)}/docs/${encodeURIComponent(docId)}/retry`,
      { method: 'POST' },
    )
  }

  async listKbMembers(kbId: string): Promise<KbMemberRecord[]> {
    return this.request<KbMemberRecord[]>(`/kb/${encodeURIComponent(kbId)}/members`)
  }

  async addKbMember(
    kbId: string,
    email: string,
    role: 'OWNER' | 'ADMIN' | 'EDITOR' | 'VIEWER' = 'VIEWER',
  ): Promise<KbMemberRecord> {
    return this.request<KbMemberRecord>(`/kb/${encodeURIComponent(kbId)}/members`, {
      method: 'POST',
      body: JSON.stringify({ email, role }),
    })
  }

  async removeKbMember(kbId: string, userId: string): Promise<void> {
    await this.request(`/kb/${encodeURIComponent(kbId)}/members/${encodeURIComponent(userId)}`, {
      method: 'DELETE',
    })
  }

  async listDocumentVersions(kbId: string, docId: string): Promise<DocumentVersionRecord[]> {
    return this.request<DocumentVersionRecord[]>(
      `/admin/kb/${encodeURIComponent(kbId)}/docs/${encodeURIComponent(docId)}/versions`,
    )
  }

  async getDocumentDiff(
    kbId: string,
    docId: string,
    fromVersion: number,
    toVersion: number,
  ): Promise<DocumentDiff> {
    const diff = await this.request<DocumentDiff>(
      `/admin/kb/${encodeURIComponent(kbId)}/docs/${encodeURIComponent(docId)}/diff?from_version=${fromVersion}&to_version=${toVersion}`,
    )
    return {
      ...diff,
      doc_id: diff.doc_id ?? docId,
      added: diff.added.map(normalizeDiffChunk),
      removed: diff.removed.map(normalizeDiffChunk),
      changed: (diff.changed ?? []).map(normalizeDiffChunk),
      unchanged: (diff.unchanged ?? []).map(normalizeDiffChunk),
    }
  }

  async search(query: string, kbId: string): Promise<SearchResult[]> {
    const result = await this.request<{ results: SearchResult[] }>('/search', {
      method: 'POST',
      body: JSON.stringify({ query, kb_ids: [kbId], top_k: 10 }),
    })
    return result.results
  }

  /** @deprecated 保留向后兼容（SSE v1 事件：token/citation/done/error）。新代码请用 askStream()。 */
  async ask(
    question: string,
    kbId: string,
    onEvent: EventHandler,
    signal?: AbortSignal,
    conversationId?: string,
  ): Promise<void> {
    const body: Record<string, unknown> = {
      question,
      kb_ids: [kbId],
      options: { stream: true, max_citations: 5, stream_version: 1 },
    }
    if (conversationId) body.conversation_id = conversationId
    const response = await fetch(`${this.baseUrl}/qa/ask`, {
      method: 'POST',
      headers: this.headers(true),
      body: JSON.stringify(body),
      signal,
    })

    await this.assertResponse(response)
    if (!response.body) throw new Error('问答流没有返回内容')

    const reader = response.body.getReader()
    const decoder = new TextDecoder()
    let buffer = ''

    while (true) {
      const { done, value } = await reader.read()
      buffer += decoder.decode(value ?? new Uint8Array(), { stream: !done })
      const blocks = buffer.split('\n\n')
      buffer = blocks.pop() ?? ''

      for (const block of blocks) {
        const eventName = block.match(/^event: (.+)$/m)?.[1]
        const dataLine = block.match(/^data: (.+)$/m)?.[1]
        if (!eventName || !dataLine) continue
        try {
          onEvent({ event: eventName, data: JSON.parse(dataLine) as Record<string, unknown> })
        } catch {
          /* ignore */
        }
      }

      if (done) break
    }
  }

  /**
   * SSE v2 问答流（推荐）。
   *
   * 关键点：
   * 1. 通过响应头 X-Turn-Id 取得 turnId（失败则退化为 payload.turn_id）
   * 2. SseStreamParser 严格校验 turn_id 匹配 + seq 单调递增，避免旧流污染 / 乱序重渲染
   * 3. 所有回调不直接 setState，交由外层 RAF 批处理器合并渲染
   */
  async askStream(
    question: string,
    kbId: string,
    handlers: AskStreamHandlers,
    signal?: AbortSignal,
    conversationId?: string,
  ): Promise<AskStreamHandle> {
    const controller = signal ? undefined : new AbortController()
    const actualSignal = signal ?? controller!.signal

    const body: Record<string, unknown> = {
      question,
      kb_ids: [kbId],
      // stream_version=2 启用 v2 Conversation Stream；服务端 feature flag 关闭时会自动回退 v1
      options: { stream: true, max_citations: 5, stream_version: 2 },
    }
    if (conversationId) body.conversation_id = conversationId

    const response = await fetch(`${this.baseUrl}/qa/ask`, {
      method: 'POST',
      headers: this.headers(true),
      body: JSON.stringify(body),
      signal: actualSignal,
    })

    await this.assertResponse(response)
    if (!response.body) throw new Error('问答流没有返回内容')

    // 从响应头取得 turn_id；CORS expose_headers 需要包含 X-Turn-Id。
    // 若 header 缺失（CORS 未配置/旧中间件），SseStreamParser 会在收到第一个 request 事件时自动 latch。
    const turnIdFromHeader = response.headers.get('X-Turn-Id') ?? ''
    const parser = new SseStreamParser(turnIdFromHeader, handlers)

    const reader = response.body.getReader()
    const decoder = new TextDecoder()

    // 所有 chunk 直接喂给 parser（含首块），不再预解析丢事件。
    void (async () => {
      try {
        while (true) {
          const { done, value } = await reader.read()
          const chunk = decoder.decode(value ?? new Uint8Array(), { stream: !done })
          parser.push(chunk)
          if (done) break
        }
      } catch (err) {
        if ((err as Error).name === 'AbortError') {
          // 用户主动取消，不做协议层错误
        } else {
          handlers.onError?.({ code: 'STREAM_READ_ERROR', message: (err as Error).message })
        }
      } finally {
        parser.close()
      }
    })()

    return {
      turnId: turnIdFromHeader,
      get messageId() { return parser.messageId },
      get conversationId() { return parser.conversationId || conversationId || '' },
      abort: () => {
        if (controller) controller.abort()
      },
    }
  }

  /** 显式取消一个 QA Turn（SSE v2）。与 abort() 不同：这个是幂等 API 调用，会通知后端停止 LLM。 */
  async cancelTurn(turnId: string): Promise<TurnCancelResponse> {
    return this.request<TurnCancelResponse>(
      `/qa/turns/${encodeURIComponent(turnId)}/cancel`,
      { method: 'POST' },
    )
  }

  async sendFeedback(messageId: string, payload: FeedbackPayload): Promise<void> {
    await this.request(`/qa/messages/${encodeURIComponent(messageId)}/feedback`, {
      method: 'POST',
      body: JSON.stringify(payload),
    })
  }

  async listConversations(): Promise<ConversationSummary[]> {
    return this.request<ConversationSummary[]>('/conversations')
  }

  async getConversationMessages(
    conversationId: string,
  ): Promise<ConversationMessage[]> {
    return this.request<ConversationMessage[]>(
      `/conversations/${encodeURIComponent(conversationId)}/messages`,
    )
  }

  async deleteConversation(conversationId: string): Promise<void> {
    await this.request(`/conversations/${encodeURIComponent(conversationId)}`, {
      method: 'DELETE',
    })
  }

  // ---- M6 治理/运营：仅封装现有 /admin 端点，不新增路径或字段 ----

  async getOpsDashboard(days = 7): Promise<OpsDashboardResponse> {
    return this.request<OpsDashboardResponse>(
      `/admin/ops/dashboard?days=${encodeURIComponent(String(days))}`,
    )
  }

  async listAuditLogs(query: AuditLogQuery = {}): Promise<AuditLogListResponse> {
    return this.request<AuditLogListResponse>(`/admin/audit${buildQuery(query)}`)
  }

  async listReviewItems(query: ReviewItemQuery = {}): Promise<ReviewItemListResponse> {
    return this.request<ReviewItemListResponse>(`/admin/reviews${buildQuery(query)}`)
  }

  async getReviewItem(itemId: string): Promise<ReviewItemRecord> {
    return this.request<ReviewItemRecord>(`/admin/reviews/${encodeURIComponent(itemId)}`)
  }

  async updateReviewItem(itemId: string, payload: ReviewItemUpdate): Promise<ReviewItemRecord> {
    return this.request<ReviewItemRecord>(`/admin/reviews/${encodeURIComponent(itemId)}`, {
      method: 'PATCH',
      body: JSON.stringify(payload),
    })
  }

  async listSyncSources(kbId?: string): Promise<SyncSourceRecord[]> {
    return this.request<SyncSourceRecord[]>(`/admin/sync/sources${buildQuery({ kb_id: kbId })}`)
  }

  async createSyncSource(payload: SyncSourceCreate): Promise<SyncSourceRecord> {
    return this.request<SyncSourceRecord>('/admin/sync/sources', {
      method: 'POST',
      body: JSON.stringify(payload),
    })
  }

  async runSyncSource(sourceId: string): Promise<SyncRunResponse> {
    return this.request<SyncRunResponse>(
      `/admin/sync/sources/${encodeURIComponent(sourceId)}/run`,
      { method: 'POST' },
    )
  }

  async triggerBackup(): Promise<BackupResponse> {
    return this.request<BackupResponse>('/admin/backup', { method: 'POST' })
  }

  // ---- Profile (Phase 1) ----

  async patchMeProfile(payload: PatchProfilePayload): Promise<ProfileResponse> {
    return this.request<ProfileResponse>('/me/profile', {
      method: 'PATCH',
      body: JSON.stringify(payload),
    })
  }

  async changePassword(payload: ChangePasswordPayload): Promise<{ status: string }> {
    return this.request<{ status: string }>('/me/password', {
      method: 'POST',
      body: JSON.stringify(payload),
    })
  }

  async getPreferences(): Promise<PreferencesResponse> {
    return this.request<PreferencesResponse>('/me/preferences')
  }

  async patchPreferences(preferences: Record<string, unknown>): Promise<PreferencesResponse> {
    return this.request<PreferencesResponse>('/me/preferences', {
      method: 'PATCH',
      body: JSON.stringify({ preferences }),
    })
  }

  async getSessions(): Promise<SessionsResponse> {
    return this.request<SessionsResponse>('/me/sessions')
  }

  async revokeSession(sessionId: string, revokeAll = false): Promise<SessionRevokeResponse> {
    const qs = revokeAll ? '?revoke_all=true' : ''
    return this.request<SessionRevokeResponse>(`/me/sessions/${encodeURIComponent(sessionId)}${qs}`, {
      method: 'DELETE',
    })
  }

  async getApiKeys(): Promise<ApiKeysResponse> {
    return this.request<ApiKeysResponse>('/me/api-keys')
  }

  async createApiKey(name: string): Promise<ApiKeyCreateResponse> {
    return this.request<ApiKeyCreateResponse>('/me/api-keys', {
      method: 'POST',
      body: JSON.stringify({ name }),
    })
  }

  async revokeApiKey(keyId: string): Promise<{ status: string }> {
    return this.request<{ status: string }>(`/me/api-keys/${encodeURIComponent(keyId)}`, {
      method: 'DELETE',
    })
  }

  async getNotifications(unreadOnly = false): Promise<NotificationsResponse> {
    const qs = unreadOnly ? '?unread_only=true' : ''
    return this.request<NotificationsResponse>(`/me/notifications${qs}`)
  }

  async markNotificationRead(notificationId: string): Promise<{ status: string }> {
    return this.request<{ status: string }>(`/me/notifications/${encodeURIComponent(notificationId)}`, {
      method: 'PATCH',
    })
  }

  // ---- Recycle bin (Phase 2) ----

  async listTrash(query: TrashListQuery = {}): Promise<TrashListResponse> {
    return this.request<TrashListResponse>(`/trash${buildQuery(query)}`)
  }

  async restoreTrashItem(itemId: string): Promise<TrashRestoreResponse> {
    return this.request<TrashRestoreResponse>(`/trash/${encodeURIComponent(itemId)}/restore`, {
      method: 'POST',
    })
  }

  async purgeTrashItem(itemId: string): Promise<TrashPurgeResponse> {
    return this.request<TrashPurgeResponse>(`/trash/${encodeURIComponent(itemId)}`, {
      method: 'DELETE',
    })
  }

  async clearTrash(resourceType?: string): Promise<TrashClearResponse> {
    return this.request<TrashClearResponse>(`/trash${buildQuery({ resource_type: resourceType })}`, {
      method: 'DELETE',
    })
  }

  async getAnalyticsOverview(days = 7): Promise<AnalyticsOverviewResponse> {
    return this.request<AnalyticsOverviewResponse>(`/analytics/overview${buildQuery({ days })}`)
  }

  async getAnalyticsTrend(days = 14): Promise<AnalyticsTrendResponse> {
    return this.request<AnalyticsTrendResponse>(`/analytics/trend${buildQuery({ days })}`)
  }

  async getAnalyticsDistribution(
    days = 7,
    limit?: number,
  ): Promise<AnalyticsDistributionResponse> {
    return this.request<AnalyticsDistributionResponse>(
      `/analytics/distribution${buildQuery({ days, limit })}`,
    )
  }

  async getAnalyticsActivity(limit = 10): Promise<AnalyticsActivityResponse> {
    return this.request<AnalyticsActivityResponse>(`/analytics/activity${buildQuery({ limit })}`)
  }

  async getAppsCatalog(limit?: number): Promise<AppsCatalogResponse> {
    return this.request<AppsCatalogResponse>(`/apps/catalog${buildQuery({ limit })}`)
  }

  async getAppsInstalled(): Promise<AppsInstalledResponse> {
    return this.request<AppsInstalledResponse>('/apps/installed')
  }

  async installApp(slug: string): Promise<AppInstallResponse> {
    return this.request<AppInstallResponse>(`/apps/install/${encodeURIComponent(slug)}`, {
      method: 'POST',
    })
  }

  async uninstallApp(slug: string): Promise<AppUninstallResponse> {
    return this.request<AppUninstallResponse>(`/apps/uninstall/${encodeURIComponent(slug)}`, {
      method: 'POST',
    })
  }

  // ============ Apps Phase 4 additive endpoints ============

  async getAppDetail(slug: string): Promise<AppDetailResponse> {
    return this.request<AppDetailResponse>(`/apps/${encodeURIComponent(slug)}`)
  }

  async configureApp(
    slug: string,
    config: Record<string, unknown>,
  ): Promise<AppConfigureResponse> {
    return this.request<AppConfigureResponse>(`/apps/${encodeURIComponent(slug)}/configure`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ config }),
    })
  }

  async connectApp(slug: string): Promise<AppConnectResponse> {
    return this.request<AppConnectResponse>(`/apps/${encodeURIComponent(slug)}/connect`, {
      method: 'POST',
    })
  }

  async createAppCredential(
    slug: string,
    payload: { name: string; secret: string },
  ): Promise<AppCredentialCreateResponse> {
    return this.request<AppCredentialCreateResponse>(
      `/apps/${encodeURIComponent(slug)}/credentials`,
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      },
    )
  }

  async listAppCredentials(slug: string): Promise<AppCredentialListResponse> {
    return this.request<AppCredentialListResponse>(
      `/apps/${encodeURIComponent(slug)}/credentials`,
    )
  }

  async revokeAppCredential(credentialId: string): Promise<AppCredentialRevokeResponse> {
    return this.request<AppCredentialRevokeResponse>(
      `/apps/credentials/${encodeURIComponent(credentialId)}/revoke`,
      { method: 'POST' },
    )
  }

  async recordAppRun(
    slug: string,
    payload: {
      run_type: string
      status: 'SUCCEEDED' | 'FAILED' | string
      result_redacted?: Record<string, unknown>
      started_at: string
      completed_at?: string | null
      sync_source_id?: string | null
      error_code?: string | null
      error_message?: string | null
    },
  ): Promise<AppRunRecordResponse> {
    return this.request<AppRunRecordResponse>(`/apps/${encodeURIComponent(slug)}/runs`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    })
  }

  // ============ Favorites (Phase 6 P2 · v3_007_content_governance_compat) ============

  async listFavorites(query: FavoritesListQuery = {}): Promise<FavoritesListResponse> {
    return this.request<FavoritesListResponse>(`/favorites${buildQuery(query)}`)
  }

  async checkFavorite(resourceType: string, resourceId: string): Promise<FavoriteCheckResponse> {
    const qs = buildQuery({ resource_type: resourceType, resource_id: resourceId })
    return this.request<FavoriteCheckResponse>(`/favorites/check${qs}`)
  }

  async toggleFavorite(
    resourceType: string,
    resourceId: string,
  ): Promise<FavoriteToggleResponse> {
    return this.request<FavoriteToggleResponse>('/favorites/toggle', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ resource_type: resourceType, resource_id: resourceId }),
    })
  }

  private async request<T>(
    path: string,
    init: RequestInit = {},
    requiresAuth = true,
  ): Promise<T> {
    const headers = this.headers(init.body instanceof FormData ? false : true, requiresAuth)
    const response = await fetch(`${this.baseUrl}${path}`, {
      ...init,
      headers: { ...headers, ...init.headers },
    })

    if (response.status === 401 && requiresAuth && this.refreshToken) {
      const newToken = await this.ensureRefreshed()
      if (newToken) {
        const retryHeaders = this.headers(init.body instanceof FormData ? false : true, requiresAuth)
        const retry = await fetch(`${this.baseUrl}${path}`, {
          ...init,
          headers: { ...retryHeaders, ...init.headers },
        })
        return this.parseResponse(retry)
      }
      this.handleAuthFailure()
      throw new Error('登录已过期，请重新登录')
    }

    return this.parseResponse(response)
  }

  private async parseResponse(response: Response): Promise<any> {
    await this.assertResponse(response)
    if (response.status === 204 || response.headers.get('content-length') === '0') {
      return undefined
    }
    return await response.json()
  }

  private async ensureRefreshed(): Promise<string | null> {
    if (this.isRefreshing) {
      return new Promise<string | null>((resolve) => {
        this.refreshWaiters.push(resolve)
      })
    }
    this.isRefreshing = true
    try {
      if (!this.refreshToken) return null
      const result = await this.refresh(this.refreshToken)
      this.token = result.access_token
      this.refreshWaiters.forEach((resolve) => resolve(result.access_token))
      this.refreshWaiters = []
      return result.access_token
    } catch {
      this.refreshWaiters.forEach((resolve) => resolve(null))
      this.refreshWaiters = []
      return null
    } finally {
      this.isRefreshing = false
    }
  }

  private handleAuthFailure(): void {
    if (this.onAuthFailure) this.onAuthFailure()
  }

  private headers(json: boolean, requiresAuth = true): HeadersInit {
    const headers: Record<string, string> = {}
    if (json) headers['Content-Type'] = 'application/json'
    if (requiresAuth && this.token) headers.Authorization = `Bearer ${this.token}`
    return headers
  }

  private async assertResponse(response: Response): Promise<void> {
    if (response.ok) return
    const body = (await response.json().catch(() => ({}))) as ApiErrorBody
    const error = body.error
    throw new ApiClientError(
      response.status,
      error?.code ?? `HTTP_${response.status}`,
      error?.message ?? `请求失败（${response.status}）`,
      error?.request_id ?? response.headers.get('X-Request-Id') ?? undefined,
      error?.details ?? {},
      error?.upload ?? extractUploadMetadata(error?.details),
    )
  }
}
