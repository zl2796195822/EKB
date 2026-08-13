import {
  ArrowRight,
  DownloadSimple,
  FunnelSimple,
  LockKey,
  ShieldCheck,
  UserPlus,
  UsersThree,
} from '@phosphor-icons/react'
import { useCallback, useEffect, useRef, useState } from 'react'
import { StatePanel } from '../components/StatePanel'
import { UserAvatar } from '../components/ui/UserAvatar'
import type {
  AdapterError,
  IdentityListUsersInput,
  IdentityUserView,
  PageState,
  V2PageProps,
} from '../types'

type TeamTab = 'members' | 'roles' | 'permissions'

const ALL_FILTER = 'ALL'
const PAGE_SIZE = 20

const ROLE_OPTIONS = [
  { value: 'owner', label: '所有者' },
  { value: 'admin', label: '管理员' },
  { value: 'member', label: '成员' },
  { value: 'auditor', label: '审计员' },
  { value: 'customer', label: '客户' },
] as const

const STATUS_OPTIONS = [
  { value: 'ACTIVE', label: '活跃' },
  { value: 'INVITED', label: '已邀请' },
  { value: 'SUSPENDED', label: '已暂停' },
] as const

const ROLE_LABELS: Record<string, string> = Object.fromEntries(
  ROLE_OPTIONS.flatMap(({ value, label }) => [[value, label], [value.toUpperCase(), label], ...(value === 'customer' ? [['legacy_customer', label]] : [])]),
)

const STATUS_LABELS: Record<string, string> = Object.fromEntries(
  STATUS_OPTIONS.flatMap(({ value, label }) => [[value, label], [value.toLowerCase(), label]]),
)

function formatDate(value: string): string {
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? value : date.toLocaleDateString('zh-CN')
}

function roleLabel(role: string): string {
  return ROLE_LABELS[role] ?? ROLE_LABELS[role.toLowerCase()] ?? role
}

function statusLabel(status: string): string {
  return STATUS_LABELS[status] ?? STATUS_LABELS[status.toUpperCase()] ?? status
}

function statusStyle(status: string): { readonly background: string; readonly color: string } {
  switch (status.toUpperCase()) {
    case 'ACTIVE':
      return { background: 'var(--v2-color-secondary-soft)', color: '#277e5c' }
    case 'SUSPENDED':
      return { background: 'var(--v2-color-danger-soft)', color: '#a33f4a' }
    case 'INVITED':
      return { background: 'var(--v2-color-warning-soft)', color: '#88611b' }
    default:
      return { background: 'var(--v2-color-primary-soft)', color: 'var(--v2-color-primary)' }
  }
}

function errorMeta(error?: AdapterError): string {
  if (!error) return ''
  return [
    error.code ? `code ${error.code}` : '',
    error.requestId ? `request ${error.requestId}` : '',
  ].filter(Boolean).join(' · ')
}

export function TeamPage({ services, session }: V2PageProps) {
  const [activeTab, setActiveTab] = useState<TeamTab>('members')
  const [usersState, setUsersState] = useState<PageState>('loading')
  const [users, setUsers] = useState<readonly IdentityUserView[]>([])
  const [query, setQuery] = useState('')
  const [role, setRole] = useState(ALL_FILTER)
  const [status, setStatus] = useState(ALL_FILTER)
  const [nextCursor, setNextCursor] = useState<string | null>(null)
  const [error, setError] = useState<AdapterError | undefined>()
  const requestVersion = useRef(0)
  const activeController = useRef<AbortController | null>(null)

  const loadUsers = useCallback(async (cursor: string | null, append: boolean) => {
    activeController.current?.abort()
    const controller = new AbortController()
    activeController.current = controller
    const currentVersion = ++requestVersion.current

    setUsersState('loading')
    setError(undefined)
    if (!append) {
      setUsers([])
      setNextCursor(null)
    }

    const input: IdentityListUsersInput = {
      query,
      role,
      status,
      sort: 'updated_at_desc',
      cursor: cursor ?? undefined,
      pageSize: PAGE_SIZE,
      signal: controller.signal,
    }
    const result = await services.identity.listUsers(session.tenantId, input)

    if (currentVersion !== requestVersion.current) return

    setUsersState(result.state)
    setError(result.error)
    setUsers((current) => append ? [...current, ...(result.data?.items ?? [])] : (result.data?.items ?? []))
    setNextCursor(result.data?.nextCursor ?? null)
  }, [query, role, services.identity, session.tenantId, status])

  useEffect(() => {
    void loadUsers(null, false)
    return () => {
      requestVersion.current += 1
      activeController.current?.abort()
    }
  }, [loadUsers])

  const loadNextPage = useCallback(() => {
    if (!nextCursor || usersState === 'loading') return
    void loadUsers(nextCursor, true)
  }, [loadUsers, nextCursor, usersState])

  return (
    <div className="v2-m5-page">
      <header className="v2-m5-page-heading">
        <div>
          <p className="v2-eyebrow">KNOWLEDGE BASE / WORKSPACE</p>
          <h1>团队与权限</h1>
          <p>管理团队成员、角色与知识空间的访问权限。</p>
        </div>
        <div className="v2-m5-heading-actions">
          <button type="button" className="v2-m5-secondary-button" disabled title="后续里程碑开放：团队目录导出">
            <DownloadSimple size={15} aria-hidden="true" />
            导出
          </button>
          <button type="button" className="v2-m5-primary-button" disabled title="后续里程碑开放：用户邀请">
            <UserPlus size={15} aria-hidden="true" />
            邀请成员
          </button>
        </div>
      </header>

      <section className="v2-m5-team-card" aria-label="团队与权限管理">
        <div className="v2-m5-card-topline">
          <div className="v2-m5-tabs" role="tablist" aria-label="团队权限视图">
            <button type="button" id="team-tab-members" role="tab" aria-selected={activeTab === 'members'} aria-controls="team-panel-members" className={activeTab === 'members' ? 'is-active' : ''} onClick={() => setActiveTab('members')}>成员管理</button>
            <button type="button" id="team-tab-roles" role="tab" aria-selected={activeTab === 'roles'} aria-controls="team-panel-roles" className={activeTab === 'roles' ? 'is-active' : ''} onClick={() => setActiveTab('roles')}>角色管理</button>
            <button type="button" id="team-tab-permissions" role="tab" aria-selected={activeTab === 'permissions'} aria-controls="team-panel-permissions" className={activeTab === 'permissions' ? 'is-active' : ''} onClick={() => setActiveTab('permissions')}>权限矩阵</button>
          </div>
        </div>

        {activeTab === 'members' ? (
          <DirectoryPanel
            state={usersState}
            users={users}
            query={query}
            role={role}
            status={status}
            nextCursor={nextCursor}
            error={error}
            onQueryChange={setQuery}
            onRoleChange={setRole}
            onStatusChange={setStatus}
            onNextPage={loadNextPage}
          />
        ) : null}
        {activeTab === 'roles' ? <RolesPanel session={session} /> : null}
        {activeTab === 'permissions' ? <PermissionsPanel session={session} /> : null}
      </section>
    </div>
  )
}

interface DirectoryPanelProps {
  readonly state: PageState
  readonly users: readonly IdentityUserView[]
  readonly query: string
  readonly role: string
  readonly status: string
  readonly nextCursor: string | null
  readonly error?: AdapterError
  readonly onQueryChange: (value: string) => void
  readonly onRoleChange: (value: string) => void
  readonly onStatusChange: (value: string) => void
  readonly onNextPage: () => void
}

function DirectoryPanel({ state, users, query, role, status, nextCursor, error, onQueryChange, onRoleChange, onStatusChange, onNextPage }: DirectoryPanelProps) {
  return (
    <div id="team-panel-members" className="v2-m5-tab-panel" role="tabpanel" aria-labelledby="team-tab-members">
      <div className="v2-m5-panel-intro">
        <div><h2>成员管理</h2><p>显示当前租户真实用户目录；搜索、角色和状态筛选由服务端处理。</p></div>
        <span className="v2-m5-record-count">{state === 'ready' ? `${users.length} 条已加载` : '等待真实用户目录'}</span>
      </div>

      <div className="v2-m5-toolbar">
        <label className="v2-m5-search-field">
          <UsersThree size={15} aria-hidden="true" />
          <span className="v2-sr-only">搜索成员姓名、邮箱或部门</span>
          <input value={query} onChange={(event) => onQueryChange(event.target.value)} placeholder="搜索成员姓名、邮箱…" aria-label="搜索成员姓名、邮箱或部门" disabled={state === 'loading'} />
        </label>
        <label className="v2-m5-filter-field">
          <span>角色</span>
          <select value={role} onChange={(event) => onRoleChange(event.target.value)} disabled={state === 'loading'} aria-label="按角色筛选">
            <option value={ALL_FILTER}>全部角色</option>
            {ROLE_OPTIONS.map((option) => <option value={option.value} key={option.value}>{option.label}</option>)}
          </select>
        </label>
        <label className="v2-m5-filter-field">
          <FunnelSimple size={14} aria-hidden="true" />
          <span>状态</span>
          <select value={status} onChange={(event) => onStatusChange(event.target.value)} disabled={state === 'loading'} aria-label="按状态筛选">
            <option value={ALL_FILTER}>全部状态</option>
            {STATUS_OPTIONS.map((option) => <option value={option.value} key={option.value}>{option.label}</option>)}
          </select>
        </label>
      </div>

      <div className="v2-m5-table-note"><LockKey size={14} aria-hidden="true" />用户资料和租户成员状态来自 identity-v3 目录，只读展示。</div>
      {state === 'loading' ? <StatePanel state="loading" message="正在加载当前租户的真实用户目录。" /> : null}
      {state === 'permission-denied' || state === 'error' ? <StatePanel state={state} message="用户目录加载失败，未使用本地示例数据替代。" reason={errorMeta(error)} /> : null}
      {state === 'empty' ? <StatePanel state="empty" message="当前筛选条件没有匹配的真实租户用户。" /> : null}
      {state === 'ready' && users.length > 0 ? <UserTable users={users} /> : null}

      {nextCursor ? (
        <div style={{ display: 'flex', justifyContent: 'center', marginTop: '16px' }}>
          <button type="button" className="v2-m5-secondary-button" onClick={onNextPage} disabled={state === 'loading'}>加载下一页</button>
        </div>
      ) : null}
    </div>
  )
}

function UserTable({ users }: { readonly users: readonly IdentityUserView[] }) {
  return (
    <div className="v2-m5-table-wrap">
      <table className="v2-m5-table">
        <thead><tr><th scope="col">成员</th><th scope="col">所属部门</th><th scope="col">角色</th><th scope="col">状态</th><th scope="col">加入时间</th><th scope="col">操作</th></tr></thead>
        <tbody>
          {users.map((user) => (
            <tr key={user.id}>
              <td>
                <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                  <UserAvatar name={user.name} compact />
                  <span><strong>{user.name}</strong><small>{user.email}</small></span>
                </div>
              </td>
              <td>{user.department || '未设置'}</td>
              <td><span className="v2-m5-role-pill">{roleLabel(user.role)}</span></td>
              <td><span className="v2-m5-role-pill" style={statusStyle(user.status)}>{statusLabel(user.status)}</span></td>
              <td><time dateTime={user.joinedAt}>{formatDate(user.joinedAt)}</time></td>
              <td><button type="button" className="v2-m5-icon-action" disabled title="后续里程碑开放：用户操作" aria-label={`${user.name} 的用户操作`}><ArrowRight size={15} aria-hidden="true" /></button></td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

function RolesPanel({ session }: { readonly session: V2PageProps['session'] }) {
  const currentTenant = session.tenants.find((tenant) => tenant.id === session.tenantId) ?? session.tenants[0]
  return (
    <div id="team-panel-roles" className="v2-m5-tab-panel" role="tabpanel" aria-labelledby="team-tab-roles">
      <div className="v2-m5-panel-intro"><div><h2>角色管理</h2><p>v4 P1 身份能力：自定义角色、角色继承与 RBAC 规则编辑规划中；当前读取 auth session 返回的真实角色。</p></div><ShieldCheck size={24} aria-hidden="true" /></div>
      <div className="v2-m5-fact-grid"><div><span>当前主体</span><strong>{session.user.id}</strong></div><div><span>当前租户</span><strong>{currentTenant?.name ?? '—'}</strong></div><div><span>租户角色</span><strong>{currentTenant?.role ?? '—'}</strong></div><div><span>policyVersion</span><strong>{session.policyVersion}</strong></div></div>
      <div className="v2-m5-unavailable-card">
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 8 }}>
          <span style={{ padding: '2px 8px', borderRadius: 999, fontSize: 10, fontWeight: 500, background: '#EFF6FF', color: '#1D4ED8' }}>v4 P1 · 身份能力</span>
          <span style={{ padding: '2px 8px', borderRadius: 999, fontSize: 10, fontWeight: 500, background: '#FEF3C7', color: '#92400E' }}>已授权只读</span>
        </div>
        <strong>角色 CRUD 暂不开放</strong>
        <p>当前里程碑不写 role 表，不修改 policyVersion；角色编辑、角色继承和批量分配预计在 v4 P1 身份平台里程碑开放。</p>
        <button type="button" className="v2-m5-secondary-button" disabled title="v4 P1 开放：角色模板、继承层级、批量分配与 role_version 变更审计">编辑角色</button>
      </div>
    </div>
  )
}

function PermissionsPanel({ session }: { readonly session: V2PageProps['session'] }) {
  const currentTenant = session.tenants.find((tenant) => tenant.id === session.tenantId) ?? session.tenants[0]
  return (
    <div id="team-panel-permissions" className="v2-m5-tab-panel" role="tabpanel" aria-labelledby="team-tab-permissions">
      <div className="v2-m5-panel-intro"><div><h2>权限矩阵</h2><p>只读展示当前会话的租户、主体与 capabilities；权限矩阵编辑在 v4 P3 企业身份平台里程碑开放。</p></div><LockKey size={24} aria-hidden="true" /></div>
      <div className="v2-m5-policy-card"><div><span>tenant</span><strong>{currentTenant ? `${currentTenant.name} · ${currentTenant.id}` : '—'}</strong></div><div><span>subject</span><strong>{session.subjectId}</strong></div><div><span>capabilities</span><strong>{session.capabilities.length} 项真实能力</strong></div></div>
      <div className="v2-m5-capability-block"><h3>当前 capabilities</h3><div className="v2-m5-capability-list">{session.capabilities.length > 0 ? session.capabilities.map((capability) => <span key={capability}>{capability}</span>) : <span className="v2-m5-unavailable">暂无能力声明</span>}</div></div>
      <div className="v2-m5-unavailable-card">
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 8 }}>
          <span style={{ padding: '2px 8px', borderRadius: 999, fontSize: 10, fontWeight: 500, background: '#ECFDF5', color: '#047857' }}>v4 P3 · 企业身份</span>
          <span style={{ padding: '2px 8px', borderRadius: 999, fontSize: 10, fontWeight: 500, background: '#FEF3C7', color: '#92400E' }}>已授权只读</span>
        </div>
        <strong>权限写入暂不开放</strong>
        <p>当前不修改用户状态、角色或租户权限；capability 矩阵编辑、用户批量导入导出与状态切换预计在 v4 P3 企业身份与连接器平台里程碑开放。</p>
        <button type="button" className="v2-m5-secondary-button" disabled title="v4 P3 开放：capability 矩阵编辑、SCIM 同步、批量邀请/禁用/导出审计">编辑权限</button>
      </div>
    </div>
  )
}
