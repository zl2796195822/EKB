import type { ApiClient } from '../../lib/api'
import type { AdapterCapability, AdapterError, AttachmentsServices, AttachmentUploadResult } from '../types'
import { stateForError, toAdapterError } from './index'

function errorResult(error: unknown, fallbackMessage: string): AttachmentUploadResult {
  const mapped: AdapterError = toAdapterError(error, fallbackMessage)
  return { state: stateForError(mapped), error: mapped }
}

async function sha256File(file: File): Promise<string> {
  const digest = await crypto.subtle.digest('SHA-256', await file.arrayBuffer())
  return Array.from(new Uint8Array(digest), (byte) => byte.toString(16).padStart(2, '0')).join('')
}

export function createAttachmentsAdapter(client: ApiClient): AttachmentsServices {
  return {
    upload: async (file, conversationId) => {
      try {
        const sha256 = await sha256File(file)
        const session = await client.createAttachmentSession({
          client_request_id: crypto.randomUUID(),
          detected_mime: file.type || 'application/octet-stream',
          byte_size: file.size,
          sha256,
          ...(conversationId ? { conversation_id: conversationId } : {}),
        })
        await client.putAttachmentObject(session.upload_url, file)
        const processed = await client.processAttachment(session.attachment.id)
        if (processed.status !== 'READY') {
          throw new Error(`附件处理失败：${processed.status}`)
        }
        return {
          state: 'ready',
          data: {
            id: session.attachment.id,
            title: file.name,
            status: processed.status,
            mimeType: session.attachment.detected_mime,
            byteSize: session.attachment.byte_size,
          },
        }
      } catch (error) {
        return errorResult(error, '附件上传或处理失败')
      }
    },
  }
}

export const ATTACHMENT_CAPABILITIES = [
  {
    id: 'attachments.upload-process-context',
    status: 'available',
    reason: '浏览器上传、远程处理和问答上下文使用真实附件端点。',
  },
] as const satisfies readonly AdapterCapability[]
