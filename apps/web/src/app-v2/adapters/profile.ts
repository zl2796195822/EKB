import type {
  ApiKeyCreateResponse,
  ApiKeyItem,
  ApiKeysResponse,
  ChangePasswordPayload,
  MeProfileResponse,
  NotificationItem,
  NotificationsResponse,
  PatchProfilePayload,
  PreferencesResponse,
  ProfileResponse,
  SessionItem,
  SessionRevokeResponse,
  SessionsResponse,
} from '../../types/api'
import type {
  ApiKeyCreateResult,
  ApiKeyListResult,
  ApiKeyView,
  MeResult,
  MeView,
  MutationResult,
  NotificationListResult,
  NotificationView,
  PasswordChangeInput,
  PreferencesResult,
  ProfileDetailView,
  ProfileServices,
  ProfileUpdateInput,
  ProfileUpdateResult,
  SessionListResult,
  SessionRevokeResult,
  SessionView,
} from '../types/profile'
import { stateForData, stateForError, toAdapterError } from './index'

/** app-v2 只依赖这些方法，避免把整个 ApiClient 表面积带进 UI 层。 */
export interface ProfileApiClient {
  getMe(): Promise<MeProfileResponse>
  patchMeProfile(payload: PatchProfilePayload): Promise<ProfileResponse>
  changePassword(payload: ChangePasswordPayload): Promise<{ status: string }>
  getPreferences(): Promise<PreferencesResponse>
  patchPreferences(preferences: Record<string, unknown>): Promise<PreferencesResponse>
  getSessions(): Promise<SessionsResponse>
  revokeSession(sessionId: string, revokeAll?: boolean): Promise<SessionRevokeResponse>
  getApiKeys(): Promise<ApiKeysResponse>
  createApiKey(name: string): Promise<ApiKeyCreateResponse>
  revokeApiKey(keyId: string): Promise<{ status: string }>
  getNotifications(unreadOnly?: boolean): Promise<NotificationsResponse>
  markNotificationRead(notificationId: string): Promise<{ status: string }>
}

function mapProfileDetail(input: MeProfileResponse['profile']): ProfileDetailView {
  return {
    displayName: input.display_name,
    department: input.department,
    locale: input.locale,
    timezone: input.timezone,
    avatarUrl: input.avatar_url,
  }
}

function mapMe(input: MeProfileResponse): MeView {
  return {
    userId: input.user.id,
    name: input.user.name,
    email: input.user.email,
    role: input.user.role,
    tenantId: input.tenant_id,
    roleId: input.role_id,
    policyVersion: input.policy_version,
    capabilities: input.capabilities,
    tenants: input.tenants,
    profile: mapProfileDetail(input.profile),
  }
}

function mapSession(input: SessionItem): SessionView {
  return {
    id: input.id,
    ipHash: input.ip_hash,
    uaHash: input.ua_hash,
    status: input.status,
    createdAt: input.created_at,
    lastUsedAt: input.last_used_at,
  }
}

function mapApiKey(input: ApiKeyItem | ApiKeyCreateResponse): ApiKeyView {
  return {
    id: input.id,
    prefix: input.prefix,
    name: input.name,
    status: input.status,
    createdAt: input.created_at,
    lastUsedAt: input.last_used_at ?? null,
  }
}

function mapNotification(input: NotificationItem): NotificationView {
  return {
    id: input.id,
    type: input.type,
    title: input.title,
    body: input.body,
    priority: input.priority,
    readAt: input.read_at,
    createdAt: input.created_at,
  }
}

function failure(error: unknown, fallbackMessage: string) {
  const mapped = toAdapterError(error, fallbackMessage)
  return { state: stateForError(mapped), error: mapped }
}

function toPatchPayload(input: ProfileUpdateInput): PatchProfilePayload {
  return {
    ...(input.displayName !== undefined ? { display_name: input.displayName } : {}),
    ...(input.department !== undefined ? { department: input.department } : {}),
    ...(input.locale !== undefined ? { locale: input.locale } : {}),
    ...(input.timezone !== undefined ? { timezone: input.timezone } : {}),
    ...(input.avatarUrl !== undefined ? { avatar_url: input.avatarUrl } : {}),
  }
}

export function createProfileAdapter(client: ProfileApiClient): ProfileServices {
  return {
    getMe: async (): Promise<MeResult> => {
      try {
        return { state: 'ready', data: mapMe(await client.getMe()) }
      } catch (error) {
        return failure(error, '个人资料加载失败')
      }
    },

    updateProfile: async (input): Promise<ProfileUpdateResult> => {
      try {
        const response = await client.patchMeProfile(toPatchPayload(input))
        return { state: 'ready', data: mapProfileDetail(response.profile) }
      } catch (error) {
        return failure(error, '个人资料保存失败')
      }
    },

    changePassword: async (input: PasswordChangeInput): Promise<MutationResult> => {
      try {
        await client.changePassword({ old_password: input.oldPassword, new_password: input.newPassword })
        return { state: 'ready' }
      } catch (error) {
        return failure(error, '密码修改失败')
      }
    },

    getPreferences: async (): Promise<PreferencesResult> => {
      try {
        const response = await client.getPreferences()
        return { state: 'ready', data: response.preferences }
      } catch (error) {
        return failure(error, '偏好设置加载失败')
      }
    },

    updatePreferences: async (preferences): Promise<PreferencesResult> => {
      try {
        const response = await client.patchPreferences(preferences)
        return { state: 'ready', data: response.preferences }
      } catch (error) {
        return failure(error, '偏好设置保存失败')
      }
    },

    listSessions: async (): Promise<SessionListResult> => {
      try {
        const data = (await client.getSessions()).items.map(mapSession)
        return { state: stateForData(data), data }
      } catch (error) {
        return failure(error, '登录会话加载失败')
      }
    },

    revokeSession: async (sessionId): Promise<SessionRevokeResult> => {
      if (!sessionId.trim()) {
        return { state: 'error', error: { code: 'SESSION_ID_REQUIRED', message: '会话 ID 不能为空' } }
      }
      try {
        await client.revokeSession(sessionId, false)
        return { state: 'ready', revokedCount: 1 }
      } catch (error) {
        return failure(error, '会话撤销失败')
      }
    },

    revokeAllSessions: async (): Promise<SessionRevokeResult> => {
      try {
        const response = await client.revokeSession('all', true)
        const parsed = response.revoked_count ? Number.parseInt(response.revoked_count, 10) : 0
        return { state: 'ready', revokedCount: Number.isFinite(parsed) ? parsed : 0 }
      } catch (error) {
        return failure(error, '批量撤销会话失败')
      }
    },

    listApiKeys: async (): Promise<ApiKeyListResult> => {
      try {
        const data = (await client.getApiKeys()).items.map(mapApiKey)
        return { state: stateForData(data), data }
      } catch (error) {
        return failure(error, 'API Key 列表加载失败')
      }
    },

    createApiKey: async (name): Promise<ApiKeyCreateResult> => {
      const trimmed = name.trim()
      if (!trimmed) {
        return { state: 'error', error: { code: 'API_KEY_NAME_REQUIRED', message: '密钥名称不能为空' } }
      }
      try {
        const response = await client.createApiKey(trimmed)
        return { state: 'ready', data: mapApiKey(response), secret: response.secret }
      } catch (error) {
        return failure(error, 'API Key 创建失败')
      }
    },

    revokeApiKey: async (keyId): Promise<MutationResult> => {
      try {
        await client.revokeApiKey(keyId)
        return { state: 'ready' }
      } catch (error) {
        return failure(error, 'API Key 撤销失败')
      }
    },

    listNotifications: async (unreadOnly = false): Promise<NotificationListResult> => {
      try {
        const data = (await client.getNotifications(unreadOnly)).items.map(mapNotification)
        return { state: stateForData(data), data }
      } catch (error) {
        return failure(error, '通知列表加载失败')
      }
    },

    markNotificationRead: async (notificationId): Promise<MutationResult> => {
      try {
        await client.markNotificationRead(notificationId)
        return { state: 'ready' }
      } catch (error) {
        return failure(error, '通知标记失败')
      }
    },
  }
}

export type ProfileAdapter = ProfileServices

export const PROFILE_CAPABILITIES = [
  { id: 'profile.read', status: 'available' },
  { id: 'profile.update', status: 'available' },
  { id: 'profile.password-change', status: 'available' },
  { id: 'profile.preferences', status: 'available' },
  { id: 'profile.sessions', status: 'available' },
  { id: 'profile.api-keys', status: 'available' },
  { id: 'profile.notifications', status: 'available' },
  {
    id: 'profile.avatar-upload',
    status: 'unavailable',
    reason: '后端仅接受 avatar_url 字符串，未提供对象存储上传端点。',
  },
] as const
