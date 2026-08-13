import type { JSX } from 'react'
import { CaretDown, CaretRight, ChatCircleDots, ClockCounterClockwise, FileText, FolderOpen, FolderSimple, Plus, Star, Trash } from '@phosphor-icons/react'
import { StatePanel } from '../StatePanel'
import type { KnowledgeBaseView, PageState } from '../../types'
import type { FavoriteItemView, FavoritesResourceKind } from '../../types/favorites'

interface KnowledgeSpaceRailProps {
  readonly state: PageState
  readonly spaces: readonly KnowledgeBaseView[]
  readonly selectedId: string
  readonly onSelect: (id: string) => void
  readonly onCreate: () => void
  readonly favoritesState: PageState
  readonly favoritesItems: readonly FavoriteItemView[]
  readonly favoritesTotal: number
  readonly favoritesCounts: Readonly<Record<string, number>>
  readonly onToggleFavorites: () => void
  readonly favoritesOpen: boolean
  readonly onOpenFavorite: (resourceType: FavoritesResourceKind, resourceId: string, parentId: string | null) => void
}

const RESOURCE_BAR_COLORS: Record<FavoritesResourceKind, string> = {
  KB: '#D97706',
  DOCUMENT: '#0EA5E9',
  CONVERSATION: '#059669',
}

const RESOURCE_ICONS: Record<FavoritesResourceKind, (props: { size: number; 'aria-hidden': boolean }) => JSX.Element> = {
  KB: FolderSimple as unknown as (props: { size: number; 'aria-hidden': boolean }) => JSX.Element,
  DOCUMENT: FileText as unknown as (props: { size: number; 'aria-hidden': boolean }) => JSX.Element,
  CONVERSATION: ChatCircleDots as unknown as (props: { size: number; 'aria-hidden': boolean }) => JSX.Element,
}

const RESOURCE_LABELS: Record<FavoritesResourceKind, string> = {
  KB: '知识库',
  DOCUMENT: '文档',
  CONVERSATION: '会话',
}

function formatFavoritedAt(value: string): string {
  const d = new Date(value)
  if (Number.isNaN(d.getTime())) return ''
  const diff = Date.now() - d.getTime()
  const minutes = Math.floor(diff / 60000)
  if (minutes < 1) return '刚刚'
  if (minutes < 60) return `${minutes} 分钟前`
  const hours = Math.floor(minutes / 60)
  if (hours < 24) return `${hours} 小时前`
  const days = Math.floor(hours / 24)
  if (days < 7) return `${days} 天前`
  return d.toLocaleDateString('zh-CN')
}

export function KnowledgeSpaceRail({
  state,
  spaces,
  selectedId,
  onSelect,
  onCreate,
  favoritesState,
  favoritesItems,
  favoritesTotal,
  favoritesCounts,
  onToggleFavorites,
  favoritesOpen,
  onOpenFavorite,
}: KnowledgeSpaceRailProps) {
  return (
    <aside className="v2-knowledge-rail" aria-label="知识空间导航">
      <div className="v2-m3-rail-heading-row">
        <strong className="v2-rail-heading">知识空间</strong>
        <button type="button" className="v2-plain-icon-button" onClick={onCreate} aria-label="创建知识库" title="创建知识库">
          <Plus size={15} aria-hidden="true" />
        </button>
      </div>
      <button type="button" className="v2-rail-item v2-rail-item--active" onClick={() => onSelect('')}>
        <FolderOpen size={16} aria-hidden="true" />
        <span>全部空间</span>
      </button>
      <div className="v2-m3-space-list" aria-label="已授权知识库">
        {state === 'loading' ? <StatePanel state="loading" message="正在加载授权知识库。" /> : null}
        {state === 'error' || state === 'permission-denied' ? (
          <StatePanel state={state} message="知识库列表不可用，请按页面提示重试。" />
        ) : null}
        {state === 'empty' ? <StatePanel state="empty" message="当前主体尚未访问任何知识库。" /> : null}
        {state === 'ready'
          ? spaces.map((space) => (
              <button
                type="button"
                className={space.id === selectedId ? 'v2-rail-item v2-rail-item--selected' : 'v2-rail-item'}
                key={space.id}
                onClick={() => onSelect(space.id)}
                title={`${space.name} · ${space.role}`}
              >
                <span className="v2-m3-space-dot" aria-hidden="true" />
                <span>{space.name}</span>
              </button>
            ))
          : null}
      </div>
      <div className="v2-rail-divider" />
      <a href="#/dashboard" className="v2-rail-item" title="跳转到工作台最近访问区" style={{ textDecoration: 'none', color: 'inherit' }}>
        <ClockCounterClockwise size={16} aria-hidden="true" />
        <span>最近访问</span>
      </a>
      <div className="v2-m3-favorites-group">
        <button
          type="button"
          className={favoritesOpen ? 'v2-rail-item v2-rail-item--open' : 'v2-rail-item'}
          onClick={onToggleFavorites}
          aria-expanded={favoritesOpen}
          title={`收藏夹：共 ${favoritesTotal} 条星标（KB ${favoritesCounts.KB ?? 0} · 文档 ${favoritesCounts.DOCUMENT ?? 0} · 会话 ${favoritesCounts.CONVERSATION ?? 0}）`}
        >
          {favoritesOpen ? <CaretDown size={12} aria-hidden="true" /> : <CaretRight size={12} aria-hidden="true" />}
          <Star size={16} aria-hidden="true" weight={favoritesTotal > 0 ? 'fill' : 'regular'} />
          <span style={{ display: 'inline-flex', alignItems: 'center', gap: 8 }}>
            收藏夹
            <small
              aria-hidden="true"
              style={{
                padding: '1px 7px',
                borderRadius: 999,
                fontSize: 10,
                fontWeight: 500,
                background: 'rgba(15, 118, 110, 0.08)',
                color: '#0F766E',
                lineHeight: 1.4,
                fontVariantNumeric: 'tabular-nums',
              }}
            >
              {favoritesTotal}
            </small>
          </span>
        </button>
        {favoritesOpen ? (
          <div className="v2-m3-favorites-panel" role="region" aria-label="星标收藏列表">
            {favoritesState === 'loading' ? (
              <StatePanel state="loading" message="正在加载星标收藏…" />
            ) : favoritesState === 'error' || favoritesState === 'permission-denied' ? (
              <StatePanel state={favoritesState} message="收藏列表加载失败。" />
            ) : favoritesState === 'empty' || favoritesTotal === 0 ? (
              <StatePanel
                state="empty"
                message="还没有星标收藏。"
                reason="在知识库、文档或会话页点击☆即可添加；星标仅你自己可见。"
              />
            ) : favoritesItems.length === 0 ? (
              <StatePanel
                state="unavailable"
                message="收藏投影存在，但资源均已不可见（可能已删除或越权）。"
                reason="投影行保留用于审计；服务端 list 已过滤硬删除/越权条目。"
              />
            ) : (
              <ul className="v2-m3-favorites-list">
                {favoritesItems.map((item) => {
                  const ResourceIcon = RESOURCE_ICONS[item.resourceType]
                  const barColor = RESOURCE_BAR_COLORS[item.resourceType]
                  return (
                    <li key={item.id}>
                      <button
                        type="button"
                        className="v2-m3-favorite-item"
                        onClick={() => onOpenFavorite(item.resourceType, item.resourceId, item.parentId)}
                        title={`${RESOURCE_LABELS[item.resourceType]} · ${item.title}${item.parentTitle ? ` · ${item.parentTitle}` : ''}`}
                      >
                        <span className="v2-m3-favorite-bar" aria-hidden="true" style={{ background: barColor }} />
                        <ResourceIcon size={14} aria-hidden={true} />
                        <span className="v2-m3-favorite-body">
                          <span className="v2-m3-favorite-title">{item.title || '未命名'}</span>
                          <span className="v2-m3-favorite-meta">
                            <small className="v2-m3-favorite-kind">{RESOURCE_LABELS[item.resourceType]}</small>
                            {item.parentTitle ? <small>· {item.parentTitle}</small> : null}
                            <small>· {formatFavoritedAt(item.favoritedAt)}</small>
                          </span>
                        </span>
                      </button>
                    </li>
                  )
                })}
              </ul>
            )}
          </div>
        ) : null}
      </div>
      <a href="#/recycle" className="v2-rail-item" title="跳转到回收站，可还原或永久删除已删除的资源" style={{ textDecoration: 'none', color: 'inherit' }}>
        <Trash size={16} aria-hidden="true" />
        <span>回收站</span>
      </a>
    </aside>
  )
}
