import type {
  AppCatalogRecord,
  AppsCatalogResponse,
  AppsInstalledResponse,
  AppInstallResponse,
  AppUninstallResponse,
  AppDetailResponse,
  AppConfigureResponse,
  AppConnectResponse,
  AppCredentialCreateResponse,
  AppCredentialListResponse,
  AppCredentialRevokeResponse,
} from '../../types/api'
import type {
  AppActionView,
  AppActionResult,
  AppCategory,
  AppConfigureResult,
  AppConnectResult,
  AppCredentialCreateResult,
  AppCredentialListResult,
  AppCredentialView,
  AppDetailResult,
  AppDetailView,
  AppInstallationView,
  AppItemView,
  AppRevokeResult,
  AppRunView,
  AppStatus,
  AppsCatalogResult,
  AppsCatalogView,
  AppsInstalledResult,
  AppsInstalledView,
  AppsServices,
  CredentialStatus,
  RunStatus,
} from '../types/apps'
import { APP_CATEGORIES, APP_STATUSES, CREDENTIAL_STATUSES, RUN_STATUSES } from '../types/apps'
import { stateForError, toAdapterError } from './index'

/** app-v2 adapter 契约：避免把整个 ApiClient 表面积带进 UI 层。 */
export interface AppsApiClient {
  getAppsCatalog(limit?: number): Promise<AppsCatalogResponse>
  getAppsInstalled(): Promise<AppsInstalledResponse>
  installApp(slug: string): Promise<AppInstallResponse>
  uninstallApp(slug: string): Promise<AppUninstallResponse>
  // Apps Phase 4 additive
  getAppDetail(slug: string): Promise<AppDetailResponse>
  configureApp(slug: string, config: Record<string, unknown>): Promise<AppConfigureResponse>
  connectApp(slug: string): Promise<AppConnectResponse>
  createAppCredential(
    slug: string,
    payload: { name: string; secret: string },
  ): Promise<AppCredentialCreateResponse>
  listAppCredentials(slug: string): Promise<AppCredentialListResponse>
  revokeAppCredential(credentialId: string): Promise<AppCredentialRevokeResponse>
}

export const APPS_DEFAULT_LIMIT = 50

function isCategory(value: string): value is AppCategory {
  return (APP_CATEGORIES as readonly string[]).includes(value)
}

function isStatus(value: string): value is AppStatus {
  return (APP_STATUSES as readonly string[]).includes(value)
}

function isCredentialStatus(value: string): value is CredentialStatus {
  return (CREDENTIAL_STATUSES as readonly string[]).includes(value)
}

function isRunStatus(value: string): value is RunStatus {
  return (RUN_STATUSES as readonly string[]).includes(value)
}

function mapCategory(value: string): AppCategory {
  return isCategory(value) ? value : 'UNKNOWN'
}

function mapStatus(value: string): AppStatus {
  return isStatus(value) ? value : 'AVAILABLE'
}

function mapCredentialStatus(value: string): CredentialStatus | string {
  return isCredentialStatus(value) ? value : value
}

function mapRunStatus(value: string): RunStatus | string {
  return isRunStatus(value) ? value : value
}

function mapItem(input: AppCatalogRecord): AppItemView {
  return {
    id: input.id,
    slug: input.slug,
    name: input.name,
    category: mapCategory(input.category),
    description: input.description,
    iconSlug: input.icon_slug,
    status: mapStatus(input.status),
    installedAt: input.installed_at,
    uninstalledAt: input.uninstalled_at,
    errorMessage: input.error_message,
  }
}

function mapInstallation(input: AppDetailResponse['installation']): AppInstallationView | null {
  if (!input) return null
  return {
    id: input.id,
    status: input.status,
    config: input.config ?? {},
    installedBy: input.installed_by ?? null,
    installedAt: input.installed_at ?? null,
    updatedAt: input.updated_at ?? null,
    uninstalledAt: input.uninstalled_at ?? null,
  }
}

function mapCredential(input: AppCredentialListResponse['items'][number]): AppCredentialView {
  return {
    id: input.id,
    name: input.name,
    prefix: input.prefix,
    status: mapCredentialStatus(input.status),
    keyVersion: input.key_version,
    createdAt: input.created_at ?? null,
    revokedAt: input.revoked_at ?? null,
  }
}

function mapRun(
  input: AppDetailResponse['runs'][number],
): AppRunView {
  return {
    id: input.id,
    runType: input.run_type,
    status: mapRunStatus(input.status),
    syncSourceId: input.sync_source_id ?? null,
    resultRedacted: input.result_redacted ?? {},
    startedAt: input.started_at ?? null,
    completedAt: input.completed_at ?? null,
    errorCode: input.error_code ?? null,
    errorMessage: input.error_message ?? null,
  }
}

function mapDetail(input: AppDetailResponse): AppDetailView {
  return {
    slug: input.slug,
    displayName: input.display_name,
    providerName: input.provider_name,
    description: input.description,
    category: mapCategory(input.category),
    capabilities: Object.freeze([...(input.capabilities ?? [])]),
    recommendedRank: input.recommended_rank ?? 0,
    installation: mapInstallation(input.installation),
    credentials: Object.freeze(input.credentials?.map(mapCredential) ?? []),
    runs: Object.freeze(input.runs?.map(mapRun) ?? []),
  }
}

function failure(error: unknown, fallbackMessage: string) {
  const mapped = toAdapterError(error, fallbackMessage)
  return { state: stateForError(mapped), error: mapped }
}

export function createAppsAdapter(client: AppsApiClient): AppsServices {
  return {
    getCatalog: async (limit = APPS_DEFAULT_LIMIT): Promise<AppsCatalogResult> => {
      try {
        const data = await client.getAppsCatalog(limit)
        const view: AppsCatalogView = {
          items: data.items.map(mapItem),
          total: data.total,
        }
        return { state: view.total > 0 ? 'ready' : 'empty', data: view }
      } catch (error) {
        return failure(error, '应用目录加载失败')
      }
    },

    getInstalled: async (): Promise<AppsInstalledResult> => {
      try {
        const data = await client.getAppsInstalled()
        const view: AppsInstalledView = {
          items: data.items.map(mapItem),
          count: data.count,
        }
        return { state: view.count > 0 ? 'ready' : 'empty', data: view }
      } catch (error) {
        return failure(error, '已安装应用列表加载失败')
      }
    },

    install: async (slug): Promise<AppActionResult> => {
      if (!slug.trim()) {
        return { state: 'error', error: { code: 'APP_SLUG_REQUIRED', message: '应用标识不能为空' } }
      }
      try {
        const response = await client.installApp(slug)
        return {
          state: 'ready',
          data: {
            success: response.success,
            appId: response.app_id,
            slug: response.slug,
            name: response.name,
            status: mapStatus(response.status),
            error: response.error ?? undefined,
          },
        }
      } catch (error) {
        return failure(error, '安装失败')
      }
    },

    uninstall: async (slug): Promise<AppActionResult> => {
      if (!slug.trim()) {
        return { state: 'error', error: { code: 'APP_SLUG_REQUIRED', message: '应用标识不能为空' } }
      }
      try {
        const response = await client.uninstallApp(slug)
        return {
          state: 'ready',
          data: {
            success: response.success,
            appId: response.app_id,
            slug: response.slug,
            name: slug, // 卸载响应不返回 name，用 slug 兜底
            status: mapStatus(response.previous_status === 'INSTALLED' ? 'UNINSTALLED' : 'AVAILABLE'),
            error: response.error ?? undefined,
          },
        }
      } catch (error) {
        return failure(error, '卸载失败')
      }
    },

    // ===================== Apps Phase 4 additive methods =====================

    getDetail: async (slug): Promise<AppDetailResult> => {
      if (!slug.trim()) {
        return { state: 'error', error: { code: 'APP_SLUG_REQUIRED', message: '应用标识不能为空' } }
      }
      try {
        const response = await client.getAppDetail(slug)
        return { state: 'ready', data: mapDetail(response) }
      } catch (error) {
        return failure(error, '应用详情加载失败')
      }
    },

    configure: async (slug, config): Promise<AppConfigureResult> => {
      if (!slug.trim()) {
        return { state: 'error', error: { code: 'APP_SLUG_REQUIRED', message: '应用标识不能为空' } }
      }
      try {
        const response = await client.configureApp(slug, config)
        return {
          state: 'ready',
          data: {
            id: response.id,
            slug: response.slug,
            status: response.status,
            config: response.config ?? {},
            updatedAt: response.updated_at,
          },
        }
      } catch (error) {
        return failure(error, '应用配置保存失败')
      }
    },

    connect: async (slug): Promise<AppConnectResult> => {
      if (!slug.trim()) {
        return { state: 'error', error: { code: 'APP_SLUG_REQUIRED', message: '应用标识不能为空' } }
      }
      try {
        const response = await client.connectApp(slug)
        return {
          state: 'ready',
          data: {
            id: response.id,
            slug: response.slug,
            status: response.status,
            connected: Boolean(response.connected),
            error: response.error,
          },
        }
      } catch (error) {
        return failure(error, '应用连接失败')
      }
    },

    listCredentials: async (slug): Promise<AppCredentialListResult> => {
      if (!slug.trim()) {
        return { state: 'error', error: { code: 'APP_SLUG_REQUIRED', message: '应用标识不能为空' } }
      }
      try {
        const response = await client.listAppCredentials(slug)
        return {
          state: 'ready',
          data: {
            slug: response.slug,
            count: response.count,
            items: Object.freeze(response.items.map(mapCredential)),
          },
        }
      } catch (error) {
        return failure(error, '凭据列表加载失败')
      }
    },

    createCredential: async (slug, { name, secret }): Promise<AppCredentialCreateResult> => {
      if (!slug.trim()) {
        return { state: 'error', error: { code: 'APP_SLUG_REQUIRED', message: '应用标识不能为空' } }
      }
      if (!name.trim()) {
        return { state: 'error', error: { code: 'CREDENTIAL_NAME_REQUIRED', message: '凭据名称不能为空' } }
      }
      if (!secret) {
        return { state: 'error', error: { code: 'SECRET_REQUIRED', message: '凭据内容不能为空' } }
      }
      try {
        const response = await client.createAppCredential(slug, { name: name.trim(), secret })
        return {
          state: 'ready',
          data: {
            success: Boolean(response.success),
            credentialId: response.credential_id,
            name: response.name,
            prefix: response.prefix,
            status: mapCredentialStatus(response.status),
            keyVersion: response.key_version,
            // IMPORTANT: 前端仅在创建时拿到一次 plaintextOnce；之后 adapter 永远不会再拿到。
            plaintextOnce: response.plaintext_once,
          },
        }
      } catch (error) {
        return failure(error, '凭据创建失败')
      }
    },

    revokeCredential: async (credentialId): Promise<AppRevokeResult> => {
      if (!credentialId.trim()) {
        return {
          state: 'error',
          error: { code: 'CREDENTIAL_ID_REQUIRED', message: '凭据标识不能为空' },
        }
      }
      try {
        const response = await client.revokeAppCredential(credentialId)
        return {
          state: 'ready',
          data: {
            success: Boolean(response.success),
            id: response.id,
            name: response.name,
            prefix: response.prefix,
            status: mapCredentialStatus(response.status),
            revokedAt: response.revoked_at,
          },
        }
      } catch (error) {
        return failure(error, '凭据吊销失败')
      }
    },
  }
}

export type AppsAdapter = AppsServices

export const APPS_CAPABILITIES = [
  { id: 'apps.catalog', status: 'available' },
  { id: 'apps.installed', status: 'available' },
  { id: 'apps.install', status: 'available' },
  { id: 'apps.uninstall', status: 'available' },
  // Phase 4 additive capability IDs
  { id: 'apps.detail', status: 'available' },
  { id: 'apps.configure', status: 'available' },
  { id: 'apps.connect', status: 'available' },
  { id: 'apps.credentials.write_once', status: 'available' },
  { id: 'apps.credentials.list_prefix_only', status: 'available' },
  { id: 'apps.credentials.revoke', status: 'available' },
  { id: 'apps.runs.ledger', status: 'available' },
  {
    id: 'apps.marketplace',
    status: 'unavailable',
    reason: '后端只有内置目录，没有外部应用市场接入。',
  },
] as const
