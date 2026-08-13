import { readFileSync } from 'node:fs'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { ApiClient, ApiClientError } from '../../lib/api'
import { PROFILE_CAPABILITIES, createProfileAdapter, type ProfileApiClient } from '../adapters/profile'
import type {
  ApiKeyCreateResponse,
  ApiKeysResponse,
  ChangePasswordPayload,
  MeProfileResponse,
  NotificationsResponse,
  PatchProfilePayload,
  PreferencesResponse,
  ProfileResponse,
  SessionRevokeResponse,
  SessionsResponse,
} from '../../types/api'

const meResponse: MeProfileResponse = {
  user: {
    id: 'user-1',
    name: '张运维',
    email: 'ops@jinbo.example',
    role: 'ADMIN',
    avatar_url: null,
    department: '运维中心',
  },
  tenant_id: 'tenant-1',
  role_id: 'role-admin',
  policy_version: 3,
  capabilities: ['kb.read', 'kb.write'],
  tenants: [{ id: 'tenant-1', name: '金博医药', role: 'ADMIN' }],
  profile: {
    display_name: '张运维',
    department: '运维中心',
    locale: 'zh-CN',
    timezone: 'Asia/Shanghai',
    avatar_url: null,
  },
}

const profileResponse: ProfileResponse = {
  profile: {
    display_name: '张三',
    department: '交付部',
    locale: 'zh-CN',
    timezone: 'Asia/Shanghai',
    avatar_url: null,
  },
}

const preferencesResponse: PreferencesResponse = {
  preferences: { theme: 'dark', density: 'compact' },
}

const sessionsResponse: SessionsResponse = {
  items: [
    {
      id: 'sess-1',
      ip_hash: 'ip-abc',
      ua_hash: 'ua-abc',
      status: 'ACTIVE',
      created_at: '2026-08-10T01:00:00Z',
      last_used_at: '2026-08-10T02:00:00Z',
    },
  ],
}

const apiKeysResponse: ApiKeysResponse = {
  items: [
    {
      id: 'key-1',
      prefix: 'ekb_live_ab',
      name: '集成测试',
      status: 'ACTIVE',
      created_at: '2026-08-09T10:00:00Z',
      last_used_at: null,
    },
  ],
}

/** 后端 POST /me/api-keys 不返回 last_used_at，适配器必须自己补 null。 */
const apiKeyCreateResponse: ApiKeyCreateResponse = {
  id: 'key-2',
  prefix: 'ekb_live_cd',
  name: '同步任务',
  secret: 'ekb_live_cd_secret_only_once',
  status: 'ACTIVE',
  created_at: '2026-08-10T03:00:00Z',
}

const notificationsResponse: NotificationsResponse = {
  items: [
    {
      id: 'notif-1',
      type: 'REVIEW',
      title: '有 3 条低置信回答待复核',
      body: null,
      metadata: null,
      priority: 'HIGH',
      read_at: null,
      created_at: '2026-08-10T04:00:00Z',
    },
  ],
}

interface Calls {
  patchPayloads: PatchProfilePayload[]
  passwordPayloads: ChangePasswordPayload[]
  preferencePayloads: Record<string, unknown>[]
  revokeCalls: { sessionId: string; revokeAll?: boolean }[]
  createKeyNames: string[]
  revokeKeyIds: string[]
  notificationFilters: (boolean | undefined)[]
  markReadIds: string[]
}

function createStubClient(overrides: Partial<ProfileApiClient> = {}): {
  client: ProfileApiClient
  calls: Calls
} {
  const calls: Calls = {
    patchPayloads: [],
    passwordPayloads: [],
    preferencePayloads: [],
    revokeCalls: [],
    createKeyNames: [],
    revokeKeyIds: [],
    notificationFilters: [],
    markReadIds: [],
  }

  const base: ProfileApiClient = {
    getMe: async () => meResponse,
    patchMeProfile: async (payload) => {
      calls.patchPayloads.push(payload)
      return profileResponse
    },
    changePassword: async (payload) => {
      calls.passwordPayloads.push(payload)
      return { status: 'ok' }
    },
    getPreferences: async () => preferencesResponse,
    patchPreferences: async (preferences) => {
      calls.preferencePayloads.push(preferences)
      return { preferences }
    },
    getSessions: async () => sessionsResponse,
    revokeSession: async (sessionId, revokeAll): Promise<SessionRevokeResponse> => {
      calls.revokeCalls.push({ sessionId, revokeAll })
      return revokeAll ? { status: 'ok', revoked_count: '4' } : { status: 'ok' }
    },
    getApiKeys: async () => apiKeysResponse,
    createApiKey: async (name) => {
      calls.createKeyNames.push(name)
      return apiKeyCreateResponse
    },
    revokeApiKey: async (keyId) => {
      calls.revokeKeyIds.push(keyId)
      return { status: 'ok' }
    },
    getNotifications: async (unreadOnly) => {
      calls.notificationFilters.push(unreadOnly)
      return notificationsResponse
    },
    markNotificationRead: async (notificationId) => {
      calls.markReadIds.push(notificationId)
      return { status: 'ok' }
    },
  }

  return { client: { ...base, ...overrides }, calls }
}

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('v3 profile adapter', () => {
  it('maps GET /me into the app-v2 view without fabricating missing profile fields', async () => {
    const { client } = createStubClient()
    const me = await createProfileAdapter(client).getMe()

    expect(me.state).toBe('ready')
    expect(me.data).toMatchObject({
      userId: 'user-1',
      email: 'ops@jinbo.example',
      tenantId: 'tenant-1',
      roleId: 'role-admin',
      policyVersion: 3,
    })
    expect(me.data?.profile).toEqual({
      displayName: '张运维',
      department: '运维中心',
      locale: 'zh-CN',
      timezone: 'Asia/Shanghai',
      avatarUrl: null,
    })
  })

  it('only sends profile fields the caller explicitly provided', async () => {
    const { client, calls } = createStubClient()
    const updated = await createProfileAdapter(client).updateProfile({ displayName: '张三', department: '交付部' })

    expect(updated.state).toBe('ready')
    expect(updated.data?.displayName).toBe('张三')
    expect(calls.patchPayloads[0]).toEqual({ display_name: '张三', department: '交付部' })
    expect(calls.patchPayloads[0]).not.toHaveProperty('locale')
    expect(calls.patchPayloads[0]).not.toHaveProperty('avatar_url')
  })

  it('forwards the snake_case password contract', async () => {
    const { client, calls } = createStubClient()
    const result = await createProfileAdapter(client).changePassword({
      oldPassword: 'old-pass',
      newPassword: 'new-pass',
    })

    expect(result.state).toBe('ready')
    expect(calls.passwordPayloads[0]).toEqual({ old_password: 'old-pass', new_password: 'new-pass' })
  })

  it('reads and writes preferences as the raw server object', async () => {
    const { client, calls } = createStubClient()
    const profile = createProfileAdapter(client)

    const prefs = await profile.getPreferences()
    expect(prefs.state).toBe('ready')
    expect(prefs.data).toEqual({ theme: 'dark', density: 'compact' })

    const saved = await profile.updatePreferences({ theme: 'light' })
    expect(saved.data).toEqual({ theme: 'light' })
    expect(calls.preferencePayloads[0]).toEqual({ theme: 'light' })
  })

  it('separates single-session revoke from revoke-all on the shared endpoint', async () => {
    const { client, calls } = createStubClient()
    const profile = createProfileAdapter(client)

    const sessions = await profile.listSessions()
    expect(sessions.state).toBe('ready')
    expect(sessions.data?.[0]).toEqual({
      id: 'sess-1',
      ipHash: 'ip-abc',
      uaHash: 'ua-abc',
      status: 'ACTIVE',
      createdAt: '2026-08-10T01:00:00Z',
      lastUsedAt: '2026-08-10T02:00:00Z',
    })

    const one = await profile.revokeSession('sess-1')
    expect(one.state).toBe('ready')
    expect(calls.revokeCalls[0]).toEqual({ sessionId: 'sess-1', revokeAll: false })

    const all = await profile.revokeAllSessions()
    expect(all.state).toBe('ready')
    expect(all.revokedCount).toBe(4)
    expect(calls.revokeCalls[1]?.revokeAll).toBe(true)

    const blank = await profile.revokeSession('   ')
    expect(blank.state).toBe('error')
    expect(calls.revokeCalls).toHaveLength(2)
  })

  it('surfaces the api key secret exactly once and defaults a missing last_used_at', async () => {
    const { client, calls } = createStubClient()
    const profile = createProfileAdapter(client)

    const keys = await profile.listApiKeys()
    expect(keys.state).toBe('ready')
    expect(keys.data?.[0]).toMatchObject({ prefix: 'ekb_live_ab', lastUsedAt: null })

    const created = await profile.createApiKey('  同步任务  ')
    expect(created.state).toBe('ready')
    expect(calls.createKeyNames).toEqual(['同步任务'])
    expect(created.secret).toBe('ekb_live_cd_secret_only_once')
    expect(created.data?.lastUsedAt).toBeNull()
    expect(created.data).not.toHaveProperty('secret')

    const blank = await profile.createApiKey('   ')
    expect(blank.state).toBe('error')
    expect(calls.createKeyNames).toHaveLength(1)

    const revoked = await profile.revokeApiKey('key-1')
    expect(revoked.state).toBe('ready')
    expect(calls.revokeKeyIds).toEqual(['key-1'])
  })

  it('forwards the unread notification filter and the mark-read id', async () => {
    const { client, calls } = createStubClient()
    const profile = createProfileAdapter(client)

    const list = await profile.listNotifications()
    expect(list.state).toBe('ready')
    expect(list.data?.[0]).toMatchObject({ priority: 'HIGH', readAt: null })
    expect(calls.notificationFilters[0]).toBe(false)

    await profile.listNotifications(true)
    expect(calls.notificationFilters[1]).toBe(true)

    const marked = await profile.markNotificationRead('notif-1')
    expect(marked.state).toBe('ready')
    expect(calls.markReadIds).toEqual(['notif-1'])
  })

  it('maps empty collections to empty so pages render an empty state instead of fake rows', async () => {
    const { client } = createStubClient({
      getSessions: async () => ({ items: [] }),
      getApiKeys: async () => ({ items: [] }),
      getNotifications: async () => ({ items: [] }),
    })
    const profile = createProfileAdapter(client)

    expect((await profile.listSessions()).state).toBe('empty')
    expect((await profile.listApiKeys()).state).toBe('empty')
    expect((await profile.listNotifications()).state).toBe('empty')
  })

  it('maps 403 to permission-denied and other failures to error', async () => {
    const { client: deniedClient } = createStubClient({
      getMe: async () => {
        throw new ApiClientError(403, 'PERMISSION_DENIED', '当前账号无权查看个人资料', 'request-profile-1')
      },
    })
    const denied = await createProfileAdapter(deniedClient).getMe()
    expect(denied.state).toBe('permission-denied')
    expect(denied.error).toMatchObject({
      code: 'PERMISSION_DENIED',
      status: 403,
      requestId: 'request-profile-1',
    })

    const { client: brokenClient } = createStubClient({
      getPreferences: async () => {
        throw new ApiClientError(500, 'INTERNAL', '偏好设置服务异常')
      },
    })
    const broken = await createProfileAdapter(brokenClient).getPreferences()
    expect(broken.state).toBe('error')
    expect(broken.error?.message).toBe('偏好设置服务异常')
  })

  it('serializes the /me family contract in ApiClient', async () => {
    const requests: { url: string; method: string; body?: string }[] = []
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      requests.push({
        url: String(input),
        method: init?.method ?? 'GET',
        body: typeof init?.body === 'string' ? init.body : undefined,
      })
      return new Response(JSON.stringify({ items: [], status: 'ok', preferences: {} }), {
        status: 200,
        headers: { 'content-type': 'application/json' },
      })
    })
    vi.stubGlobal('fetch', fetchMock)

    const client = new ApiClient('/api/v1')
    await client.getNotifications(true)
    await client.revokeSession('sess/1', true)
    await client.markNotificationRead('notif-1')

    const notificationsUrl = new URL(requests[0]!.url, 'http://example.test')
    expect(notificationsUrl.pathname).toBe('/api/v1/me/notifications')
    expect(notificationsUrl.searchParams.get('unread_only')).toBe('true')

    const revokeUrl = new URL(requests[1]!.url, 'http://example.test')
    expect(revokeUrl.pathname).toBe('/api/v1/me/sessions/sess%2F1')
    expect(revokeUrl.searchParams.get('revoke_all')).toBe('true')
    expect(requests[1]!.method).toBe('DELETE')

    // CORS 只放行 GET/POST/PATCH/DELETE/OPTIONS，标记已读必须是 PATCH 而不是 PUT。
    expect(requests[2]!.method).toBe('PATCH')
    expect(new URL(requests[2]!.url, 'http://example.test').pathname).toBe('/api/v1/me/notifications/notif-1')
  })

  it('keeps ProfilePage behind the profile service boundary', () => {
    const source = readFileSync(new URL('../pages/ProfilePage.tsx', import.meta.url), 'utf8')

    expect(source).toContain('services.profile')
    expect(source).not.toMatch(/src\/lib\/api|from '\.\.\/\.\.\/lib\/api'/)
    expect(source).not.toMatch(/\bfetch\s*\(/)
    // M0 规则：页面禁止内联 style，视觉一律走 CSS 变量与类名。
    expect(source).not.toMatch(/style=\{\{/)
  })

  it('declares avatar upload as unavailable because the backend only accepts a URL string', () => {
    const statusOf = (id: string): string | undefined =>
      PROFILE_CAPABILITIES.find((capability) => capability.id === id)?.status

    expect(statusOf('profile.read')).toBe('available')
    expect(statusOf('profile.update')).toBe('available')
    expect(statusOf('profile.password-change')).toBe('available')
    expect(statusOf('profile.preferences')).toBe('available')
    expect(statusOf('profile.sessions')).toBe('available')
    expect(statusOf('profile.api-keys')).toBe('available')
    expect(statusOf('profile.notifications')).toBe('available')
    expect(statusOf('profile.avatar-upload')).toBe('unavailable')
  })
})
