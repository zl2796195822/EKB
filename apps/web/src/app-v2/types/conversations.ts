import type { AdapterError } from './adapters'
import type { PageState } from './view-state'

export interface ConversationView {
  readonly id: string
  readonly title: string
  readonly createdAt: string
  readonly updatedAt: string
  readonly archivedAt: string | null
}

export interface ConversationBranchView {
  readonly id: string
  readonly conversationId: string
  readonly parentBranchId: string | null
  readonly forkMessageId: string | null
  readonly label: string | null
  readonly createdBy: string
  readonly createdAt: string
}

export type ConversationMessageRole = 'user' | 'assistant' | 'system'

export interface ConversationMessageView {
  readonly id: string
  readonly role: ConversationMessageRole
  readonly content: string
  readonly createdAt: string
  /** PH4-3 刷新恢复：assistant 消息关联的 turn_id（user/system 通常为空）。 */
  readonly turnId?: string
  /** 消息状态：pending/streaming/completed/failed 等，用于判断非终态。 */
  readonly status?: string
}

export interface ConversationListResult {
  readonly state: PageState
  readonly data?: readonly ConversationView[]
  readonly error?: AdapterError
}

export interface ConversationMessagesResult {
  readonly state: PageState
  readonly data?: readonly ConversationMessageView[]
  readonly error?: AdapterError
}

export interface ConversationBranchesResult {
  readonly state: PageState
  readonly data?: {
    readonly activeBranchId: string | null
    readonly branches: readonly ConversationBranchView[]
  }
  readonly error?: AdapterError
}

export interface ConversationMutationResult {
  readonly state: PageState
  readonly error?: AdapterError
}

export interface ConversationsServices {
  readonly list: () => Promise<ConversationListResult>
  readonly messages: (conversationId: string, branchId?: string) => Promise<ConversationMessagesResult>
  readonly listBranches: (conversationId: string) => Promise<ConversationBranchesResult>
  readonly setActiveBranch: (conversationId: string, branchId: string) => Promise<ConversationMutationResult>
  readonly remove: (conversationId: string) => Promise<ConversationMutationResult>
  readonly rename: (conversationId: string, title: string) => Promise<ConversationMutationResult>;
}
