import type {
  FavoriteCheckResponse,
  FavoriteItemRecord,
  FavoritesListQuery,
  FavoritesListResponse,
  FavoriteToggleResponse,
} from '../../types/api'
import type {
  FavoriteCheckResult,
  FavoriteItemView,
  FavoritesListInput,
  FavoritesListResult,
  FavoritesPageView,
  FavoritesResourceKind,
  FavoritesServices,
  FavoriteToggleResult,
} from '../types/favorites'
import { FAVORITES_RESOURCE_TYPES } from '../types/favorites'
import { stateForError, toAdapterError } from './index'

export interface FavoritesApiClient {
  listFavorites(query?: FavoritesListQuery): Promise<FavoritesListResponse>
  checkFavorite(resourceType: string, resourceId: string): Promise<FavoriteCheckResponse>
  toggleFavorite(resourceType: string, resourceId: string): Promise<FavoriteToggleResponse>
}

export const FAVORITES_LIST_PAGE_SIZE = 50

function isResourceKind(value: string): value is FavoritesResourceKind {
  return (FAVORITES_RESOURCE_TYPES as readonly string[]).includes(value)
}

function mapResourceKind(value: string): FavoritesResourceKind {
  return isResourceKind(value) ? value : 'DOCUMENT'
}

function mapItem(input: FavoriteItemRecord): FavoriteItemView {
  return {
    id: input.id,
    resourceType: mapResourceKind(input.resource_type),
    resourceId: input.resource_id,
    title: input.title,
    parentId: input.parent_id,
    parentTitle: input.parent_title,
    favoritedAt: input.favorited_at,
  }
}

function mapPage(input: FavoritesListResponse): FavoritesPageView {
  const rts = (input.resource_types ?? FAVORITES_RESOURCE_TYPES).map(mapResourceKind)
  return {
    items: input.items.map(mapItem),
    total: input.total,
    counts: input.counts ?? {},
    resourceTypes: rts,
    limit: input.limit,
    offset: input.offset,
  }
}

function failure(error: unknown, fallbackMessage: string) {
  const mapped = toAdapterError(error, fallbackMessage)
  return { state: stateForError(mapped), error: mapped }
}

function toListQuery(input: FavoritesListInput): FavoritesListQuery {
  const limit = input.limit ?? FAVORITES_LIST_PAGE_SIZE
  const offset = input.offset ?? 0
  return {
    ...(input.resourceType ? { resource_type: input.resourceType } : {}),
    limit,
    offset,
  }
}

export function createFavoritesAdapter(client: FavoritesApiClient): FavoritesServices {
  return {
    list: async (input: FavoritesListInput = {}): Promise<FavoritesListResult> => {
      try {
        const data = mapPage(await client.listFavorites(toListQuery(input)))
        // items 是后端过滤掉已硬删除 / 越权条目的剩余列表：
        //   - total === 0：真空态
        //   - total > 0 && items.length === 0：全量都是不可见条目（保持 ready 但 UI 显示空列表）
        const state: FavoritesListResult['state'] =
          data.total === 0 ? 'empty' : 'ready'
        return { state, data }
      } catch (error) {
        return failure(error, '收藏夹加载失败')
      }
    },

    check: async (resourceType, resourceId): Promise<FavoriteCheckResult> => {
      if (!resourceType || !resourceId.trim()) {
        return {
          state: 'error',
          error: { code: 'FAVORITE_ID_REQUIRED', message: '收藏校验参数不能为空' },
        }
      }
      try {
        const response = await client.checkFavorite(resourceType, resourceId)
        return {
          state: 'ready',
          resourceType: mapResourceKind(response.resource_type),
          resourceId: response.resource_id,
          favorited: response.favorited,
          favoritedAt: response.favorited_at,
        }
      } catch (error) {
        return failure(error, '收藏状态校验失败')
      }
    },

    toggle: async (resourceType, resourceId): Promise<FavoriteToggleResult> => {
      if (!resourceType || !resourceId.trim()) {
        return {
          state: 'error',
          error: { code: 'FAVORITE_ID_REQUIRED', message: '收藏操作参数不能为空' },
        }
      }
      try {
        const response = await client.toggleFavorite(resourceType, resourceId)
        return {
          state: 'ready',
          resourceType: mapResourceKind(response.resource_type),
          resourceId: response.resource_id,
          favorited: response.favorited,
          favoritedAt: response.favorited_at,
        }
      } catch (error) {
        return failure(error, '收藏切换失败')
      }
    },
  }
}

export type FavoritesAdapter = FavoritesServices

export const FAVORITES_CAPABILITIES = [
  { id: 'favorites.list', status: 'available' },
  { id: 'favorites.filter-by-type', status: 'available' },
  { id: 'favorites.check-before-toggle', status: 'available' },
  { id: 'favorites.toggle-star', status: 'available' },
  {
    id: 'favorites.cross-tenant-visibility',
    status: 'unavailable',
    reason: 'favorites 为私密投影，每个 subject 只能看到自己的星标集合；跨用户共享收藏桶规划在后续 folders/tags/shares 路线图中。',
  },
  {
    id: 'favorites.bulk-toggle',
    status: 'unavailable',
    reason: '当前仅提供单笔 toggle；批量端点（收藏/取消收藏 N 条资源）若需要可在后续扩展，现阶段单笔幂等切换已经覆盖所有现有 UI 入口。',
  },
] as const
