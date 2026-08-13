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

export interface AttachmentsServices {
  readonly upload: (file: File, conversationId?: string) => Promise<AttachmentUploadResult>
}
