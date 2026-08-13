import type { IdentityUserView } from '../types/v3Identity'

export const TEAM_USER_MANAGE_CAPABILITY = 'team:user:manage'

export const INVITE_ROLE_OPTIONS = [
  { value: 'MEMBER', label: '成员' },
  { value: 'ADMIN', label: '管理员' },
] as const

export interface InviteFormValue {
  readonly email: string
  readonly name: string
  readonly password: string
  readonly role: string
}

export function validateInviteForm(input: InviteFormValue): string | null {
  const email = input.email.trim()
  const name = input.name.trim()
  if (!email || email.length > 254 || !/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(email)) {
    return '请输入有效的成员邮箱。'
  }
  if (!name || name.length > 255) {
    return '成员姓名长度应为 1–255 个字符。'
  }
  if (input.password.length < 8 || input.password.length > 256) {
    return '初始密码长度应为 8–256 个字符。'
  }
  if (!INVITE_ROLE_OPTIONS.some((option) => option.value === input.role)) {
    return '请选择有效的成员角色。'
  }
  return null
}

export function canInviteMembers(session: {
  readonly capabilities: readonly string[]
  readonly tenantId: string
  readonly tenants: readonly { readonly id: string; readonly role: string }[]
}): boolean {
  if (session.capabilities.includes(TEAM_USER_MANAGE_CAPABILITY)) return true
  const tenant = session.tenants.find((item) => item.id === session.tenantId)
  return tenant ? ['OWNER', 'ADMIN'].includes(tenant.role.toUpperCase()) : false
}

export function canExportDirectory(
  state: string,
  users: readonly IdentityUserView[],
): boolean {
  return state === 'ready' && users.length > 0
}

function quoteCsvCell(value: string): string {
  const safeValue = /^[=+\-@]/.test(value.trimStart()) ? `'${value}` : value
  return `"${safeValue.replaceAll('"', '""')}"`
}

export function buildTeamDirectoryCsv(users: readonly IdentityUserView[]): string {
  const rows = [
    ['姓名', '邮箱', '部门', '角色', '状态', '加入时间'],
    ...users.map((user) => [
      user.name,
      user.email,
      user.department ?? '',
      user.role,
      user.status,
      user.joinedAt,
    ]),
  ]
  return rows.map((row) => row.map(quoteCsvCell).join(',')).join('\r\n') + '\r\n'
}
