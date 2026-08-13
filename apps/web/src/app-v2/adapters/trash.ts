import type {
  TrashClearResponse,
  TrashItemRecord,
  TrashListQuery,
  TrashListResponse,
  TrashPurgeResponse,
  TrashRestoreResponse,
} from '../../types/api'
import type {
  TrashClearResult,
  TrashItemView,
  TrashListInput,
  TrashListResult,
  TrashPageView,
  TrashPurgeResult,
  TrashResourceKind,
  TrashRestoreResult,
  TrashServices,
} from '../types/trash'
import { TRASH_RESOURCE_TYPES } from '../types/trash'
import { stateForError, toAdapterError } from './index'

/** app-v2 只依赖这四个方法，避免把整个 ApiClient 表面积带进 UI 层。 */
export interface TrashApiClient {
  listTrash(query?: TrashListQuery): Promise<TrashListResponse>
  restoreTrashItem(itemId: string): Promise<TrashRestoreResponse>
  purgeTrashItem(itemId: string): Promise<TrashPurgeResponse>
  clearTrash(resourceType?: string): Promise<TrashClearResponse>
}

export const TRASH_LIST_PAGE_SIZE = 20

function isResourceKind(value: string): value is TrashResourceKind {
  return (TRASH_RESOURCE_TYPES as readonly string[]).includes(value)
}

/** 后端理论上只会给这三种值，但投影表是自由文本列，兜底到 DOCUMENT 而不是抛错。 */
function mapResourceKind(value: string): TrashResourceKind {
  return isResourceKind(value) ? value : 'DOCUMENT'
}

function mapItem(input: TrashItemRecord): TrashItemView {
  return {
    id: input.id,
    resourceType: mapResourceKind(input.resource_type),
    resourceId: input.resource_id,
    title: input.title,
    parentId: input.parent_id,
    parentTitle: input.parent_title,
    deletedBy: input.deleted_by,
    deletedAt: input.deleted_at,
    expiresAt: input.expires_at,
    restorable: input.restorable,
    blockedReason: input.blocked_reason,
  }
}

function mapPage(input: TrashListResponse): TrashPageView {
  return {
    items: input.items.map(mapItem),
    total: input.total,
    counts: input.counts ?? {},
    limit: input.limit,
    offset: input.offset,
  }
}

function failure(error: unknown, fallbackMessage: string) {
  const mapped = toAdapterError(error, fallbackMessage)
  return { state: stateForError(mapped), error: mapped }
}

function toListQuery(input: TrashListInput): TrashListQuery {
  const limit = input.limit ?? TRASH_LIST_PAGE_SIZE
  const offset = input.offset ?? 0
  const trimmed = input.query?.trim()
  return {
    ...(input.resourceType ? { resource_type: input.resourceType } : {}),
    ...(trimmed ? { q: trimmed } : {}),
    limit,
    offset,
  }
}

export function createTrashAdapter(client: TrashApiClient): TrashServices {
  return {
    list: async (input: TrashListInput = {}): Promise<TrashListResult> => {
      try {
        const data = mapPage(await client.listTrash(toListQuery(input)))
        // total 为 0 才是真空态；当前页翻过头只是 ready 的空列表，不该显示"回收站是空的"。
        return { state: data.total > 0 ? 'ready' : 'empty', data }
      } catch (error) {
        return failure(error, '回收站列表加载失败')
      }
    },

    restore: async (itemId): Promise<TrashRestoreResult> => {
      if (!itemId.trim()) {
        return { state: 'error', error: { code: 'TRASH_ITEM_REQUIRED', message: '回收站条目 ID 不能为空' } }
      }
      try {
        const response = await client.restoreTrashItem(itemId)
        return {
          state: 'ready',
          resourceType: mapResourceKind(response.item.resource_type),
          resourceId: response.item.resource_id,
        }
      } catch (error) {
        return failure(error, '还原失败')
      }
    },

    purge: async (itemId): Promise<TrashPurgeResult> => {
      if (!itemId.trim()) {
        return { state: 'error', error: { code: 'TRASH_ITEM_REQUIRED', message: '回收站条目 ID 不能为空' } }
      }
      try {
        const response = await client.purgeTrashItem(itemId)
        return {
          state: 'ready',
          resourceType: mapResourceKind(response.resource_type),
          resourceId: response.resource_id,
          removed: response.removed ?? {},
        }
      } catch (error) {
        return failure(error, '永久删除失败')
      }
    },

    clear: async (resourceType): Promise<TrashClearResult> => {
      try {
        const response = await client.clearTrash(resourceType ?? undefined)
        return { state: 'ready', purged: response.purged }
      } catch (error) {
        return failure(error, '清空回收站失败')
      }
    },
  }
}

export type TrashAdapter = TrashServices

export const TRASH_CAPABILITIES = [
  { id: 'trash.list', status: 'available' },
  { id: 'trash.filter-by-type', status: 'available' },
  { id: 'trash.keyword-search', status: 'available' },
  { id: 'trash.restore', status: 'available' },
  { id: 'trash.purge', status: 'available' },
  { id: 'trash.clear', status: 'available' },
  {
    id: 'trash.auto-purge-job',
    status: 'available',
  },
] as const
