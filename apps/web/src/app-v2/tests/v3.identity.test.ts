import { readFileSync } from 'node:fs'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { ApiClient, ApiClientError } from '../../lib/api'
import { createIdentityAdapter } from '../adapters/v3Identity'
import type {
  IdentityApiClient,
  V3IdentityListUsersResponse,
  V3IdentityListUsersQuery,
} from '../types/v3Identity'

const firstPage: V3IdentityListUsersResponse = {
  items: [
    {
      id: 'user-1',
      email: 'alice@example.test',
      display_name: 'Alice',
      department: '产品设计',
      role: 'admin',
      role_id: 'role-admin',
      status: 'ACTIVE',
      joined_at: '2026-08-01T00:00:00Z',
      updated_at: '2026-08-09T00:00:00Z',
    },
  ],
  next_cursor: 'cursor-2',
  page_size: 25,
}

const lastPage: V3IdentityListUsersResponse = {
  items: [],
  next_cursor: null,
  page_size: 25,
}

function createPageClient(pages: readonly V3IdentityListUsersResponse[]) {
  const calls: Array<{ tenantId: string; query: V3IdentityListUsersQuery; signal?: AbortSignal }> = []
  let pageIndex = 0
  const client: IdentityApiClient = {
    listTenantUsers: async (tenantId, query, signal) => {
      calls.push({ tenantId, query, signal })
      const page = pages[pageIndex]
      pageIndex += 1
      if (!page) throw new Error('unexpected page request')
      return page
    },
  }
  return { client, calls }
}

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('v3 identity adapter', () => {
  it('maps tenant user response fields and next_cursor to the app-v2 view', async () => {
    const { client } = createPageClient([firstPage])
    const result = await createIdentityAdapter(client).listUsers('tenant-1', { pageSize: 25 })

    expect(result.state).toBe('ready')
    expect(result.data).toEqual({
      items: [{
        id: 'user-1',
        name: 'Alice',
        email: 'alice@example.test',
        department: '产品设计',
        role: 'admin',
        roleId: 'role-admin',
        status: 'ACTIVE',
        joinedAt: '2026-08-01T00:00:00Z',
        updatedAt: '2026-08-09T00:00:00Z',
      }],
      nextCursor: 'cursor-2',
      pageSize: 25,
    })
  })

  it('forwards server filters, sort, cursor and page_size without local filtering', async () => {
    const { client, calls } = createPageClient([firstPage])
    await createIdentityAdapter(client).listUsers('tenant-1', {
      query: '  Alice  ',
      role: 'admin',
      status: 'ACTIVE',
      sort: 'updated_at_desc',
      cursor: 'cursor-1',
      pageSize: 25,
    })

    expect(calls).toHaveLength(1)
    expect(calls[0]).toMatchObject({
      tenantId: 'tenant-1',
      query: {
        query: 'Alice',
        role: 'admin',
        status: 'ACTIVE',
        sort: 'updated_at_desc',
        cursor: 'cursor-1',
        page_size: 25,
      },
    })
  })

  it('preserves a null next cursor and maps an API error envelope', async () => {
    const { client } = createPageClient([lastPage])
    const empty = await createIdentityAdapter(client).listUsers('tenant-1')
    expect(empty.state).toBe('empty')
    expect(empty.data?.items).toEqual([])
    expect(empty.data?.nextCursor).toBeNull()

    const deniedClient: IdentityApiClient = {
      listTenantUsers: async () => {
        throw new ApiClientError(403, 'PERMISSION_DENIED', '当前账号无权查看租户用户', 'request-v3-1', { scope: 'tenant' })
      },
    }
    const denied = await createIdentityAdapter(deniedClient).listUsers('tenant-1')
    expect(denied.state).toBe('permission-denied')
    expect(denied.error).toMatchObject({
      code: 'PERMISSION_DENIED',
      status: 403,
      requestId: 'request-v3-1',
      details: { scope: 'tenant' },
    })
  })

  it('serializes the backend query contract in ApiClient', async () => {
    let requestedUrl = ''
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      requestedUrl = String(input)
      return new Response(JSON.stringify(lastPage), {
        status: 200,
        headers: { 'content-type': 'application/json' },
      })
    })
    vi.stubGlobal('fetch', fetchMock)

    const client = new ApiClient('/api/v1')
    await client.listTenantUsers('tenant/1', {
      query: 'Alice',
      role: 'admin',
      status: 'ACTIVE',
      sort: 'updated_at_desc',
      cursor: 'cursor-1',
      page_size: 25,
    })

    const query = new URL(requestedUrl, 'http://example.test').searchParams
    expect(new URL(requestedUrl, 'http://example.test').pathname).toBe('/api/v1/tenants/tenant%2F1/users')
    expect(query.get('query')).toBe('Alice')
    expect(query.get('role')).toBe('admin')
    expect(query.get('status')).toBe('ACTIVE')
    expect(query.get('sort')).toBe('updated_at_desc')
    expect(query.get('cursor')).toBe('cursor-1')
    expect(query.get('page_size')).toBe('25')
  })

  it('maps the backend error envelope through the identity service boundary', async () => {
    const fetchMock = vi.fn(async () => new Response(JSON.stringify({
      error: {
        code: 'PERMISSION_DENIED',
        message: '当前账号无权查看租户用户',
        request_id: 'request-envelope-1',
        details: { scope: 'tenant' },
      },
    }), {
      status: 403,
      headers: { 'content-type': 'application/json' },
    }))
    vi.stubGlobal('fetch', fetchMock)

    const result = await createIdentityAdapter(new ApiClient('/api/v1')).listUsers('tenant-1')

    expect(result.state).toBe('permission-denied')
    expect(result.error).toMatchObject({
      code: 'PERMISSION_DENIED',
      message: '当前账号无权查看租户用户',
      requestId: 'request-envelope-1',
      details: { scope: 'tenant' },
    })
  })

  it('keeps TeamPage behind the identity service boundary', () => {
    const source = readFileSync(new URL('../pages/TeamPage.tsx', import.meta.url), 'utf8')

    expect(source).toContain('services.identity.listUsers')
    expect(source).toContain('nextCursor')
    expect(source).not.toMatch(/src\/lib\/api/)
    expect(source).not.toMatch(/\bfetch\s*\(/)
    expect(source).not.toMatch(/\blistMembers\b|\baddMember\b|\bremoveMember\b/)
    expect(source).not.toContain('StatusPill')
  })
})
