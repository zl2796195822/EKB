import { describe, expect, it } from 'vitest'
import type { ConversationsApiClient } from '../adapters/conversations'
import { createConversationsAdapter } from '../adapters/conversations'

function createFakeClient(renameConversation: ConversationsApiClient['renameConversation']): ConversationsApiClient {
  return {
    async listConversations() {
      return []
    },
    async getConversationMessages() {
      return []
    },
    async deleteConversation() {
      return undefined
    },
    renameConversation,
  }
}

describe('conversation rename contract', () => {
  it('returns ready and forwards the conversation id and title', async () => {
    let received: { id: string; title: string } | undefined
    const client = createFakeClient(async (id, title) => {
      received = { id, title }
      return { conversation_id: 'c1', title, title_locked: true }
    })

    const result = await createConversationsAdapter(client).rename('c1', '新标题')

    expect(result.state).toBe('ready')
    expect(received).toEqual({ id: 'c1', title: '新标题' })
  })

  it('maps rename failures without leaking the original error', async () => {
    const client = createFakeClient(async () => {
      throw new Error('secret-not-exposed')
    })

    const result = await createConversationsAdapter(client).rename('c1', '新标题')

    expect(result.state).toBe('error')
    expect(result.error).toMatchObject({
      code: 'CLIENT_ERROR',
      message: '会话重命名失败',
    })
    expect(result.error?.message).not.toContain('secret-not-exposed')
  })
})
