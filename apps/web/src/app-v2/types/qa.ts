import type { AdapterError } from './adapters'
import type { PageState } from './view-state'

export interface CitationView {
  readonly citationId: string
  readonly documentId: string
  readonly chunkId: string
  readonly title: string
  readonly sectionPath: readonly string[]
  readonly version?: number
  readonly updatedAt?: string
}

export interface QaRequestEvent {
  readonly kind: 'request'
  readonly turnId: string
  readonly requestId: string
  readonly sequence?: number
  readonly messageId: string
  readonly conversationId: string
}

export interface QaPhaseEvent {
  readonly kind: 'phase'
  readonly phase: 'retrieval_started' | 'retrieval_completed' | 'generation_started'
  readonly turnId: string
  readonly sequence?: number
  readonly chunkCount?: number
  readonly evidenceCount?: number
  readonly elapsedMs?: number
  readonly provider?: string
  readonly model?: string
}

export interface QaContentDeltaEvent {
  readonly kind: 'content_delta'
  readonly turnId: string
  readonly sequence?: number
  readonly text: string
  readonly delta: string
}

export interface QaCitationsEvent {
  readonly kind: 'citations'
  readonly turnId: string
  readonly sequence?: number
  readonly items: readonly CitationView[]
}

export type QaFinishReason = 'stop' | 'refusal' | 'cancelled' | 'timeout' | 'error'

export interface QaDoneEvent {
  readonly kind: 'done'
  readonly turnId: string
  readonly sequence?: number
  readonly messageId?: string
  readonly finishReason: QaFinishReason
  readonly confidence?: 'low' | 'medium' | 'high'
  readonly lastSequence: number
  readonly citationsCount?: number
}

export interface QaErrorEvent {
  readonly kind: 'error'
  readonly turnId: string
  readonly sequence?: number
  readonly code: string
  readonly message: string
  readonly requestId?: string
}

export type QaProtocolErrorCode = 'stale_turn' | 'seq_mismatch' | 'bad_envelope' | 'json_decode'

export interface QaProtocolErrorEvent {
  readonly kind: 'protocol_error'
  readonly turnId: string
  readonly sequence?: number
  readonly code: QaProtocolErrorCode
}

export type QaStreamEvent =
  | QaRequestEvent
  | QaPhaseEvent
  | QaContentDeltaEvent
  | QaCitationsEvent
  | QaDoneEvent
  | QaErrorEvent
  | QaProtocolErrorEvent

export interface QaStreamHandle {
  readonly turnId: string
  readonly messageId: string
  readonly conversationId: string
  readonly abort: () => void
}

export type ThinkingLevel =
  | 'off'
  | 'light'
  | 'mild'
  | 'standard'
  | 'medium'
  | 'high'
  | 'intensive'
  | 'extreme'

export interface ComposerAskOptions {
  /** 深度思考（老布尔字段，已由 thinkingLevel 主导）：保留向后兼容 */
  readonly deepThinking?: boolean
  /** 思考程度：off 关闭（快速回答）/ standard 标准推理（默认，推荐）/ intensive 深度推理（慢，更严谨） */
  readonly thinkingLevel?: ThinkingLevel
  /** 用户选择的模型 id（格式如 provider/model；传 undefined 用默认） */
  readonly model?: string
  /** 「添加文件」传入的 doc_id 列表，合并到检索范围 */
  readonly attachmentDocIds?: readonly string[]
  /** 最大引用数；不传用默认 5 */
  readonly maxCitations?: number
  /** 流版本；不传用默认 2 */
  readonly streamVersion?: 1 | 2
}

export interface QaStreamInput {
  readonly question: string
  readonly knowledgeBaseId: string
  readonly conversationId?: string
  /** Phase 2：Composer 功能按钮传入的 AskOptions */
  readonly options?: ComposerAskOptions
}

export interface ComposerModelInfo {
  readonly id: string
  readonly name: string
  readonly provider: string
  readonly description?: string
  readonly supportsDeepThinking: boolean
}

export interface ComposerCapabilitiesInfo {
  readonly attachmentsEnabled: boolean
  readonly deepThinkingEnabled: boolean
  readonly modelChoiceEnabled: boolean
}

export interface ComposerCapabilitiesView {
  readonly capabilities: ComposerCapabilitiesInfo
  readonly models: readonly ComposerModelInfo[]
  readonly defaults: Required<
    Pick<ComposerAskOptions, 'deepThinking' | 'thinkingLevel' | 'maxCitations' | 'streamVersion'>
  > & {
    readonly model?: string
    readonly attachmentDocIds: readonly string[]
  }
}

export interface ComposerCapabilitiesResult {
  readonly state: PageState
  readonly data?: ComposerCapabilitiesView
  readonly error?: AdapterError
}

export type QaStreamEventHandler = (event: QaStreamEvent) => void

export interface QaStreamStartResult {
  readonly state: PageState
  readonly data?: QaStreamHandle
  readonly error?: AdapterError
}

export interface QaCancelView {
  readonly turnId: string
  readonly status: 'cancelled' | 'already_completed' | 'not_found'
  readonly accepted: boolean
  readonly message?: string
}

export interface QaCancelResult {
  readonly state: PageState
  readonly data?: QaCancelView
  readonly error?: AdapterError
}

export interface QaStreamServices {
  readonly ask: (input: QaStreamInput, onEvent: QaStreamEventHandler) => Promise<QaStreamStartResult>
  readonly cancel: (turnId: string) => Promise<QaCancelResult>
  readonly getCapabilities: () => Promise<ComposerCapabilitiesResult>
}
