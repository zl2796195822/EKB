import { describe, expect, it } from 'vitest'
import { createConversationsAdapter, type ConversationsApiClient } from '../adapters/conversations'

interface FakeClientOverrides {
  readonly getConversationMessages?: ConversationsApiClient['getConversationMessages']
  readonly listConversationBranches?: ConversationsApiClient['listConversationBranches']
  readonly setActiveConversationBranch?: ConversationsApiClient['setActiveConversationBranch']
}

interface FakeClientCalls {
  message?: { conversationId: string; branchId?: string }
  activeBranch?: { conversationId: string; branchId: string }
}

function createFakeClient(
  overrides: FakeClientOverrides = {},
  calls: FakeClientCalls = {},
): ConversationsApiClient {
  return {
    async listConversations() {
      return []
    },
    async getConversationMessages(conversationId: string, branchId?: string) {
      calls.message = { conversationId, branchId }
      if (overrides.getConversationMessages) {
        return overrides.getConversationMessages(conversationId, branchId)
      }
      return []
    },
    async deleteConversation() {
      return undefined
    },
    async renameConversation(conversationId: string, title: string) {
      return { conversation_id: conversationId, title, title_locked: true }
    },
    async listConversationBranches(conversationId: string) {
      if (overrides.listConversationBranches) {
        return overrides.listConversationBranches(conversationId)
      }
      return { conversation_id: conversationId, active_branch_id: null, branches: [] }
    },
    async setActiveConversationBranch(conversationId: string, branchId: string) {
      calls.activeBranch = { conversationId, branchId }
      if (overrides.setActiveConversationBranch) {
        return overrides.setActiveConversationBranch(conversationId, branchId)
      }
      return { conversation_id: conversationId, active_branch_id: branchId }
    },
  }
}

describe('conversation branches adapter', () => {
  it('returns ready and maps the active branch and snake_case branch fields', async () => {
    const client = createFakeClient({
      listConversationBranches: async (conversationId) => ({
        conversation_id: conversationId,
        active_branch_id: 'branch-2',
        branches: [
          {
            id: 'branch-2',
            conversation_id: conversationId,
            parent_branch_id: 'branch-1',
            fork_message_id: 'message-3',
            label: 'Follow-up',
            created_by: 'user-1',
            created_at: '2026-08-14T01:02:03Z',
          },
        ],
      }),
    })

    const result = await createConversationsAdapter(client).listBranches('c1')

    expect(result).toEqual({
      state: 'ready',
      data: {
        activeBranchId: 'branch-2',
        branches: [
          {
            id: 'branch-2',
            conversationId: 'c1',
            parentBranchId: 'branch-1',
            forkMessageId: 'message-3',
            label: 'Follow-up',
            createdBy: 'user-1',
            createdAt: '2026-08-14T01:02:03Z',
          },
        ],
      },
    })
  })

  it('forwards branchId to messages and maps an assistant message', async () => {
    const calls: FakeClientCalls = {}
    const client = createFakeClient(
      {
        getConversationMessages: async () => [
          {
            id: 'message-4',
            role: 'ASSISTANT',
            content: '分支回答',
            created_at: '2026-08-14T02:00:00Z',
          },
        ],
      },
      calls,
    )

    const result = await createConversationsAdapter(client).messages('c1', 'branch-2')

    expect(calls.message).toEqual({ conversationId: 'c1', branchId: 'branch-2' })
    expect(result).toEqual({
      state: 'ready',
      data: [
        {
          id: 'message-4',
          role: 'assistant',
          content: '分支回答',
          createdAt: '2026-08-14T02:00:00Z',
        },
      ],
    })
  })

  it('returns ready and forwards the conversation id and branch id when activating a branch', async () => {
    const calls: FakeClientCalls = {}
    const client = createFakeClient({}, calls)

    const result = await createConversationsAdapter(client).setActiveBranch('c1', 'branch-2')

    expect(result).toEqual({ state: 'ready' })
    expect(calls.activeBranch).toEqual({ conversationId: 'c1', branchId: 'branch-2' })
  })

  it('returns the fallback error message without leaking the original error', async () => {
    const client = createFakeClient({
      listConversationBranches: async () => {
        throw new Error('secret')
      },
    })

    const result = await createConversationsAdapter(client).listBranches('c1')

    expect(result.state).toBe('error')
    expect(result.error?.message).toBe('会话分支列表加载失败')
    expect(result.error?.message).not.toContain('secret')
  })
})
