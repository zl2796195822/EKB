export type KbVisibility = 'PRIVATE' | 'TEAM' | 'PUBLIC'
export type KbRole = 'OWNER' | 'ADMIN' | 'EDITOR' | 'VIEWER'
export type DocumentStatus = 'PROCESSING' | 'INDEXING' | 'READY' | 'FAILED' | 'DELETED'

export interface KnowledgeBaseUpdate {
  name?: string
  description?: string
  visibility?: KbVisibility
}

export interface KbMemberRecord {
  id: string
  kb_id: string
  user_id: string
  role: KbRole | string
  granted_by?: string | null
  created_at: string
  updated_at: string
}

export interface FolderRecord {
  id: string
  tenant_id: string
  kb_id: string
  parent_id: string | null
  name: string
  deleted_at: string | null
  created_by: string
  created_at: string
  updated_at: string
  child_count: number
  document_count: number
}

export interface FolderListResponse {
  items: FolderRecord[]
  parent_id: string | null
  kb_id: string
}

export interface UploadAcceptedResponse {
  doc_id: string
  job_id: string
  status: string
  trace_id: string
}

export interface UploadBatchItemResponse {
  client_item_id: string
  accepted: boolean
  normalized_relative_path: string | null
  display_path: string | null
  byte_size: number
  detected_mime: string | null
  upload_item_id: string | null
  error_code?: string | null
  error_detail?: Record<string, unknown> | null
  upload_session?: {
    session_id: string
    method: string
    provider_upload_id: string | null
    upload_urls: string[]
    part_size: number | null
    expires_at: string
  } | null
}

export interface UploadBatchResponse {
  id: string
  batch_id: string
  status: string
  created: boolean
  items: UploadBatchItemResponse[]
}

export interface UploadBatchProjectionItem {
  id: string
  client_item_id: string
  relative_path: string
  status: string
  progress: Record<string, unknown> | null
  version_id: string | null
  job_id: string | null
  error: { code: string; message: string; retryable: boolean } | null
}

export interface UploadBatchProjection {
  id: string
  kb_id: string
  mode: string
  status: string
  item_count: number
  total_bytes: number
  created_at: string
  updated_at: string
  items: UploadBatchProjectionItem[]
}

export interface DocumentRetryResponse {
  status: 'accepted' | string
  trace_id: string
}

export interface ApiErrorDetails {
  readonly [key: string]: unknown
}

export interface ApiErrorUploadMetadata {
  readonly doc_id?: string
  readonly job_id?: string
  readonly status?: string
  readonly trace_id?: string
  readonly max_bytes?: number
  readonly max_bytes_human?: string
  readonly actual_bytes?: number
  readonly actual_bytes_human?: string
  readonly allowed_extensions?: string[]
  readonly content_type?: string
}

// ---- SSE v2 Conversation Stream ----
/** SSE v2 FinishReason：前端根据它切换空消息可见性/反馈按钮可用性 */
export type FinishReason = 'stop' | 'refusal' | 'cancelled' | 'timeout' | 'error'

/** SSE v2 TurnStatus（主要用于可观测性面板，聊天流用 phase + finishReason 即可） */
export type TurnStatus = 'running' | 'completed' | 'cancelled' | 'timeout' | 'error'

/** 问答阶段：前端用于展示「正在检索/正在生成」等阶段状态 */
export type TurnPhase = 'retrieval' | 'generation' | 'done'

/** v2 协议统一 envelope，所有 payload 都是 envelope.payload 的子集。 */
export interface SseV2Envelope<TPayload = unknown> {
  turn_id: string
  request_id: string
  seq: number
  timestamp: string
  payload: TPayload
}

/** request 事件 payload：携带 turn_id / message_id / stream_version 等初始化信息。 */
export interface RequestPayload {
  turn_id: string
  request_id: string
  message_id: string
  conversation_id: string
  stream_version: 1 | 2
}

/** 阶段事件 payload（retrieval_started / retrieval_completed / generation_started）。 */
export interface PhasePayload {
  phase?: TurnPhase
  authorized_kb_count?: number
  chunk_count?: number
  elapsed_ms?: number
  evidence_count?: number
  provider?: string
  model?: string
}

/** 流式 token 增量 payload（v2 用 content_delta，取代 v1 的 token 单条）。 */
export interface ContentDeltaPayload {
  text: string
  delta: string
  citations?: CitationSlim[]
}

/** citations 事件 payload（v2 单批发送，替代 v1 的 citation 逐条发送）。 */
export interface CitationsPayload {
  items: CitationSlim[]
}

/** 精简 citation：SSE v2 citations 事件和引用直连使用的最小字段集。 */
export interface CitationSlim {
  citation_id: string
  doc_id: string
  chunk_id: string
  title: string
  section_path: string[]
  version?: number
  updated_at?: string
}

/** done 事件 payload：携带最终 finish_reason / message_id / citations_count。 */
export interface DonePayload {
  message_id?: string
  finish_reason: FinishReason
  confidence?: 'low' | 'medium' | 'high'
  last_seq: number
  citations_count?: number
}

/** error 事件 payload。 */
export interface ErrorPayload {
  code: string
  message: string
  request_id?: string
}

/** 显式取消路由响应。 */
export interface TurnCancelResponse {
  turn_id: string
  status: 'cancelled' | 'already_completed' | 'not_found'
  accepted: boolean
  message?: string
}

/** QAPage / App 层统一持有：一个 QA Turn 的全状态（前端状态机）。 */
export interface TurnState {
  turnId: string
  messageId: string
  conversationId: string
  phase: TurnPhase
  phaseElapsedMs?: number
  chunkCount?: number
  evidenceCount?: number
  provider?: string
  model?: string
  isStreaming: boolean
  finishReason?: FinishReason
  confidence?: 'low' | 'medium' | 'high'
  errorCode?: string
  errorMessage?: string
  /** 期望收到的下一个 seq；用于检测 seq gap 和 stale_turn 丢弃。 */
  expectedSeq: number
  /** 检测到的 gap 数，供上报指标。 */
  seqGapCount: number
  /** 发起于 perf.now()，供本地测量 TTFB。 */
  startedAt: number
}

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

export interface TenantCreate {
  name: string
  owner_email: string
  owner_name: string
  owner_password: string
  model_routing_key: string
  egress_policy: string
  quota_daily_qa: number
  quota_storage_docs: number
  quota_storage_bytes_per_file: number
}

export interface TenantResponse {
  id: string
  name: string
  role: string
  model_routing_key: string
  egress_policy: string
  quota_daily_qa: number
  quota_storage_docs: number
  quota_storage_bytes_per_file: number
}

export interface UserInvite {
  email: string
  name: string
  password: string
  role: string
}

export interface UserInviteResponse {
  id: string
  email: string
  name: string
  role: string
  tenant_id: string
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
  file_size?: number | null
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

export interface DocumentVersionRecord {
  id: string
  doc_id: string
  version: number
  checksum: string
  chunk_count: number
  content_snapshot: DocumentVersionChunk[]
  created_at: string
}

export interface DocumentVersionChunk {
  chunk_index?: number
  section_path?: string[]
  content_hash?: string
  content_preview?: string
}

export interface DiffChunk {
  chunk_index?: number
  content_hash?: string
  content?: string
  content_preview?: string
  before?: string
  after?: string
  section_path: string[]
}

export interface DocumentDiff {
  doc_id?: string
  from_version: number
  to_version: number
  from_chunk_count?: number
  to_chunk_count?: number
  added: DiffChunk[]
  removed: DiffChunk[]
  changed: DiffChunk[]
  unchanged?: DiffChunk[]
}

// ---- M6 治理/运营（仅映射现有 /admin 端点，不新增契约）----

/** GET /admin/ops/dashboard?days= 的既有 OpsDashboardResponse 字段。 */
export interface OpsDashboardResponse {
  qa_volume: number
  answered: number
  refused: number
  timeout: number
  error: number
  accuracy: number | null
  refusal_rate: number | null
  satisfaction: number | null
  feedback_up: number
  feedback_down: number
  pending_reviews: number
  kb_count: number
  doc_count: number
}

/** GET /admin/audit 的既有 results 元素字段。 */
export interface AuditLogRecord {
  id: string
  tenant_id: string | null
  actor_id: string | null
  action: string
  target_type: string | null
  target_id: string | null
  result: string
  trace_id: string | null
  metadata: Record<string, unknown> | null
  ip_hash: string | null
  user_agent_hash: string | null
  created_at: string
}

export interface AuditLogListResponse {
  results: AuditLogRecord[]
  next_cursor: string | null
  trace_id: string | null
}

/** GET /admin/audit 的既有查询参数。 */
export interface AuditLogQuery {
  tenant_id?: string
  actor_id?: string
  action?: string
  result?: string
  trace_id?: string
  created_after?: string
  created_before?: string
  page_size?: number
  cursor?: string
}

export interface ReviewItemRecord {
  id: string
  conversation_id: string | null
  message_id: string | null
  question_preview: string
  answer_preview: string
  confidence: string
  evidence_summary: Record<string, unknown>[]
  status: string
  resolution: string | null
  resolved_by: string | null
  created_at: string
  updated_at: string
}

export interface ReviewItemListResponse {
  results: ReviewItemRecord[]
  next_cursor: string | null
}

export interface ReviewItemQuery {
  status?: string
  page_size?: number
  cursor?: string
}

/** PATCH /admin/reviews/{item_id} 的既有 ReviewItemUpdate 字段。 */
export interface ReviewItemUpdate {
  status: 'PENDING' | 'REVIEWED' | 'RESOLVED'
  resolution?: string
}

export interface SyncSourceRecord {
  id: string
  tenant_id: string
  kb_id: string
  name: string
  source_type: string
  source_url: string
  cursor: string | null
  status: string
  last_sync_at: string | null
  last_sync_count: number
  error_message: string | null
  retry_count: number
  created_at: string
  updated_at: string
}

/** POST /admin/sync/sources 的既有 SyncSourceCreate 字段。 */
export interface SyncSourceCreate {
  kb_id: string
  name: string
  source_type?: 'WEBDAV' | 'TICKET' | 'API'
  source_url?: string
}

export interface SyncRunResponse {
  source_id: string
  status: string
  synced_count: number
  error_message: string | null
}

/** POST /admin/backup 的既有返回元数据。 */
export interface BackupResponse {
  backup_path: string
  backup_sha256: string
  backup_size_bytes: number
  elapsed_seconds: number
  tables: Record<string, unknown>[]
}

// ---- Phase 1: Profile / Preferences / Sessions / API Keys / Notifications ----

export interface MeProfileResponse {
  user: {
    id: string
    name: string
    email: string
    role: string
    avatar_url: string | null
    department: string | null
  }
  tenants: Array<{ id: string; name: string; role: string }>
  capabilities: string[]
  policy_version: number
  tenant_id: string
  role_id: string
  profile: {
    display_name: string | null
    department: string | null
    locale: string | null
    timezone: string | null
    avatar_url: string | null
  }
}

export interface PatchProfilePayload {
  display_name?: string
  department?: string
  locale?: string
  timezone?: string
  avatar_url?: string
}

export interface ProfileResponse {
  profile: {
    display_name: string | null
    department: string | null
    locale: string | null
    timezone: string | null
    avatar_url: string | null
  }
}

export interface ChangePasswordPayload {
  old_password: string
  new_password: string
}

export interface PreferencesResponse {
  preferences: Record<string, unknown>
}

export interface SessionItem {
  id: string
  ip_hash: string | null
  ua_hash: string | null
  status: string
  created_at: string
  last_used_at: string | null
}

export interface SessionsResponse {
  items: SessionItem[]
}

export interface SessionRevokeResponse {
  status: string
  revoked_count?: string
}

export interface ApiKeyItem {
  id: string
  prefix: string
  name: string
  created_at: string
  last_used_at: string | null
  status: string
}

export interface ApiKeysResponse {
  items: ApiKeyItem[]
}

/** POST /me/api-keys 只在创建时返回一次明文 secret，且不含 last_used_at。 */
export interface ApiKeyCreateResponse {
  id: string
  prefix: string
  name: string
  secret: string
  created_at: string
  status: string
  last_used_at?: string | null
}

export interface NotificationItem {
  id: string
  type: string
  title: string
  body: string | null
  metadata: Record<string, unknown> | null
  priority: string
  read_at: string | null
  created_at: string
}

export interface NotificationsResponse {
  items: NotificationItem[]
}

// ---- Phase 2: Recycle bin (trash_items projection) ----

export type TrashResourceType = 'KB' | 'DOCUMENT' | 'CONVERSATION'

export interface TrashItemRecord {
  id: string
  resource_type: string
  resource_id: string
  title: string
  parent_id: string | null
  parent_title: string | null
  deleted_by: string | null
  deleted_at: string
  expires_at: string
  restorable: boolean
  blocked_reason: string | null
}

export interface TrashListQuery {
  resource_type?: string
  q?: string
  limit?: number
  offset?: number
}

export interface TrashListResponse {
  items: TrashItemRecord[]
  total: number
  counts: Record<string, number>
  resource_types: string[]
  limit: number
  offset: number
}

export interface TrashRestoreResponse {
  status: string
  item: TrashItemRecord
}

export interface TrashPurgeResponse {
  status: string
  id: string
  resource_type: string
  resource_id: string
  removed: Record<string, number>
}

export interface TrashClearResponse {
  status: string
  purged: number
}

// ---- Phase 6 P2: Favorites (favorites projection · v3_007_content_governance_compat) ----

export type FavoritesResourceType = 'KB' | 'DOCUMENT' | 'CONVERSATION'

export interface FavoriteItemRecord {
  id: string
  resource_type: string
  resource_id: string
  title: string
  parent_id: string | null
  parent_title: string | null
  favorited_at: string
}

export interface FavoritesListQuery {
  resource_type?: string
  limit?: number
  offset?: number
}

export interface FavoritesListResponse {
  items: FavoriteItemRecord[]
  total: number
  counts: Record<string, number>
  resource_types: FavoritesResourceType[]
  limit: number
  offset: number
}

export interface FavoriteCheckResponse {
  resource_type: FavoritesResourceType | string
  resource_id: string
  favorited: boolean
  favorited_at: string | null
}

export interface FavoriteToggleResponse {
  resource_type: FavoritesResourceType | string
  resource_id: string
  favorited: boolean
  favorited_at: string | null
}

// ---- Phase 3: Knowledge analytics (resource_access_events projection) ----

export interface AnalyticsOverviewResponse {
  days: number
  since: string
  accesses: number
  visitors: number
  qa_volume: number
  kb_count: number
  doc_count: number
  member_count: number
  accesses_today: number
  qa_today: number
}

export interface AnalyticsTrendPointRecord {
  day: string
  accesses: number
  visitors: number
}

export interface AnalyticsTrendResponse {
  days: number
  points: AnalyticsTrendPointRecord[]
}

export interface AnalyticsDistributionItemRecord {
  kb_id: string
  name: string
  documents: number
  accesses: number
}

export interface AnalyticsDistributionResponse {
  days: number
  items: AnalyticsDistributionItemRecord[]
}

export interface AnalyticsActivityItemRecord {
  id: string
  resource_type: string
  resource_id: string
  title: string | null
  access_kind: string
  actor_id: string | null
  actor_name: string | null
  occurred_at: string
}

export interface AnalyticsActivityResponse {
  items: AnalyticsActivityItemRecord[]
  limit: number
}

// ---- Phase 4: Apps center (installed_apps registry) ----

export type AppCategory = 'DOCUMENT_SYNC' | 'MESSAGING' | 'ANALYTICS' | 'GOVERNANCE' | 'UNKNOWN'

export type AppStatus = 'AVAILABLE' | 'INSTALLED' | 'UNINSTALLED' | 'ERROR'

export interface AppCatalogRecord {
  id: string
  slug: string
  name: string
  category: AppCategory
  description: string
  icon_slug: string
  status: AppStatus
  installed_at: string | null
  uninstalled_at: string | null
  error_message: string | null
}

export interface AppsCatalogResponse {
  items: AppCatalogRecord[]
  total: number
}

export interface AppsInstalledResponse {
  items: AppCatalogRecord[]
  count: number
}

export interface AppInstallResponse {
  success: boolean
  app_id: string
  slug: string
  name: string
  status: AppStatus
  error: string | null
}

export interface AppUninstallResponse {
  success: boolean
  app_id: string
  slug: string
  previous_status: string
  error: string | null
}

// ========== Apps Phase 4 additive types (capabilities, credentials, runs) ==========

/** 安装生命周期：INSTALLED → CONFIGURED → CONNECTED → UNINSTALLED */
export type AppInstallationStatus =
  | 'INSTALLED'
  | 'CONFIGURED'
  | 'CONNECTED'
  | 'UNINSTALLED'

export interface AppInstallationView {
  id: string
  status: AppInstallationStatus | string
  config: Readonly<Record<string, unknown>>
  installed_by: string | null
  installed_at: string | null
  updated_at: string | null
  uninstalled_at: string | null
}

export interface AppCredentialItem {
  id: string
  name: string
  /** 明文前 8 字符；短 secret 等于整个 secret。 */
  prefix: string
  /** 仅在 detail 端点返回；list 端点移除。 */
  key_version?: string
  status: 'ACTIVE' | 'REVOKED' | string
  created_at: string | null
  updated_at: string | null
  revoked_at: string | null
}

export interface AppRunItem {
  id: string
  run_type: string
  status: 'SUCCEEDED' | 'FAILED' | string
  sync_source_id: string | null
  result_redacted: Readonly<Record<string, unknown>>
  started_at: string | null
  completed_at: string | null
  error_code: string | null
  error_message: string | null
}

export interface AppDetailResponse {
  slug: string
  display_name: string
  provider_name: string
  description: string
  category: AppCategory
  capabilities: string[]
  recommended_rank: number
  installation: AppInstallationView | null
  credentials: AppCredentialItem[]
  runs: AppRunItem[]
}

export interface AppConfigureResponse {
  id: string
  slug: string
  status: AppInstallationStatus | string
  config: Readonly<Record<string, unknown>>
  updated_at: string
}

export interface AppConnectResponse {
  id: string
  slug: string
  status: AppInstallationStatus | string
  connected: boolean
  error?: string
}

/** 仅创建响应包含 plaintext_once；列表/详情一律不含明文。 */
export interface AppCredentialCreateResponse {
  success: boolean
  credential_id: string
  name: string
  prefix: string
  status: 'ACTIVE' | 'REVOKED' | string
  key_version: string
  plaintext_once: string | null
}

export interface AppCredentialListResponse {
  slug: string
  count: number
  items: AppCredentialItem[]
}

export interface AppCredentialRevokeResponse {
  success: boolean
  id: string
  name: string
  prefix: string
  status: 'ACTIVE' | 'REVOKED' | string
  revoked_at: string
}

export interface AppRunRecordResponse {
  success: boolean
  run_id: string
}

// ---- Phase: LLM Providers / Models ----

export interface LLMProviderItem {
  id: string
  preset_provider_id: string | null
  provider_key: string
  name: string
  logo: string | null
  description: string | null
  websites: Record<string, string>
  default_chat_endpoint: string | null
  endpoint_configs: Record<string, any>
  auth_type: string
  api_key_label: string | null
  has_api_key: boolean
  api_features: Record<string, boolean>
  settings: Record<string, unknown>
  model_list_source: string
  is_enabled: boolean
  is_preset_builtin: boolean
  created_at: string
  updated_at: string
}

export interface LLMProvidersResponse {
  items: LLMProviderItem[]
}

export interface PresetProviderItem {
  id: string
  name: string
  description: string | null
  logo: string | null
  default_chat_endpoint: string | null
  auth_type: string
  auth_optional: boolean
  websites: Record<string, string | undefined>
}

export interface PresetProvidersResponse {
  items: PresetProviderItem[]
}

export interface LLMModelItem {
  id: string
  provider_id: string
  model_id: string
  display_name: string
  model_type: string
  context_window: number | null
  max_output_tokens: number | null
  endpoint_type: string | null
  capabilities: Record<string, boolean>
  input_price: string | null
  output_price: string | null
  is_enabled: boolean
  is_custom: boolean
  notes: string | null
  created_at: string
  updated_at: string
}

export interface LLMModelsResponse {
  items: LLMModelItem[]
}

export interface CreateLLMProviderPayload {
  preset_provider_id?: string
  provider_key: string
  name: string
  description?: string
  default_chat_endpoint?: string
  endpoint_configs?: Record<string, unknown>
  auth_type?: string
  api_key?: string
  api_key_label?: string
  websites?: Record<string, string>
  is_enabled?: boolean
}

export interface UpdateLLMProviderPayload {
  name?: string
  description?: string
  default_chat_endpoint?: string
  endpoint_configs?: Record<string, unknown>
  api_key?: string
  api_key_label?: string
  websites?: Record<string, string>
  settings?: Record<string, unknown>
  is_enabled?: boolean
}

export interface CreateLLMModelPayload {
  model_id: string
  display_name: string
  model_type?: string
  context_window?: number
  max_output_tokens?: number
  endpoint_type?: string
  capabilities?: Record<string, boolean>
  input_price?: string
  output_price?: string
  is_enabled?: boolean
  notes?: string
}

export interface UpdateLLMModelPayload {
  display_name?: string
  model_type?: string
  context_window?: number | null
  max_output_tokens?: number | null
  endpoint_type?: string
  capabilities?: Record<string, boolean>
  input_price?: string | null
  output_price?: string | null
  is_enabled?: boolean
  notes?: string
}

export interface LLMSyncResponse {
  status: string
  provider_id: string
  provider_key: string
  models_found: number
  models_created: number
  models_updated: number
  models: LLMModelItem[]
  source: 'remote'
}
