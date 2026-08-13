import { createConversationsAdapter } from '../adapters/conversations'
import { createFeedbackAdapter } from '../adapters/feedback'
import { createQaStreamAdapter, type QaStreamApiClient } from '../adapters/qaStream'
import type { AskStreamHandle, AskStreamHandlers, QaCapabilitiesResponse } from '../../lib/api'
import type { QaStreamEvent } from '../types'

function assert(condition: unknown, message: string): asserts condition {
  if (!condition) throw new Error(message)
}

function expectEqual<T>(actual: T, expected: T, message: string): void {
  assert(actual === expected, `${message}: expected ${String(expected)}, received ${String(actual)}`)
}

interface ControlledStream {
  readonly handlers: AskStreamHandlers
  readonly handle: AskStreamHandle
}

class FakeConversationClient {
  async listConversations() {
    return [{ id: 'conversation-1', title: '真实会话', created_at: '2026-08-09T00:00:00Z', updated_at: '2026-08-09T01:00:00Z', archived_at: null }]
  }

  async getConversationMessages() {
    return [{ id: 'message-1', role: 'ASSISTANT', content: '真实回答', created_at: '2026-08-09T01:00:00Z' }]
  }

  async deleteConversation(): Promise<void> {
    return undefined
  }
}

class FakeQaClient implements QaStreamApiClient {
  controlled: ControlledStream | null = null
  cancelCalls: string[] = []
  cancelResult: { turn_id: string; status: 'cancelled' | 'already_completed' | 'not_found'; accepted: boolean; message?: string } = {
    turn_id: 'turn-1',
    status: 'cancelled',
    accepted: true,
  }
  shouldFailStart = false
  private currentTurnId = ''
  private currentMessageId = ''
  private currentConversationId = ''

  async askStream(_question: string, _kbId: string, handlers: AskStreamHandlers): Promise<AskStreamHandle> {
    if (this.shouldFailStart) throw new Error('stream unavailable')
    const owner = this
    let aborted = false
    const handle: AskStreamHandle = {
      get turnId() { return owner.currentTurnId },
      get messageId() { return owner.currentMessageId },
      get conversationId() { return owner.currentConversationId },
      abort: () => { aborted = true },
    }
    this.controlled = { handlers, handle }
    void aborted
    return handle
  }

  emitRequest(): void {
    const handlers = this.controlled?.handlers
    if (!handlers) throw new Error('stream has not started')
    // The values intentionally become available only after request, matching ApiClient.askStream getters.
    this.currentTurnId = 'turn-1'
    this.currentMessageId = 'message-1'
    this.currentConversationId = 'conversation-1'
    handlers.onRequest?.(
      {
        turn_id: 'turn-1',
        request_id: 'request-1',
        message_id: 'message-1',
        conversation_id: 'conversation-1',
        stream_version: 2,
      },
      {
        turn_id: 'turn-1',
        request_id: 'request-1',
        seq: 1,
        timestamp: '2026-08-09T01:00:00Z',
        payload: { turn_id: 'turn-1', request_id: 'request-1', message_id: 'message-1', conversation_id: 'conversation-1', stream_version: 2 },
      },
    )
  }

  async cancelTurn(turnId: string) {
    this.cancelCalls.push(turnId)
    return this.cancelResult
  }

  async fetchQaCapabilities(): Promise<QaCapabilitiesResponse> {
    return {
      capabilities: {
        attachments_enabled: true,
        deep_thinking_enabled: true,
        model_choice_enabled: true,
      },
      models: [
        {
          id: 'test-provider/test-model',
          name: '测试模型',
          provider: 'test-provider',
          description: 'FakeQaClient 内置测试模型',
          supports_deep_thinking: true,
        },
      ],
      defaults: {
        stream: true,
        max_citations: 5,
        stream_version: 2,
        deep_thinking: false,
        thinking_level: 'standard',
        model: null,
        attachment_doc_ids: [],
      },
    }
  }
}

class FakeFeedbackClient {
  calls: Array<{ messageId: string; rating: string; reason: string; comment?: string }> = []
  shouldFail = false

  async sendFeedback(messageId: string, payload: { rating: 'UP' | 'DOWN'; reason: string; comment?: string }): Promise<void> {
    if (this.shouldFail) throw new Error('feedback unavailable')
    this.calls.push({ messageId, ...payload })
  }
}

export async function runM4ContractTests(): Promise<void> {
  const conversations = createConversationsAdapter(new FakeConversationClient())
  const conversationList = await conversations.list()
  expectEqual(conversationList.data?.[0]?.updatedAt, '2026-08-09T01:00:00Z', 'conversation adapter maps updatedAt')
  const messages = await conversations.messages('conversation-1')
  expectEqual(messages.data?.[0]?.role, 'assistant', 'conversation adapter normalizes assistant role')
  expectEqual((await conversations.remove('conversation-1')).state, 'ready', 'conversation delete uses real client')

  const client = new FakeQaClient()
  const events: QaStreamEvent[] = []
  const adapter = createQaStreamAdapter(client)
  const start = await adapter.ask({ question: '真实问题', knowledgeBaseId: 'kb-1' }, (event) => events.push(event))
  assert(start.data, 'qa adapter returns a stream handle')
  expectEqual(start.data.messageId, '', 'messageId is empty before request event')
  expectEqual(start.data.conversationId, '', 'conversationId is empty before request event')
  client.emitRequest()
  expectEqual(start.data.messageId, 'message-1', 'messageId remains a dynamic getter after request')
  expectEqual(start.data.conversationId, 'conversation-1', 'conversationId remains a dynamic getter after request')
  expectEqual(start.data.turnId, 'turn-1', 'turnId maps the request event')
  expectEqual(events[0]?.kind, 'request', 'request event is visible')
  expectEqual(events[0]?.sequence, 1, 'request uses the real envelope seq')

  client.controlled?.handlers.onPhase?.('retrieval_started', { phase: 'retrieval' })
  client.controlled?.handlers.onContentDelta?.({ text: '片段', delta: '片段' }, 4)
  client.controlled?.handlers.onCitations?.({ items: [{ citation_id: 'citation-1', doc_id: 'doc-1', chunk_id: 'chunk-1', title: '真实文档', section_path: ['章节'] }] })
  client.controlled?.handlers.onProtocolError?.('stale_turn', 'hidden detail')
  client.controlled?.handlers.onError?.({ code: 'RETRIEVAL_TIMEOUT', message: '检索阶段超时' })
  client.controlled?.handlers.onDone?.({ message_id: 'message-1', finish_reason: 'timeout', last_seq: 7 })

  const phase = events.find((event) => event.kind === 'phase')
  assert(phase?.sequence === undefined, 'phase must not fabricate a server seq')
  const delta = events.find((event) => event.kind === 'content_delta')
  expectEqual(delta?.sequence, 4, 'content_delta preserves its real seq')
  const citations = events.find((event) => event.kind === 'citations')
  assert(citations?.sequence === undefined, 'citations must not fabricate a server seq')
  const protocol = events.find((event) => event.kind === 'protocol_error')
  assert(protocol?.kind === 'protocol_error' && protocol.sequence === undefined, 'protocol errors are diagnostic only and have no fabricated seq')
  const done = events.find((event) => event.kind === 'done')
  expectEqual(done?.sequence, 7, 'done uses payload last_seq')
  expectEqual(done?.kind === 'done' ? done.finishReason : undefined, 'timeout', 'done maps finish reason')

  const cancel = await adapter.cancel('turn-1')
  expectEqual(cancel.data?.status, 'cancelled', 'cancel maps real server status')
  expectEqual(client.cancelCalls[0], 'turn-1', 'cancel forwards request event turnId')
  client.cancelResult = { turn_id: 'turn-1', status: 'already_completed', accepted: false }
  expectEqual((await adapter.cancel('turn-1')).data?.status, 'already_completed', 'cancel preserves already completed status')
  expectEqual((await adapter.cancel('')).error?.code, 'TURN_ID_REQUIRED', 'missing turnId does not call server cancel')

  client.shouldFailStart = true
  const failedStart = await adapter.ask({ question: '失败问题', knowledgeBaseId: 'kb-1' }, () => undefined)
  expectEqual(failedStart.state, 'error', 'stream startup error maps to adapter error')

  const feedbackClient = new FakeFeedbackClient()
  const feedback = createFeedbackAdapter(feedbackClient)
  expectEqual((await feedback.submit('message-1', { rating: 'UP', reason: '   ' })).state, 'error', 'feedback rejects empty reason')
  expectEqual(feedbackClient.calls.length, 0, 'empty reason never calls server')
  expectEqual((await feedback.submit('message-1', { rating: 'UP', reason: '有帮助' })).state, 'ready', 'feedback submits real payload')
  expectEqual(feedbackClient.calls[0]?.reason, '有帮助', 'feedback trims and forwards reason')
  feedbackClient.shouldFail = true
  expectEqual((await feedback.submit('message-1', { rating: 'DOWN', reason: '缺少依据' })).state, 'error', 'feedback failure is retryable')
}

void runM4ContractTests().then(() => console.log('M4 contract tests: PASS'))
