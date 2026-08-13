import type { ApiClient } from '../../lib/api'
import type { AdapterCapability, AdapterError, FeedbackInput, FeedbackResult, FeedbackServices } from '../types'
import { stateForError, toAdapterError } from './index'

export interface FeedbackApiClient {
  sendFeedback: ApiClient['sendFeedback']
}

export function createFeedbackAdapter(client: FeedbackApiClient): FeedbackServices {
  return {
    submit: async (messageId, input): Promise<FeedbackResult> => {
      const reason = input.reason.trim()
      if (!messageId.trim() || !reason) {
        const error: AdapterError = {
          code: 'INVALID_FEEDBACK',
          message: '反馈需要真实 assistant messageId 和非空 reason。',
        }
        return { state: 'error', error }
      }

      try {
        await client.sendFeedback(messageId, {
          rating: input.rating,
          reason,
          ...(input.comment?.trim() ? { comment: input.comment.trim() } : {}),
        })
        return { state: 'ready' }
      } catch (error) {
        const mapped = toAdapterError(error, '反馈提交失败')
        return { state: stateForError(mapped), error: mapped }
      }
    },
  }
}

export const FEEDBACK_CAPABILITIES = [
  {
    id: 'feedback.up-down-reason',
    status: 'available',
    reason: '只有真实 assistant messageId 存在时提交 UP/DOWN，reason 按服务端约束非空。',
  },
] as const satisfies readonly AdapterCapability[]
