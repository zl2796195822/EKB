import type { AdapterError } from './adapters'
import type { PageState } from './view-state'

/** 后端 trash_items 投影只认这三种资源类型。 */
export const TRASH_RESOURCE_TYPES = ['KB', 'DOCUMENT', 'CONVERSATION'] as const

export type TrashResourceKind = (typeof TRASH_RESOURCE_TYPES)[number]

/** 后端保留期常量，与 migrations/v3_002_content.py 的 TRASH_RETENTION_DAYS 对齐。 */
export const TRASH_RETENTION_DAYS = 30

export interface TrashItemView {
  readonly id: string
  readonly resourceType: TrashResourceKind
  readonly resourceId: string
  readonly title: string
  readonly parentId: string | null
  readonly parentTitle: string | null
  readonly deletedBy: string | null
  readonly deletedAt: string
  readonly expiresAt: string
  /** 父知识库仍在回收站时，文档不允许单独还原。 */
  readonly restorable: boolean
  readonly blockedReason: string | null
}

export interface TrashPageView {
  readonly items: readonly TrashItemView[]
  readonly total: number
  readonly counts: Readonly<Record<string, number>>
  readonly limit: number
  readonly offset: number
}

export interface TrashListInput {
  readonly resourceType?: TrashResourceKind | null
  readonly query?: string
  readonly limit?: number
  readonly offset?: number
}

export interface TrashListResult {
  readonly state: PageState
  readonly data?: TrashPageView
  readonly error?: AdapterError
}

export interface TrashRestoreResult {
  readonly state: PageState
  readonly resourceType?: TrashResourceKind
  readonly resourceId?: string
  readonly error?: AdapterError
}

export interface TrashPurgeResult {
  readonly state: PageState
  readonly resourceType?: TrashResourceKind
  readonly resourceId?: string
  readonly removed?: Readonly<Record<string, number>>
  readonly error?: AdapterError
}

export interface TrashClearResult {
  readonly state: PageState
  readonly purged?: number
  readonly error?: AdapterError
}

export interface TrashServices {
  list(input?: TrashListInput): Promise<TrashListResult>
  restore(itemId: string): Promise<TrashRestoreResult>
  purge(itemId: string): Promise<TrashPurgeResult>
  clear(resourceType?: TrashResourceKind | null): Promise<TrashClearResult>
}
