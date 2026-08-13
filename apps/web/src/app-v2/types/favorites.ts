import type { AdapterError } from './adapters'
import type { PageState } from './view-state'

/** 与 v3_007_content_governance_compat 的 CHECK 约束保持一致。 */
export const FAVORITES_RESOURCE_TYPES = ['KB', 'DOCUMENT', 'CONVERSATION'] as const

export type FavoritesResourceKind = (typeof FAVORITES_RESOURCE_TYPES)[number]

export interface FavoriteItemView {
  readonly id: string
  readonly resourceType: FavoritesResourceKind
  readonly resourceId: string
  readonly title: string
  readonly parentId: string | null
  readonly parentTitle: string | null
  readonly favoritedAt: string
}

export interface FavoritesPageView {
  readonly items: readonly FavoriteItemView[]
  readonly total: number
  readonly counts: Readonly<Record<string, number>>
  readonly resourceTypes: readonly FavoritesResourceKind[]
  readonly limit: number
  readonly offset: number
}

export interface FavoritesListInput {
  readonly resourceType?: FavoritesResourceKind | null
  readonly limit?: number
  readonly offset?: number
}

export interface FavoritesListResult {
  readonly state: PageState
  readonly data?: FavoritesPageView
  readonly error?: AdapterError
}

export interface FavoriteCheckResult {
  readonly state: PageState
  readonly resourceType?: FavoritesResourceKind
  readonly resourceId?: string
  readonly favorited?: boolean
  readonly favoritedAt?: string | null
  readonly error?: AdapterError
}

export interface FavoriteToggleResult {
  readonly state: PageState
  readonly resourceType?: FavoritesResourceKind
  readonly resourceId?: string
  readonly favorited?: boolean
  readonly favoritedAt?: string | null
  readonly error?: AdapterError
}

export interface FavoritesServices {
  list(input?: FavoritesListInput): Promise<FavoritesListResult>
  check(resourceType: FavoritesResourceKind, resourceId: string): Promise<FavoriteCheckResult>
  toggle(resourceType: FavoritesResourceKind, resourceId: string): Promise<FavoriteToggleResult>
}
