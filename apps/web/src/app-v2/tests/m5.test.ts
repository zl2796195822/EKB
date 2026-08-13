import { ApiClientError } from '../../lib/api'
import { createAdminAdapter, type AdminApiClient } from '../adapters/admin'
import type { TenantCreate, TenantResponse, UserInvite, UserInviteResponse } from '../../types/api'

function assert(condition: unknown, message: string): asserts condition {
  if (!condition) throw new Error(message)
}

function expectEqual<T>(actual: T, expected: T, message: string): void {
  assert(actual === expected, `${message}: expected ${String(expected)}, received ${String(actual)}`)
}

export async function runM5ContractTests(): Promise<void> {
  const invitePayloads: UserInvite[] = []
  const tenantPayloads: TenantCreate[] = []
  let inviteShouldFail = false
  let unsupportedUserListCalls = 0
  let unsupportedProfileUpdateCalls = 0
  let tenantListShouldFail = false

  const inviteResponse: UserInviteResponse = {
    id: 'user-2',
    email: 'new@example.com',
    name: '新成员',
    role: 'MEMBER',
    tenant_id: 'tenant-1',
  }
  const tenantResponse: TenantResponse = {
    id: 'tenant-2',
    name: '新租户',
    role: 'OWNER',
    model_routing_key: 'default',
    egress_policy: 'allow',
    quota_daily_qa: 0,
    quota_storage_docs: 0,
    quota_storage_bytes_per_file: 0,
  }

  const fakeClient: AdminApiClient & {
    getUsers: () => Promise<never[]>
    updateProfile: () => Promise<never>
  } = {
    inviteUser: async (payload) => {
      invitePayloads.push(payload)
      if (inviteShouldFail) {
        throw new ApiClientError(403, 'PERMISSION_DENIED', '当前账号无权执行该操作', 'request-invite-403')
      }
      return inviteResponse
    },
    listTenants: async () => {
      if (tenantListShouldFail) {
        throw new ApiClientError(403, 'PERMISSION_DENIED', '当前账号无权执行该操作', 'request-403')
      }
      return [tenantResponse]
    },
    createTenant: async (payload) => {
      tenantPayloads.push(payload)
      return tenantResponse
    },
    getUsers: async () => {
      unsupportedUserListCalls += 1
      return []
    },
    updateProfile: async () => {
      unsupportedProfileUpdateCalls += 1
      throw new Error('unsupported profile endpoint must not be called')
    },
    // M6 治理端点不参与 M5 断言，保留为「被调用即失败」的守卫桩。
    getOpsDashboard: async () => {
      throw new Error('M5 tests must not call the ops dashboard endpoint')
    },
    listAuditLogs: async () => {
      throw new Error('M5 tests must not call the audit endpoint')
    },
    listReviewItems: async () => {
      throw new Error('M5 tests must not call the reviews list endpoint')
    },
    getReviewItem: async () => {
      throw new Error('M5 tests must not call the review detail endpoint')
    },
    updateReviewItem: async () => {
      throw new Error('M5 tests must not call the review update endpoint')
    },
    listSyncSources: async () => {
      throw new Error('M5 tests must not call the sync sources endpoint')
    },
    createSyncSource: async () => {
      throw new Error('M5 tests must not call the sync source create endpoint')
    },
    runSyncSource: async () => {
      throw new Error('M5 tests must not call the sync run endpoint')
    },
    triggerBackup: async () => {
      throw new Error('M5 tests must not call the backup endpoint')
    },
  }

  const admin = createAdminAdapter(fakeClient)
  const invite = await admin.inviteUser({
    email: '  new@example.com ',
    name: ' 新成员 ',
    password: 'one-time-secret',
    role: 'MEMBER',
  })
  expectEqual(invite.state, 'ready', 'invite maps a successful response')
  expectEqual(invite.data?.tenantId, 'tenant-1', 'invite maps tenant_id to tenantId')
  expectEqual(invitePayloads[0]?.email, 'new@example.com', 'invite trims email')
  expectEqual(invitePayloads[0]?.name, '新成员', 'invite trims name')
  expectEqual(invitePayloads[0]?.password, 'one-time-secret', 'invite forwards required password once')

  inviteShouldFail = true
  const failedInvite = await admin.inviteUser({
    email: 'denied@example.com',
    name: '被拒绝成员',
    password: 'not-persisted-secret',
    role: 'MEMBER',
  })
  expectEqual(failedInvite.state, 'permission-denied', 'invite failure maps to permission-denied')
  expectEqual(failedInvite.error?.code, 'PERMISSION_DENIED', 'invite failure preserves safe code')
  expectEqual(failedInvite.error?.requestId, 'request-invite-403', 'invite failure preserves request id')
  inviteShouldFail = false

  const tenantList = await admin.listTenants()
  expectEqual(tenantList.state, 'ready', 'tenant list maps a non-empty response')
  expectEqual(tenantList.data?.[0]?.modelRoutingKey, 'default', 'tenant list maps model routing key')
  expectEqual(tenantList.data?.[0]?.quotaStorageBytesPerFile, 0, 'tenant list maps all quota fields')

  const tenantCreate = await admin.createTenant({
    name: ' 新租户 ',
    ownerEmail: ' owner@example.com ',
    ownerName: ' 租户负责人 ',
    ownerPassword: 'owner-one-time-secret',
    modelRoutingKey: 'route-a',
    egressPolicy: 'deny',
    quotaDailyQa: 12,
    quotaStorageDocs: 34,
    quotaStorageBytesPerFile: 56,
  })
  expectEqual(tenantCreate.state, 'ready', 'tenant create maps a successful response')
  expectEqual(tenantCreate.data?.id, 'tenant-2', 'tenant create maps response id')
  expectEqual(tenantPayloads[0]?.owner_password, 'owner-one-time-secret', 'tenant create forwards owner password once')
  expectEqual(tenantPayloads[0]?.quota_storage_bytes_per_file, 56, 'tenant create forwards all fields')
  expectEqual(tenantPayloads[0]?.egress_policy, 'deny', 'tenant create forwards egress policy')

  tenantListShouldFail = true
  const denied = await admin.listTenants()
  expectEqual(denied.state, 'permission-denied', '403 maps to permission-denied')
  expectEqual(denied.error?.code, 'PERMISSION_DENIED', '403 preserves error code')
  expectEqual(denied.error?.requestId, 'request-403', '403 preserves request id')

  assert(unsupportedUserListCalls === 0, 'admin adapter never calls a nonexistent user-list endpoint')
  assert(unsupportedProfileUpdateCalls === 0, 'admin adapter never calls a nonexistent profile-update endpoint')
}

void runM5ContractTests().then(() => console.log('M5 contract tests: PASS'))
