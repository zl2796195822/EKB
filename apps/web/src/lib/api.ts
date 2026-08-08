import type {
  ConversationMessage,
  ConversationSummary,
  DocumentDiff,
  DocumentRecord,
  DocumentVersionRecord,
  FeedbackPayload,
  KnowledgeBase,
  LoginResponse,
  MeResponse,
  RefreshResponse,
  SearchResult,
  SseEvent,
} from '../types/api'

interface ApiErrorBody {
  error?: {
    message?: string
  }
}

type EventHandler = (event: SseEvent) => void

type AuthFailureHandler = () => void

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

  async getMe(): Promise<MeResponse> {
    return this.request<MeResponse>('/me')
  }

  async listKnowledgeBases(): Promise<KnowledgeBase[]> {
    return this.request<KnowledgeBase[]>('/kb')
  }

  async listDocuments(kbId: string): Promise<DocumentRecord[]> {
    return this.request<DocumentRecord[]>(`/kb/${encodeURIComponent(kbId)}/docs`)
  }

  async uploadDocument(kbId: string, file: File): Promise<void> {
    const formData = new FormData()
    formData.append('file', file)
    formData.append('title', file.name)

    await this.request(`/kb/${encodeURIComponent(kbId)}/docs`, {
      method: 'POST',
      body: formData,
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
    return this.request<DocumentDiff>(
      `/admin/kb/${encodeURIComponent(kbId)}/docs/${encodeURIComponent(docId)}/diff?from_version=${fromVersion}&to_version=${toVersion}`,
    )
  }

  async search(query: string, kbId: string): Promise<SearchResult[]> {
    const result = await this.request<{ results: SearchResult[] }>('/search', {
      method: 'POST',
      body: JSON.stringify({ query, kb_ids: [kbId], top_k: 10 }),
    })
    return result.results
  }

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
      options: { stream: true, max_citations: 5 },
    }
    if (conversationId) body.conversation_id = conversationId
    const response = await fetch(`${this.baseUrl}/qa/ask`, {
      method: 'POST',
      headers: this.headers(true),
      body: JSON.stringify(body),
      signal,
    })

    await this.assertResponse(response)
    if (!response.body) {
      throw new Error('问答流没有返回内容')
    }

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
        onEvent({ event: eventName, data: JSON.parse(dataLine) as Record<string, unknown> })
      }

      if (done) break
    }
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

    // 受保护接口遇到 401 时尝试用 refresh token 续期并重试一次。
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

  /**
   * 串行化 refresh：并发请求只触发一次刷新，其余等待结果。
   * 返回新的 access token；刷新失败返回 null。
   */
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
    throw new Error(body.error?.message ?? `请求失败（${response.status}）`)
  }
}
