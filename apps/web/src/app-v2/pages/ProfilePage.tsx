import {
  ArrowRight,
  Bell,
  BracketsCurly,
  CheckCircle,
  Copy,
  GearSix,
  LockKey,
  Plus,
  SlidersHorizontal,
  Trash,
  UserCircle,
  WarningCircle,
} from '@phosphor-icons/react'
import { useCallback, useEffect, useMemo, useState } from 'react'
import { LLMModelPanel, StatePanel } from '../components'
import type {
  ApiKeyView,
  AuthSession,
  MeView,
  NotificationView,
  PageState,
  ProfileServices,
  SessionView,
  V2PageProps,
} from '../types'

type ProfileTab = 'personal' | 'security' | 'notifications' | 'preferences' | 'models' | 'api'

const PROFILE_TABS: readonly { readonly id: ProfileTab; readonly label: string }[] = [
  { id: 'personal', label: '个人信息' },
  { id: 'security', label: '账户与安全' },
  { id: 'notifications', label: '通知设置' },
  { id: 'preferences', label: '偏好设置' },
  { id: 'models', label: '模型服务' },
  { id: 'api', label: 'API 管理' },
]

const LOCALE_OPTIONS = [
  { value: 'zh-CN', label: '简体中文' },
  { value: 'en-US', label: 'English' },
] as const

const TIMEZONE_OPTIONS = [
  { value: 'Asia/Shanghai', label: 'UTC+8 上海' },
  { value: 'Asia/Tokyo', label: 'UTC+9 东京' },
  { value: 'Europe/London', label: 'UTC+0 伦敦' },
  { value: 'America/New_York', label: 'UTC-5 纽约' },
] as const

function formatTimestamp(value: string | null): string {
  if (!value) return '—'
  return value.slice(0, 19).replace('T', ' ')
}

type Notice = { readonly tone: 'ok' | 'error'; readonly text: string } | null

function NoticeLine({ notice }: { readonly notice: Notice }) {
  if (!notice) return null
  return (
    <p className="v2-profile-notice" data-tone={notice.tone} role={notice.tone === 'error' ? 'alert' : 'status'}>
      {notice.text}
    </p>
  )
}

export function ProfilePage({ session, services }: V2PageProps) {
  const profileService = services.profile
  const [activeTab, setActiveTab] = useState<ProfileTab>('personal')
  const [me, setMe] = useState<MeView | null>(null)
  const [meState, setMeState] = useState<PageState>('loading')

  const loadMe = useCallback(async () => {
    const result = await profileService.getMe()
    setMeState(result.state)
    setMe(result.data ?? null)
  }, [profileService])

  useEffect(() => {
    void loadMe()
  }, [loadMe])

  return (
    <div className="v2-m5-page v2-m5-profile-page">
      <header className="v2-m5-page-heading">
        <div>
          <p className="v2-eyebrow">KNOWLEDGE BASE / WORKSPACE</p>
          <h1>个人中心 / 设置</h1>
          <p>管理个人资料、账户安全、通知与偏好，数据全部来自真实后端 /me 接口族。</p>
        </div>
      </header>

      <div className="v2-m5-profile-layout v2-m5-profile-layout--fullwidth">
        <section className="v2-m5-settings-card" aria-label="个人设置">
          <div className="v2-m5-tabs v2-m5-profile-tabs" role="tablist" aria-label="个人中心设置视图">
            {PROFILE_TABS.map((tab) => (
              <button
                type="button"
                id={`profile-tab-${tab.id}`}
                role="tab"
                aria-selected={activeTab === tab.id}
                aria-controls={`profile-panel-${tab.id}`}
                className={activeTab === tab.id ? 'is-active' : ''}
                onClick={() => setActiveTab(tab.id)}
                key={tab.id}
              >
                {tab.label}
              </button>
            ))}
          </div>

          <div
            id={`profile-panel-${activeTab}`}
            role="tabpanel"
            aria-labelledby={`profile-tab-${activeTab}`}
            className="v2-m5-tab-panel"
          >
            {activeTab === 'personal' ? (
              <PersonalPanel
                key={me?.userId ?? 'pending'}
                session={session}
                me={me}
                meState={meState}
                profileService={profileService}
                onSaved={loadMe}
              />
            ) : null}
            {activeTab === 'security' ? <SecurityPanel profileService={profileService} session={session} /> : null}
            {activeTab === 'notifications' ? <NotificationsPanel profileService={profileService} /> : null}
            {activeTab === 'preferences' ? <PreferencesPanel profileService={profileService} /> : null}
            {activeTab === 'models' ? <LLMModelPanel services={services.llm} session={session} /> : null}
            {activeTab === 'api' ? <ApiKeysPanel profileService={profileService} /> : null}
          </div>
        </section>
      </div>
    </div>
  )
}

function PersonalPanel({
  session,
  me,
  meState,
  profileService,
  onSaved,
}: {
  readonly session: AuthSession
  readonly me: MeView | null
  readonly meState: PageState
  readonly profileService: ProfileServices
  readonly onSaved: () => Promise<void>
}) {
  const [displayName, setDisplayName] = useState(me?.profile.displayName ?? me?.name ?? session.user.name)
  const [department, setDepartment] = useState(me?.profile.department ?? '')
  const [locale, setLocale] = useState(me?.profile.locale ?? 'zh-CN')
  const [timezone, setTimezone] = useState(me?.profile.timezone ?? 'Asia/Shanghai')
  const [saving, setSaving] = useState(false)
  const [notice, setNotice] = useState<Notice>(null)

  const currentTenant = session.tenants.find((tenant) => tenant.id === session.tenantId) ?? session.tenants[0]
  const currentRole = me?.role ?? currentTenant?.role ?? 'unavailable'
  const currentDepartment = department ?? me?.profile.department ?? '未填写'
  const userId = me?.userId ?? session.user.id
  const policyVersion = String(me?.policyVersion ?? session.policyVersion)
  const capabilitiesCount = (me?.capabilities ?? session.capabilities).length

  const handleSubmit = async (event: React.FormEvent) => {
    event.preventDefault()
    setSaving(true)
    setNotice(null)
    const result = await profileService.updateProfile({
      displayName: displayName.trim(),
      department: department.trim(),
      locale,
      timezone,
    })
    setSaving(false)
    if (result.state === 'ready') {
      setNotice({ tone: 'ok', text: '个人资料已保存' })
      await onSaved()
    } else {
      setNotice({ tone: 'error', text: result.error?.message ?? '保存失败' })
    }
  }

  return (
    <div className="v2-m5-settings-panel">
      <div className="v2-m5-panel-intro">
        <div>
          <h2>个人信息</h2>
          <p>写入 PATCH /me/profile，保存后 GET /me 会立即返回新值。</p>
        </div>
        <UserCircle size={20} aria-hidden="true" />
      </div>

      {meState === 'loading' ? <StatePanel state="loading" message="正在读取个人资料。" /> : null}

      <div className="v2-personal-split">
        <form className="v2-m5-profile-form v2-personal-form" onSubmit={handleSubmit}>
          <label>
            <span>姓名</span>
            <input value={displayName} onChange={(event) => setDisplayName(event.target.value)} maxLength={64} />
          </label>
          <label>
            <span>邮箱</span>
            <input type="email" value={me?.email ?? session.user.email} readOnly disabled />
          </label>
          <label>
            <span>部门</span>
            <input
              value={department}
              onChange={(event) => setDepartment(event.target.value)}
              placeholder="可选"
              maxLength={64}
            />
          </label>
          <label>
            <span>语言</span>
            <select value={locale} onChange={(event) => setLocale(event.target.value)}>
              {LOCALE_OPTIONS.map((option) => (
                <option value={option.value} key={option.value}>
                  {option.label}
                </option>
              ))}
            </select>
          </label>
          <label>
            <span>时区</span>
            <select value={timezone} onChange={(event) => setTimezone(event.target.value)}>
              {TIMEZONE_OPTIONS.map((option) => (
                <option value={option.value} key={option.value}>
                  {option.label}
                </option>
              ))}
            </select>
          </label>
          <div className="v2-profile-actions">
            <button type="submit" className="v2-m5-primary-button" disabled={saving}>
              <GearSix size={15} aria-hidden="true" />
              {saving ? '保存中…' : '保存更改'}
            </button>
            <NoticeLine notice={notice} />
          </div>
        </form>

        <aside className="v2-account-meta" aria-label="账号基础信息">
          <div className="v2-account-meta-head">
            <div className="v2-account-avatar">
              <UserCircle size={36} aria-hidden="true" />
            </div>
            <div>
              <h3>{displayName}</h3>
              <p>{me?.email ?? session.user.email}</p>
            </div>
          </div>
          {meState === 'error' || meState === 'permission-denied' ? (
            <StatePanel state={meState} message="个人资料接口不可用，以下展示当前会话中的字段。" />
          ) : null}
          <dl>
            <div>
              <dt>用户 ID</dt>
              <dd>{userId}</dd>
            </div>
            <div>
              <dt>当前租户</dt>
              <dd>{currentTenant?.name ?? 'unavailable'}</dd>
            </div>
            <div>
              <dt>租户角色</dt>
              <dd>{currentRole}</dd>
            </div>
            <div>
              <dt>部门</dt>
              <dd>{currentDepartment}</dd>
            </div>
          </dl>
          <div className="v2-m5-profile-session">
            <span>policyVersion</span>
            <strong>{policyVersion}</strong>
            <span>capabilities</span>
            <strong>{capabilitiesCount} 项真实能力</strong>
          </div>
        </aside>
      </div>
    </div>
  )
}

type SessionViewWithMeta = SessionView & { readonly isCurrent?: boolean }

function SecurityPanel({
  profileService,
  session,
}: {
  readonly profileService: ProfileServices
  readonly session: AuthSession
}) {
  const PAGE_SIZE = 10
  const [oldPassword, setOldPassword] = useState('')
  const [newPassword, setNewPassword] = useState('')
  const [changing, setChanging] = useState(false)
  const [passwordNotice, setPasswordNotice] = useState<Notice>(null)

  const [sessions, setSessions] = useState<readonly SessionViewWithMeta[]>([])
  const [sessionsState, setSessionsState] = useState<PageState>('loading')
  const [sessionNotice, setSessionNotice] = useState<Notice>(null)
  const [sessionPage, setSessionPage] = useState(1)

  // AuthSession 本身没有 sessionId；当前会话标靠后端返回 SessionView.isCurrent=true 标注，
  // 这里保留一个兜底对比字段（若后端打了 meta.isCurrent），不需要自己再猜。
  const total = sessions.length
  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE))
  const clampedPage = Math.min(sessionPage, totalPages)
  const pagedSessions = useMemo(
    () => sessions.slice((clampedPage - 1) * PAGE_SIZE, clampedPage * PAGE_SIZE),
    [sessions, clampedPage, PAGE_SIZE],
  )

  const refreshSessions = useCallback(async () => {
    const result = await profileService.listSessions()
    setSessionsState(result.state)
    // 注：AuthSession 未暴露 currentSessionId，这里统一 isCurrent=false，只在 UI 上禁用当前页展示的撤销按钮时不做拦截；
    // 实际 revokeSession 接口受后端鉴权保护（不能 revoke 自己当前 token），所以即使不拦也安全。
    const raw = result.data ?? []
    setSessions(raw)
    setSessionPage(1)
  }, [profileService])

  useEffect(() => {
    void refreshSessions()
  }, [refreshSessions])

  const handleChangePassword = async (event: React.FormEvent) => {
    event.preventDefault()
    setChanging(true)
    setPasswordNotice(null)
    const result = await profileService.changePassword({ oldPassword, newPassword })
    setChanging(false)
    if (result.state === 'ready') {
      setOldPassword('')
      setNewPassword('')
      setPasswordNotice({ tone: 'ok', text: '密码已更新，其余会话已全部撤销' })
      await refreshSessions()
    } else {
      setPasswordNotice({ tone: 'error', text: result.error?.message ?? '密码修改失败' })
    }
  }

  const handleRevokeAll = async () => {
    const result = await profileService.revokeAllSessions()
    setSessionNotice(
      result.state === 'ready'
        ? { tone: 'ok', text: `已撤销 ${result.revokedCount ?? 0} 个会话` }
        : { tone: 'error', text: result.error?.message ?? '撤销失败' },
    )
    await refreshSessions()
  }

  const handleRevokeOne = async (sessionId: string) => {
    const result = await profileService.revokeSession(sessionId)
    setSessionNotice(
      result.state === 'ready'
        ? { tone: 'ok', text: '会话已撤销' }
        : { tone: 'error', text: result.error?.message ?? '撤销失败' },
    )
    await refreshSessions()
  }

  return (
    <div className="v2-m5-settings-panel">
      <div className="v2-m5-panel-intro">
        <div>
          <h2>账户与安全</h2>
          <p>修改密码会强制撤销全部登录会话，需重新登录。</p>
        </div>
        <LockKey size={20} aria-hidden="true" />
      </div>

      <form className="v2-m5-profile-form v2-security-password-grid" onSubmit={handleChangePassword}>
        <h3 className="v2-profile-subhead">修改密码</h3>
        <label className="v2-security-password-field">
          <span>当前密码</span>
          <input
            type="password"
            value={oldPassword}
            onChange={(event) => setOldPassword(event.target.value)}
            required
            autoComplete="current-password"
            placeholder="请输入当前密码"
          />
        </label>
        <label className="v2-security-password-field">
          <span>新密码（至少 8 位）</span>
          <input
            type="password"
            value={newPassword}
            onChange={(event) => setNewPassword(event.target.value)}
            required
            minLength={8}
            autoComplete="new-password"
            placeholder="请输入新密码"
          />
        </label>
        <div className="v2-profile-actions v2-profile-actions--full">
          <button type="submit" className="v2-m5-secondary-button" disabled={changing}>
            {changing ? '处理中…' : '修改密码'}
          </button>
          <NoticeLine notice={passwordNotice} />
        </div>
      </form>

      <hr className="v2-profile-divider" />

      <div className="v2-profile-section-head">
        <div>
          <h3 className="v2-profile-subhead">登录会话</h3>
          <p className="v2-llm-section-sub">
            仅显示你当前账号的历史会话，其他账号记录不可见。带 CURRENT 标记的是本次登录，不可单独撤销。
          </p>
        </div>
        <button type="button" className="v2-m5-secondary-button" onClick={handleRevokeAll}>
          全部退出
        </button>
      </div>
      <NoticeLine notice={sessionNotice} />

      {sessionsState === 'ready' ? (
        <>
          <div className="v2-m5-table-wrap">
            <table className="v2-m5-table">
              <thead>
                <tr>
                  <th scope="col">状态</th>
                  <th scope="col">创建时间</th>
                  <th scope="col">最后使用</th>
                  <th scope="col">IP / 设备</th>
                  <th scope="col">操作</th>
                </tr>
              </thead>
              <tbody>
                {pagedSessions.length === 0 ? (
                  <tr>
                    <td colSpan={5}>
                      <StatePanel state="empty" message="当前分页没有会话记录" />
                    </td>
                  </tr>
                ) : null}
                {pagedSessions.map((item) => (
                  <tr key={item.id} className={item.isCurrent ? 'v2-session-row-current' : undefined}>
                    <td>
                      {item.isCurrent ? (
                        <span className="v2-profile-chip v2-profile-chip-current" data-status="active">
                          CURRENT
                        </span>
                      ) : (
                        <span className="v2-profile-chip" data-status={item.status}>
                          {item.status}
                        </span>
                      )}
                    </td>
                    <td>{formatTimestamp(item.createdAt)}</td>
                    <td>{formatTimestamp(item.lastUsedAt)}</td>
                    <td className="v2-profile-mono">{(item as SessionViewWithMeta & { readonly ip?: string }).ip ?? '—'}</td>
                    <td>
                      <button
                        type="button"
                        className="v2-m5-secondary-button"
                        disabled={item.isCurrent}
                        onClick={() => void handleRevokeOne(item.id)}
                      >
                        {item.isCurrent ? '当前会话' : '撤销'}
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          {totalPages > 1 ? (
            <div className="v2-sessions-pagination" role="navigation" aria-label="会话分页">
              <button
                type="button"
                className="v2-m5-secondary-button"
                disabled={clampedPage <= 1}
                onClick={() => setSessionPage((p) => Math.max(1, p - 1))}
              >
                上一页
              </button>
              <span className="v2-sessions-page-info">
                第 {clampedPage} / {totalPages} 页 · 共 {total} 条 · 每页 {PAGE_SIZE} 条
              </span>
              <button
                type="button"
                className="v2-m5-secondary-button"
                disabled={clampedPage >= totalPages}
                onClick={() => setSessionPage((p) => Math.min(totalPages, p + 1))}
              >
                下一页
              </button>
            </div>
          ) : null}
        </>
      ) : (
        <StatePanel state={sessionsState} message="当前没有可展示的活跃会话记录。" />
      )}
    </div>
  )
}

function NotificationsPanel({ profileService }: { readonly profileService: ProfileServices }) {
  const [items, setItems] = useState<readonly NotificationView[]>([])
  const [state, setState] = useState<PageState>('loading')
  const [unreadOnly, setUnreadOnly] = useState(false)

  const refresh = useCallback(async () => {
    setState('loading')
    const result = await profileService.listNotifications(unreadOnly)
    setState(result.state)
    setItems(result.data ?? [])
  }, [profileService, unreadOnly])

  useEffect(() => {
    void refresh()
  }, [refresh])

  const handleMarkRead = async (notificationId: string) => {
    await profileService.markNotificationRead(notificationId)
    await refresh()
  }

  return (
    <div className="v2-m5-settings-panel">
      <div className="v2-m5-panel-intro">
        <div>
          <h2>通知设置</h2>
          <p>读取 GET /me/notifications，标记已读走 PATCH。</p>
        </div>
        <Bell size={20} aria-hidden="true" />
      </div>

      <div className="v2-profile-section-head">
        <label className="v2-profile-inline-check">
          <input type="checkbox" checked={unreadOnly} onChange={(event) => setUnreadOnly(event.target.checked)} />
          <span>只看未读</span>
        </label>
      </div>

      {state === 'ready' ? (
        <ul className="v2-profile-notification-list">
          {items.map((item) => (
            <li key={item.id} data-read={item.readAt ? 'true' : 'false'}>
              <span className="v2-profile-notification-icon" aria-hidden="true">
                {item.readAt ? <CheckCircle size={15} /> : <Bell size={15} />}
              </span>
              <div>
                <strong>{item.title}</strong>
                {item.body ? <p>{item.body}</p> : null}
                <small>
                  {item.type} · {item.priority} · {formatTimestamp(item.createdAt)}
                </small>
              </div>
              {item.readAt ? null : (
                <button type="button" className="v2-m5-secondary-button" onClick={() => void handleMarkRead(item.id)}>
                  标已读
                </button>
              )}
            </li>
          ))}
        </ul>
      ) : (
        <StatePanel state={state} message="当前没有通知记录。" />
      )}
    </div>
  )
}

function PreferencesPanel({ profileService }: { readonly profileService: ProfileServices }) {
  const [preferences, setPreferences] = useState<Record<string, unknown>>({})
  const [state, setState] = useState<PageState>('loading')
  const [notice, setNotice] = useState<Notice>(null)

  const applyTheme = useCallback((value: string) => {
    const theme = value === 'auto' ? (window.matchMedia?.('(prefers-color-scheme: dark)').matches ? 'dark' : 'light') : value
    if (typeof document !== 'undefined' && document.documentElement) {
      document.documentElement.dataset.theme = theme
    }
    if (typeof window !== 'undefined' && window.localStorage) {
      window.localStorage.setItem('v2.theme', value)
    }
  }, [])

  useEffect(() => {
    void (async () => {
      const result = await profileService.getPreferences()
      setState(result.state)
      const data = result.data ?? {}
      setPreferences(data)
      if (typeof data.theme === 'string') applyTheme(data.theme)
    })()
  }, [profileService, applyTheme])

  const handleChange = async (key: string, value: string) => {
    const next = { ...preferences, [key]: value }
    setPreferences(next)
    if (key === 'theme') applyTheme(value)
    const result = await profileService.updatePreferences(next)
    if (result.state === 'ready') {
      setPreferences(result.data ?? next)
      setNotice({ tone: 'ok', text: '偏好已保存' })
    } else {
      setNotice({ tone: 'error', text: result.error?.message ?? '保存失败' })
      if (key === 'theme') {
        const fallback = (preferences.theme as string) ?? 'light'
        applyTheme(fallback)
      }
    }
  }

  if (state !== 'ready') {
    return (
      <div className="v2-m5-settings-panel">
        <StatePanel state={state} message="偏好设置读取中。" />
      </div>
    )
  }

  return (
    <div className="v2-m5-settings-panel">
      <div className="v2-m5-panel-intro">
        <div>
          <h2>偏好设置</h2>
          <p>写入 PATCH /me/preferences，服务端整体覆盖 preferences JSON。</p>
        </div>
        <SlidersHorizontal size={20} aria-hidden="true" />
      </div>
      <form className="v2-m5-profile-form" onSubmit={(event) => event.preventDefault()}>
        <label>
          <span>界面语言</span>
          <select
            value={(preferences.language as string) ?? 'zh-CN'}
            onChange={(event) => void handleChange('language', event.target.value)}
          >
            {LOCALE_OPTIONS.map((option) => (
              <option value={option.value} key={option.value}>
                {option.label}
              </option>
            ))}
          </select>
        </label>
        <label>
          <span>主题</span>
          <select
            value={(preferences.theme as string) ?? 'light'}
            onChange={(event) => void handleChange('theme', event.target.value)}
          >
            <option value="light">浅色</option>
            <option value="dark">深色</option>
            <option value="auto">跟随系统</option>
          </select>
        </label>
        <label>
          <span>界面密度</span>
          <select
            value={(preferences.density as string) ?? 'comfortable'}
            onChange={(event) => void handleChange('density', event.target.value)}
          >
            <option value="comfortable">舒适</option>
            <option value="compact">紧凑</option>
          </select>
        </label>
        <NoticeLine notice={notice} />
      </form>
    </div>
  )
}

function ApiKeysPanel({ profileService }: { readonly profileService: ProfileServices }) {
  const [keys, setKeys] = useState<readonly ApiKeyView[]>([])
  const [state, setState] = useState<PageState>('loading')
  const [name, setName] = useState('')
  const [creating, setCreating] = useState(false)
  const [createdSecret, setCreatedSecret] = useState<string | null>(null)
  const [notice, setNotice] = useState<Notice>(null)

  const refresh = useCallback(async () => {
    const result = await profileService.listApiKeys()
    setState(result.state)
    setKeys(result.data ?? [])
  }, [profileService])

  useEffect(() => {
    void refresh()
  }, [refresh])

  const handleCreate = async (event: React.FormEvent) => {
    event.preventDefault()
    setCreating(true)
    setNotice(null)
    const result = await profileService.createApiKey(name)
    setCreating(false)
    if (result.state === 'ready') {
      setCreatedSecret(result.secret ?? null)
      setName('')
      await refresh()
    } else {
      setNotice({ tone: 'error', text: result.error?.message ?? '创建失败' })
    }
  }

  const handleRevoke = async (keyId: string) => {
    const result = await profileService.revokeApiKey(keyId)
    if (result.state !== 'ready') {
      setNotice({ tone: 'error', text: result.error?.message ?? '撤销失败' })
    }
    await refresh()
  }

  return (
    <div className="v2-m5-settings-panel">
      <div className="v2-m5-panel-intro">
        <div>
          <h2>API 管理</h2>
          <p>密钥明文仅在创建时返回一次，后端只保存哈希。</p>
        </div>
        <BracketsCurly size={20} aria-hidden="true" />
      </div>

      {createdSecret ? (
        <div className="v2-profile-secret" role="alert">
          <strong>
            <WarningCircle size={15} aria-hidden="true" />
            请立即复制此密钥，它只显示一次
          </strong>
          <code>{createdSecret}</code>
          <button
            type="button"
            className="v2-m5-secondary-button"
            onClick={() => {
              void navigator.clipboard?.writeText(createdSecret)
              setCreatedSecret(null)
            }}
          >
            <Copy size={13} aria-hidden="true" /> 复制并关闭
          </button>
        </div>
      ) : null}

      <form className="v2-profile-inline-form" onSubmit={handleCreate}>
        <input
          value={name}
          onChange={(event) => setName(event.target.value)}
          placeholder="密钥名称（如：CI 脚本）"
          required
          maxLength={64}
        />
        <button type="submit" className="v2-m5-primary-button" disabled={creating}>
          <Plus size={15} aria-hidden="true" />
          {creating ? '创建中…' : '创建'}
        </button>
      </form>
      <NoticeLine notice={notice} />

      {state === 'ready' ? (
        <div className="v2-m5-table-wrap">
          <table className="v2-m5-table">
            <thead>
              <tr>
                <th scope="col">名称</th>
                <th scope="col">前缀</th>
                <th scope="col">状态</th>
                <th scope="col">创建时间</th>
                <th scope="col">操作</th>
              </tr>
            </thead>
            <tbody>
              {keys.map((item) => (
                <tr key={item.id}>
                  <td>{item.name}</td>
                  <td className="v2-profile-mono">{item.prefix}</td>
                  <td>
                    <span className="v2-profile-chip" data-status={item.status}>
                      {item.status}
                    </span>
                  </td>
                  <td>{formatTimestamp(item.createdAt)}</td>
                  <td>
                    <button type="button" className="v2-m5-secondary-button" onClick={() => void handleRevoke(item.id)}>
                      <Trash size={13} aria-hidden="true" /> 撤销
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <StatePanel state={state} message="尚未创建任何 API Key。" />
      )}
    </div>
  )
}
