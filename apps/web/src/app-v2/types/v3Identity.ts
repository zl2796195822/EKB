import type { AdapterResult } from './adapters'

export interface V3IdentityUserRecord {
  readonly id: string
  readonly email: string
  readonly display_name: string
  readonly department: string | null
  readonly role: string
  readonly role_id: string
  readonly status: string
  readonly joined_at: string
  readonly updated_at: string
}

export interface V3IdentityListUsersResponse {
  readonly items: readonly V3IdentityUserRecord[]
  readonly next_cursor: string | null
  readonly page_size: number
}

export interface V3IdentityListUsersQuery {
  readonly query?: string
  readonly role?: string
  readonly status?: string
  readonly sort?: 'updated_at_desc'
  readonly cursor?: string
  readonly page_size?: number
}

export interface IdentityUserView {
  readonly id: string
  readonly name: string
  readonly email: string
  readonly department: string | null
  readonly role: string
  readonly roleId: string
  readonly status: string
  readonly joinedAt: string
  readonly updatedAt: string
}

export interface IdentityUserPageView {
  readonly items: readonly IdentityUserView[]
  readonly nextCursor: string | null
  readonly pageSize: number
}

export interface IdentityListUsersInput {
  readonly query?: string
  readonly role?: string
  readonly status?: string
  readonly sort?: 'updated_at_desc'
  readonly cursor?: string
  readonly pageSize?: number
  readonly signal?: AbortSignal
}

export type IdentityUserListResult = AdapterResult<IdentityUserPageView>

export interface IdentityServices {
  readonly listUsers: (
    tenantId: string,
    input?: IdentityListUsersInput,
  ) => Promise<IdentityUserListResult>
}

export interface IdentityApiClient {
  readonly listTenantUsers: (
    tenantId: string,
    query: V3IdentityListUsersQuery,
    signal?: AbortSignal,
  ) => Promise<V3IdentityListUsersResponse>
}
