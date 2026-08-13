import { ArrowUp, Brain, CaretDown, Check, Cpu, Globe, Paperclip, PaperPlaneTilt, Stop, X } from '@phosphor-icons/react'
import { useEffect, useMemo, useRef, useState } from 'react'
import type { FormEvent, ReactNode } from 'react'
import type {
  AdapterError,
  AttachmentPromotionInput,
  AttachmentPromotionResult,
  AttachmentPromotionView,
  ComposerCapabilitiesInfo,
  ComposerModelInfo,
  KnowledgeBaseView,
  ThinkingLevel,
} from '../../types'

export interface ComposerSelectionState {
  readonly attachmentDocIds: readonly string[]
  readonly attachmentLabels: Readonly<Record<string, string>>
  readonly webSearch: boolean
  /** 旧布尔字段（向后兼容）：实际以 thinkingLevel 为准；light→false，其余→true */
  readonly deepThinking: boolean
  /** 思考程度：light/mild/medium/high/extreme，五档。默认 medium */
  readonly thinkingLevel: ThinkingLevel
  /** 选中模型的 id（ComposerModelInfo.id），空串代表用后端默认 */
  readonly selectedModelId: string
}

interface AssistantComposerProps {
  readonly value: string
  readonly disabled: boolean
  readonly streaming: boolean
  readonly capabilities: ComposerCapabilitiesInfo | undefined
  readonly models: readonly ComposerModelInfo[]
  readonly selection: ComposerSelectionState
  readonly onChange: (value: string) => void
  readonly onSelectionChange: (next: ComposerSelectionState) => void
  readonly onSubmit: (event: FormEvent<HTMLFormElement>) => void
  readonly onCancel: () => void
  readonly onPickAttachments?: () => Promise<Array<{ readonly docId: string; readonly title: string }>>
  readonly knowledgeBases: readonly KnowledgeBaseView[]
  readonly selectedKnowledgeBaseId: string
  readonly onPromoteAttachment?: (
    attachmentId: string,
    input: AttachmentPromotionInput,
  ) => Promise<AttachmentPromotionResult>
}

interface PromotionUiState {
  readonly data?: AttachmentPromotionView
  readonly error?: AdapterError
}

function ToggleButton(props: {
  readonly active: boolean
  readonly disabled: boolean
  readonly title?: string
  readonly icon: ReactNode
  readonly children: ReactNode
  readonly onClick: () => void
  readonly 'aria-pressed'?: boolean
}) {
  return (
    <button
      type="button"
      className={props.active ? 'v2-m4-tool-btn v2-m4-tool-btn--active' : 'v2-m4-tool-btn'}
      aria-pressed={props.active}
      disabled={props.disabled}
      title={props.title}
      onClick={props.onClick}
    >
      {props.icon}
      <span>{props.children}</span>
      {props.active ? <Check size={12} aria-hidden="true" /> : null}
    </button>
  )
}

type PaneTab = 'model' | 'thinking'

/** 五档思考程度：只显示中文名称（无描述，强度由名称与档位指示） */
const THINKING_LEVELS: ReadonlyArray<{
  readonly value: ThinkingLevel
  readonly label: string
  /** 5 格强度显示（1..5），用于 UI 指示 */
  readonly level: 1 | 2 | 3 | 4 | 5
  readonly icon: ReactNode
}> = [
  { value: 'light', label: '极简', level: 1, icon: <Cpu size={13} aria-hidden="true" /> },
  { value: 'mild', label: '轻量', level: 2, icon: <Cpu size={13} aria-hidden="true" /> },
  { value: 'medium', label: '中等', level: 3, icon: <Brain size={13} aria-hidden="true" /> },
  { value: 'high', label: '增强', level: 4, icon: <Brain size={13} aria-hidden="true" /> },
  { value: 'extreme', label: '极致', level: 5, icon: <Brain size={13} weight="fill" aria-hidden="true" /> },
]

export function AssistantComposer({
  value,
  disabled,
  streaming,
  capabilities,
  models,
  selection,
  onChange,
  onSelectionChange,
  onSubmit,
  onCancel,
  onPickAttachments,
  knowledgeBases,
  selectedKnowledgeBaseId,
  onPromoteAttachment,
}: AssistantComposerProps) {
  const [panelOpen, setPanelOpen] = useState(false)
  const [activeTab, setActiveTab] = useState<PaneTab>('model')
  const menuRef = useRef<HTMLDivElement | null>(null)
  const [promotionAttachmentId, setPromotionAttachmentId] = useState<string | null>(null)
  const [promotionTargetKnowledgeBaseId, setPromotionTargetKnowledgeBaseId] = useState('')
  const [promotionRelativePath, setPromotionRelativePath] = useState('')
  const [promotionSubmitting, setPromotionSubmitting] = useState(false)
  const [promotionStates, setPromotionStates] = useState<Readonly<Record<string, PromotionUiState>>>({})

  useEffect(() => {
    if (!panelOpen) return
    function onDocClick(e: MouseEvent) {
      if (!menuRef.current) return
      const t = e.target as Node | null
      if (t && menuRef.current.contains(t)) return
      setPanelOpen(false)
    }
    document.addEventListener('mousedown', onDocClick)
    return () => document.removeEventListener('mousedown', onDocClick)
  }, [panelOpen])

  const attachmentsEnabled = Boolean(capabilities?.attachmentsEnabled)
  const webSearchEnabled = Boolean(capabilities?.webSearchEnabled)
  const deepThinkingEnabled = Boolean(capabilities?.deepThinkingEnabled)
  const modelChoiceEnabled = Boolean(capabilities?.modelChoiceEnabled) && models.length > 0

  // 默认模型：若 selectedModelId 为空或不在列表里 → 选第一个（后端 defaults.model 已经带过来，这里做兜底）
  const effectiveSelectedModelId = useMemo(() => {
    if (selection.selectedModelId && models.some((m) => m.id === selection.selectedModelId)) {
      return selection.selectedModelId
    }
    return models[0]?.id ?? ''
  }, [selection.selectedModelId, models])

  const selectedModel = models.find((m) => m.id === effectiveSelectedModelId)

  /** 底部按钮显示：「模型名 · 思考程度名」 */
  const modelNameLabel = selectedModel ? selectedModel.name : (models[0]?.name ?? '')

  const thinkingMeta = THINKING_LEVELS.find((t) => t.value === selection.thinkingLevel)
  const thinkingLabel = thinkingMeta?.label ?? '中等'

  const triggerLabel = models.length > 0 ? `${modelNameLabel} · ${thinkingLabel}` : thinkingLabel

  const toggleWebSearch = () =>
    onSelectionChange({ ...selection, webSearch: !selection.webSearch })

  const pickAttachments = async () => {
    if (streaming) return
    if (onPickAttachments) {
      const picked = await onPickAttachments()
      const nextIds = new Set(selection.attachmentDocIds)
      const nextLabels: Record<string, string> = { ...selection.attachmentLabels }
      for (const p of picked) {
        nextIds.add(p.docId)
        nextLabels[p.docId] = p.title
      }
      onSelectionChange({
        ...selection,
        attachmentDocIds: Array.from(nextIds),
        attachmentLabels: nextLabels,
      })
    }
  }

  const removeAttachment = (docId: string) => {
    const nextIds = selection.attachmentDocIds.filter((x) => x !== docId)
    const nextLabels = { ...selection.attachmentLabels }
    delete nextLabels[docId]
    onSelectionChange({ ...selection, attachmentDocIds: nextIds, attachmentLabels: nextLabels })
    if (promotionAttachmentId === docId) setPromotionAttachmentId(null)
  }

  const openPromotion = (attachmentId: string) => {
    setPromotionAttachmentId(attachmentId)
    setPromotionTargetKnowledgeBaseId(selectedKnowledgeBaseId || knowledgeBases[0]?.id || '')
    setPromotionRelativePath(selection.attachmentLabels[attachmentId] ?? attachmentId)
    setPromotionSubmitting(false)
  }

  const closePromotion = () => {
    if (promotionSubmitting) return
    setPromotionAttachmentId(null)
  }

  const submitPromotion = async () => {
    if (!promotionAttachmentId || !onPromoteAttachment || promotionSubmitting) return
    const relativePath = promotionRelativePath.trim()
    if (!promotionTargetKnowledgeBaseId || !relativePath) return
    setPromotionSubmitting(true)
    const result = await onPromoteAttachment(promotionAttachmentId, {
      targetKnowledgeBaseId: promotionTargetKnowledgeBaseId,
      relativePath,
    })
    if (result.state === 'ready' && result.data) {
      setPromotionStates((current) => ({ ...current, [promotionAttachmentId]: { data: result.data } }))
      setPromotionAttachmentId(null)
    } else {
      setPromotionStates((current) => ({
        ...current,
        [promotionAttachmentId]: { error: result.error ?? { code: 'PROMOTION_FAILED', message: '附件提升失败。' } },
      }))
    }
    setPromotionSubmitting(false)
  }

  const chooseThinking = (value: ThinkingLevel) => {
    onSelectionChange({
      ...selection,
      thinkingLevel: value,
      deepThinking: value !== 'light',
    })
  }

  const chooseModel = (modelId: string) => {
    onSelectionChange({ ...selection, selectedModelId: modelId })
  }

  const panelDisabled = disabled || streaming

  return (
    <form className="v2-m4-composer" onSubmit={onSubmit}>
      {selection.attachmentDocIds.length > 0 ? (
        <div className="v2-m4-composer-attachments" aria-label="已添加的附件">
          {selection.attachmentDocIds.map((docId) => (
            <div key={docId} className="v2-m4-attachment-item">
              <span className="v2-m4-attachment-chip">
              <Paperclip size={12} aria-hidden="true" />
              <span className="v2-m4-attachment-chip__title">
                {selection.attachmentLabels[docId] ?? docId}
              </span>
              {onPromoteAttachment ? (
                <button
                  type="button"
                  className="v2-m4-attachment-chip__promote"
                  onClick={() => openPromotion(docId)}
                  aria-expanded={promotionAttachmentId === docId}
                  aria-label={`提升附件 ${selection.attachmentLabels[docId] ?? docId} 到知识库`}
                  disabled={streaming || knowledgeBases.length === 0}
                  title={knowledgeBases.length > 0 ? '提升到知识库' : '没有可用的授权知识库'}
                >
                  <ArrowUp size={12} aria-hidden="true" />
                  提升
                </button>
              ) : null}
              <button
                type="button"
                className="v2-m4-attachment-chip__remove"
                onClick={() => removeAttachment(docId)}
                aria-label={`移除附件 ${selection.attachmentLabels[docId] ?? docId}`}
                disabled={streaming}
              >
                <X size={12} aria-hidden="true" />
              </button>
              </span>
              {promotionStates[docId]?.data ? (
                <div className="v2-m4-attachment-promotion-result" role="status">
                  <strong>状态：{promotionStates[docId].data.status}</strong>
                  <span>promotion_id：{promotionStates[docId].data.promotionId}</span>
                  <span>job_id：{promotionStates[docId].data.ingestJobId}</span>
                </div>
              ) : null}
              {promotionAttachmentId === docId ? (
                <div className="v2-m4-attachment-promotion" role="group" aria-label="提升附件到知识库">
                  <label>
                    <span>目标知识库</span>
                    <select
                      aria-label="选择提升目标知识库"
                      value={promotionTargetKnowledgeBaseId}
                      onChange={(event) => setPromotionTargetKnowledgeBaseId(event.target.value)}
                      disabled={promotionSubmitting}
                    >
                      <option value="">请选择</option>
                      {knowledgeBases.map((knowledgeBase) => (
                        <option value={knowledgeBase.id} key={knowledgeBase.id}>
                          {knowledgeBase.name}
                        </option>
                      ))}
                    </select>
                  </label>
                  <label>
                    <span>知识库相对路径</span>
                    <input
                      aria-label="输入知识库相对路径"
                      value={promotionRelativePath}
                      onChange={(event) => setPromotionRelativePath(event.target.value)}
                      placeholder="例如：资料/附件.pdf"
                      maxLength={4096}
                      disabled={promotionSubmitting}
                    />
                  </label>
                  {promotionStates[docId]?.error ? (
                    <div className="v2-m4-attachment-promotion-error" role="alert">
                      {promotionStates[docId].error.status ? `${promotionStates[docId].error.status} ` : ''}
                      <strong>{promotionStates[docId].error.code}</strong>{' '}
                      {promotionStates[docId].error.message}
                    </div>
                  ) : null}
                  <div className="v2-m4-attachment-promotion-actions">
                    <button type="button" onClick={closePromotion} disabled={promotionSubmitting}>取消</button>
                    <button
                      type="button"
                      onClick={() => void submitPromotion()}
                      disabled={promotionSubmitting || !promotionTargetKnowledgeBaseId || !promotionRelativePath.trim()}
                    >
                      {promotionSubmitting ? '提交中…' : '确认提升'}
                    </button>
                  </div>
                </div>
              ) : null}
            </div>
          ))}
        </div>
      ) : null}
      <textarea
        aria-label="向 AI 助手提问"
        value={value}
        onChange={(event) => onChange(event.target.value)}
        placeholder={disabled ? '请先选择一个当前授权知识库' : '向当前知识库提问…'}
        disabled={disabled || streaming}
        rows={3}
      />
      <div className="v2-m4-composer-toolbar">
        <div className="v2-m4-composer-tools">
          <ToggleButton
            active={selection.attachmentDocIds.length > 0}
            disabled={panelDisabled || !attachmentsEnabled}
            title={
              attachmentsEnabled
                ? '添加文件到当前问答的检索范围'
                : '当前租户未启用附件/添加文件能力'
            }
            icon={<Paperclip size={15} aria-hidden="true" />}
            onClick={pickAttachments}
          >
            添加文件
            {selection.attachmentDocIds.length > 0 ? ` · ${selection.attachmentDocIds.length}` : ''}
          </ToggleButton>
          <ToggleButton
            active={selection.webSearch}
            disabled={panelDisabled || !webSearchEnabled}
            title={webSearchEnabled ? '开启联网搜索（配合知识库证据一同检索）' : '联网搜索功能暂未启用'}
            icon={<Globe size={15} aria-hidden="true" />}
            onClick={toggleWebSearch}
          >
            联网搜索
          </ToggleButton>

          {/* 模型 + 思考：参考截图式抽屉（左列两项「模型」「思考强度」，点击哪项右列才显示哪项内容）。
              底部按钮显示「模型名 · 思考强度名」。抽屉默认打开时只显示「模型」的右列。 */}
          <div className="v2-m4-model-menu v2-m4-model-menu--drawer" ref={menuRef}>
            <button
              type="button"
              className="v2-m4-tool-btn v2-m4-model-menu__trigger"
              aria-haspopup="dialog"
              aria-expanded={panelOpen}
              disabled={panelDisabled || (!modelChoiceEnabled && !deepThinkingEnabled)}
              title={
                modelChoiceEnabled || deepThinkingEnabled
                  ? `选择模型和思考强度（当前：${triggerLabel}）`
                  : '当前租户没有可选模型或思考能力'
              }
              onClick={() => setPanelOpen((v) => !v)}
            >
              <Cpu size={15} aria-hidden="true" />
              <span className="v2-m4-model-menu__trigger-name">{triggerLabel}</span>
              <CaretDown size={12} aria-hidden="true" />
            </button>
            {panelOpen ? (
              <div
                className="v2-m4-model-menu__drawer"
                role="dialog"
                aria-label="选择模型和思考强度"
              >
                <div className="v2-m4-model-menu__drawer-menu">
                  <button
                    type="button"
                    className={
                      activeTab === 'model'
                        ? 'v2-m4-model-menu__drawer-menu-item v2-m4-model-menu__drawer-menu-item--active'
                        : 'v2-m4-model-menu__drawer-menu-item'
                    }
                    aria-pressed={activeTab === 'model'}
                    onClick={() => setActiveTab('model')}
                  >
                    <span className="v2-m4-model-menu__drawer-menu-title">模型</span>
                    <span className="v2-m4-model-menu__drawer-menu-subtitle">{modelNameLabel}</span>
                  </button>
                  <button
                    type="button"
                    className={
                      activeTab === 'thinking'
                        ? 'v2-m4-model-menu__drawer-menu-item v2-m4-model-menu__drawer-menu-item--active'
                        : 'v2-m4-model-menu__drawer-menu-item'
                    }
                    aria-pressed={activeTab === 'thinking'}
                    onClick={() => setActiveTab('thinking')}
                  >
                    <span className="v2-m4-model-menu__drawer-menu-title">思考强度</span>
                    <span className="v2-m4-model-menu__drawer-menu-subtitle">{thinkingLabel}</span>
                  </button>
                </div>

                <div className="v2-m4-model-menu__drawer-body">
                  {activeTab === 'model' ? (
                    <div className="v2-m4-model-menu__list" role="radiogroup" aria-label="选择模型">
                      {models.map((m) => {
                        const isActive = effectiveSelectedModelId === m.id
                        return (
                          <button
                            type="button"
                            role="radio"
                            aria-checked={isActive}
                            key={m.id}
                            className={
                              isActive
                                ? 'v2-m4-model-menu__row v2-m4-model-menu__row--selected'
                                : 'v2-m4-model-menu__row'
                            }
                            onClick={() => chooseModel(m.id)}
                          >
                            <Cpu size={14} aria-hidden="true" />
                            {/* 只显示模型名称（不显示 provider、上下文、token、描述） */}
                            <span className="v2-m4-model-menu__row-name">{m.name}</span>
                            {isActive ? (
                              <Check size={14} aria-hidden="true" className="v2-m4-model-menu__row-check" />
                            ) : null}
                          </button>
                        )
                      })}
                    </div>
                  ) : (
                    <div className="v2-m4-model-menu__list" role="radiogroup" aria-label="选择思考强度">
                      {THINKING_LEVELS.map((opt) => {
                        const isActive = selection.thinkingLevel === opt.value
                        const optDisabled = opt.value !== 'light' && !deepThinkingEnabled
                        return (
                          <button
                            type="button"
                            role="radio"
                            aria-checked={isActive}
                            key={opt.value}
                            disabled={optDisabled}
                            title={optDisabled ? '思考增强暂未启用' : undefined}
                            className={
                              isActive
                                ? 'v2-m4-model-menu__row v2-m4-model-menu__row--selected v2-m4-model-menu__row--thinking'
                                : 'v2-m4-model-menu__row v2-m4-model-menu__row--thinking'
                            }
                            onClick={() => chooseThinking(opt.value)}
                          >
                            {opt.icon}
                            <span className="v2-m4-model-menu__row-name">{opt.label}</span>
                            {isActive ? (
                              <Check size={14} aria-hidden="true" className="v2-m4-model-menu__row-check" />
                            ) : null}
                          </button>
                        )
                      })}
                    </div>
                  )}
                </div>
              </div>
            ) : null}
          </div>
        </div>
        {streaming ? (
          <button type="button" className="v2-m4-stop-button" onClick={onCancel}>
            <Stop size={15} weight="bold" aria-hidden="true" />停止
          </button>
        ) : (
          <button type="submit" className="v2-m4-send-button" disabled={disabled || !value.trim()}>
            <PaperPlaneTilt size={15} weight="fill" aria-hidden="true" />发送
          </button>
        )}
      </div>
    </form>
  )
}
