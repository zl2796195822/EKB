import type { AdapterError } from './adapters'
import type { PageState } from './view-state'

export type FeedbackRating = 'UP' | 'DOWN'

export interface FeedbackInput {
  readonly rating: FeedbackRating
  readonly reason: string
  readonly comment?: string
}

export interface FeedbackResult {
  readonly state: PageState
  readonly error?: AdapterError
}

export interface FeedbackServices {
  readonly submit: (messageId: string, input: FeedbackInput) => Promise<FeedbackResult>
}
