import type { ApiClient } from '../../lib/api'
import type { AdapterCapability, AdapterError } from '../types'
import type {
  ConversationListResult,
  ConversationMessageRole,
  ConversationMessagesResult,
  ConversationMutationResult,
  ConversationView,
  ConversationsServices,
} from '../types'
import { stateForData, stateForError, toAdapterError } from './index'

export interface ConversationsApiClient {
  listConversations: ApiClient['listConversations']
  getConversationMessages: ApiClient['getConversationMessages']
  deleteConversation: ApiClient['deleteConversation']
}

function errorResult(error: unknown, fallbackMessage: string): { state: 'error' | 'permission-denied'; error: AdapterError } {
  const mapped = toAdapterError(error, fallbackMessage)
  return { state: stateForError(mapped) as 'error' | 'permission-denied', error: mapped }
}

function mapConversation(input: Awaited<ReturnType<ApiClient['listConversations']>>[number]): ConversationView {
  return {
    id: input.id,
    title: input.title,
    createdAt: input.created_at,
    updatedAt: input.updated_at,
    archivedAt: input.archived_at,
  }
}

function mapRole(role: string): ConversationMessageRole {
  const normalized = role.toLowerCase()
  if (normalized === 'assistant') return 'assistant'
  if (normalized === 'system') return 'system'
  return 'user'
}

function mapMessage(input: Awaited<ReturnType<ApiClient['getConversationMessages']>>[number]) {
  return {
    id: input.id,
    role: mapRole(input.role),
    content: input.content,
    createdAt: input.created_at,
  }
}

export function createConversationsAdapter(client: ConversationsApiClient): ConversationsServices {
  return {
    list: async (): Promise<ConversationListResult> => {
      try {
        const data = (await client.listConversations()).map(mapConversation)
        return { state: stateForData(data), data }
      } catch (error) {
        return errorResult(error, '会话列表加载失败')
      }
    },
    messages: async (conversationId): Promise<ConversationMessagesResult> => {
      try {
        const data = (await client.getConversationMessages(conversationId)).map(mapMessage)
        return { state: stateForData(data), data }
      } catch (error) {
        return errorResult(error, '会话消息加载失败')
      }
    },
    remove: async (conversationId): Promise<ConversationMutationResult> => {
      try {
        await client.deleteConversation(conversationId)
        return { state: 'ready' }
      } catch (error) {
        return errorResult(error, '会话删除失败')
      }
    },
  }
}

export const CONVERSATION_CAPABILITIES = [
  {
    id: 'conversations.list-messages-delete',
    status: 'available',
    reason: '列表、消息读取和当前会话删除使用现有授权端点。',
  },
  {
    id: 'conversations.favorite-projects-rename-export-share',
    status: 'disabled',
    reason: '当前 API 未提供收藏、项目、重命名、导出或分享端点。',
  },
] as const satisfies readonly AdapterCapability[]
