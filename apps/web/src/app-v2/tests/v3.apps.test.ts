import { readFileSync } from 'node:fs'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { ApiClient, ApiClientError } from '../../lib/api'
import {
  APPS_CAPABILITIES,
  APPS_DEFAULT_LIMIT,
  createAppsAdapter,
  type AppsApiClient,
} from '../adapters/apps'
import type {
  AppCatalogRecord,
  AppsCatalogResponse,
  AppInstallResponse,
  AppsInstalledResponse,
  AppUninstallResponse,
} from '../../types/api'

const catalogResponse: AppsCatalogResponse = {
  items: [
    {
      id: 'app-1',
      slug: 'feishu-sync',
      name: '飞书集成',
      category: 'DOCUMENT_SYNC',
      description: '同步飞书文档与知识空间。',
      icon_slug: 'chat-circle-dots',
      status: 'AVAILABLE',
      installed_at: null,
      uninstalled_at: null,
      error_message: null,
    },
    {
      id: 'app-2',
      slug: 'analytics-pro',
      name: '数据看板 Pro',
      category: 'ANALYTICS',
      description: '扩展运营看板的分析维度。',
      icon_slug: 'chart-line',
      status: 'INSTALLED',
      installed_at: '2026-08-11T01:52:37Z',
      uninstalled_at: null,
      error_message: null,
    },
  ],
  total: 2,
}

const installedResponse: AppsInstalledResponse = {
  items: [
    {
      id: 'app-2',
      slug: 'analytics-pro',
      name: '数据看板 Pro',
      category: 'ANALYTICS',
      description: '扩展运营看板的分析维度。',
      icon_slug: 'chart-line',
      status: 'INSTALLED',
      installed_at: '2026-08-11T01:52:37Z',
      uninstalled_at: null,
      error_message: null,
    },
  ],
  count: 1,
}

const installResponse: AppInstallResponse = {
  success: true,
  app_id: 'app-1',
  slug: 'feishu-sync',
  name: '飞书集成',
  status: 'INSTALLED',
  error: null,
}

const uninstallResponse: AppUninstallResponse = {
  success: true,
  app_id: 'app-1',
  slug: 'feishu-sync',
  previous_status: 'INSTALLED',
  error: null,
}

function stubClient(overrides: Partial<AppsApiClient> = {}): AppsApiClient {
  return {
    getAppsCatalog: async () => catalogResponse,
    getAppsInstalled: async () => installedResponse,
    installApp: async () => installResponse,
    uninstallApp: async () => uninstallResponse,
    getAppDetail: async (slug) => ({
      slug,
      display_name: '',
      provider_name: '',
      description: '',
      category: 'UNKNOWN',
      capabilities: [],
      recommended_rank: 0,
      installation: null,
      credentials: [],
      runs: [],
    }),
    configureApp: async (slug) => ({
      id: '',
      slug,
      status: 'CONFIGURED',
      config: {},
      updated_at: new Date().toISOString(),
    }),
    connectApp: async (slug) => ({
      id: '',
      slug,
      status: 'CONNECTED',
      connected: true,
    }),
    createAppCredential: async (slug, payload) => ({
      success: true,
      credential_id: '',
      name: payload.name,
      prefix: payload.secret.slice(0, 8),
      status: 'ACTIVE',
      key_version: 'v1',
      plaintext_once: payload.secret,
    }),
    listAppCredentials: async (slug) => ({
      slug,
      count: 0,
      items: [],
    }),
    revokeAppCredential: async (credentialId) => ({
      success: true,
      id: credentialId,
      name: '',
      prefix: '',
      status: 'REVOKED',
      revoked_at: new Date().toISOString(),
    }),
    ...overrides,
  }
}

afterEach(() => {
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

describe('Phase 4 · 应用中心适配器', () => {
  it('maps the snake_case catalog into camelCase view models', async () => {
    const result = await createAppsAdapter(stubClient()).getCatalog()

    expect(result.state).toBe('ready')
    expect(result.data?.total).toBe(2)
    expect(result.data?.items[0]).toMatchObject({
      slug: 'feishu-sync',
      name: '飞书集成',
      category: 'DOCUMENT_SYNC',
      iconSlug: 'chat-circle-dots',
      status: 'AVAILABLE',
    })
  })

  it('returns the installed list with only INSTALLED apps', async () => {
    const result = await createAppsAdapter(stubClient()).getInstalled()

    expect(result.state).toBe('ready')
    expect(result.data?.count).toBe(1)
    expect(result.data?.items[0].slug).toBe('analytics-pro')
  })

  it('maps install response to action view with success state', async () => {
    const result = await createAppsAdapter(stubClient()).install('feishu-sync')

    expect(result.state).toBe('ready')
    expect(result.data?.success).toBe(true)
    expect(result.data?.slug).toBe('feishu-sync')
    expect(result.data?.status).toBe('INSTALLED')
  })

  it('maps uninstall response and derives UNINSTALLED status', async () => {
    const result = await createAppsAdapter(stubClient()).uninstall('feishu-sync')

    expect(result.state).toBe('ready')
    expect(result.data?.success).toBe(true)
    expect(result.data?.status).toBe('UNINSTALLED')
  })

  it('guards blank slugs before hitting the network', async () => {
    const installApp = vi.fn(async () => installResponse)
    const apps = createAppsAdapter(stubClient({ installApp }))

    const result = await apps.install('   ')
    expect(result.state).toBe('error')
    expect(result.error?.code).toBe('APP_SLUG_REQUIRED')
    expect(installApp).not.toHaveBeenCalled()
  })

  it('reports empty catalog when tenant has no apps', async () => {
    const result = await createAppsAdapter(
      stubClient({
        getAppsCatalog: async () => ({ items: [], total: 0 }),
        getAppsInstalled: async () => ({ items: [], count: 0 }),
      }),
    ).getCatalog()

    expect(result.state).toBe('empty')
  })

  it('maps 403 to permission-denied and other failures to error', async () => {
    const denied = await createAppsAdapter(
      stubClient({
        getAppsCatalog: async () => {
          throw new ApiClientError(403, 'PERMISSION_DENIED', '无权查看应用目录')
        },
      }),
    ).getCatalog()
    expect(denied.state).toBe('permission-denied')

    const broken = await createAppsAdapter(
      stubClient({
        installApp: async () => {
          throw new Error('boom')
        },
      }),
    ).install('test')
    expect(broken.state).toBe('error')
    expect(broken.error?.code).toBe('CLIENT_ERROR')
  })

  it('passes limit through to the catalog endpoint', async () => {
    const seen: (number | undefined)[] = []
    await createAppsAdapter(
      stubClient({
        getAppsCatalog: async (limit) => {
          seen.push(limit)
          return catalogResponse
        },
      }),
    ).getCatalog(20)

    expect(seen).toEqual([20])
    expect(APPS_DEFAULT_LIMIT).toBe(50)
  })
})

describe('Phase 4 · /apps 契约与页面边界', () => {
  it('serializes the /apps contract in ApiClient', async () => {
    const requests: { url: string; method: string }[] = []
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      requests.push({ url: String(input), method: init?.method ?? 'GET' })
      return new Response(JSON.stringify({ items: [], total: 0, count: 0, success: true }), {
        status: 200,
        headers: { 'content-type': 'application/json' },
      })
    })
    vi.stubGlobal('fetch', fetchMock)

    const client = new ApiClient('/api/v1')
    await client.getAppsCatalog(10)
    await client.getAppsInstalled()
    await client.installApp('feishu-sync')
    await client.uninstallApp('feishu-sync')

    const paths = requests.map((request) => new URL(request.url, 'http://example.test'))
    expect(paths.map((url) => url.pathname)).toEqual([
      '/api/v1/apps/catalog',
      '/api/v1/apps/installed',
      '/api/v1/apps/install/feishu-sync',
      '/api/v1/apps/uninstall/feishu-sync',
    ])
    expect(paths[0]!.searchParams.get('limit')).toBe('10')
    expect(requests[2]!.method).toBe('POST')
    expect(requests[3]!.method).toBe('POST')
  })

  it('keeps AppsPage behind the service boundary and free of unavailable panels', () => {
    const source = readFileSync(new URL('../pages/AppsPage.tsx', import.meta.url), 'utf8')

    expect(source).toContain('services.apps')
    expect(source).not.toMatch(/src\/lib\/api|from '\.\.\/\.\.\/lib\/api'/)
    expect(source).not.toMatch(/\bfetch\s*\(/)
    expect(source).not.toMatch(/style=\{\{/)
    // Phase 4 的核心目标：应用卡不能再是"后端没有该能力"的占位。
    expect(source).not.toMatch(/state="unavailable"/)
    // 按钮在操作进行中可以 disabled（loading 态），但不能像旧版那样全部 disabled。
    expect(source).not.toMatch(/disabled(?=\s*title=.*不可用)/)
  })

  it('declares marketplace as the only unavailable capability', () => {
    const statusOf = (id: string): string | undefined =>
      APPS_CAPABILITIES.find((capability) => capability.id === id)?.status

    expect(statusOf('apps.catalog')).toBe('available')
    expect(statusOf('apps.installed')).toBe('available')
    expect(statusOf('apps.install')).toBe('available')
    expect(statusOf('apps.uninstall')).toBe('available')
    expect(statusOf('apps.marketplace')).toBe('unavailable')
  })
})
