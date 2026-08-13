import {
  CaretDown,
  Check,
  Download,
  Eye,
  EyeSlash,
  GearSix,
  MagnifyingGlass,
  Plus,
  Trash,
  X,
} from '@phosphor-icons/react'
import { useCallback, useEffect, useMemo, useState } from 'react'
import { StatePanel } from '.'
import type {
  AuthSession,
  LLMModelView,
  LLMProfileServices,
  LLMProviderView,
  PageState,
  PresetProviderView,
} from '../types'

type LLMModelPanelProps = {
  readonly services: LLMProfileServices
  readonly session: AuthSession
}

type ListItem =
  | { kind: 'configured'; provider: LLMProviderView }
  | { kind: 'preset'; preset: PresetProviderView }

type Notice = { readonly tone: 'ok' | 'error'; readonly text: string } | null

const LOCAL_PROVIDER_KEYS = new Set([
  'ollama',
  'ollama-chat',
  'lmstudio',
  'lm-studio',
  'new-api',
  'newapi',
  'gpustack',
  'gpu-stack',
  'ovms',
  'openvino',
  'openvino-model-server',
  'opencode',
  'opencode-go',
])

function isRemoteHttpUrl(value: string): boolean {
  try {
    const url = new URL(value.trim())
    const hostname = url.hostname.replace(/^\[|\]$/g, '').toLowerCase()
    if (!['http:', 'https:'].includes(url.protocol)) return false
    if (hostname === 'localhost' || hostname.endsWith('.localhost')) return false
    if (hostname === '127.0.0.1' || hostname === '::1' || hostname === '0.0.0.0') return false
    return true
  } catch {
    return false
  }
}

function isRemoteProvider(provider: LLMProviderView): boolean {
  if (LOCAL_PROVIDER_KEYS.has(provider.providerKey.trim().toLowerCase())) return false
  return Object.values(provider.endpointConfigs).every((config) => {
    const urls = [
      config.baseUrl,
      ...Object.values(config.modelsApiUrls ?? {}),
    ].filter((value): value is string => Boolean(value))
    return urls.every(isRemoteHttpUrl)
  })
}

type ModelDraft = {
  readonly id?: string
  readonly modelId: string
  readonly displayName: string
  readonly modelType: string
  readonly contextWindow: string
  readonly maxOutputTokens: string
  readonly isEnabled: boolean
  readonly notes: string
}

const EMPTY_MODEL_DRAFT: ModelDraft = {
  modelId: '',
  displayName: '',
  modelType: 'chat',
  contextWindow: '',
  maxOutputTokens: '',
  isEnabled: true,
  notes: '',
}

function NoticeLine({ notice }: { readonly notice: Notice }) {
  if (!notice) return null
  return (
    <p
      className="v2-profile-notice"
      data-tone={notice.tone}
      role={notice.tone === 'error' ? 'alert' : 'status'}
    >
      {notice.text}
    </p>
  )
}

function formatPrice(price: string | null): string {
  if (!price) return '—'
  const n = Number(price)
  if (Number.isNaN(n)) return price
  if (n === 0) return '0.00'
  const formatter = new Intl.NumberFormat('en-US', {
    minimumFractionDigits: 2,
    maximumFractionDigits: n < 0.01 ? 6 : n < 1 ? 4 : n < 10 ? 3 : 2,
  })
  return formatter.format(n)
}

type ModelVisual = {
  readonly kind: 'emoji' | 'initials'
  readonly value: string
  readonly tone:
    | 'default'
    | 'reason'
    | 'chat'
    | 'vision'
    | 'embed'
    | 'flash'
    | 'pro'
    | 'code'
    | 'audio'
}

const MODEL_EMOJI_RULES: readonly {
  readonly test: RegExp
  readonly visual: Omit<ModelVisual, 'kind'> & { readonly kind?: ModelVisual['kind'] }
}[] = [
  { test: /reason/i, visual: { value: '🧠', tone: 'reason' } },
  { test: /deepseek-r|r1|deep-reason/i, visual: { value: '🧠', tone: 'reason' } },
  { test: /flash/i, visual: { value: '⚡', tone: 'flash' } },
  { test: /turbo/i, visual: { value: '🚀', tone: 'flash' } },
  { test: /pro/i, visual: { value: '🏆', tone: 'pro' } },
  { test: /o1|o3|o4/i, visual: { value: '🧠', tone: 'reason' } },
  { test: /vision|vl|v-|image|pix|qwen2.?vl|gpt-4o|gemini-1\.5|mini-omni/i, visual: { value: '👁️', tone: 'vision' } },
  { test: /embed|embedding|bge|e5|gte|uae/i, visual: { value: '📐', tone: 'embed' } },
  { test: /coder|code-|codegeex|starcoder|codestral|deepseek-coder|qwen.?coder/i, visual: { value: '💻', tone: 'code' } },
  { test: /whisper|audio|tts|speech|voice/i, visual: { value: '🎙️', tone: 'audio' } },
  { test: /chat|chat-\d|sonnet|haiku|opus|hermes|qwen|glm|doubao|yi-|moonshot|minimax|stepfun|hunyuan|doubao/i, visual: { value: '💬', tone: 'chat' } },
  { test: /^deepseek-v?$|^deepseek$|^deepseek-chat$/i, visual: { value: '💬', tone: 'chat' } },
]

function getModelVisual(provider: { readonly id?: string; readonly name?: string }, model: { readonly modelId: string; readonly displayName?: string; readonly modelType?: string }): ModelVisual {
  const haystack = `${provider.id ?? ''} ${provider.name ?? ''} ${model.modelId} ${model.displayName ?? ''} ${model.modelType ?? ''}`.toLowerCase()
  for (const rule of MODEL_EMOJI_RULES) {
    if (rule.test.test(haystack)) {
      return { kind: rule.visual.kind ?? 'emoji', value: rule.visual.value, tone: rule.visual.tone }
    }
  }
  const mt = model.modelType
  if (mt === 'reasoning') return { kind: 'emoji', value: '🧠', tone: 'reason' }
  if (mt === 'image') return { kind: 'emoji', value: '👁️', tone: 'vision' }
  if (mt === 'embedding') return { kind: 'emoji', value: '📐', tone: 'embed' }
  if (mt === 'audio') return { kind: 'emoji', value: '🎙️', tone: 'audio' }
  if (mt === 'chat') return { kind: 'emoji', value: '💬', tone: 'chat' }
  return {
    kind: 'initials',
    value: (model.modelId || model.displayName || provider.name || 'M').slice(0, 2).toUpperCase(),
    tone: 'default',
  }
}

function groupModelsByPrefix(models: readonly LLMModelView[]): Map<string, LLMModelView[]> {
  const groups = new Map<string, LLMModelView[]>()
  for (const model of models) {
    const idx = model.modelId.indexOf('-')
    const prefix = idx > 0 ? model.modelId.slice(0, idx) : model.modelId.slice(0, 4) || 'other'
    if (!groups.has(prefix)) groups.set(prefix, [])
    groups.get(prefix)!.push(model)
  }
  return groups
}

export function LLMModelPanel({ services }: LLMModelPanelProps) {
  const [providers, setProviders] = useState<readonly LLMProviderView[]>([])
  const [providersState, setProvidersState] = useState<PageState>('loading')
  const [presets, setPresets] = useState<readonly PresetProviderView[]>([])
  const [presetsState, setPresetsState] = useState<PageState>('loading')
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [searchText, setSearchText] = useState('')
  const [notice, setNotice] = useState<Notice>(null)

  const loadProviders = useCallback(async () => {
    const result = await services.listLLMProviders()
    setProvidersState(result.state)
    setProviders(result.data ?? [])
  }, [services])

  const loadPresets = useCallback(async () => {
    const result = await services.getPresetProviders()
    setPresetsState(result.state)
    setPresets(result.data ?? [])
  }, [services])

  useEffect(() => {
    void loadProviders()
    void loadPresets()
  }, [loadProviders, loadPresets])

  const configured = providers.filter((p) => !p.isPresetBuiltin && isRemoteProvider(p))
  const remotePresets = presets.filter((p) => !LOCAL_PROVIDER_KEYS.has(p.id.trim().toLowerCase()))
  const configuredIds = useMemo(() => new Set(configured.map((p) => p.id)), [configured])

  const listItems: ListItem[] = useMemo(() => {
    const items: ListItem[] = []
    const lower = searchText.trim().toLowerCase()
    for (const p of configured) {
      if (!lower || p.name.toLowerCase().includes(lower)) {
        items.push({ kind: 'configured', provider: p })
      }
    }
    for (const preset of remotePresets) {
      if (configuredIds.has(preset.id)) continue
      if (!lower || preset.name.toLowerCase().includes(lower)) {
        items.push({ kind: 'preset', preset })
      }
    }
    return items
  }, [configured, remotePresets, configuredIds, searchText])

  const configuredItems = listItems.filter((i) => i.kind === 'configured')
  const presetItems = listItems.filter((i) => i.kind === 'preset')

  useEffect(() => {
    if (!selectedId) {
      if (configuredItems.length > 0 && configuredItems[0].kind === 'configured') {
        setSelectedId(configuredItems[0].provider.id)
      } else if (presetItems.length > 0 && presetItems[0].kind === 'preset') {
        setSelectedId(presetItems[0].preset.id)
      }
    }
  }, [selectedId, configuredItems, presetItems])

  const selectedProvider = providers.find((p) => p.id === selectedId) ?? null
  const selectedPreset = presets.find((p) => p.id === selectedId) ?? null

  return (
    <div className="v2-m5-settings-panel v2-llm-panel">
      <div className="v2-m5-panel-intro">
        <div>
          <h2>模型服务</h2>
          <p>配置 AI 服务商的 API 密钥、端点地址和可用模型。启用后将自动注册到 AI 助手的默认模型下拉。</p>
        </div>
        <GearSix size={20} aria-hidden="true" />
      </div>

      <NoticeLine notice={notice} />

      <div className="v2-llm-layout">
        <aside className="v2-llm-sidebar" style={{ width: 320 }}>
          <div className="v2-llm-sidebar-search">
            <MagnifyingGlass size={14} aria-hidden="true" />
            <input
              type="text"
              placeholder="搜索服务商名称…"
              value={searchText}
              onChange={(e) => setSearchText(e.target.value)}
            />
          </div>

          <button
            type="button"
            className="v2-m5-primary-button v2-llm-add-btn"
            onClick={() => setNotice({ tone: 'ok', text: '请在下方可用预设中选择，或联系管理员新增自定义服务商。' })}
          >
            <Plus size={15} aria-hidden="true" />
            添加服务商
          </button>

          {configuredItems.length > 0 ? (
            <div className="v2-llm-group">
              <div className="v2-llm-group-title">已配置 · {configuredItems.length}</div>
              <ul>
                {configuredItems.map((item) => {
                  if (item.kind !== 'configured') return null
                  const p = item.provider
                  const active = selectedId === p.id
                  return (
                    <li
                      key={p.id}
                      data-active={active ? 'true' : 'false'}
                      data-state={p.isEnabled ? 'ok' : 'warn'}
                      onClick={() => setSelectedId(p.id)}
                    >
                      <div className="v2-llm-avatar">
                        {p.logo ? (
                          <img src={p.logo} alt="" />
                        ) : (
                          <span>{p.name.slice(0, 1).toUpperCase()}</span>
                        )}
                      </div>
                      <div className="v2-llm-meta">
                        <div className="v2-llm-meta-title">
                          <strong>{p.name}</strong>
                          {p.isEnabled ? (
                            <span className="v2-llm-li-badge ok">已启用</span>
                          ) : p.hasApiKey ? (
                            <span className="v2-llm-li-badge warn">待启用</span>
                          ) : (
                            <span className="v2-llm-li-badge warn">密钥未填</span>
                          )}
                        </div>
                        <span>
                          {p.description
                            ? p.description.length > 20
                              ? p.description.slice(0, 20) + '…'
                              : p.description
                            : '—'}
                        </span>
                      </div>
                      <span
                        className={`v2-llm-status-dot${p.isEnabled ? ' is-enabled' : ''}`}
                        aria-label={p.isEnabled ? '已启用' : '未启用'}
                      />
                    </li>
                  )
                })}
              </ul>
            </div>
          ) : null}

          {presetItems.length > 0 ? (
            <div className="v2-llm-group">
              <div className="v2-llm-group-title">可用预设 · {presetItems.length}</div>
              <ul>
                {presetItems.map((item) => {
                  if (item.kind !== 'preset') return null
                  const p = item.preset
                  const active = selectedId === p.id
                  return (
                    <li
                      key={p.id}
                      data-active={active ? 'true' : 'false'}
                      onClick={() => setSelectedId(p.id)}
                    >
                      <div className="v2-llm-avatar is-preset">
                        {p.logo ? (
                          <img src={p.logo} alt="" />
                        ) : (
                          <span>{p.name.slice(0, 1).toUpperCase()}</span>
                        )}
                      </div>
                      <div className="v2-llm-meta">
                        <div className="v2-llm-meta-title">
                          <strong>{p.name}</strong>
                          <span className="v2-llm-li-badge">预设</span>
                        </div>
                        <span>
                          {p.description
                            ? p.description.length > 20
                              ? p.description.slice(0, 20) + '…'
                              : p.description
                            : '官方 API'}
                        </span>
                      </div>
                      <span
                        className="v2-llm-status-dot is-preset"
                        aria-label="预设"
                      />
                    </li>
                  )
                })}
              </ul>
            </div>
          ) : null}

          {providersState === 'loading' || presetsState === 'loading' ? (
            <StatePanel state="loading" message="正在加载服务商列表。" />
          ) : listItems.length === 0 ? (
            <StatePanel state="empty" message="没有匹配的服务商。" />
          ) : null}
        </aside>

        <section className="v2-llm-main">
          {selectedProvider ? (
            <ProviderDetail
              key={selectedProvider.id}
              provider={selectedProvider}
              preset={selectedPreset}
              services={services}
              onChanged={() => {
                void loadProviders()
              }}
              onNotice={(n) => setNotice(n)}
            />
          ) : selectedPreset ? (
            <PresetDetail
              preset={selectedPreset}
              services={services}
              onCreated={(id) => {
                setSelectedId(id)
                void loadProviders()
              }}
              onNotice={(n) => setNotice(n)}
            />
          ) : (
            <StatePanel state="empty" message="请从左侧选择一个服务商。" />
          )}
        </section>
      </div>
    </div>
  )
}

function ProviderDetail({
  provider,
  preset,
  services,
  onChanged,
  onNotice,
}: {
  readonly provider: LLMProviderView
  readonly preset: PresetProviderView | null
  readonly services: LLMProfileServices
  readonly onChanged: () => void
  readonly onNotice: (n: Notice) => void
}) {
  const [isEnabled, setIsEnabled] = useState(provider.isEnabled)
  const [enabling, setEnabling] = useState(false)
  const [apiKey, setApiKey] = useState('')
  const [showKey, setShowKey] = useState(false)
  const [savingKey, setSavingKey] = useState(false)
  const [models, setModels] = useState<readonly LLMModelView[]>([])
  const [modelsState, setModelsState] = useState<PageState>('loading')
  const [syncing, setSyncing] = useState(false)
  const [expandedGroups, setExpandedGroups] = useState<Record<string, boolean>>({})
  const [editingEndpoint, setEditingEndpoint] = useState<string | null>(null)
  const [modelEditor, setModelEditor] = useState<'create' | 'edit' | null>(null)
  const [modelDraft, setModelDraft] = useState<ModelDraft>(EMPTY_MODEL_DRAFT)
  const [modelSaving, setModelSaving] = useState(false)
  const [endpointValues, setEndpointValues] = useState<Record<string, string>>(() => {
    const out: Record<string, string> = {}
    for (const key of Object.keys(provider.endpointConfigs)) {
      out[key] = provider.endpointConfigs[key]?.baseUrl ?? ''
    }
    return out
  })

  const loadModels = useCallback(async () => {
    setModelsState('loading')
    const result = await services.listLLMModels(provider.id)
    setModelsState(result.state)
    setModels(result.data ?? [])
  }, [services, provider.id])

  useEffect(() => {
    void loadModels()
  }, [loadModels])

  const toggleEnabled = async () => {
    setEnabling(true)
    const next = !isEnabled
    const result = await services.enableLLMProvider(provider.id, next)
    setEnabling(false)
    if (result.state === 'ready' && result.data) {
      setIsEnabled(result.data.isEnabled)
      onChanged()
    } else {
      onNotice({ tone: 'error', text: result.error?.message ?? '状态切换失败' })
    }
  }

  const handleSaveKey = async () => {
    if (!apiKey.trim()) {
      onNotice({ tone: 'error', text: '请输入 API 密钥' })
      return
    }
    setSavingKey(true)
    const result = await services.updateLLMProvider(provider.id, { apiKey: apiKey.trim() })
    setSavingKey(false)
    if (result.state === 'ready') {
      setApiKey('')
      onNotice({ tone: 'ok', text: 'API 密钥已更新' })
      onChanged()
    } else {
      onNotice({ tone: 'error', text: result.error?.message ?? '密钥保存失败' })
    }
  }

  const handleSync = async () => {
    setSyncing(true)
    const result = await services.syncLLMModels(provider.id)
    setSyncing(false)
    if (result.state === 'ready') {
      onNotice({ tone: 'ok', text: `同步完成：新增 ${result.added ?? 0} 个，更新 ${result.updated ?? 0} 个` })
      void loadModels()
    } else {
      onNotice({ tone: 'error', text: result.error?.message ?? '同步失败' })
    }
  }

  const handleSaveEndpoint = async (type: string) => {
    const value = endpointValues[type]?.trim() ?? ''
    const configs = {
      ...provider.endpointConfigs,
      [type]: {
        ...(provider.endpointConfigs[type] ?? {}),
        baseUrl: value || undefined,
      },
    }
    const result = await services.updateLLMProvider(provider.id, { endpointConfigs: configs })
    if (result.state === 'ready') {
      setEditingEndpoint(null)
      onNotice({ tone: 'ok', text: '端点地址已保存' })
      onChanged()
    } else {
      onNotice({ tone: 'error', text: result.error?.message ?? '保存失败' })
    }
  }

  const openModelEditor = (mode: 'create' | 'edit', model?: LLMModelView) => {
    setModelEditor(mode)
    setModelDraft(model ? {
      id: model.id,
      modelId: model.modelId,
      displayName: model.displayName,
      modelType: model.modelType,
      contextWindow: model.contextWindow?.toString() ?? '',
      maxOutputTokens: model.maxOutputTokens?.toString() ?? '',
      isEnabled: model.isEnabled,
      notes: model.notes ?? '',
    } : EMPTY_MODEL_DRAFT)
  }

  const closeModelEditor = () => {
    if (!modelSaving) setModelEditor(null)
  }

  const saveModel = async (event: React.FormEvent) => {
    event.preventDefault()
    if (!modelEditor || !modelDraft.modelId.trim() || !modelDraft.displayName.trim() || modelSaving) return
    const contextWindow = modelDraft.contextWindow.trim() ? Number(modelDraft.contextWindow) : undefined
    const maxOutputTokens = modelDraft.maxOutputTokens.trim() ? Number(modelDraft.maxOutputTokens) : undefined
    if ((contextWindow !== undefined && (!Number.isInteger(contextWindow) || contextWindow <= 0)) ||
        (maxOutputTokens !== undefined && (!Number.isInteger(maxOutputTokens) || maxOutputTokens <= 0))) {
      onNotice({ tone: 'error', text: '上下文和输出上限必须是正整数。' })
      return
    }
    setModelSaving(true)
    const input = {
      modelId: modelDraft.modelId.trim(),
      displayName: modelDraft.displayName.trim(),
      modelType: modelDraft.modelType,
      ...(contextWindow !== undefined ? { contextWindow } : {}),
      ...(maxOutputTokens !== undefined ? { maxOutputTokens } : {}),
      isEnabled: modelDraft.isEnabled,
      ...(modelDraft.notes.trim() ? { notes: modelDraft.notes.trim() } : {}),
    }
    const result = modelEditor === 'create'
      ? await services.createLLMModel(provider.id, input)
      : await services.updateLLMModel(modelDraft.id ?? '', {
          displayName: input.displayName,
          modelType: input.modelType,
          ...(contextWindow !== undefined ? { contextWindow } : {}),
          ...(maxOutputTokens !== undefined ? { maxOutputTokens } : {}),
          isEnabled: input.isEnabled,
          notes: modelDraft.notes.trim() || undefined,
        })
    setModelSaving(false)
    if (result.state === 'ready') {
      setModelEditor(null)
      onNotice({ tone: 'ok', text: modelEditor === 'create' ? '自定义模型已添加。' : '模型设置已保存。' })
      await loadModels()
    } else {
      onNotice({ tone: 'error', text: result.error?.message ?? '模型保存失败' })
    }
  }

  const modelGroups = useMemo(() => groupModelsByPrefix(models), [models])

  const apiKeyUrl = preset?.websites.apiKey ?? provider.websites.apiKey
  const defaultEndpointType = provider.defaultChatEndpoint ?? 'chat'
  const endpointTypes = Object.keys(provider.endpointConfigs).length > 0
    ? Object.keys(provider.endpointConfigs)
    : [defaultEndpointType]

  function formatEndpointType(key: string): string {
    const known: Record<string, string> = {
      'openai-chat-completions': 'Chat',
      'chat': 'Chat',
      'anthropic-messages': 'Messages',
      'completions': 'Completions',
      'openai-completions': 'Completions',
      'embeddings': 'Embeddings',
      'openai-embeddings': 'Embeddings',
      'images': 'Images',
      'moderations': 'Moderations',
      'rerank': 'Rerank',
      'audio-speech': 'TTS',
      'audio-transcriptions': 'STT',
      'realtime': 'Realtime',
    }
    if (known[key]) return known[key]
    const cleaned = key.replace(/^(openai|anthropic|azure|google|cohere|mistral|gemini|deepseek)[-_]/i, '')
    if (!cleaned) return key
    const words = cleaned.split(/[-_]/).filter(Boolean)
    if (words.length === 0) return key
    return words
      .map((w) => (w.length <= 3 ? w.toUpperCase() : w.charAt(0).toUpperCase() + w.slice(1).toLowerCase()))
      .join(' ')
  }

  const hasAnyEnabledModel = models.some((m) => m.isEnabled)
  const showCtaBar = provider.isEnabled && modelsState === 'ready' && hasAnyEnabledModel

  return (
    <div className="v2-llm-detail">
      <header className="v2-llm-detail-head">
        <div className="v2-llm-avatar lg">
          {provider.logo ? (
            <img src={provider.logo} alt="" />
          ) : (
            <span>{provider.name.slice(0, 1).toUpperCase()}</span>
          )}
        </div>
        <div className="v2-llm-detail-meta">
          <h3>{provider.name}</h3>
          {provider.description ? <p>{provider.description}</p> : null}
          <div className="v2-llm-detail-tags">
            <span className="v2-profile-chip">
              auth: {provider.authType}
            </span>
            <span className="v2-profile-chip">
              source: {provider.modelListSource}
            </span>
            {endpointTypes.map((t) => (
              <span className="v2-profile-chip" key={t}>
                endpoint: {formatEndpointType(t)}
              </span>
            ))}
          </div>
        </div>
        <div className="v2-llm-head-right">
          <div className="v2-llm-status-line" data-ok={provider.isEnabled ? 'true' : 'false'}>
            {provider.isEnabled
              ? hasAnyEnabledModel
                ? `已连接 · ${models.filter((m) => m.isEnabled).length} 个模型可用`
                : '已启用 · 尚未同步模型'
              : '未启用 · AI 助手暂不可选'}
          </div>
          <label className={`v2-llm-switch${isEnabled ? ' is-on' : ''}`}>
            <input
              type="checkbox"
              checked={isEnabled}
              disabled={enabling}
              onChange={toggleEnabled}
            />
            <span className="v2-llm-switch-track">
              <span className="v2-llm-switch-thumb" />
            </span>
            <small>{isEnabled ? '已启用' : '已禁用'}</small>
          </label>
        </div>
      </header>

      {showCtaBar ? (
        <div className="v2-llm-cta-bar" role="status">
          <div className="v2-llm-cta-icon">✓</div>
          <div className="v2-llm-cta-text">
            <div className="v2-llm-cta-title">模型已注册到 AI 助手默认模型下拉列表</div>
            <div className="v2-llm-cta-desc">
              共 {models.filter((m) => m.isEnabled).length} 个模型对 AI 助手开放，包括{' '}
              {models
                .filter((m) => m.isEnabled)
                .slice(0, 2)
                .map((m) => m.displayName)
                .join('、')}
              {models.filter((m) => m.isEnabled).length > 2 ? ' 等' : ''}
            </div>
          </div>
          <div
            className="v2-llm-cta-action v2-m5-primary-button"
            role="link"
            onClick={() => {
              window.location.hash = '#/assistant'
            }}
          >
            去 AI 助手试试 →
          </div>
        </div>
      ) : null}

      <div className="v2-m5-card v2-llm-section">
        <div className="v2-llm-section-head">
          <div className="v2-llm-section-num">1</div>
          <h4>API 密钥</h4>
          {apiKeyUrl ? (
            <a
              className="v2-llm-link"
              href={apiKeyUrl}
              target="_blank"
              rel="noreferrer noopener"
            >
              获取密钥 →
            </a>
          ) : null}
          <span className="v2-llm-section-sub">
            {provider.hasApiKey ? '已设置 · 留空不修改' : '尚未设置'}
          </span>
        </div>
        <div className="v2-llm-api-key-row">
          <div className="v2-llm-password-wrap">
            <input
              type={showKey ? 'text' : 'password'}
              value={apiKey}
              onChange={(e) => setApiKey(e.target.value)}
              placeholder={
                provider.hasApiKey
                  ? `${provider.apiKeyLabel ?? 'API Key'} 已设置，留空则不修改`
                  : `请输入 ${provider.apiKeyLabel ?? 'API Key'}…`
              }
            />
            <button
              type="button"
              className="v2-llm-icon-btn"
              onClick={() => setShowKey((v) => !v)}
              aria-label={showKey ? '隐藏密钥' : '显示密钥'}
            >
              {showKey ? <EyeSlash size={15} /> : <Eye size={15} />}
            </button>
          </div>
          <button
            type="button"
            className="v2-m5-primary-button"
            onClick={() => void handleSaveKey()}
            disabled={savingKey}
          >
            {savingKey ? '保存中…' : '保存密钥'}
          </button>
        </div>
      </div>

      <div className="v2-m5-card v2-llm-section">
        <div className="v2-llm-section-head">
          <div className="v2-llm-section-num">2</div>
          <h4>API 地址 &amp; 端点配置</h4>
          <button
            type="button"
            className="v2-m5-secondary-button"
            onClick={() => setEditingEndpoint(editingEndpoint ? null : defaultEndpointType)}
          >
            <GearSix size={13} aria-hidden="true" />
            自定义端点配置
          </button>
          <span className="v2-llm-section-sub">
            {editingEndpoint
              ? '编辑中，保存后立即生效'
              : provider.defaultChatEndpoint
                ? `默认端点：${provider.defaultChatEndpoint}`
                : '未配置默认端点'}
          </span>
        </div>
        <div className="v2-llm-endpoint-list">
          {endpointTypes.map((type) => {
            const cfg = provider.endpointConfigs[type] ?? {}
            const editing = editingEndpoint === type
            const fallbackBase = provider.defaultChatEndpoint ?? ''
            const displayBaseUrl = cfg.baseUrl ?? fallbackBase
            const currentValue = endpointValues[type] ?? cfg.baseUrl ?? fallbackBase
            const isDefault = !cfg.baseUrl
            return (
              <div className="v2-llm-endpoint-row" key={type}>
                <span className="v2-llm-endpoint-type">{formatEndpointType(type)}</span>
                {editing ? (
                  <>
                    <input
                      type="url"
                      value={endpointValues[type] ?? currentValue}
                      onChange={(e) =>
                        setEndpointValues({ ...endpointValues, [type]: e.target.value })
                      }
                      placeholder="https://api.example.com/v1"
                    />
                    <div className="v2-llm-endpoint-actions">
                      <button
                        type="button"
                        className="v2-m5-primary-button"
                        onClick={() => void handleSaveEndpoint(type)}
                      >
                        <Check size={13} />
                        保存
                      </button>
                      <button
                        type="button"
                        className="v2-m5-secondary-button"
                        onClick={() => setEditingEndpoint(null)}
                      >
                        <X size={13} />
                        取消
                      </button>
                    </div>
                  </>
                ) : (
                  <>
                    <code className="v2-llm-endpoint-url">{displayBaseUrl || '（未配置）'}</code>
                    {isDefault && displayBaseUrl ? (
                      <span className="v2-llm-endpoint-default-tag">默认端点</span>
                    ) : isDefault ? null : (
                      <span className="v2-llm-endpoint-custom-tag">已覆盖</span>
                    )}
                    <button
                      type="button"
                      className="v2-llm-icon-btn"
                      onClick={() => {
                        setEditingEndpoint(type)
                        setEndpointValues({ ...endpointValues, [type]: cfg.baseUrl ?? fallbackBase })
                      }}
                      aria-label="编辑端点"
                    >
                      <GearSix size={15} />
                    </button>
                  </>
                )}
              </div>
            )
          })}
        </div>
      </div>

      <div className="v2-m5-card v2-llm-section">
        <div className="v2-llm-section-head">
          <div className="v2-llm-section-num">3</div>
          <h4>模型</h4>
          <div className="v2-llm-section-actions">
            <button
              type="button"
              className="v2-m5-secondary-button"
              onClick={() => void handleSync()}
              disabled={syncing}
            >
              <Download size={13} aria-hidden="true" />
              {syncing ? '同步中…' : '获取模型列表'}
            </button>
            <button
              type="button"
              className="v2-m5-primary-button"
              onClick={() => openModelEditor('create')}
            >
              <Plus size={13} aria-hidden="true" />
              新增
            </button>
          </div>
          <span className="v2-llm-section-sub">
            {modelsState === 'ready'
              ? `共 ${models.length} 个 · ${models.filter((m) => m.isEnabled).length} 个已开放给 AI 助手`
              : modelsState === 'loading'
              ? '加载中'
              : '尚未同步'}
          </span>
        </div>

        {modelsState === 'loading' ? (
          <StatePanel state="loading" message="正在加载模型列表。" />
        ) : modelsState === 'ready' ? (
          <div className="v2-llm-models-table">
            <div className="v2-llm-model-head">
              <div></div>
              <div>模型</div>
              <div>能力</div>
              <div>上下文 / 输出</div>
              <div>价格（输入 / 输出）</div>
              <div style={{ justifySelf: 'end' }}>操作</div>
            </div>
            {models.map((m) => {
              const recommended =
                m.capabilities.reasoning ||
                m.modelId.includes('chat') ||
                m.modelId.includes('pro')
              const visual = getModelVisual(provider, m)
              const avatarCls = `v2-llm-mini-avatar v2-llm-mini-avatar--${visual.tone}`
              return (
                <div key={m.id} className="v2-llm-model-row">
                  <div>
                    <span
                      className={`v2-llm-status-dot small${m.isEnabled ? ' is-enabled' : ''}`}
                      aria-label={m.isEnabled ? '已启用' : '未启用'}
                    />
                  </div>
                  <div className="v2-llm-cell-name">
                    <div className="v2-llm-cell-title">
                      <span className={avatarCls} aria-hidden="true">
                        {visual.kind === 'emoji' ? (
                          <span className="v2-llm-mini-emoji">{visual.value}</span>
                        ) : (
                          <span>{visual.value}</span>
                        )}
                      </span>
                      <span className="v2-llm-cell-title-name">{m.displayName}</span>
                      {recommended ? <span className="v2-llm-rec-pill">推荐</span> : null}
                    </div>
                    <div className="v2-llm-cell-id">{m.modelId}</div>
                  </div>
                  <div className="v2-llm-cell-caps">
                    {m.modelType === 'reasoning' || m.capabilities?.reasoning ? (
                      <span className="v2-llm-cap-tag reason">Reason</span>
                    ) : m.modelType === 'image' || m.capabilities?.vision ? (
                      <span className="v2-llm-cap-tag image">Vision</span>
                    ) : m.modelType === 'embedding' ? (
                      <span className="v2-llm-cap-tag embed">Embed</span>
                    ) : m.modelType === 'chat' ? (
                      <span className="v2-llm-cap-tag chat">Chat</span>
                    ) : (
                      <span className="v2-llm-cap-tag">
                        {(m.modelType || 'model').charAt(0).toUpperCase() +
                          (m.modelType || 'model').slice(1).toLowerCase()}
                      </span>
                    )}
                  </div>
                  <div className="v2-llm-cell-metrics">
                    {m.contextWindow ? (
                      <div className="v2-llm-metric">
                        <small>
                          <b>{m.contextWindow.toLocaleString()}</b> ctx
                        </small>
                      </div>
                    ) : null}
                    {m.maxOutputTokens ? (
                      <div className="v2-llm-metric">
                        <small>
                          <b>{m.maxOutputTokens.toLocaleString()}</b> out
                        </small>
                      </div>
                    ) : null}
                  </div>
                  <div className="v2-llm-cell-prices">
                    <span className="v2-llm-price-chip">
                      <span className="v2-llm-price-label">入</span>
                      <b>${formatPrice(m.inputPrice)}</b>
                      <span className="v2-llm-price-label">/M</span>
                    </span>
                    <span className="v2-llm-price-chip">
                      <span className="v2-llm-price-label">出</span>
                      <b>${formatPrice(m.outputPrice)}</b>
                      <span className="v2-llm-price-label">/M</span>
                    </span>
                  </div>
                  <div className="v2-llm-cell-actions">
                    <button
                      type="button"
                      className="v2-llm-icon-btn"
                      onClick={() => openModelEditor('edit', m)}
                      aria-label="模型设置"
                    >
                      <GearSix size={15} />
                    </button>
                    <button
                      type="button"
                      className="v2-llm-icon-btn danger"
                      onClick={async () => {
                        const result = await services.deleteLLMModel(m.id)
                        if (result.state === 'ready') {
                          onNotice({ tone: 'ok', text: '模型已删除' })
                          void loadModels()
                        } else {
                          onNotice({ tone: 'error', text: result.error?.message ?? '删除失败' })
                        }
                      }}
                      aria-label="删除模型"
                    >
                      <Trash size={15} />
                    </button>
                  </div>
                </div>
              )
            })}
          </div>
        ) : (
          <StatePanel state={modelsState} message="尚未同步模型列表。" />
        )}
      </div>
      {modelEditor ? <div className="v2-m3-modal-backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) closeModelEditor() }}>
        <section className="v2-m3-modal v2-llm-model-modal" role="dialog" aria-modal="true" aria-labelledby="llm-model-editor-title">
          <header className="v2-m3-modal-heading"><div><h2 id="llm-model-editor-title">{modelEditor === 'create' ? '新增自定义模型' : '编辑模型'}</h2><p>模型 ID 必须与远程服务商实际支持的模型一致。</p></div><button type="button" className="v2-llm-icon-btn" onClick={closeModelEditor} aria-label="关闭"><X size={16} /></button></header>
          <form className="v2-m3-form" onSubmit={(event) => void saveModel(event)}>
            <label>模型 ID<input value={modelDraft.modelId} onChange={(event) => setModelDraft({ ...modelDraft, modelId: event.target.value })} placeholder="例如 gpt-4o-mini" required disabled={modelEditor === 'edit'} autoFocus /></label>
            <label>显示名称<input value={modelDraft.displayName} onChange={(event) => setModelDraft({ ...modelDraft, displayName: event.target.value })} placeholder="例如 GPT-4o mini" required /></label>
            <label>模型类型<select value={modelDraft.modelType} onChange={(event) => setModelDraft({ ...modelDraft, modelType: event.target.value })}><option value="chat">chat · 对话</option><option value="reasoning">reasoning · 推理</option><option value="embedding">embedding · 向量</option><option value="image">image · 图像</option><option value="reranker">reranker · 重排</option></select></label>
            <div className="v2-llm-model-form-grid"><label>上下文窗口<input type="number" min="1" value={modelDraft.contextWindow} onChange={(event) => setModelDraft({ ...modelDraft, contextWindow: event.target.value })} placeholder="可选" /></label><label>最大输出<input type="number" min="1" value={modelDraft.maxOutputTokens} onChange={(event) => setModelDraft({ ...modelDraft, maxOutputTokens: event.target.value })} placeholder="可选" /></label></div>
            <label className="v2-llm-model-checkbox"><input type="checkbox" checked={modelDraft.isEnabled} onChange={(event) => setModelDraft({ ...modelDraft, isEnabled: event.target.checked })} />开放给 AI 助手</label>
            <label>备注<textarea rows={2} value={modelDraft.notes} onChange={(event) => setModelDraft({ ...modelDraft, notes: event.target.value })} placeholder="可选" /></label>
            <div className="v2-m3-modal-actions"><button type="button" className="v2-m5-secondary-button" onClick={closeModelEditor}>取消</button><button type="submit" className="v2-m5-primary-button" disabled={modelSaving || !modelDraft.modelId.trim() || !modelDraft.displayName.trim()}>{modelSaving ? '保存中…' : '保存模型'}</button></div>
          </form>
        </section>
      </div> : null}
    </div>
  )
}

function PresetDetail({
  preset,
  services,
  onCreated,
  onNotice,
}: {
  readonly preset: PresetProviderView
  readonly services: LLMProfileServices
  readonly onCreated: (id: string) => void
  readonly onNotice: (n: Notice) => void
}) {
  const [apiKey, setApiKey] = useState('')
  const [showKey, setShowKey] = useState(false)
  const [creating, setCreating] = useState(false)
  const [name, setName] = useState(preset.name)

  const handleCreate = async () => {
    setCreating(true)
    const result = await services.createLLMProvider({
      presetProviderId: preset.id,
      providerKey: preset.id.toLowerCase(),
      name,
      authType: preset.authType,
      defaultChatEndpoint: preset.defaultChatEndpoint ?? undefined,
      websites: preset.websites,
      apiKey: apiKey.trim() || undefined,
      isEnabled: true,
    })
    setCreating(false)
    if (result.state === 'ready' && result.data) {
      onNotice({ tone: 'ok', text: '服务商已创建，请在右侧配置模型。' })
      onCreated(result.data.id)
    } else {
      onNotice({ tone: 'error', text: result.error?.message ?? '创建失败' })
    }
  }

  return (
    <div className="v2-llm-detail">
      <header className="v2-llm-detail-head">
        <div className="v2-llm-avatar lg is-preset">
          {preset.logo ? (
            <img src={preset.logo} alt="" />
          ) : (
            <span>{preset.name.slice(0, 1).toUpperCase()}</span>
          )}
        </div>
        <div className="v2-llm-detail-meta">
          <h3>{preset.name}</h3>
          {preset.description ? <p>{preset.description}</p> : null}
          <div className="v2-llm-detail-tags">
            <span className="v2-profile-chip">预设目录</span>
            <span className="v2-profile-chip">
              auth: {preset.authType}
              {preset.authOptional ? '（可选）' : ''}
            </span>
          </div>
        </div>
        <span className="v2-llm-preset-badge">未配置</span>
      </header>

      <div className="v2-m5-card v2-llm-section">
        <div className="v2-llm-section-head">
          <h4>快速配置</h4>
          {preset.websites.docs ? (
            <a
              className="v2-llm-link"
              href={preset.websites.docs}
              target="_blank"
              rel="noreferrer noopener"
            >
              查看文档 →
            </a>
          ) : null}
        </div>

        <div className="v2-m5-profile-form">
          <label>
            <span>显示名称</span>
            <input value={name} onChange={(e) => setName(e.target.value)} maxLength={64} />
          </label>

          <div className="v2-llm-api-key-row">
            <label style={{ flex: 1, minWidth: 0 }}>
              <span>
                API 密钥
                {preset.websites.apiKey ? (
                  <a
                    className="v2-llm-link"
                    href={preset.websites.apiKey}
                    target="_blank"
                    rel="noreferrer noopener"
                    style={{ marginLeft: 8 }}
                  >
                    去获取 →
                  </a>
                ) : null}
              </span>
              <div className="v2-llm-password-wrap">
                <input
                  type={showKey ? 'text' : 'password'}
                  value={apiKey}
                  onChange={(e) => setApiKey(e.target.value)}
                  placeholder={preset.authOptional ? '可选，跳过使用匿名访问' : '请输入 API 密钥…'}
                />
                <button
                  type="button"
                  className="v2-llm-icon-btn"
                  onClick={() => setShowKey((v) => !v)}
                  aria-label={showKey ? '隐藏密钥' : '显示密钥'}
                >
                  {showKey ? <EyeSlash size={15} /> : <Eye size={15} />}
                </button>
              </div>
            </label>
          </div>

          <div className="v2-profile-actions">
            <button
              type="button"
              className="v2-m5-primary-button"
              onClick={() => void handleCreate()}
              disabled={creating}
            >
              <Plus size={15} aria-hidden="true" />
              {creating ? '创建中…' : '创建并启用服务商'}
            </button>
          </div>
        </div>
      </div>

      <div className="v2-m5-card v2-llm-section">
        <div className="v2-llm-section-head">
          <h4>服务商链接</h4>
        </div>
        <div className="v2-llm-links">
          {preset.websites.official ? (
            <a href={preset.websites.official} target="_blank" rel="noreferrer noopener">
              官网 →
            </a>
          ) : null}
          {preset.websites.docs ? (
            <a href={preset.websites.docs} target="_blank" rel="noreferrer noopener">
              API 文档 →
            </a>
          ) : null}
          {preset.websites.models ? (
            <a href={preset.websites.models} target="_blank" rel="noreferrer noopener">
              模型列表 →
            </a>
          ) : null}
        </div>
      </div>
    </div>
  )
}
