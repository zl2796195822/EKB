import { describe, expect, it } from 'vitest'
import { ApiClientError } from '../../lib/api'
import { createAdminAdapter, type AdminApiClient } from '../adapters/admin'
import {
  buildTeamDirectoryCsv,
  canExportDirectory,
  canInviteMembers,
  validateInviteForm,
} from '../pages/teamDirectory'
import type { IdentityUserView } from '../types/v3Identity'

const users: IdentityUserView[] = [
  {
    id: 'user-1',
    name: '=SUM(1,1)',
    email: '+member@example.test',
    department: '产品,"平台"',
    role: 'MEMBER',
    roleId: 'role-member',
    status: 'ACTIVE',
    joinedAt: '2026-08-14T00:00:00Z',
    updatedAt: '2026-08-14T00:00:00Z',
  },
]

describe('team directory actions', () => {
  it('maps a real invite adapter success and failure without exposing the password', async () => {
    const successClient = {
      inviteUser: async () => ({
        id: 'user-2',
        email: 'new@example.test',
        name: 'New Member',
        role: 'MEMBER',
        tenant_id: 'tenant-1',
      }),
    } as unknown as AdminApiClient
    const success = await createAdminAdapter(successClient).inviteUser({
      email: 'new@example.test',
      name: 'New Member',
      password: 'one-time-secret',
      role: 'MEMBER',
    })
    expect(success.state).toBe('ready')
    expect(success.data?.tenantId).toBe('tenant-1')

    const failureClient = {
      inviteUser: async () => {
        throw new ApiClientError(403, 'PERMISSION_DENIED', '当前账号无权执行该操作', 'request-invite-1')
      },
    } as unknown as AdminApiClient
    const failure = await createAdminAdapter(failureClient).inviteUser({
      email: 'denied@example.test',
      name: 'Denied Member',
      password: 'not-persisted-secret',
      role: 'MEMBER',
    })
    expect(failure.state).toBe('permission-denied')
    expect(failure.error).toMatchObject({ code: 'PERMISSION_DENIED', requestId: 'request-invite-1' })
    expect(JSON.stringify(failure)).not.toContain('not-persisted-secret')
  })

  it('enables invite only for team managers and export only for ready data', () => {
    expect(canInviteMembers({
      tenantId: 'tenant-1',
      capabilities: ['team:user:manage'],
      tenants: [{ id: 'tenant-1', role: 'member' }],
    })).toBe(true)
    expect(canInviteMembers({
      tenantId: 'tenant-1',
      capabilities: [],
      tenants: [{ id: 'tenant-1', role: 'MEMBER' }],
    })).toBe(false)
    expect(canInviteMembers({
      tenantId: 'tenant-1',
      capabilities: [],
      tenants: [{ id: 'tenant-1', role: 'ADMIN' }],
    })).toBe(true)
    expect(canExportDirectory('loading', users)).toBe(false)
    expect(canExportDirectory('ready', [])).toBe(false)
    expect(canExportDirectory('ready', users)).toBe(true)
  })

  it('validates invite fields without accepting unsupported roles', () => {
    expect(validateInviteForm({
      email: ' member@example.test ',
      name: ' Member ',
      password: 'member-secret',
      role: 'MEMBER',
    })).toBeNull()
    expect(validateInviteForm({
      email: 'invalid',
      name: 'Member',
      password: 'member-secret',
      role: 'MEMBER',
    })).toContain('邮箱')
    expect(validateInviteForm({
      email: 'member@example.test',
      name: 'Member',
      password: 'short',
      role: 'OWNER',
    })).toContain('密码')
  })

  it('quotes RFC4180 cells and prefixes formula-like values', () => {
    const csv = buildTeamDirectoryCsv(users)

    expect(csv).toContain("\"'=SUM(1,1)\"")
    expect(csv).toContain('"\'+member@example.test"')
    expect(csv).toContain('"产品,""平台"""')
    expect(csv).toContain('"姓名","邮箱","部门","角色","状态","加入时间"\r\n')
    expect(csv).not.toContain('member-secret')
    expect(csv).not.toContain('tenant-1')
  })
})
