import type { AdapterResult } from '../types'
import type {
  IdentityApiClient,
  IdentityListUsersInput,
  IdentityServices,
  IdentityUserPageView,
  IdentityUserView,
  V3IdentityListUsersQuery,
  V3IdentityListUsersResponse,
  V3IdentityUserRecord,
} from '../types/v3Identity'
import { stateForData, stateForError, toAdapterError } from './index'

const DEFAULT_PAGE_SIZE = 20
const DEFAULT_SORT = 'updated_at_desc' as const

function mapUser(input: V3IdentityUserRecord): IdentityUserView {
  return {
    id: input.id,
    name: input.display_name,
    email: input.email,
    department: input.department,
    role: input.role,
    roleId: input.role_id,
    status: input.status,
    joinedAt: input.joined_at,
    updatedAt: input.updated_at,
  }
}

function mapPage(input: V3IdentityListUsersResponse): IdentityUserPageView {
  return {
    items: input.items.map(mapUser),
    nextCursor: input.next_cursor,
    pageSize: input.page_size,
  }
}

function toQuery(input: IdentityListUsersInput = {}): V3IdentityListUsersQuery {
  const normalizedQuery = input.query?.trim()
  return {
    sort: input.sort ?? DEFAULT_SORT,
    page_size: input.pageSize ?? DEFAULT_PAGE_SIZE,
    ...(normalizedQuery ? { query: normalizedQuery } : {}),
    ...(input.role && input.role !== 'ALL' ? { role: input.role } : {}),
    ...(input.status && input.status !== 'ALL' ? { status: input.status } : {}),
    ...(input.cursor ? { cursor: input.cursor } : {}),
  }
}

function errorResult(error: unknown, fallbackMessage: string): AdapterResult<IdentityUserPageView> {
  const mapped = toAdapterError(error, fallbackMessage)
  return { state: stateForError(mapped), error: mapped }
}

export function createIdentityAdapter(client: IdentityApiClient): IdentityServices {
  return {
    listUsers: async (tenantId, input = {}) => {
      if (!tenantId.trim()) {
        return {
          state: 'error',
          error: {
            code: 'TENANT_ID_REQUIRED',
            message: '租户 ID 不能为空',
          },
        }
      }

      try {
        const data = mapPage(await client.listTenantUsers(tenantId, toQuery(input), input.signal))
        return { state: stateForData(data.items), data }
      } catch (error) {
        return errorResult(error, '租户用户目录加载失败')
      }
    },
  }
}

export type IdentityAdapter = IdentityServices
export type { IdentityApiClient } from '../types/v3Identity'

export const IDENTITY_CAPABILITIES = [
  {
    id: 'identity.tenant-users-list',
    status: 'available',
  },
  {
    id: 'identity.user-write-actions',
    status: 'unavailable',
    reason: 'T01b 仅提供租户用户目录读取，不提供邀请、角色、状态或权限写入。',
  },
] as const
