import { ApiClientError } from '../../lib/api'
import type { ApiClient } from '../../lib/api'
import type { AdapterCapability, AdapterError } from '../types'
import type {
  ConversationBranchView,
  ConversationBranchesResult,
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
  listConversationBranches: ApiClient['listConversationBranches']
  setActiveConversationBranch: ApiClient['setActiveConversationBranch']
  deleteConversation: ApiClient['deleteConversation']
  renameConversation: ApiClient['renameConversation']
}

function errorResult(error: unknown, fallbackMessage: string): { state: 'error' | 'permission-denied'; error: AdapterError } {
  const mapped = toAdapterError(error, fallbackMessage)
  return { state: stateForError(mapped) as 'error' | 'permission-denied', error: mapped }
}

function safeErrorResult(error: unknown, fallbackMessage: string): { state: 'error' | 'permission-denied'; error: AdapterError } {
  if (error instanceof ApiClientError) {
    return errorResult(error, fallbackMessage)
  }
  return {
    state: 'error',
    error: { code: 'CLIENT_ERROR', message: fallbackMessage },
  }
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

function mapBranch(input: Awaited<ReturnType<ApiClient['listConversationBranches']>>['branches'][number]): ConversationBranchView {
  return {
    id: input.id,
    conversationId: input.conversation_id,
    parentBranchId: input.parent_branch_id,
    forkMessageId: input.fork_message_id,
    label: input.label,
    createdBy: input.created_by,
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
    messages: async (conversationId, branchId): Promise<ConversationMessagesResult> => {
      try {
        const data = (await client.getConversationMessages(conversationId, branchId)).map(mapMessage)
        return { state: stateForData(data), data }
      } catch (error) {
        return safeErrorResult(error, '会话消息加载失败')
      }
    },
    listBranches: async (conversationId): Promise<ConversationBranchesResult> => {
      try {
        const result = await client.listConversationBranches(conversationId)
        const branches = result.branches.map(mapBranch)
        return {
          state: 'ready',
          data: {
            activeBranchId: result.active_branch_id,
            branches,
          },
        }
      } catch (error) {
        return safeErrorResult(error, '会话分支列表加载失败')
      }
    },
    setActiveBranch: async (conversationId, branchId): Promise<ConversationMutationResult> => {
      try {
        await client.setActiveConversationBranch(conversationId, branchId)
        return { state: 'ready' }
      } catch (error) {
        return safeErrorResult(error, '会话分支切换失败')
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
    rename: async (conversationId, title): Promise<ConversationMutationResult> => {
      try {
        await client.renameConversation(conversationId, title)
        return { state: 'ready' }
      } catch (error) {
        return safeErrorResult(error, '会话重命名失败')
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
    id: 'conversations.rename',
    status: 'available',
    reason: '会话重命名使用现有真实 PATCH 端点。',
  },
  {
    id: 'conversations.favorite-projects-export-share',
    status: 'disabled',
    reason: '当前项目仍禁用收藏、项目、导出或分享能力。',
  },
] as const satisfies readonly AdapterCapability[]
