import { ApiClientError } from '../../lib/api'
import type { AdapterError, PageState } from '../types'

export function toAdapterError(error: unknown, fallbackMessage: string): AdapterError {
  if (error instanceof ApiClientError) {
    return {
      code: error.code,
      message: error.message,
      status: error.status,
      requestId: error.requestId,
      details: error.details,
      upload: error.upload ? { ...error.upload } : undefined,
    }
  }

  return {
    code: 'CLIENT_ERROR',
    message: error instanceof Error && error.message ? error.message : fallbackMessage,
  }
}

export function stateForError(error: AdapterError): PageState {
  return error.status === 401 || error.status === 403 ? 'permission-denied' : 'error'
}

export function stateForData<T>(data: readonly T[]): PageState {
  return data.length > 0 ? 'ready' : 'empty'
}

export {
  ADMIN_CAPABILITIES,
  OPS_DASHBOARD_MAX_DAYS,
  OPS_DASHBOARD_MIN_DAYS,
  clampOpsDays,
  createAdminAdapter,
} from './admin'
export type { AdminAdapter, AdminApiClient, OpsDashboard } from './admin'
export { AUTH_CAPABILITIES } from './auth'
export type { AuthAdapter, AuthLoginInput, AuthSession } from './auth'
export { CONVERSATION_CAPABILITIES } from './conversations'
export { createConversationsAdapter } from './conversations'
export type { ConversationsApiClient } from './conversations'
export { DOCUMENT_CAPABILITIES } from './documents'
export { createDocumentsAdapter } from './documents'
export type { DocumentRef, DocumentsAdapter } from './documents'
export { KNOWLEDGE_CAPABILITIES } from './knowledge'
export { createKnowledgeAdapter } from './knowledge'
export type { KnowledgeAdapter, KnowledgeSpace } from './knowledge'
export { QA_STREAM_CAPABILITIES } from './qaStream'
export { createQaStreamAdapter } from './qaStream'
export type { QaStreamApiClient } from './qaStream'
export { FEEDBACK_CAPABILITIES } from './feedback'
export { createFeedbackAdapter } from './feedback'
export type { FeedbackApiClient } from './feedback'
export { SEARCH_CAPABILITIES } from './search'
export { createSearchAdapter } from './search'
export type { SearchAdapter, SearchHit } from './search'
export { IDENTITY_CAPABILITIES, createIdentityAdapter } from './v3Identity'
export type { IdentityAdapter, IdentityApiClient } from './v3Identity'
export { PROFILE_CAPABILITIES, createProfileAdapter } from './profile'
export type { ProfileAdapter, ProfileApiClient } from './profile'
export { TRASH_CAPABILITIES, TRASH_LIST_PAGE_SIZE, createTrashAdapter } from './trash'
export type { TrashAdapter, TrashApiClient } from './trash'
export {
  FAVORITES_CAPABILITIES,
  FAVORITES_LIST_PAGE_SIZE,
  createFavoritesAdapter,
} from './favorites'
export type { FavoritesAdapter, FavoritesApiClient } from './favorites'
export {
  ANALYTICS_ACTIVITY_LIMIT,
  ANALYTICS_CAPABILITIES,
  ANALYTICS_DEFAULT_DAYS,
  ANALYTICS_TREND_DAYS,
  createAnalyticsAdapter,
} from './analytics'
export type { AnalyticsAdapter, AnalyticsApiClient } from './analytics'
export { APPS_CAPABILITIES, APPS_DEFAULT_LIMIT, createAppsAdapter } from './apps'
export type { AppsAdapter, AppsApiClient } from './apps'
export { LLM_CAPABILITIES, createLLMAdapter } from './llm'
export type { LLMAdapter, LLMApiClient } from './llm'
export { ATTACHMENT_CAPABILITIES, createAttachmentsAdapter } from './attachments'
