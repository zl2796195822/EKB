import { readFileSync } from 'node:fs'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { ApiClient, ApiClientError } from '../../lib/api'
import {
  TRASH_CAPABILITIES,
  TRASH_LIST_PAGE_SIZE,
  createTrashAdapter,
  type TrashApiClient,
} from '../adapters/trash'
import type {
  TrashClearResponse,
  TrashItemRecord,
  TrashListQuery,
  TrashListResponse,
  TrashPurgeResponse,
  TrashRestoreResponse,
} from '../../types/api'

function record(overrides: Partial<TrashItemRecord> = {}): TrashItemRecord {
  return {
    id: 'trash-kb-1',
    resource_type: 'KB',
    resource_id: 'kb-1',
    title: '药品注册法规库',
    parent_id: null,
    parent_title: null,
    deleted_by: 'user-1',
    deleted_at: '2026-08-10T02:10:00+00:00',
    expires_at: '2026-09-09T02:10:00+00:00',
    restorable: true,
    blocked_reason: null,
    ...overrides,
  }
}

const listResponse: TrashListResponse = {
  items: [
    record(),
    record({
      id: 'trash-doc-1',
      resource_type: 'DOCUMENT',
      resource_id: 'doc-1',
      title: 'GSP 附录三.pdf',
      parent_id: 'kb-1',
      parent_title: '药品注册法规库',
      restorable: false,
      blocked_reason: 'parent_deleted',
    }),
  ],
  total: 2,
  counts: { KB: 1, DOCUMENT: 1, CONVERSATION: 0 },
  resource_types: ['KB', 'DOCUMENT', 'CONVERSATION'],
  limit: 20,
  offset: 0,
}

function stubClient(overrides: Partial<TrashApiClient> = {}): TrashApiClient {
  return {
    listTrash: async () => listResponse,
    restoreTrashItem: async (): Promise<TrashRestoreResponse> => ({ status: 'ok', item: record() }),
    purgeTrashItem: async (): Promise<TrashPurgeResponse> => ({
      status: 'ok',
      id: 'trash-kb-1',
      resource_type: 'KB',
      resource_id: 'kb-1',
      removed: { documents: 3, chunks: 42 },
    }),
    clearTrash: async (): Promise<TrashClearResponse> => ({ status: 'ok', purged: 7 }),
    ...overrides,
  }
}

afterEach(() => {
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

describe('Phase 2 · 回收站适配器', () => {
  it('maps the snake_case projection into camelCase view models', async () => {
    const result = await createTrashAdapter(stubClient()).list()

    expect(result.state).toBe('ready')
    expect(result.data?.total).toBe(2)
    expect(result.data?.counts).toEqual({ KB: 1, DOCUMENT: 1, CONVERSATION: 0 })

    const [kb, doc] = result.data!.items
    expect(kb).toMatchObject({
      id: 'trash-kb-1',
      resourceType: 'KB',
      resourceId: 'kb-1',
      title: '药品注册法规库',
      deletedBy: 'user-1',
      restorable: true,
      blockedReason: null,
    })
    expect(doc).toMatchObject({
      resourceType: 'DOCUMENT',
      parentId: 'kb-1',
      parentTitle: '药品注册法规库',
      restorable: false,
      blockedReason: 'parent_deleted',
    })
  })

  it('sends type/keyword/paging filters and trims the keyword', async () => {
    const seen: TrashListQuery[] = []
    const trash = createTrashAdapter(
      stubClient({
        listTrash: async (query = {}) => {
          seen.push(query)
          return listResponse
        },
      }),
    )

    await trash.list({ resourceType: 'DOCUMENT', query: '  GSP  ', offset: 40 })
    await trash.list()

    expect(seen[0]).toEqual({ resource_type: 'DOCUMENT', q: 'GSP', limit: TRASH_LIST_PAGE_SIZE, offset: 40 })
    // 空筛选不能把 resource_type/q 传成空串，否则后端会当作非法资源类型返回 400。
    expect(seen[1]).toEqual({ limit: TRASH_LIST_PAGE_SIZE, offset: 0 })
  })

  it('reports empty only when the tenant really has nothing in the bin', async () => {
    const emptied = await createTrashAdapter(
      stubClient({
        listTrash: async () => ({ ...listResponse, items: [], total: 0, counts: {} }),
      }),
    ).list()
    expect(emptied.state).toBe('empty')

    // 翻过头的页是 ready 的空列表，不该显示"回收站是空的"。
    const overshoot = await createTrashAdapter(
      stubClient({
        listTrash: async () => ({ ...listResponse, items: [], total: 2, offset: 40 }),
      }),
    ).list({ offset: 40 })
    expect(overshoot.state).toBe('ready')
  })

  it('returns the restored/purged resource identity for the caller notice', async () => {
    const trash = createTrashAdapter(stubClient())

    const restored = await trash.restore('trash-kb-1')
    expect(restored).toMatchObject({ state: 'ready', resourceType: 'KB', resourceId: 'kb-1' })

    const purged = await trash.purge('trash-kb-1')
    expect(purged.state).toBe('ready')
    expect(purged.removed).toEqual({ documents: 3, chunks: 42 })

    const cleared = await trash.clear('DOCUMENT')
    expect(cleared).toMatchObject({ state: 'ready', purged: 7 })
  })

  it('guards blank ids before hitting the network', async () => {
    const listTrash = vi.fn(async () => listResponse)
    const restoreTrashItem = vi.fn(async () => ({ status: 'ok', item: record() }))
    const trash = createTrashAdapter(stubClient({ listTrash, restoreTrashItem }))

    const result = await trash.restore('   ')
    expect(result.state).toBe('error')
    expect(result.error?.code).toBe('TRASH_ITEM_REQUIRED')
    expect(restoreTrashItem).not.toHaveBeenCalled()
  })

  it('maps 403 to permission-denied and other failures to error', async () => {
    const denied = await createTrashAdapter(
      stubClient({
        purgeTrashItem: async () => {
          throw new ApiClientError(403, 'PERMISSION_DENIED', '当前账号无权永久删除回收站内容')
        },
      }),
    ).purge('trash-kb-1')
    expect(denied.state).toBe('permission-denied')
    expect(denied.error?.message).toBe('当前账号无权永久删除回收站内容')

    const conflict = await createTrashAdapter(
      stubClient({
        restoreTrashItem: async () => {
          throw new ApiClientError(409, 'CONFLICT', '请先还原所属知识库')
        },
      }),
    ).restore('trash-doc-1')
    expect(conflict.state).toBe('error')
    expect(conflict.error?.status).toBe(409)

    const broken = await createTrashAdapter(
      stubClient({
        listTrash: async () => {
          throw new Error('boom')
        },
      }),
    ).list()
    expect(broken.state).toBe('error')
    expect(broken.error?.code).toBe('CLIENT_ERROR')
  })
})

describe('Phase 2 · /trash 契约与页面边界', () => {
  it('serializes the /trash contract in ApiClient', async () => {
    const requests: { url: string; method: string }[] = []
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      requests.push({ url: String(input), method: init?.method ?? 'GET' })
      return new Response(JSON.stringify({ status: 'ok', items: [], total: 0, counts: {}, purged: 0 }), {
        status: 200,
        headers: { 'content-type': 'application/json' },
      })
    })
    vi.stubGlobal('fetch', fetchMock)

    const client = new ApiClient('/api/v1')
    await client.listTrash({ resource_type: 'DOCUMENT', q: '注册', limit: 20, offset: 20 })
    await client.restoreTrashItem('trash/1')
    await client.purgeTrashItem('trash-kb-1')
    await client.clearTrash('KB')

    const listUrl = new URL(requests[0]!.url, 'http://example.test')
    expect(listUrl.pathname).toBe('/api/v1/trash')
    expect(listUrl.searchParams.get('resource_type')).toBe('DOCUMENT')
    expect(listUrl.searchParams.get('q')).toBe('注册')
    expect(listUrl.searchParams.get('offset')).toBe('20')
    expect(requests[0]!.method).toBe('GET')

    // 路径参数必须转义，否则带斜杠的 id 会打到别的路由上。
    expect(new URL(requests[1]!.url, 'http://example.test').pathname).toBe('/api/v1/trash/trash%2F1/restore')
    expect(requests[1]!.method).toBe('POST')

    expect(new URL(requests[2]!.url, 'http://example.test').pathname).toBe('/api/v1/trash/trash-kb-1')
    expect(requests[2]!.method).toBe('DELETE')

    const clearUrl = new URL(requests[3]!.url, 'http://example.test')
    expect(clearUrl.pathname).toBe('/api/v1/trash')
    expect(clearUrl.searchParams.get('resource_type')).toBe('KB')
    expect(requests[3]!.method).toBe('DELETE')
  })

  it('keeps RecyclePage behind the trash service boundary', () => {
    const source = readFileSync(new URL('../pages/RecyclePage.tsx', import.meta.url), 'utf8')

    expect(source).toContain('services.trash')
    expect(source).not.toMatch(/src\/lib\/api|from '\.\.\/\.\.\/lib\/api'/)
    expect(source).not.toMatch(/\bfetch\s*\(/)
    // M0 规则：页面禁止内联 style，视觉一律走 CSS 变量与类名。
    expect(source).not.toMatch(/style=\{\{/)
    // Phase 2 的核心目标：这一页不能再有"后端没有该能力"的占位。
    expect(source).not.toMatch(/state="unavailable"/)
    expect(source).not.toMatch(/\sdisabled(\s|=|\/|>)/)
  })

  it('declares the auto-purge job as available', () => {
    const statusOf = (id: string): string | undefined =>
      TRASH_CAPABILITIES.find((capability) => capability.id === id)?.status

    expect(statusOf('trash.list')).toBe('available')
    expect(statusOf('trash.filter-by-type')).toBe('available')
    expect(statusOf('trash.keyword-search')).toBe('available')
    expect(statusOf('trash.restore')).toBe('available')
    expect(statusOf('trash.purge')).toBe('available')
    expect(statusOf('trash.clear')).toBe('available')
    expect(statusOf('trash.auto-purge-job')).toBe('available')
  })
})
