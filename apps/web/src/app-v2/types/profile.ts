import type { AdapterError } from './adapters'
import type { PageState } from './view-state'

/** GET /me 的 profile 子对象。 */
export interface ProfileDetailView {
  readonly displayName: string | null
  readonly department: string | null
  readonly locale: string | null
  readonly timezone: string | null
  readonly avatarUrl: string | null
}

export interface MeView {
  readonly userId: string
  readonly name: string
  readonly email: string
  readonly role: string
  readonly tenantId: string
  readonly roleId: string
  readonly policyVersion: number
  readonly capabilities: readonly string[]
  readonly tenants: readonly { readonly id: string; readonly name: string; readonly role: string }[]
  readonly profile: ProfileDetailView
}

export interface MeResult {
  readonly state: PageState
  readonly data?: MeView
  readonly error?: AdapterError
}

export interface ProfileUpdateInput {
  readonly displayName?: string
  readonly department?: string
  readonly locale?: string
  readonly timezone?: string
  readonly avatarUrl?: string
}

export interface ProfileUpdateResult {
  readonly state: PageState
  readonly data?: ProfileDetailView
  readonly error?: AdapterError
}

export interface PasswordChangeInput {
  readonly oldPassword: string
  readonly newPassword: string
}

export interface MutationResult {
  readonly state: PageState
  readonly error?: AdapterError
}

export interface PreferencesResult {
  readonly state: PageState
  readonly data?: Record<string, unknown>
  readonly error?: AdapterError
}

export interface SessionView {
  readonly id: string
  readonly ipHash: string | null
  readonly uaHash: string | null
  readonly status: string
  readonly createdAt: string
  readonly lastUsedAt: string | null
}

export interface SessionListResult {
  readonly state: PageState
  readonly data?: readonly SessionView[]
  readonly error?: AdapterError
}

export interface SessionRevokeResult {
  readonly state: PageState
  readonly revokedCount?: number
  readonly error?: AdapterError
}

export interface ApiKeyView {
  readonly id: string
  readonly prefix: string
  readonly name: string
  readonly status: string
  readonly createdAt: string
  readonly lastUsedAt: string | null
}

export interface ApiKeyListResult {
  readonly state: PageState
  readonly data?: readonly ApiKeyView[]
  readonly error?: AdapterError
}

/** 创建结果里的 secret 只会在这一次返回，后端不再存明文。 */
export interface ApiKeyCreateResult {
  readonly state: PageState
  readonly data?: ApiKeyView
  readonly secret?: string
  readonly error?: AdapterError
}

export interface NotificationView {
  readonly id: string
  readonly type: string
  readonly title: string
  readonly body: string | null
  readonly priority: string
  readonly readAt: string | null
  readonly createdAt: string
}

export interface NotificationListResult {
  readonly state: PageState
  readonly data?: readonly NotificationView[]
  readonly error?: AdapterError
}

export interface ProfileServices {
  getMe(): Promise<MeResult>
  updateProfile(input: ProfileUpdateInput): Promise<ProfileUpdateResult>
  changePassword(input: PasswordChangeInput): Promise<MutationResult>
  getPreferences(): Promise<PreferencesResult>
  updatePreferences(preferences: Record<string, unknown>): Promise<PreferencesResult>
  listSessions(): Promise<SessionListResult>
  revokeSession(sessionId: string): Promise<SessionRevokeResult>
  revokeAllSessions(): Promise<SessionRevokeResult>
  listApiKeys(): Promise<ApiKeyListResult>
  createApiKey(name: string): Promise<ApiKeyCreateResult>
  revokeApiKey(keyId: string): Promise<MutationResult>
  listNotifications(unreadOnly?: boolean): Promise<NotificationListResult>
  markNotificationRead(notificationId: string): Promise<MutationResult>
}
