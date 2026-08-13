import type { AdapterError } from './adapters'
import type { PageState } from './view-state'

export interface AttachmentUploadView {
  readonly id: string
  readonly title: string
  readonly status: string
  readonly mimeType: string
  readonly byteSize: number
}

export interface AttachmentUploadResult {
  readonly state: PageState
  readonly data?: AttachmentUploadView
  readonly error?: AdapterError
}

export interface AttachmentPromotionView {
  readonly promotionId: string
  readonly attachmentId: string
  readonly targetKnowledgeBaseId: string
  readonly normalizedRelativePath: string
  readonly clientRequestId: string
  readonly documentId: string
  readonly documentVersionId: string
  readonly ingestJobId: string
  readonly status: string
  readonly requestId: string
}

export interface AttachmentPromotionInput {
  readonly targetKnowledgeBaseId: string
  readonly relativePath: string
}

export interface AttachmentPromotionResult {
  readonly state: PageState
  readonly data?: AttachmentPromotionView
  readonly error?: AdapterError
}

export interface AttachmentsServices {
  readonly upload: (file: File, conversationId?: string) => Promise<AttachmentUploadResult>
  readonly promote: (
    attachmentId: string,
    input: AttachmentPromotionInput,
  ) => Promise<AttachmentPromotionResult>
}
