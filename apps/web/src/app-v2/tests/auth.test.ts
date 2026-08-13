import { APP_V2_SESSION_STORAGE_KEY, createAuthAdapter, type AuthApiClient, type AuthStorage } from '../adapters/auth'
import type { LoginResponse, MeResponse, RefreshResponse } from '../../types/api'

class MemoryStorage implements AuthStorage {
  private readonly values = new Map<string, string>()

  getItem(key: string): string | null {
    return this.values.get(key) ?? null
  }

  removeItem(key: string): void {
    this.values.delete(key)
  }

  setItem(key: string, value: string): void {
    this.values.set(key, value)
  }
}

class FakeAuthClient implements AuthApiClient {
  loginResponse: LoginResponse = {
    access_token: 'server-issued-access-token',
    expires_in: 900,
    refresh_token: 'server-issued-refresh-token',
    token_type: 'bearer',
    user: { id: 'user-1', name: '真实用户', email: 'user@example.test' },
    tenants: [{ id: 'tenant-1', name: '真实租户', role: 'OWNER' }],
  }

  refreshResponse: RefreshResponse = {
    access_token: 'server-issued-refreshed-access-token',
    expires_in: 900,
    token_type: 'bearer',
  }

  meResponse: MeResponse = {
    user: { id: 'me-user-1', name: '服务端主体', email: 'me-user@example.test' },
    tenants: [{ id: 'me-tenant-1', name: '服务端租户', role: 'ADMIN' }],
    capabilities: ['kb:read', 'kb:write'],
    policy_version: 7,
  }

  logoutShouldFail = false
  meShouldFail = false
  refreshCalls: number = 0
  meCalls: number = 0
  logoutCalls: number = 0
  events: string[] = []
  token: string | null = null
  refreshToken: string | null = null
  authFailureHandler: (() => void) | null = null

  async login(): Promise<LoginResponse> {
    this.events.push('login')
    return this.loginResponse
  }

  async refresh(): Promise<RefreshResponse> {
    this.events.push('refresh')
    this.refreshCalls += 1
    return this.refreshResponse
  }

  async getMe(): Promise<MeResponse> {
    this.events.push('getMe')
    this.meCalls += 1
    if (this.meShouldFail) throw new Error('me unavailable')
    return this.meResponse
  }

  async logout(): Promise<void> {
    this.events.push('logout')
    this.logoutCalls += 1
    if (this.logoutShouldFail) throw new Error('service unavailable')
  }

  setToken(token: string | null): void {
    this.token = token
  }

  setRefreshToken(token: string | null): void {
    this.refreshToken = token
  }

  setOnAuthFailure(handler: (() => void) | null): void {
    this.authFailureHandler = handler
  }
}

function assert(condition: unknown, message: string): asserts condition {
  if (!condition) throw new Error(message)
}

export async function runAuthAdapterTests(): Promise<void> {
  const storage = new MemoryStorage()
  const client = new FakeAuthClient()
  const adapter = createAuthAdapter(client, storage)

  const loggedIn = await adapter.login('user@example.test', 'password-from-form')
  assert(loggedIn.subjectId === 'me-user-1', 'login should return the getMe subject')
  assert(loggedIn.tenantId === 'me-tenant-1', 'login should return the getMe tenant')
  assert(loggedIn.capabilities.join(',') === 'kb:read,kb:write', 'login should return getMe capabilities')
  assert(loggedIn.policyVersion === 7, 'login should return the getMe policy version')
  const loginMeCalls = client.meCalls
  assert(loginMeCalls === 1, 'login should validate the session with getMe immediately')
  assert(client.events.join('>') === 'login>getMe', 'login should call getMe after setting the session')
  assert(storage.getItem(APP_V2_SESSION_STORAGE_KEY) !== null, 'login should persist the app-v2 session')

  const restored = await adapter.restore()
  assert(restored?.tenantId === 'me-tenant-1', 'restore should retain the authorized tenant')
  assert(client.refreshCalls === 1, 'restore should refresh before validating the session')
  const restoreMeCalls = client.meCalls
  assert(restoreMeCalls === 2, 'restore should validate the refreshed session with getMe')
  assert(client.events.join('>') === 'login>getMe>refresh>getMe', 'restore should call refresh before getMe')

  client.logoutShouldFail = true
  const logoutResult = await adapter.logout()
  assert(logoutResult.error !== undefined, 'logout should surface a service error')
  assert(client.logoutCalls === 1, 'logout should call the real logout endpoint even when it fails')
  assert(client.events.at(-1) === 'logout', 'logout should be the final service call')
  assert(storage.getItem(APP_V2_SESSION_STORAGE_KEY) === null, 'logout should clear the app-v2 session after failure')
  assert(client.token === null && client.refreshToken === null, 'logout should clear client credentials')

  const failedStorage = new MemoryStorage()
  const failedClient = new FakeAuthClient()
  failedClient.meShouldFail = true
  const failedAdapter = createAuthAdapter(failedClient, failedStorage)
  let loginFailed = false
  try {
    await failedAdapter.login('user@example.test', 'password-from-form')
  } catch {
    loginFailed = true
  }
  assert(loginFailed, 'login should fail when immediate getMe validation fails')
  assert(failedStorage.getItem(APP_V2_SESSION_STORAGE_KEY) === null, 'failed login should clear storage')
  assert(failedClient.token === null && failedClient.refreshToken === null, 'failed login should clear client credentials')
}
