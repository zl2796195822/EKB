import type { AdapterError } from './adapters'
import type { PageState } from './view-state'

export interface PresetProviderView {
  readonly id: string
  readonly name: string
  readonly description: string | null
  readonly logo: string | null
  readonly defaultChatEndpoint: string | null
  readonly authType: string
  readonly authOptional: boolean
  readonly websites: {
    readonly official?: string
    readonly docs?: string
    readonly apiKey?: string
    readonly models?: string
  }
}

export interface LLMProviderView {
  readonly id: string
  readonly presetProviderId: string | null
  readonly providerKey: string
  readonly name: string
  readonly logo: string | null
  readonly description: string | null
  readonly websites: Record<string, string>
  readonly defaultChatEndpoint: string | null
  readonly endpointConfigs: Record<string, {
    readonly baseUrl?: string
    readonly adapterFamily?: string
    readonly reasoningFormatType?: string
    readonly modelsApiUrls?: Record<string, string>
  }>
  readonly authType: 'api-key' | 'oauth' | 'iam-aws' | 'api-key-aws' | 'iam-gcp' | 'iam-azure'
  readonly apiKeyLabel: string | null
  readonly hasApiKey: boolean
  readonly apiFeatures: {
    readonly arrayContent: boolean
    readonly streamOptions: boolean
    readonly developerRole: boolean
    readonly serviceTier: boolean
    readonly verbosity: boolean
  }
  readonly settings: Record<string, unknown>
  readonly modelListSource: 'api' | 'registry'
  readonly isEnabled: boolean
  readonly isPresetBuiltin: boolean
  readonly createdAt: string
  readonly updatedAt: string
}

export interface LLMModelView {
  readonly id: string
  readonly providerId: string
  readonly modelId: string
  readonly displayName: string
  readonly modelType: 'chat' | 'embedding' | 'reranker' | 'image' | 'reasoning'
  readonly contextWindow: number | null
  readonly maxOutputTokens: number | null
  readonly endpointType: string | null
  readonly capabilities: {
    readonly vision?: boolean
    readonly toolUse?: boolean
    readonly functionCalling?: boolean
    readonly streaming?: boolean
    readonly reasoning?: boolean
  }
  readonly inputPrice: string | null
  readonly outputPrice: string | null
  readonly isEnabled: boolean
  readonly isCustom: boolean
  readonly notes: string | null
  readonly createdAt: string
  readonly updatedAt: string
}

export interface LLMProviderListResult {
  readonly state: PageState
  readonly data?: readonly LLMProviderView[]
  readonly error?: AdapterError
}

export interface LLMProviderResult {
  readonly state: PageState
  readonly data?: LLMProviderView
  readonly error?: AdapterError
}

export interface LLMModelListResult {
  readonly state: PageState
  readonly data?: readonly LLMModelView[]
  readonly error?: AdapterError
}

export interface LLMModelResult {
  readonly state: PageState
  readonly data?: LLMModelView
  readonly error?: AdapterError
}

export interface PresetProviderListResult {
  readonly state: PageState
  readonly data?: readonly PresetProviderView[]
  readonly error?: AdapterError
}

export interface LLMSyncResult {
  readonly state: PageState
  readonly added?: number
  readonly updated?: number
  readonly found?: number
  readonly error?: AdapterError
}

export interface LLMProfileServices {
  listLLMProviders(): Promise<LLMProviderListResult>
  getPresetProviders(): Promise<PresetProviderListResult>
  getLLMProvider(id: string): Promise<LLMProviderResult>
  createLLMProvider(input: CreateLLMProviderInput): Promise<LLMProviderResult>
  updateLLMProvider(id: string, input: UpdateLLMProviderInput): Promise<LLMProviderResult>
  deleteLLMProvider(id: string): Promise<MutationResult>
  enableLLMProvider(id: string, enabled: boolean): Promise<LLMProviderResult>
  listLLMModels(providerId: string): Promise<LLMModelListResult>
  createLLMModel(providerId: string, input: CreateLLMModelInput): Promise<LLMModelResult>
  updateLLMModel(id: string, input: UpdateLLMModelInput): Promise<LLMModelResult>
  deleteLLMModel(id: string): Promise<MutationResult>
  syncLLMModels(providerId: string): Promise<LLMSyncResult>
}

export interface CreateLLMProviderInput {
  readonly presetProviderId?: string
  readonly providerKey: string
  readonly name: string
  readonly description?: string
  readonly defaultChatEndpoint?: string
  readonly endpointConfigs?: Record<string, unknown>
  readonly authType?: string
  readonly apiKey?: string
  readonly apiKeyLabel?: string
  readonly websites?: Record<string, string>
  readonly isEnabled?: boolean
}

export interface UpdateLLMProviderInput {
  readonly name?: string
  readonly description?: string
  readonly defaultChatEndpoint?: string
  readonly endpointConfigs?: Record<string, unknown>
  readonly apiKey?: string
  readonly apiKeyLabel?: string
  readonly websites?: Record<string, string>
  readonly settings?: Record<string, unknown>
  readonly isEnabled?: boolean
}

export interface CreateLLMModelInput {
  readonly modelId: string
  readonly displayName: string
  readonly modelType?: string
  readonly contextWindow?: number
  readonly maxOutputTokens?: number
  readonly endpointType?: string
  readonly capabilities?: Record<string, boolean>
  readonly inputPrice?: string
  readonly outputPrice?: string
  readonly isEnabled?: boolean
  readonly notes?: string
}

export interface UpdateLLMModelInput {
  readonly displayName?: string
  readonly modelType?: string
  readonly contextWindow?: number | null
  readonly maxOutputTokens?: number | null
  readonly endpointType?: string
  readonly capabilities?: Record<string, boolean>
  readonly inputPrice?: string | null
  readonly outputPrice?: string | null
  readonly isEnabled?: boolean
  readonly notes?: string
}

export interface MutationResult {
  readonly state: PageState
  readonly error?: AdapterError
}
