import { ChatCircleDots, FolderSimple, Heart, MagnifyingGlass, Plus, ClockCounterClockwise } from '@phosphor-icons/react'
import { StatePanel } from '../StatePanel'
import type { ConversationView, KnowledgeBaseView, PageState } from '../../types'
import type { FavoriteItemView } from '../../types/favorites'

export interface ConversationGroup {
  readonly label: string
  readonly items: readonly ConversationView[]
}

interface AssistantSidebarProps {
  readonly open: boolean
  readonly conversationsState: PageState
  readonly groups: readonly ConversationGroup[]
  readonly selectedConversationId: string
  readonly selectedKnowledgeBase: KnowledgeBaseView | null
  readonly searchValue: string
  readonly activeTab: 'recent' | 'favorites' | 'projects'
  readonly onSearchChange: (value: string) => void
  readonly onTabChange: (tab: 'recent' | 'favorites' | 'projects') => void
  readonly onSelectConversation: (conversationId: string) => void
  readonly onNewConversation: () => void
  readonly errorMessage?: string
  readonly favoritesState: PageState
  readonly favoriteConversations: readonly FavoriteItemView[]
  readonly favoritesTotal: number
}

function formatConversationTime(value: string): string {
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return '—'
  return new Intl.DateTimeFormat('zh-CN', { hour: '2-digit', minute: '2-digit' }).format(date)
}

function formatFavoritedAtShort(value: string): string {
  const d = new Date(value)
  if (Number.isNaN(d.getTime())) return '—'
  return new Intl.DateTimeFormat('zh-CN', { hour: '2-digit', minute: '2-digit' }).format(d)
}

export function AssistantSidebar({
  open,
  conversationsState,
  groups,
  selectedConversationId,
  selectedKnowledgeBase,
  searchValue,
  activeTab,
  onSearchChange,
  onTabChange,
  onSelectConversation,
  onNewConversation,
  errorMessage,
  favoritesState,
  favoriteConversations,
  favoritesTotal,
}: AssistantSidebarProps) {
  const className = open ? 'v2-m4-history v2-m4-history--mobile-open' : 'v2-m4-history'

  return (
    <aside className={className} aria-label="会话列表">
      <div className="v2-m4-rail-heading">
        <div>
          <p className="v2-eyebrow">WORKSPACE</p>
          <h2>AI 助手</h2>
        </div>
        <ChatCircleDots size={18} aria-hidden="true" />
      </div>
      <button type="button" className="v2-m4-new-conversation" onClick={onNewConversation}>
        <Plus size={16} aria-hidden="true" />
        新建会话
      </button>
      <label className="v2-m4-session-search">
        <MagnifyingGlass size={15} aria-hidden="true" />
        <input
          aria-label="搜索已加载会话"
          value={searchValue}
          onChange={(event) => onSearchChange(event.target.value)}
          placeholder="搜索会话"
        />
      </label>
      <div className="v2-m4-tabs" role="tablist" aria-label="会话视图">
        <button type="button" role="tab" aria-selected={activeTab === 'recent'} className={activeTab === 'recent' ? 'is-active' : ''} onClick={() => onTabChange('recent')}>
          <ClockCounterClockwise size={14} aria-hidden="true" />最近
        </button>
        <button
          type="button"
          role="tab"
          aria-selected={activeTab === 'favorites'}
          className={activeTab === 'favorites' ? 'is-active' : ''}
          onClick={() => onTabChange('favorites')}
          title={`收藏会话：共 ${favoritesTotal} 条星标会话`}
        >
          <Heart size={14} aria-hidden="true" weight={favoritesTotal > 0 ? 'fill' : 'regular'} />收藏
          <small
            aria-hidden="true"
            style={{
              marginLeft: 4,
              padding: '1px 5px',
              borderRadius: 999,
              fontSize: 9,
              fontWeight: 500,
              background: favoritesTotal > 0 ? 'rgba(5, 150, 105, 0.1)' : 'rgba(148, 163, 184, 0.1)',
              color: favoritesTotal > 0 ? '#059669' : '#64748B',
            }}
          >
            {favoritesTotal}
          </small>
        </button>
        <button type="button" role="tab" aria-selected={false} aria-disabled="true" disabled title="v4 P2 内容治理：文件夹/项目桶端点（会话分组 + 拖拽）规划中，当前不伪造本地项目容器。">
          <FolderSimple size={14} aria-hidden="true" />项目
          <small aria-hidden="true" style={{ marginLeft: 4, padding: '1px 5px', borderRadius: 999, fontSize: 9, fontWeight: 500, background: '#FEF3C7', color: '#92400E' }}>v4 P2</small>
        </button>
      </div>

      <div className="v2-m4-session-list">
        {activeTab === 'favorites' ? (
          favoritesState === 'loading' ? (
            <StatePanel state="loading" message="正在加载已星标会话…" />
          ) : favoritesState === 'error' || favoritesState === 'permission-denied' ? (
            <StatePanel state={favoritesState} message="收藏会话列表加载失败。" />
          ) : favoritesState === 'empty' || favoriteConversations.length === 0 ? (
            <StatePanel
              state="empty"
              message="还没有收藏任何会话。"
              reason="在聊天区点击 ☆ 即可收藏当前会话；收藏仅你自己可见，便于快速回溯重要对话。"
            />
          ) : (
            <section className="v2-m4-session-group" key="favorites">
              <h3>已收藏会话 · {favoriteConversations.length}</h3>
              {favoriteConversations.map((favorite) => (
                <button
                  type="button"
                  key={favorite.id}
                  className={favorite.resourceId === selectedConversationId ? 'v2-m4-session-item is-selected' : 'v2-m4-session-item'}
                  onClick={() => onSelectConversation(favorite.resourceId)}
                  title={`星标会话 · ${favorite.title || '未命名会话'}${favorite.parentTitle ? ` · ${favorite.parentTitle}` : ''}`}
                >
                  <span className="v2-m4-session-item-title">
                    <Heart size={12} aria-hidden="true" weight="fill" style={{ color: '#059669', marginRight: 6, flexShrink: 0 }} />
                    {favorite.title || '未命名会话'}
                  </span>
                  <time dateTime={favorite.favoritedAt}>{formatFavoritedAtShort(favorite.favoritedAt)}</time>
                </button>
              ))}
            </section>
          )
        ) : activeTab === 'projects' ? (
          <StatePanel
            state="unavailable"
            message="项目视图：规划中（v4 P2 内容治理）"
            reason="会话分组（项目桶）+ 拖拽排序端点完成后，此处切换为真实项目列表；当前不伪造本地项目容器。"
          />
        ) : conversationsState === 'loading' ? (
          <StatePanel state="loading" message="正在加载当前授权范围内的真实会话。" />
        ) : conversationsState === 'error' || conversationsState === 'permission-denied' ? (
          <StatePanel state={conversationsState} message={errorMessage ?? '会话列表加载失败。'} />
        ) : groups.length === 0 ? (
          <StatePanel state="empty" message={searchValue.trim() ? '没有匹配的已加载会话。' : '当前授权范围内还没有会话。'} />
        ) : (
          groups.map((group) => (
            <section className="v2-m4-session-group" key={group.label}>
              <h3>{group.label}</h3>
              {group.items.map((conversation) => (
                <button
                  type="button"
                  key={conversation.id}
                  className={conversation.id === selectedConversationId ? 'v2-m4-session-item is-selected' : 'v2-m4-session-item'}
                  onClick={() => onSelectConversation(conversation.id)}
                >
                  <span className="v2-m4-session-item-title">{conversation.title || '未命名会话'}</span>
                  <time dateTime={conversation.updatedAt}>{formatConversationTime(conversation.updatedAt)}</time>
                </button>
              ))}
            </section>
          ))
        )}
      </div>
      <div className="v2-m4-current-space">
        <span className="v2-m4-space-dot" aria-hidden="true" />
        <span>
          <small>当前知识空间</small>
          <strong>{selectedKnowledgeBase?.name ?? '未选择知识库'}</strong>
        </span>
      </div>
    </aside>
  )
}
