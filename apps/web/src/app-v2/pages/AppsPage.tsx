import {
  ArrowRight,
  ChatCircleDots,
  ChartLine,
  CheckCircle,
  Copy,
  FileText,
  Key,
  LinkSimple,
  MagnifyingGlass,
  Plug,
  SealWarning,
  ShieldCheck,
  SlidersHorizontal,
  Spinner,
  Storefront,
  Trash,
  X,
} from '@phosphor-icons/react'
import { useCallback, useEffect, useMemo, useState, type ReactNode } from 'react'
import { StatePanel } from '../components/StatePanel'
import type {
  AdapterError,
  AppCategory,
  AppCredentialCreateView,
  AppCredentialView,
  AppDetailView,
  AppItemView,
  AppRunView,
  PageState,
  V2PageProps,
} from '../types'

type AppsTab = 'all' | 'installed' | 'recommended'

const APPS_TABS: readonly { id: AppsTab; label: string }[] = [
  { id: 'all', label: '全部应用' },
  { id: 'installed', label: '已安装' },
  { id: 'recommended', label: '推荐' },
]

function errorMeta(error?: AdapterError): string {
  if (!error) return ''
  return [
    error.code ? `code ${error.code}` : '',
    error.requestId ? `request ${error.requestId}` : '',
  ].filter(Boolean).join(' · ')
}

/** 把后端 icon_slug 映射到 phosphor 图标组件。 */
function iconForSlug(slug: string): ReactNode {
  const iconMap: Record<string, ReactNode> = {
    'chat-circle-dots': <ChatCircleDots size={20} aria-hidden="true" />,
    'file-text': <FileText size={20} aria-hidden="true" />,
    'chart-line': <ChartLine size={20} aria-hidden="true" />,
    'shield-check': <ShieldCheck size={20} aria-hidden="true" />,
  }
  return iconMap[slug] ?? <Plug size={20} aria-hidden="true" />
}

const CATEGORY_LABEL: Readonly<Record<string, string>> = {
  DOCUMENT_SYNC: '文档同步',
  MESSAGING: '消息通知',
  ANALYTICS: '数据分析',
  GOVERNANCE: '治理审计',
  UNKNOWN: '其他',
}

const STATUS_LABEL: Readonly<Record<string, string>> = {
  AVAILABLE: '可安装',
  INSTALLED: '已安装',
  UNINSTALLED: '已卸载',
  ERROR: '异常',
}

const INSTALLATION_LABEL: Readonly<Record<string, string>> = {
  INSTALLED: '已安装',
  CONFIGURED: '已配置',
  CONNECTED: '已连接',
  UNINSTALLED: '已卸载',
}

const CAP_LABEL: Readonly<Record<string, string>> = {
  VIEW_ANALYTICS: '查看运营数据',
  MANAGE_AUDIT: '管理权限审计',
  READ_WORKSPACE_DOCUMENTS: '读取空间文档',
  WRITE_WORKSPACE_DOCUMENTS: '写入空间文档',
  SEND_NOTIFICATIONS: '发送消息通知',
  SYNC_EXTERNAL_DOCUMENTS: '同步外部文档',
  VIEW_AUDIT_LOGS: '查看审计日志',
  TRIGGER_EKBS: '调用EKB任务',
}

function capLabel(cap: string) {
  return CAP_LABEL[cap] ?? cap
}

function shortDate(value: string | null | undefined) {
  if (!value) return '—'
  return value.slice(0, 19).replace('T', ' ')
}

function redactPrefixDot(prefix: string) {
  if (!prefix) return '····'
  return prefix + '····'
}

function copyToClipboard(text: string) {
  if (typeof navigator !== 'undefined' && 'clipboard' in navigator) {
    void navigator.clipboard.writeText(text).catch(() => {})
  }
}

export function AppsPage({ services }: V2PageProps) {
  const [activeTab, setActiveTab] = useState<AppsTab>('all')
  const [query, setQuery] = useState('')

  // 目录数据
  const [catalogState, setCatalogState] = useState<PageState>('loading')
  const [catalog, setCatalog] = useState<readonly AppItemView[]>([])
  const [catalogError, setCatalogError] = useState<AdapterError | undefined>()

  // 已安装列表
  const [installedState, setInstalledState] = useState<PageState>('loading')
  const [installed, setInstalled] = useState<readonly AppItemView[]>([])
  const [installedError, setInstalledError] = useState<AdapterError | undefined>()

  // 操作反馈
  const [actionSlug, setActionSlug] = useState<string | null>(null)
  const [actionState, setActionState] = useState<'idle' | 'loading' | 'done'>('idle')

  // 详情抽屉：选中 slug，展示 capability / 配置 / 凭据 / runs
  const [selectedSlug, setSelectedSlug] = useState<string | null>(null)
  const [detailState, setDetailState] = useState<PageState>('empty')
  const [detail, setDetail] = useState<AppDetailView | null>(null)
  const [detailError, setDetailError] = useState<AdapterError | undefined>()

  // 本地详情子状态
  const [configText, setConfigText] = useState<string>('')
  const [configBusy, setConfigBusy] = useState(false)
  const [configSaveMsg, setConfigSaveMsg] = useState<string | null>(null)

  const [connectBusy, setConnectBusy] = useState(false)
  const [connectMsg, setConnectMsg] = useState<string | null>(null)

  const [newCredName, setNewCredName] = useState('')
  const [newCredSecret, setNewCredSecret] = useState('')
  const [createCredBusy, setCreateCredBusy] = useState(false)
  const [createdOnceCredential, setCreatedOnceCredential] = useState<AppCredentialCreateView | null>(null)

  const [revokeBusyMap, setRevokeBusyMap] = useState<Record<string, boolean>>({})

  const loadCatalog = useCallback(async () => {
    setCatalogState('loading')
    const result = await services.apps.getCatalog()
    setCatalogState(result.state)
    setCatalog(result.data?.items ?? [])
    setCatalogError(result.error)
  }, [services.apps])

  const loadInstalled = useCallback(async () => {
    setInstalledState('loading')
    const result = await services.apps.getInstalled()
    setInstalledState(result.state)
    setInstalled(result.data?.items ?? [])
    setInstalledError(result.error)
  }, [services.apps])

  const loadDetail = useCallback(
    async (slug: string) => {
      setDetailState('loading')
      const result = await services.apps.getDetail(slug)
      setDetailState(result.state)
      setDetail(result.data ?? null)
      setDetailError(result.error)
      if (result.data?.installation?.config) {
        try {
          setConfigText(JSON.stringify(result.data.installation.config, null, 2))
        } catch {
          setConfigText('{}')
        }
      } else {
        setConfigText('{}')
      }
      setConfigSaveMsg(null)
      setConnectMsg(null)
    },
    [services.apps],
  )

  useEffect(() => {
    void loadCatalog()
    void loadInstalled()
  }, [loadCatalog, loadInstalled])

  useEffect(() => {
    if (selectedSlug) void loadDetail(selectedSlug)
  }, [selectedSlug, loadDetail])

  const visibleItems = useMemo(() => {
    const keyword = query.trim().toLowerCase()
    if (!keyword) return catalog
    return catalog.filter(
      (item) =>
        `${item.name} ${item.slug} ${item.description}`.toLowerCase().includes(keyword),
    )
  }, [catalog, query])

  const refreshAfterChange = useCallback(async () => {
    await Promise.all([loadCatalog(), loadInstalled()])
    if (selectedSlug) await loadDetail(selectedSlug)
  }, [loadCatalog, loadInstalled, loadDetail, selectedSlug])

  const handleInstall = async (slug: string) => {
    setActionSlug(slug)
    setActionState('loading')
    await services.apps.install(slug)
    setActionState('done')
    await refreshAfterChange()
    setTimeout(() => setActionState('idle'), 1500)
  }

  const handleUninstall = async (slug: string) => {
    setActionSlug(slug)
    setActionState('loading')
    await services.apps.uninstall(slug)
    setActionState('done')
    await refreshAfterChange()
    setTimeout(() => setActionState('idle'), 1500)
  }

  const handleSaveConfig = async () => {
    if (!selectedSlug || !detail?.installation) return
    let parsed: Record<string, unknown> = {}
    try {
      parsed = JSON.parse(configText || '{}')
    } catch (e) {
      setConfigSaveMsg('配置不是合法 JSON')
      return
    }
    setConfigBusy(true)
    setConfigSaveMsg(null)
    const result = await services.apps.configure(selectedSlug, parsed)
    setConfigBusy(false)
    if (result.state === 'ready' && result.data) {
      setConfigSaveMsg(`已保存 · 状态：${result.data.status}`)
    } else {
      setConfigSaveMsg(result.error?.message ?? '保存失败')
    }
    await refreshAfterChange()
  }

  const handleConnect = async () => {
    if (!selectedSlug) return
    setConnectBusy(true)
    setConnectMsg(null)
    const result = await services.apps.connect(selectedSlug)
    setConnectBusy(false)
    if (result.state === 'ready' && result.data?.connected) {
      setConnectMsg('连接检查通过，状态已切换为 CONNECTED。')
    } else if (result.data) {
      setConnectMsg(result.data.error ?? '连接失败')
    } else {
      setConnectMsg(result.error?.message ?? '连接失败')
    }
    await refreshAfterChange()
  }

  const handleCreateCredential = async (event: React.FormEvent) => {
    event.preventDefault()
    if (!selectedSlug) return
    if (!newCredName.trim() || !newCredSecret.trim()) return
    setCreateCredBusy(true)
    setCreatedOnceCredential(null)
    const result = await services.apps.createCredential(selectedSlug, {
      name: newCredName,
      secret: newCredSecret,
    })
    setCreateCredBusy(false)
    if (result.state === 'ready' && result.data) {
      setCreatedOnceCredential(result.data)
      setNewCredName('')
      setNewCredSecret('')
    }
    await refreshAfterChange()
  }

  const handleRevoke = async (credId: string) => {
    if (!confirm('确认吊销该凭据？此操作不可恢复。')) return
    setRevokeBusyMap((prev) => ({ ...prev, [credId]: true }))
    await services.apps.revokeCredential(credId)
    setRevokeBusyMap((prev) => ({ ...prev, [credId]: false }))
    await refreshAfterChange()
  }

  return (
    <div className="v2-m5-page v2-m6-apps-page">
      <header className="v2-m5-page-heading">
        <div>
          <p className="v2-eyebrow">KNOWLEDGE BASE / WORKSPACE</p>
          <h1>应用中心</h1>
          <p>发现并连接更多应用，扩展知识库的协作能力。</p>
        </div>
      </header>

      <div className="v2-m6-apps-layout">
        <section className="v2-m6-apps-card" aria-label="应用目录">
          <div className="v2-m5-card-topline">
            <div className="v2-m5-tabs" role="tablist" aria-label="应用中心视图">
              {APPS_TABS.map((tab) => (
                <button
                  type="button"
                  key={tab.id}
                  id={`apps-tab-${tab.id}`}
                  role="tab"
                  aria-selected={activeTab === tab.id}
                  aria-controls={`apps-panel-${tab.id}`}
                  className={activeTab === tab.id ? 'is-active' : ''}
                  onClick={() => setActiveTab(tab.id)}
                >
                  {tab.label}
                </button>
              ))}
            </div>
            <label className="v2-m5-search-field">
              <MagnifyingGlass size={15} aria-hidden="true" />
              <span className="v2-sr-only">搜索应用</span>
              <input
                value={query}
                onChange={(event) => setQuery(event.target.value)}
                placeholder="搜索应用…"
                aria-label="搜索应用"
              />
            </label>
          </div>

          {/* 全部 / 推荐 tab */}
          {activeTab === 'all' || activeTab === 'recommended' ? (
            <div
              id={`apps-panel-${activeTab === 'recommended' ? 'recommended' : 'all'}`}
              className="v2-m5-tab-panel"
              role="tabpanel"
              aria-labelledby={`apps-tab-${activeTab === 'recommended' ? 'recommended' : 'all'}`}
            >
              <div className="v2-m5-panel-intro">
                <div>
                  <h2>{activeTab === 'recommended' ? '推荐' : '全部应用'}</h2>
                  <p>
                    来自后端 <code>app_catalog</code> 权威目录的 6 个内置应用；
                    安装、配置、连接、凭据写入、运行记录均实时落库。
                  </p>
                </div>
                <Storefront size={20} aria-hidden="true" />
              </div>

              {catalogState === 'loading' ? (
                <StatePanel state="loading" message="正在加载应用目录。" />
              ) : null}
              {catalogState === 'permission-denied' ? (
                <StatePanel state="permission-denied" message="当前主体没有知识库读取能力。" reason={errorMeta(catalogError)} />
              ) : null}
              {catalogState === 'error' ? (
                <StatePanel state="error" message="应用目录加载失败。" reason={errorMeta(catalogError)} />
              ) : null}
              {catalogState === 'empty' ? (
                <StatePanel state="empty" message="当前租户还没有可用的应用。" />
              ) : null}
              {catalogState === 'ready' && visibleItems.length > 0 ? (
                <div className="v2-m6-app-grid">
                  {visibleItems.map((app) => (
                    <AppTile
                      key={app.id}
                      app={app}
                      isBusy={actionState === 'loading' && actionSlug === app.slug}
                      isSelected={selectedSlug === app.slug}
                      onSelect={() => setSelectedSlug(app.slug)}
                      onInstall={() => handleInstall(app.slug)}
                      onUninstall={() => handleUninstall(app.slug)}
                    />
                  ))}
                </div>
              ) : catalogState === 'ready' ? (
                <StatePanel state="empty" message="没有匹配的应用。" />
              ) : null}
            </div>
          ) : null}

          {/* 已安装 tab */}
          {activeTab === 'installed' ? (
            <div id="apps-panel-installed" className="v2-m5-tab-panel" role="tabpanel" aria-labelledby="apps-tab-installed">
              <div className="v2-m5-panel-intro">
                <div><h2>已安装</h2><p>当前租户已安装并激活的应用。</p></div>
                <Plug size={20} aria-hidden="true" />
              </div>

              {installedState === 'loading' ? (
                <StatePanel state="loading" message="正在加载已安装应用。" />
              ) : null}
              {installedState === 'permission-denied' ? (
                <StatePanel state="permission-denied" message="无权限查看已安装应用。" reason={errorMeta(installedError)} />
              ) : null}
              {installedState === 'error' ? (
                <StatePanel state="error" message="已安装列表加载失败。" reason={errorMeta(installedError)} />
              ) : null}
              {installedState === 'ready' && installed.length > 0 ? (
                <div className="v2-m6-app-grid">
                  {installed.map((app) => (
                    <AppTile
                      key={app.id}
                      app={app}
                      isBusy={actionState === 'loading' && actionSlug === app.slug}
                      isSelected={selectedSlug === app.slug}
                      onSelect={() => setSelectedSlug(app.slug)}
                      onInstall={() => handleInstall(app.slug)}
                      onUninstall={() => handleUninstall(app.slug)}
                    />
                  ))}
                </div>
              ) : installedState === 'ready' ? (
                <StatePanel state="empty" message="还没有安装任何应用。去「全部应用」看看吧。" />
              ) : null}
            </div>
          ) : null}
        </section>

        {selectedSlug ? (
          <aside className="v2-m6-apps-detail" aria-label="应用详情">
            <header className="v2-m6-apps-detail-head">
              <button
                type="button"
                className="v2-m6-apps-close"
                onClick={() => setSelectedSlug(null)}
                aria-label="关闭详情"
              >
                <X size={16} />
              </button>
              {detailState === 'loading' ? (
                <div className="v2-m6-loading-inline"><Spinner size={16} /> 加载中…</div>
              ) : detailState === 'error' ? (
                <div className="v2-m6-error-inline">{detailError?.message ?? '加载失败'}</div>
              ) : detail ? (
                <>
                  <h2>
                    <span className="v2-m6-detail-icon">{iconForSlug([...catalog, ...installed].find((x) => x.slug === detail.slug)?.iconSlug ?? 'plug')}</span>
                    {detail.displayName}
                    <small>{CATEGORY_LABEL[detail.category] ?? detail.category}</small>
                  </h2>
                  <p className="v2-m6-provider">{detail.providerName} · Rank {detail.recommendedRank}</p>
                  <p className="v2-m6-desc">{detail.description}</p>
                  <ul className="v2-m6-cap-chips" aria-label="能力">
                    {detail.capabilities.length ? (
                      detail.capabilities.map((cap: string) => (
                        <li key={cap}>
                          <ShieldCheck size={12} /> {capLabel(cap)}
                        </li>
                      ))
                    ) : (
                      <li className="is-muted">无额外能力声明</li>
                    )}
                  </ul>
                </>
              ) : null}
            </header>

            {detail && detailState === 'ready' ? (
              <div className="v2-m6-detail-sections">
                {/* 安装状态 + 配置 + 连接 */}
                <section>
                  <div className="v2-m6-section-head">
                    <h3><SlidersHorizontal size={16} /> 安装与配置</h3>
                    <span className={`v2-m6-status-chip is-${detail.installation?.status ?? 'available'}`}>
                      {detail.installation ? (INSTALLATION_LABEL[detail.installation.status] ?? detail.installation.status) : '未安装'}
                    </span>
                  </div>
                  <dl className="v2-m6-meta">
                    <div><dt>安装时间</dt><dd>{shortDate(detail.installation?.installedAt)}</dd></div>
                    <div><dt>更新时间</dt><dd>{shortDate(detail.installation?.updatedAt)}</dd></div>
                    <div><dt>安装者</dt><dd>{detail.installation?.installedBy ?? '—'}</dd></div>
                    <div><dt>卸载时间</dt><dd>{shortDate(detail.installation?.uninstalledAt)}</dd></div>
                  </dl>
                  {detail.installation && detail.installation.status !== 'UNINSTALLED' ? (
                    <>
                      <label className="v2-m6-form-label">
                        配置 <span className="is-muted">（JSON，保存后状态提升为 CONFIGURED）</span>
                      </label>
                      <textarea
                        className="v2-m6-config-json"
                        spellCheck={false}
                        value={configText}
                        onChange={(e) => setConfigText(e.target.value)}
                        aria-label="应用配置 JSON"
                        rows={8}
                      />
                      <div className="v2-m6-form-actions">
                        <button
                          type="button"
                          className="v2-m5-secondary-button"
                          disabled={configBusy}
                          onClick={handleSaveConfig}
                        >
                          {configBusy ? <Spinner size={14} /> : <CheckCircle size={14} />}
                          保存配置
                        </button>
                        {configSaveMsg ? <span className={`v2-m6-inline-msg ${configSaveMsg.includes('失败') || configSaveMsg.includes('合法') ? 'is-error' : ''}`}>{configSaveMsg}</span> : null}
                      </div>
                      <div className="v2-m6-form-actions">
                        <button
                          type="button"
                          className="v2-m5-primary-button"
                          disabled={connectBusy || detail.installation.status === 'INSTALLED'}
                          title={detail.installation.status === 'INSTALLED' ? '请先保存配置再连接' : ''}
                          onClick={handleConnect}
                        >
                          {connectBusy ? <Spinner size={14} /> : <LinkSimple size={14} />}
                          连接检查（CONNECTIVITY_CHECK）
                        </button>
                        {connectMsg ? <span className={`v2-m6-inline-msg ${connectMsg.includes('失败') ? 'is-error' : ''}`}>{connectMsg}</span> : null}
                      </div>
                    </>
                  ) : (
                    <p className="is-muted">未安装应用时无法编辑配置。</p>
                  )}
                </section>

                {/* 凭据（写一次前缀显示） */}
                <section>
                  <div className="v2-m6-section-head">
                    <h3><Key size={16} /> 凭据 · 前缀显示</h3>
                    <span className="v2-m6-count-chip">{detail.credentials.length}</span>
                  </div>
                  <p className="is-muted">
                    服务端使用 Fernet 加密存储；列表永远不返回明文或密文，仅返回前缀（前 8 字符）用于核对。
                  </p>

                  {detail.installation && detail.installation.status !== 'UNINSTALLED' ? (
                    <form className="v2-m6-cred-form" onSubmit={handleCreateCredential}>
                      <label className="v2-m6-form-label">新建凭据（写入时返回一次明文）</label>
                      <div className="v2-m6-cred-grid">
                        <input
                          type="text"
                          placeholder="凭据名称，如：生产 App Key"
                          value={newCredName}
                          onChange={(e) => setNewCredName(e.target.value)}
                          disabled={createCredBusy}
                          required
                        />
                        <input
                          type="password"
                          placeholder="明文 secret，创建后不再返回"
                          value={newCredSecret}
                          onChange={(e) => setNewCredSecret(e.target.value)}
                          disabled={createCredBusy}
                          required
                        />
                        <button type="submit" className="v2-m5-primary-button" disabled={createCredBusy}>
                          {createCredBusy ? <Spinner size={14} /> : <Copy size={14} />}
                          写入凭据
                        </button>
                      </div>
                    </form>
                  ) : null}

                  {createdOnceCredential?.plaintextOnce ? (
                    <div className="v2-m6-once-banner" role="alert">
                      <div>
                        <strong>请立即保存以下明文，仅本次可见：</strong>
                        <pre className="v2-m6-once-plaintext">{createdOnceCredential.plaintextOnce}</pre>
                      </div>
                      <button
                        type="button"
                        className="v2-m5-secondary-button"
                        onClick={() => copyToClipboard(createdOnceCredential!.plaintextOnce ?? '')}
                      >
                        <Copy size={14} /> 复制
                      </button>
                    </div>
                  ) : null}

                  {detail.credentials.length ? (
                    <ul className="v2-m6-cred-list">
                      {detail.credentials.map((cred: AppCredentialView) => (
                        <li key={cred.id} className={`v2-m6-cred-item is-${cred.status.toLowerCase()}`}>
                          <div>
                            <strong>{cred.name}</strong>
                            <code className="v2-m6-prefix" aria-label={`前缀 ${cred.prefix}`}>
                              {redactPrefixDot(cred.prefix)}
                            </code>
                          </div>
                          <div className="v2-m6-cred-meta">
                            <span>创建：{shortDate(cred.createdAt)}</span>
                            <span>吊销：{cred.status === 'REVOKED' ? shortDate(cred.revokedAt) : '—'}</span>
                            <span className={`v2-m6-status-chip is-${cred.status.toLowerCase()}`}>{cred.status}</span>
                          </div>
                          <div>
                            {cred.status === 'ACTIVE' ? (
                              <button
                                type="button"
                                className="v2-m5-danger-button"
                                disabled={Boolean(revokeBusyMap[cred.id])}
                                onClick={() => handleRevoke(cred.id)}
                                title="吊销该凭据"
                              >
                                <Trash size={14} /> 吊销
                              </button>
                            ) : (
                              <span className="is-muted"><SealWarning size={14} /> 已吊销</span>
                            )}
                          </div>
                        </li>
                      ))}
                    </ul>
                  ) : (
                    <p className="is-muted">暂无凭据。</p>
                  )}
                </section>

                {/* Runs ledger */}
                <section>
                  <div className="v2-m6-section-head">
                    <h3><Spinner size={16} /> 运行记录</h3>
                    <span className="v2-m6-count-chip">{detail.runs.length}</span>
                  </div>
                  {detail.runs.length ? (
                    <table className="v2-m6-run-table">
                      <thead>
                        <tr>
                          <th>时间</th>
                          <th>类型</th>
                          <th>状态</th>
                          <th>结果（脱敏）</th>
                          <th>错误</th>
                        </tr>
                      </thead>
                      <tbody>
                        {detail.runs.map((run: AppRunView) => (
                          <tr key={run.id}>
                            <td>{shortDate(run.startedAt)}</td>
                            <td><code>{run.runType}</code></td>
                            <td>
                              <span className={`v2-m6-status-chip is-${run.status.toLowerCase()}`}>
                                {run.status === 'SUCCEEDED' ? <CheckCircle size={12} /> : <SealWarning size={12} />}
                                {' '}{run.status}
                              </span>
                            </td>
                            <td className="is-json-cell">
                              <pre>{JSON.stringify(run.resultRedacted, null, 1)}</pre>
                            </td>
                            <td>
                              {run.errorCode ? <code>{run.errorCode}</code> : null}
                              {run.errorMessage ? <div className="is-small-error">{run.errorMessage}</div> : '—'}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  ) : (
                    <p className="is-muted">暂无运行记录。首次连接会写入一次 <code>CONNECTIVITY_CHECK</code>。</p>
                  )}
                </section>
              </div>
            ) : null}
            {detailState === 'permission-denied' ? (
              <StatePanel state="permission-denied" message="无权限查看应用详情。" reason={errorMeta(detailError)} />
            ) : null}
            {detailState === 'empty' ? (
              <StatePanel state="empty" message="找不到该应用。" />
            ) : null}
          </aside>
        ) : null}
      </div>
    </div>
  )
}

interface AppTileProps {
  readonly app: AppItemView
  readonly isBusy: boolean
  readonly isSelected: boolean
  readonly onSelect: () => void
  readonly onInstall: () => void
  readonly onUninstall: () => void
}

function AppTile({ app, isBusy, isSelected, onSelect, onInstall, onUninstall }: AppTileProps) {
  const isInstalled = app.status === 'INSTALLED'
  const isAvailable = app.status === 'AVAILABLE'
  return (
    <article
      className={`v2-m6-app-tile ${isSelected ? 'is-selected' : ''}`}
      data-status={app.status.toLowerCase()}
      onClick={onSelect}
      role="button"
      tabIndex={0}
      onKeyDown={(e) => {
        if (e.key === 'Enter' || e.key === ' ') onSelect()
      }}
    >
      <span className="v2-m6-app-icon">{iconForSlug(app.iconSlug)}</span>
      <h3>{app.name}</h3>
      <p className="v2-m6-app-category">{CATEGORY_LABEL[app.category]}</p>
      <p className="v2-m6-app-desc">{app.description}</p>
      <div className="v2-m6-app-footer">
        <span className="v2-m6-app-status">{STATUS_LABEL[app.status] ?? app.status}</span>
        <div className="v2-m6-app-actions" onClick={(e) => e.stopPropagation()}>
          <button
            type="button"
            className="v2-m5-ghost-button"
            title="查看详情、配置与凭据"
            onClick={(e) => { e.stopPropagation(); onSelect() }}
          >
            <ArrowRight size={14} /> 详情
          </button>
          {isAvailable ? (
            <button
              type="button"
              className="v2-m5-primary-button"
              disabled={isBusy}
              onClick={(e) => { e.stopPropagation(); onInstall() }}
            >
              {isBusy ? <Spinner size={14} /> : null}
              安装
            </button>
          ) : null}
          {isInstalled ? (
            <button
              type="button"
              className="v2-m5-secondary-button"
              disabled={isBusy}
              onClick={(e) => { e.stopPropagation(); onUninstall() }}
            >
              {isBusy ? <Spinner size={14} /> : null}
              卸载
            </button>
          ) : null}
        </div>
      </div>
    </article>
  )
}
