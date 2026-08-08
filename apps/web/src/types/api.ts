export type KbVisibility = 'PRIVATE' | 'TEAM' | 'PUBLIC'
export type DocumentStatus = 'PROCESSING' | 'READY' | 'FAILED' | 'DELETED'

export interface UserSummary {
  id: string
  name: string
  email: string
}

export interface TenantSummary {
  id: string
  name: string
  role: string
}

export interface LoginResponse {
  access_token: string
  expires_in: number
  refresh_token: string
  token_type: string
  user: UserSummary
  tenants: TenantSummary[]
}

export interface RefreshResponse {
  access_token: string
  expires_in: number
  token_type: string
}

export interface MeResponse {
  user: UserSummary
  tenants: TenantSummary[]
  capabilities: string[]
  policy_version: number
}

export interface KnowledgeBase {
  id: string
  tenant_id: string
  name: string
  description: string
  visibility: KbVisibility
  role: string
  document_count: number
  created_at: string
  updated_at: string
}

export interface DocumentRecord {
  id: string
  kb_id: string
  title: string
  status: DocumentStatus
  version: number
  mime_type: string
  checksum: string
  chunk_count: number
  failure_reason: string | null
  created_at: string
  updated_at: string
}

export interface SearchResult {
  chunk_id: string
  doc_id: string
  kb_id: string
  title: string
  section_path: string[]
  snippet: string
  score: number
  updated_at: string
  // citation 事件补充字段，搜索结果中不存在。
  doc_version?: number
}

export interface SseEvent {
  event: string
  data: Record<string, unknown>
}

export interface ConversationSummary {
  id: string
  title: string
  created_at: string
  updated_at: string
  archived_at: string | null
}

export interface ConversationMessage {
  id: string
  role: string
  content: string
  created_at: string
}

export type FeedbackRating = 'UP' | 'DOWN'

export interface FeedbackPayload {
  rating: FeedbackRating
  reason: string
  comment?: string
}
