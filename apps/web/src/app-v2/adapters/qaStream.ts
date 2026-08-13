import type { AskStreamComposerOptions, QaCapabilitiesResponse } from '../../lib/api'
import type { AskStreamHandle, AskStreamHandlers } from '../../lib/api'
import type { AdapterCapability, AdapterError, PageState } from '../types'
import type {
  CitationView,
  ComposerAskOptions,
  ComposerCapabilitiesInfo,
  ComposerCapabilitiesResult,
  ComposerCapabilitiesView,
  ComposerModelInfo,
  QaCancelResult,
  QaCancelView,
  QaStreamEvent,
  QaStreamEventHandler,
  QaStreamHandle,
  QaStreamServices,
  QaStreamStartResult,
  ThinkingLevel,
} from '../types'
import { stateForError, toAdapterError } from './index'

export interface QaStreamApiClient {
  askStream: (
    question: string,
    kbId: string,
    handlers: AskStreamHandlers,
    signal?: AbortSignal,
    conversationId?: string,
    composerOptions?: AskStreamComposerOptions,
  ) => Promise<AskStreamHandle>
  cancelTurn: (turnId: string) => Promise<{
    turn_id: string
    status: 'cancelled' | 'already_completed' | 'not_found'
    accepted: boolean
    message?: string
  }>
  fetchQaCapabilities: () => Promise<QaCapabilitiesResponse>
}

function errorResult<T>(error: unknown, fallbackMessage: string): { state: PageState; error: AdapterError } {
  const mapped = toAdapterError(error, fallbackMessage)
  return { state: stateForError(mapped), error: mapped }
}

function mapCitation(input: {
  citation_id: string
  doc_id: string
  chunk_id: string
  title: string
  section_path: string[]
  version?: number
  updated_at?: string
}): CitationView {
  return {
    citationId: input.citation_id,
    documentId: input.doc_id,
    chunkId: input.chunk_id,
    title: input.title,
    sectionPath: input.section_path,
    version: input.version,
    updatedAt: input.updated_at,
  }
}

function createHandle(handle: AskStreamHandle, readTurnId: () => string, readMessageId: () => string, readConversationId: () => string): QaStreamHandle {
  return {
    get turnId() {
      return readTurnId() || handle.turnId
    },
    get messageId() {
      return readMessageId() || handle.messageId
    },
    get conversationId() {
      return readConversationId() || handle.conversationId
    },
    abort: handle.abort,
  }
}

export function createQaStreamAdapter(client: QaStreamApiClient): QaStreamServices {
  const ask = async (
    input: { readonly question: string; readonly knowledgeBaseId: string; readonly conversationId?: string; readonly options?: ComposerAskOptions },
    onEvent: QaStreamEventHandler,
  ): Promise<QaStreamStartResult> => {
    let turnId = ''
    let messageId = ''
    let conversationId = input.conversationId ?? ''
    const emit = (event: QaStreamEvent) => onEvent(event)
    const handlers: AskStreamHandlers = {
      onRequest: (payload, raw) => {
        turnId = payload.turn_id || raw.turn_id
        messageId = payload.message_id
        conversationId = payload.conversation_id
        emit({
          kind: 'request',
          turnId,
          requestId: raw.request_id,
          sequence: raw.seq,
          messageId,
          conversationId,
        })
      },
      onPhase: (phase, payload) => {
        emit({
          kind: 'phase',
          phase,
          turnId,
          chunkCount: payload.chunk_count,
          evidenceCount: payload.evidence_count,
          elapsedMs: payload.elapsed_ms,
          provider: payload.provider,
          model: payload.model,
        })
      },
      onContentDelta: (payload, seq) => {
        emit({ kind: 'content_delta', turnId, sequence: seq, text: payload.text, delta: payload.delta })
      },
      onCitations: (payload) => {
        emit({ kind: 'citations', turnId, items: payload.items.map(mapCitation) })
      },
      onDone: (payload) => {
        emit({
          kind: 'done',
          turnId,
          sequence: payload.last_seq,
          messageId: payload.message_id,
          finishReason: payload.finish_reason,
          confidence: payload.confidence,
          lastSequence: payload.last_seq,
          citationsCount: payload.citations_count,
        })
      },
      onError: (payload) => {
        emit({ kind: 'error', turnId, code: payload.code, message: payload.message, requestId: payload.request_id })
      },
      onProtocolError: (code) => {
        emit({ kind: 'protocol_error', turnId, code })
      },
    }

    // 把前端 QaStreamInput.options 映射到 lib/api.ts 的 camelCase AskStreamComposerOptions
    const opts = input.options
    const tlRaw = (opts?.thinkingLevel ?? 'standard').toLowerCase() as ThinkingLevel
    const thinkingLevel = tlRaw === 'off' || tlRaw === 'standard' || tlRaw === 'intensive' ? tlRaw : 'standard'
    const composerOptions: AskStreamComposerOptions | undefined = opts
      ? {
          deepThinking: thinkingLevel !== 'off' || Boolean(opts.deepThinking),
          thinkingLevel,
          model: opts.model,
          attachmentDocIds: opts.attachmentDocIds ? Array.from(opts.attachmentDocIds) : undefined,
          maxCitations: opts.maxCitations,
          streamVersion: opts.streamVersion,
        }
      : undefined

    try {
      const handle = await client.askStream(input.question, input.knowledgeBaseId, handlers, undefined, input.conversationId, composerOptions)
      return {
        state: 'ready',
        data: createHandle(handle, () => turnId, () => messageId, () => conversationId),
      }
    } catch (error) {
      return errorResult(error, '问答流启动失败')
    }
  }

  const cancel = async (turnId: string): Promise<QaCancelResult> => {
    if (!turnId.trim()) {
      return {
        state: 'error',
        error: { code: 'TURN_ID_REQUIRED', message: '服务端尚未分配 turn_id，无法请求服务端取消。' },
      }
    }
    try {
      const result = await client.cancelTurn(turnId)
      const data: QaCancelView = {
        turnId: result.turn_id,
        status: result.status,
        accepted: result.accepted,
        message: result.message,
      }
      return { state: 'ready', data }
    } catch (error) {
      return errorResult(error, '问答取消请求失败')
    }
  }

  const getCapabilities = async (): Promise<ComposerCapabilitiesResult> => {
    try {
      const resp = await client.fetchQaCapabilities()
      const capabilities: ComposerCapabilitiesInfo = {
        attachmentsEnabled: Boolean(resp.capabilities?.attachments_enabled),
        deepThinkingEnabled: Boolean(resp.capabilities?.deep_thinking_enabled),
        modelChoiceEnabled: Boolean(resp.capabilities?.model_choice_enabled),
      }
      const models: readonly ComposerModelInfo[] = (resp.models ?? []).map((m) => ({
        id: m.id,
        name: m.name,
        provider: m.provider,
        description: m.description ?? undefined,
        supportsDeepThinking: Boolean(m.supports_deep_thinking),
      }))
      const d = resp.defaults ?? {}
      const tlRaw = (d.thinking_level ?? 'standard') as unknown
      const thinkingLevel =
        tlRaw === 'off' || tlRaw === 'standard' || tlRaw === 'intensive' ? tlRaw : 'standard'
      const defaults: ComposerCapabilitiesView['defaults'] = {
        deepThinking: thinkingLevel !== 'off' || Boolean(d.deep_thinking),
        thinkingLevel,
        maxCitations: Number.isFinite(d.max_citations) ? d.max_citations : 5,
        streamVersion: (d.stream_version === 1 ? 1 : 2) as 1 | 2,
        model: d.model ?? undefined,
        attachmentDocIds: Array.isArray(d.attachment_doc_ids) ? d.attachment_doc_ids : [],
      }
      const data: ComposerCapabilitiesView = { capabilities, models, defaults }
      return { state: 'ready', data }
    } catch (error) {
      return errorResult(error, '获取 Composer 功能按钮能力失败')
    }
  }

  return { ask, cancel, getCapabilities }
}

export const QA_STREAM_CAPABILITIES = [
  {
    id: 'qa.sse-v2-turn-seq-heartbeat-cancel',
    status: 'available',
    reason: 'ApiClient 负责 turn_id/seq/stale/重复事件校验；适配器映射阶段、心跳存活、引用、终态和幂等 cancel。',
  },
  {
    id: 'qa.feedback',
    status: 'available',
  },
  {
    id: 'qa.deep-thinking-model-choice',
    status: 'available',
    reason: '通过 GET /qa/capabilities 获取远程模型与思考能力；检索范围仅来自授权知识库和附件。',
  },
] as const satisfies readonly AdapterCapability[]
