import type { AdapterError } from './adapters'
import type { PageState } from './view-state'

export interface ConversationView {
  readonly id: string
  readonly title: string
  readonly createdAt: string
  readonly updatedAt: string
  readonly archivedAt: string | null
}

export type ConversationMessageRole = 'user' | 'assistant' | 'system'

export interface ConversationMessageView {
  readonly id: string
  readonly role: ConversationMessageRole
  readonly content: string
  readonly createdAt: string
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

export interface ConversationMutationResult {
  readonly state: PageState
  readonly error?: AdapterError
}

export interface ConversationsServices {
  readonly list: () => Promise<ConversationListResult>
  readonly messages: (conversationId: string) => Promise<ConversationMessagesResult>
  readonly remove: (conversationId: string) => Promise<ConversationMutationResult>
  readonly rename: (conversationId: string, title: string) => Promise<ConversationMutationResult>;
}
