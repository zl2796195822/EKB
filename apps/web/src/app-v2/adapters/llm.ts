import type {
  CreateLLMModelPayload,
  CreateLLMProviderPayload,
  LLMModelItem,
  LLMModelsResponse,
  LLMProviderItem,
  LLMProvidersResponse,
  LLMSyncResponse,
  PresetProviderItem,
  PresetProvidersResponse,
  UpdateLLMModelPayload,
  UpdateLLMProviderPayload,
} from '../../types/api'
import type {
  CreateLLMModelInput,
  CreateLLMProviderInput,
  LLMModelListResult,
  LLMModelResult,
  LLMModelView,
  LLMProfileServices,
  LLMProviderListResult,
  LLMProviderResult,
  LLMProviderView,
  LLMSyncResult,
  MutationResult,
  PresetProviderListResult,
  PresetProviderView,
  UpdateLLMModelInput,
  UpdateLLMProviderInput,
} from '../types/llm'
import { stateForData, stateForError, toAdapterError } from './index'

export interface LLMApiClient {
  getLLMProviders(): Promise<LLMProvidersResponse>
  getLLMProviderCatalog(): Promise<PresetProvidersResponse>
  getLLMProvider(id: string): Promise<LLMProviderItem>
  createLLMProvider(payload: CreateLLMProviderPayload): Promise<LLMProviderItem>
  patchLLMProvider(id: string, payload: UpdateLLMProviderPayload): Promise<LLMProviderItem>
  deleteLLMProvider(id: string): Promise<{ status: string }>
  enableLLMProvider(id: string, enabled: boolean): Promise<LLMProviderItem>
  getLLMModels(providerId: string): Promise<LLMModelsResponse>
  createLLMModel(providerId: string, payload: CreateLLMModelPayload): Promise<LLMModelItem>
  patchLLMModel(id: string, payload: UpdateLLMModelPayload): Promise<LLMModelItem>
  deleteLLMModel(id: string): Promise<{ status: string }>
  syncLLMModels(providerId: string): Promise<LLMSyncResponse>
}

function mapPresetProvider(input: PresetProviderItem): PresetProviderView {
  return {
    id: input.id,
    name: input.name,
    description: input.description,
    logo: input.logo,
    defaultChatEndpoint: input.default_chat_endpoint,
    authType: input.auth_type,
    authOptional: input.auth_optional,
    websites: {
      official: input.websites?.official,
      docs: input.websites?.docs,
      apiKey: input.websites?.apiKey,
      models: input.websites?.models,
    },
  }
}

function mapLLMProvider(input: LLMProviderItem): LLMProviderView {
  const endpointConfigs: LLMProviderView['endpointConfigs'] = {}
  if (input.endpoint_configs) {
    for (const key of Object.keys(input.endpoint_configs)) {
      const raw = input.endpoint_configs[key]
      endpointConfigs[key] = {
        baseUrl: raw?.base_url,
        adapterFamily: raw?.adapter_family,
        reasoningFormatType: raw?.reasoning_format_type,
        modelsApiUrls: raw?.models_api_urls,
      }
    }
  }

  const apiFeatures = input.api_features ?? {}

  return {
    id: input.id,
    presetProviderId: input.preset_provider_id,
    providerKey: input.provider_key,
    name: input.name,
    logo: input.logo,
    description: input.description,
    websites: input.websites ?? {},
    defaultChatEndpoint: input.default_chat_endpoint,
    endpointConfigs,
    authType: (input.auth_type as LLMProviderView['authType']) ?? 'api-key',
    apiKeyLabel: input.api_key_label,
    hasApiKey: input.has_api_key,
    apiFeatures: {
      arrayContent: Boolean(apiFeatures.array_content),
      streamOptions: Boolean(apiFeatures.stream_options),
      developerRole: Boolean(apiFeatures.developer_role),
      serviceTier: Boolean(apiFeatures.service_tier),
      verbosity: Boolean(apiFeatures.verbosity),
    },
    settings: input.settings ?? {},
    modelListSource: (input.model_list_source as 'api' | 'registry') ?? 'api',
    isEnabled: input.is_enabled,
    isPresetBuiltin: input.is_preset_builtin,
    createdAt: input.created_at,
    updatedAt: input.updated_at,
  }
}

function mapLLMModel(input: LLMModelItem): LLMModelView {
  return {
    id: input.id,
    providerId: input.provider_id,
    modelId: input.model_id,
    displayName: input.display_name,
    modelType: (input.model_type as LLMModelView['modelType']) ?? 'chat',
    contextWindow: input.context_window,
    maxOutputTokens: input.max_output_tokens,
    endpointType: input.endpoint_type,
    capabilities: {
      vision: input.capabilities?.vision,
      toolUse: input.capabilities?.tool_use,
      functionCalling: input.capabilities?.function_calling,
      streaming: input.capabilities?.streaming,
      reasoning: input.capabilities?.reasoning,
    },
    inputPrice: input.input_price,
    outputPrice: input.output_price,
    isEnabled: input.is_enabled,
    isCustom: input.is_custom,
    notes: input.notes,
    createdAt: input.created_at,
    updatedAt: input.updated_at,
  }
}

function failure(error: unknown, fallbackMessage: string) {
  const mapped = toAdapterError(error, fallbackMessage)
  return { state: stateForError(mapped), error: mapped }
}

function toCreateProviderPayload(input: CreateLLMProviderInput): CreateLLMProviderPayload {
  const payload: CreateLLMProviderPayload = {
    provider_key: input.providerKey,
    name: input.name,
  }
  if (input.presetProviderId !== undefined) payload.preset_provider_id = input.presetProviderId
  if (input.description !== undefined) payload.description = input.description
  if (input.defaultChatEndpoint !== undefined) payload.default_chat_endpoint = input.defaultChatEndpoint
  if (input.endpointConfigs !== undefined) payload.endpoint_configs = input.endpointConfigs
  if (input.authType !== undefined) payload.auth_type = input.authType
  if (input.apiKey !== undefined) payload.api_key = input.apiKey
  if (input.apiKeyLabel !== undefined) payload.api_key_label = input.apiKeyLabel
  if (input.websites !== undefined) payload.websites = input.websites
  if (input.isEnabled !== undefined) payload.is_enabled = input.isEnabled
  return payload
}

function toUpdateProviderPayload(input: UpdateLLMProviderInput): UpdateLLMProviderPayload {
  const payload: UpdateLLMProviderPayload = {}
  if (input.name !== undefined) payload.name = input.name
  if (input.description !== undefined) payload.description = input.description
  if (input.defaultChatEndpoint !== undefined) payload.default_chat_endpoint = input.defaultChatEndpoint
  if (input.endpointConfigs !== undefined) payload.endpoint_configs = input.endpointConfigs
  if (input.apiKey !== undefined) payload.api_key = input.apiKey
  if (input.apiKeyLabel !== undefined) payload.api_key_label = input.apiKeyLabel
  if (input.websites !== undefined) payload.websites = input.websites
  if (input.settings !== undefined) payload.settings = input.settings
  if (input.isEnabled !== undefined) payload.is_enabled = input.isEnabled
  return payload
}

function toCreateModelPayload(input: CreateLLMModelInput): CreateLLMModelPayload {
  const payload: CreateLLMModelPayload = {
    model_id: input.modelId,
    display_name: input.displayName,
  }
  if (input.modelType !== undefined) payload.model_type = input.modelType
  if (input.contextWindow !== undefined) payload.context_window = input.contextWindow
  if (input.maxOutputTokens !== undefined) payload.max_output_tokens = input.maxOutputTokens
  if (input.endpointType !== undefined) payload.endpoint_type = input.endpointType
  if (input.capabilities !== undefined) payload.capabilities = input.capabilities
  if (input.inputPrice !== undefined) payload.input_price = input.inputPrice
  if (input.outputPrice !== undefined) payload.output_price = input.outputPrice
  if (input.isEnabled !== undefined) payload.is_enabled = input.isEnabled
  if (input.notes !== undefined) payload.notes = input.notes
  return payload
}

function toUpdateModelPayload(input: UpdateLLMModelInput): UpdateLLMModelPayload {
  const payload: UpdateLLMModelPayload = {}
  if (input.displayName !== undefined) payload.display_name = input.displayName
  if (input.modelType !== undefined) payload.model_type = input.modelType
  if (input.contextWindow !== undefined) payload.context_window = input.contextWindow
  if (input.maxOutputTokens !== undefined) payload.max_output_tokens = input.maxOutputTokens
  if (input.endpointType !== undefined) payload.endpoint_type = input.endpointType
  if (input.capabilities !== undefined) payload.capabilities = input.capabilities
  if (input.inputPrice !== undefined) payload.input_price = input.inputPrice
  if (input.outputPrice !== undefined) payload.output_price = input.outputPrice
  if (input.isEnabled !== undefined) payload.is_enabled = input.isEnabled
  if (input.notes !== undefined) payload.notes = input.notes
  return payload
}

export function createLLMAdapter(client: LLMApiClient): LLMProfileServices {
  return {
    listLLMProviders: async (): Promise<LLMProviderListResult> => {
      try {
        const data = (await client.getLLMProviders()).items.map(mapLLMProvider)
        return { state: stateForData(data), data }
      } catch (error) {
        return failure(error, '服务商列表加载失败')
      }
    },

    getPresetProviders: async (): Promise<PresetProviderListResult> => {
      try {
        const data = (await client.getLLMProviderCatalog()).items.map(mapPresetProvider)
        return { state: stateForData(data), data }
      } catch (error) {
        return failure(error, '预设服务商目录加载失败')
      }
    },

    getLLMProvider: async (id): Promise<LLMProviderResult> => {
      try {
        return { state: 'ready', data: mapLLMProvider(await client.getLLMProvider(id)) }
      } catch (error) {
        return failure(error, '服务商详情加载失败')
      }
    },

    createLLMProvider: async (input): Promise<LLMProviderResult> => {
      try {
        const response = await client.createLLMProvider(toCreateProviderPayload(input))
        return { state: 'ready', data: mapLLMProvider(response) }
      } catch (error) {
        return failure(error, '服务商创建失败')
      }
    },

    updateLLMProvider: async (id, input): Promise<LLMProviderResult> => {
      try {
        const response = await client.patchLLMProvider(id, toUpdateProviderPayload(input))
        return { state: 'ready', data: mapLLMProvider(response) }
      } catch (error) {
        return failure(error, '服务商更新失败')
      }
    },

    deleteLLMProvider: async (id): Promise<MutationResult> => {
      try {
        await client.deleteLLMProvider(id)
        return { state: 'ready' }
      } catch (error) {
        return failure(error, '服务商删除失败')
      }
    },

    enableLLMProvider: async (id, enabled): Promise<LLMProviderResult> => {
      try {
        const response = await client.enableLLMProvider(id, enabled)
        return { state: 'ready', data: mapLLMProvider(response) }
      } catch (error) {
        return failure(error, '服务商状态切换失败')
      }
    },

    listLLMModels: async (providerId): Promise<LLMModelListResult> => {
      try {
        const data = (await client.getLLMModels(providerId)).items.map(mapLLMModel)
        return { state: stateForData(data), data }
      } catch (error) {
        return failure(error, '模型列表加载失败')
      }
    },

    createLLMModel: async (providerId, input): Promise<LLMModelResult> => {
      try {
        const response = await client.createLLMModel(providerId, toCreateModelPayload(input))
        return { state: 'ready', data: mapLLMModel(response) }
      } catch (error) {
        return failure(error, '模型创建失败')
      }
    },

    updateLLMModel: async (id, input): Promise<LLMModelResult> => {
      try {
        const response = await client.patchLLMModel(id, toUpdateModelPayload(input))
        return { state: 'ready', data: mapLLMModel(response) }
      } catch (error) {
        return failure(error, '模型更新失败')
      }
    },

    deleteLLMModel: async (id): Promise<MutationResult> => {
      try {
        await client.deleteLLMModel(id)
        return { state: 'ready' }
      } catch (error) {
        return failure(error, '模型删除失败')
      }
    },

    syncLLMModels: async (providerId): Promise<LLMSyncResult> => {
      try {
        const response = await client.syncLLMModels(providerId)
        return {
          state: 'ready',
          added: response.models_created,
          updated: response.models_updated,
          found: response.models_found,
        }
      } catch (error) {
        return failure(error, '模型同步失败')
      }
    },
  }
}

export type LLMAdapter = LLMProfileServices

export const LLM_CAPABILITIES = [
  { id: 'llm.providers.read', status: 'available' },
  { id: 'llm.providers.write', status: 'available' },
  { id: 'llm.models.read', status: 'available' },
  { id: 'llm.models.write', status: 'available' },
  { id: 'llm.models.sync', status: 'available' },
] as const
