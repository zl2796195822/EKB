import { ApiClient } from '../../lib/api'
import type {
  LoginResponse,
  MeResponse,
  RefreshResponse,
  TenantSummary,
  UserSummary,
} from '../../types/api'
import type { AuthSession, AuthTenant, AuthUser } from '../types'

export type { AuthSession } from '../types'

export interface AuthLoginInput {
  readonly email: string
  readonly password: string
}

export const AUTH_CAPABILITIES = [
  { id: 'auth.login-refresh-logout', status: 'available' },
  { id: 'auth.me', status: 'available' },
] as const

const API_BASE_URL =
  (import.meta as ImportMeta & { readonly env?: { readonly VITE_API_BASE_URL?: string } }).env
    ?.VITE_API_BASE_URL ?? '/api/v1'
export const APP_V2_SESSION_STORAGE_KEY = 'ekb.app-v2.session'

export interface AuthStorage {
  getItem(key: string): string | null
  removeItem(key: string): void
  setItem(key: string, value: string): void
}

interface PersistedAuthSession {
  readonly accessToken: string
  readonly refreshToken: string
  readonly accessTokenExpiresAt: string
  readonly subjectId: string
  readonly tenantId: string
  readonly user: AuthUser
  readonly tenants: readonly AuthTenant[]
  readonly capabilities: readonly string[]
  readonly policyVersion: number
}

export interface AuthApiClient {
  login(email: string, password: string): Promise<LoginResponse>
  refresh(refreshToken: string): Promise<RefreshResponse>
  logout(refreshToken: string): Promise<void>
  getMe(): Promise<MeResponse>
  setToken(token: string | null): void
  setRefreshToken(token: string | null): void
  setOnAuthFailure(handler: (() => void) | null): void
}

export interface AuthLogoutResult {
  readonly error?: string
}

export interface AuthAdapter {
  readonly login: (email: string, password: string) => Promise<AuthSession>
  readonly restore: () => Promise<AuthSession | null>
  readonly refresh: () => Promise<AuthSession>
  readonly getMe: () => Promise<AuthSession>
  readonly logout: () => Promise<AuthLogoutResult>
  readonly clear: () => void
  readonly setOnAuthFailure: (handler: (() => void) | null) => void
}

const LOGOUT_ERROR_MESSAGE = '退出登录请求未完成，会话已在本地清理。'

/**
 * Creates the single ApiClient boundary owned by app-v2.
 * AppV2 injects this instance into auth now and can pass the same instance
 * to future app-v2 adapters so token refresh and auth-failure handling stay shared.
 */
export function createAppV2ApiClient(): ApiClient {
  return new ApiClient(API_BASE_URL)
}

function getBrowserStorage(): AuthStorage | null {
  if (typeof window === 'undefined') return null
  try {
    return window.sessionStorage
  } catch {
    return null
  }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null
}

function isNonEmptyString(value: unknown): value is string {
  return typeof value === 'string' && value.trim().length > 0
}

function isUser(value: unknown): value is AuthUser {
  return (
    isRecord(value) &&
    isNonEmptyString(value.id) &&
    typeof value.name === 'string' &&
    isNonEmptyString(value.email)
  )
}

function isTenant(value: unknown): value is AuthTenant {
  return (
    isRecord(value) &&
    isNonEmptyString(value.id) &&
    typeof value.name === 'string' &&
    isNonEmptyString(value.role)
  )
}

function isPersistedSession(value: unknown): value is PersistedAuthSession {
  return (
    isRecord(value) &&
    isNonEmptyString(value.accessToken) &&
    isNonEmptyString(value.refreshToken) &&
    isNonEmptyString(value.accessTokenExpiresAt) &&
    isNonEmptyString(value.subjectId) &&
    typeof value.tenantId === 'string' &&
    isUser(value.user) &&
    Array.isArray(value.tenants) &&
    value.tenants.every(isTenant) &&
    Array.isArray(value.capabilities) &&
    value.capabilities.every((capability) => typeof capability === 'string') &&
    typeof value.policyVersion === 'number'
  )
}

function toUser(user: UserSummary): AuthUser {
  return {
    id: user.id,
    name: user.name,
    email: user.email,
  }
}

function toTenant(tenant: TenantSummary): AuthTenant {
  return {
    id: tenant.id,
    name: tenant.name,
    role: tenant.role,
  }
}

function createSession(
  accessToken: string,
  refreshToken: string,
  expiresIn: number,
  user: UserSummary,
  tenants: readonly TenantSummary[],
  capabilities: readonly string[] = [],
  policyVersion = 0,
): PersistedAuthSession {
  const mappedUser = toUser(user)
  const mappedTenants = tenants.map(toTenant)

  return {
    accessToken,
    refreshToken,
    accessTokenExpiresAt: new Date(Date.now() + expiresIn * 1000).toISOString(),
    subjectId: mappedUser.id,
    tenantId: mappedTenants[0]?.id ?? '',
    user: mappedUser,
    tenants: mappedTenants,
    capabilities: [...capabilities],
    policyVersion,
  }
}

function publicSession(session: PersistedAuthSession): AuthSession {
  return {
    subjectId: session.subjectId,
    tenantId: session.tenantId,
    accessTokenExpiresAt: session.accessTokenExpiresAt,
    user: session.user,
    tenants: session.tenants,
    capabilities: session.capabilities,
    policyVersion: session.policyVersion,
  }
}

function readStoredSession(storage: AuthStorage | null): PersistedAuthSession | null {
  if (!storage) return null

  try {
    const raw = storage.getItem(APP_V2_SESSION_STORAGE_KEY)
    if (!raw) return null
    const parsed: unknown = JSON.parse(raw)
    return isPersistedSession(parsed) ? parsed : null
  } catch {
    return null
  }
}

function persistSession(storage: AuthStorage | null, session: PersistedAuthSession): void {
  if (!storage) return
  storage.setItem(APP_V2_SESSION_STORAGE_KEY, JSON.stringify(session))
}

function sessionFromMe(
  current: PersistedAuthSession,
  me: MeResponse,
): PersistedAuthSession {
  const user = toUser(me.user)
  const tenants = me.tenants.map(toTenant)

  return {
    ...current,
    subjectId: user.id,
    tenantId: tenants[0]?.id ?? '',
    user,
    tenants,
    capabilities: [...me.capabilities],
    policyVersion: me.policy_version,
  }
}

export function createAuthAdapter(
  client: AuthApiClient = createAppV2ApiClient(),
  storage: AuthStorage | null = getBrowserStorage(),
): AuthAdapter {
  const clear = () => {
    client.setToken(null)
    client.setRefreshToken(null)
    storage?.removeItem(APP_V2_SESSION_STORAGE_KEY)
  }

  const applySession = (session: PersistedAuthSession) => {
    client.setToken(session.accessToken)
    client.setRefreshToken(session.refreshToken)
  }

  const login = async (email: string, password: string): Promise<AuthSession> => {
    clear()
    const response = await client.login(email, password)
    if (!isNonEmptyString(response.access_token) || !isNonEmptyString(response.refresh_token)) {
      throw new Error('登录响应缺少有效会话信息')
    }

    const session = createSession(
      response.access_token,
      response.refresh_token,
      response.expires_in,
      response.user,
      response.tenants,
    )
    applySession(session)
    persistSession(storage, session)
    try {
      return await getMe()
    } catch (error) {
      clear()
      throw error
    }
  }

  const getMe = async (): Promise<AuthSession> => {
    const current = readStoredSession(storage)
    if (!current) throw new Error('没有可恢复的会话')
    applySession(current)
    const me = await client.getMe()
    const session = sessionFromMe(current, me)
    persistSession(storage, session)
    return publicSession(session)
  }

  const refresh = async (): Promise<AuthSession> => {
    const current = readStoredSession(storage)
    if (!current) throw new Error('没有可刷新的会话')

    client.setRefreshToken(current.refreshToken)
    const response = await client.refresh(current.refreshToken)
    if (!isNonEmptyString(response.access_token)) {
      throw new Error('刷新响应缺少有效访问令牌')
    }

    const refreshed: PersistedAuthSession = {
      ...current,
      accessToken: response.access_token,
      accessTokenExpiresAt: new Date(Date.now() + response.expires_in * 1000).toISOString(),
    }
    applySession(refreshed)
    persistSession(storage, refreshed)
    return publicSession(refreshed)
  }

  const restore = async (): Promise<AuthSession | null> => {
    const current = readStoredSession(storage)
    if (!current) {
      clear()
      return null
    }

    try {
      await refresh()
      return await getMe()
    } catch {
      clear()
      return null
    }
  }

  const logout = async (): Promise<AuthLogoutResult> => {
    const current = readStoredSession(storage)
    if (!current) {
      clear()
      return {}
    }

    let error: string | undefined
    try {
      applySession(current)
      await client.logout(current.refreshToken)
    } catch {
      error = LOGOUT_ERROR_MESSAGE
    } finally {
      clear()
    }

    return error ? { error } : {}
  }

  return {
    login,
    restore,
    refresh,
    getMe,
    logout,
    clear,
    setOnAuthFailure: (handler) => client.setOnAuthFailure(handler),
  }
}
