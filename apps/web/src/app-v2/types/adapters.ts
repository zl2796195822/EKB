import type { PageState } from './view-state'

export type AdapterCapabilityStatus = 'available' | 'unavailable' | 'disabled'

export interface AdapterCapability {
  readonly id: string
  readonly status: AdapterCapabilityStatus
  readonly reason?: string
}

export interface AdapterContext {
  readonly tenantId?: string
  readonly subjectId?: string
  readonly signal?: AbortSignal
}

export interface AdapterError {
  readonly code: string
  readonly message: string
  readonly status?: number
  readonly requestId?: string
  readonly details?: Readonly<Record<string, unknown>>
  readonly upload?: Readonly<Record<string, unknown>>
  readonly traceId?: string
}

export interface AdapterResult<TData> {
  readonly state: PageState
  readonly data?: TData
  readonly error?: AdapterError
}

export type AdapterOperation<TInput, TOutput> = (
  input: TInput,
  context: AdapterContext,
) => Promise<AdapterResult<TOutput>>
