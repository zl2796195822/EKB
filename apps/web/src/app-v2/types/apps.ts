import type { AdapterError } from './adapters'
import type { PageState } from './view-state'

/** 与后端 installed_apps.category 对齐。 */
export const APP_CATEGORIES = ['DOCUMENT_SYNC', 'MESSAGING', 'ANALYTICS', 'GOVERNANCE', 'UNKNOWN'] as const

export type AppCategory = (typeof APP_CATEGORIES)[number]

/** 与后端 installed_apps.status 对齐。 */
export const APP_STATUSES = ['AVAILABLE', 'INSTALLED', 'UNINSTALLED', 'ERROR'] as const

export type AppStatus = (typeof APP_STATUSES)[number]

export interface AppItemView {
  readonly id: string
  readonly slug: string
  readonly name: string
  readonly category: AppCategory
  readonly description: string
  readonly iconSlug: string
  readonly status: AppStatus
  readonly installedAt: string | null
  readonly uninstalledAt: string | null
  readonly errorMessage: string | null
}

export interface AppsCatalogView {
  readonly items: readonly AppItemView[]
  readonly total: number
}

export interface AppsInstalledView {
  readonly items: readonly AppItemView[]
  readonly count: number
}

/** 安装/卸载操作返回的业务数据（不含 state/error 包装）。 */
export interface AppActionView {
  readonly success: boolean
  readonly appId: string
  readonly slug: string
  readonly name: string
  readonly status: AppStatus
  readonly error?: string
}

// === Apps Phase 4 additive views (detail / configure / connect / credentials / runs) ===

/** 生命周期：INSTALLED → CONFIGURED → CONNECTED → UNINSTALLED */
export const APP_INSTALLATION_STATUSES = [
  'INSTALLED',
  'CONFIGURED',
  'CONNECTED',
  'UNINSTALLED',
] as const
export type AppInstallationStatus = (typeof APP_INSTALLATION_STATUSES)[number]

export const CREDENTIAL_STATUSES = ['ACTIVE', 'REVOKED'] as const
export type CredentialStatus = (typeof CREDENTIAL_STATUSES)[number]

export const RUN_STATUSES = ['SUCCEEDED', 'FAILED'] as const
export type RunStatus = (typeof RUN_STATUSES)[number]

export interface AppInstallationView {
  readonly id: string
  readonly status: AppInstallationStatus | string
  readonly config: Readonly<Record<string, unknown>>
  readonly installedBy: string | null
  readonly installedAt: string | null
  readonly updatedAt: string | null
  readonly uninstalledAt: string | null
}

export interface AppCredentialView {
  readonly id: string
  readonly name: string
  /** 明文前 8 字符；读接口永远只返回这个字段。 */
  readonly prefix: string
  readonly status: CredentialStatus | string
  readonly keyVersion?: string
  readonly createdAt: string | null
  readonly revokedAt: string | null
}

export interface AppCredentialCreateView {
  readonly success: boolean
  readonly credentialId: string
  readonly name: string
  readonly prefix: string
  readonly status: CredentialStatus | string
  readonly keyVersion: string
  /** 创建时返回一次；之后任何读接口都是 null / undefined。 */
  readonly plaintextOnce: string | null
}

export interface AppRunView {
  readonly id: string
  readonly runType: string
  readonly status: RunStatus | string
  readonly syncSourceId: string | null
  readonly resultRedacted: Readonly<Record<string, unknown>>
  readonly startedAt: string | null
  readonly completedAt: string | null
  readonly errorCode: string | null
  readonly errorMessage: string | null
}

export interface AppDetailView {
  readonly slug: string
  readonly displayName: string
  readonly providerName: string
  readonly description: string
  readonly category: AppCategory
  readonly capabilities: readonly string[]
  readonly recommendedRank: number
  readonly installation: AppInstallationView | null
  readonly credentials: readonly AppCredentialView[]
  readonly runs: readonly AppRunView[]
}

export type AppDetailResult =
  | { readonly state: PageState; readonly data?: AppDetailView; readonly error?: AdapterError }

export type AppCredentialCreateResult =
  | { readonly state: PageState; readonly data?: AppCredentialCreateView; readonly error?: AdapterError }

export type AppCredentialListResult = {
  readonly state: PageState
  readonly data?: { readonly slug: string; readonly count: number; readonly items: readonly AppCredentialView[] }
  readonly error?: AdapterError
}

export type AppConfigureResult = {
  readonly state: PageState
  readonly data?: {
    readonly id: string
    readonly slug: string
    readonly status: AppInstallationStatus | string
    readonly config: Readonly<Record<string, unknown>>
    readonly updatedAt: string
  }
  readonly error?: AdapterError
}

export type AppConnectResult = {
  readonly state: PageState
  readonly data?: {
    readonly id: string
    readonly slug: string
    readonly status: AppInstallationStatus | string
    readonly connected: boolean
    readonly error?: string
  }
  readonly error?: AdapterError
}

export type AppRevokeResult = {
  readonly state: PageState
  readonly data?: {
    readonly success: boolean
    readonly id: string
    readonly name: string
    readonly prefix: string
    readonly status: CredentialStatus | string
    readonly revokedAt: string
  }
  readonly error?: AdapterError
}

export interface AppsCatalogResult {
  readonly state: PageState
  readonly data?: AppsCatalogView
  readonly error?: AdapterError
}

export interface AppsInstalledResult {
  readonly state: PageState
  readonly data?: AppsInstalledView
  readonly error?: AdapterError
}

export interface AppActionResult {
  readonly state: PageState
  readonly data?: AppActionView
  readonly error?: AdapterError
}

export interface AppsServices {
  getCatalog(limit?: number): Promise<AppsCatalogResult>
  getInstalled(): Promise<AppsInstalledResult>
  install(slug: string): Promise<AppActionResult>
  uninstall(slug: string): Promise<AppActionResult>
  // Apps Phase 4 additive surface
  getDetail(slug: string): Promise<AppDetailResult>
  configure(slug: string, config: Record<string, unknown>): Promise<AppConfigureResult>
  connect(slug: string): Promise<AppConnectResult>
  listCredentials(slug: string): Promise<AppCredentialListResult>
  createCredential(
    slug: string,
    payload: { readonly name: string; readonly secret: string },
  ): Promise<AppCredentialCreateResult>
  revokeCredential(credentialId: string): Promise<AppRevokeResult>
}
