import { CaretRight, List, MapTrifold, Plus, Question, SidebarSimple, Stack } from '@phosphor-icons/react'
import { useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import { StatePanel } from '../components/StatePanel'
import { findRoute, MODULE_MAP_ROUTE_ID, PRIMARY_ROUTE_IDS } from '../routes'
import type { AuthSession, KnowledgeBaseView, PageState, RouteId, V2Services } from '../types'
import { BrandMark, RouteIcon, SearchField, UtilityControls } from '../components/ui'

interface GlobalShellProps {
  readonly pageTitle: string
  readonly routeId: RouteId | 'unknown'
  readonly session: AuthSession
  readonly services: V2Services
  readonly onLogout: () => Promise<void>
  readonly children: ReactNode
}

const SIDEBAR_SPACE_LIMIT = 6

function routeLabel(routeId: RouteId): string {
  return findRoute(routeId).pageName.split(' / ')[0]
}

function readKbParam(hash: string): string {
  const [, query] = hash.split('?')
  if (!query) return ''
  return new URLSearchParams(query).get('kb')?.trim() ?? ''
}

export function GlobalShell({ pageTitle, routeId, session, services, onLogout, children }: GlobalShellProps) {
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false)
  const [mobileNavigationOpen, setMobileNavigationOpen] = useState(false)
  const [searchValue, setSearchValue] = useState('')
  const [spacesState, setSpacesState] = useState<PageState>('loading')
  const [spaces, setSpaces] = useState<readonly KnowledgeBaseView[]>([])
  const searchRef = useRef<HTMLInputElement | null>(null)
  const title = pageTitle.split(' / ')[0]
  const activeKbId = typeof window === 'undefined' ? '' : readKbParam(window.location.hash)

  useEffect(() => {
    const handleShortcut = (event: KeyboardEvent) => {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === 'k') {
        event.preventDefault()
        searchRef.current?.focus()
      }
    }
    window.addEventListener('keydown', handleShortcut)
    return () => window.removeEventListener('keydown', handleShortcut)
  }, [])

  useEffect(() => {
    let cancelled = false
    const loadSpaces = async () => {
      setSpacesState('loading')
      const result = await services.knowledge.list()
      if (cancelled) return
      setSpacesState(result.state)
      setSpaces(result.data ?? [])
    }
    void loadSpaces()
    return () => {
      cancelled = true
    }
  }, [services.knowledge])

  const usage = useMemo(() => {
    const documents = spaces.reduce((sum, space) => sum + space.documentCount, 0)
    return { spaceCount: spaces.length, documentCount: documents }
  }, [spaces])

  const visibleSpaces = spaces.slice(0, SIDEBAR_SPACE_LIMIT)
  const hiddenSpaceCount = spaces.length - visibleSpaces.length

  const goTo = (hash: string) => {
    setMobileNavigationOpen(false)
    window.location.hash = hash
  }

  const toggleSidebar = () => setSidebarCollapsed((collapsed) => !collapsed)
  const toggleMobileNavigation = () => setMobileNavigationOpen((open) => !open)

  return (
    <div className="v2-global-shell" data-v2-layout="global">
      <aside
        className={mobileNavigationOpen ? 'v2-global-sidebar v2-global-sidebar--mobile-open' : 'v2-global-sidebar'}
        data-collapsed={sidebarCollapsed}
        aria-label="全局导航"
      >
        <div className="v2-sidebar-brand">
          <BrandMark compact={sidebarCollapsed} />
          <span className="v2-sidebar-brand-copy">
            <strong>团队 AI 知识库</strong>
            <small>企业知识工作区</small>
          </span>
          <button
            type="button"
            className="v2-sidebar-toggle"
            aria-label={sidebarCollapsed ? '展开侧栏' : '收起侧栏'}
            title={sidebarCollapsed ? '展开侧栏' : '收起侧栏'}
            onClick={toggleSidebar}
          >
            <SidebarSimple size={16} aria-hidden="true" />
          </button>
        </div>

        <nav className="v2-primary-nav" aria-label="主导航">
          <span className="v2-nav-label">主导航</span>
          {PRIMARY_ROUTE_IDS.map((primaryRouteId) => {
            const route = findRoute(primaryRouteId)
            const isActive = routeId === primaryRouteId
            return (
              <a
                className={isActive ? 'v2-nav-item v2-nav-item--active' : 'v2-nav-item'}
                href={route.hash}
                aria-current={isActive ? 'page' : undefined}
                key={route.id}
                onClick={() => setMobileNavigationOpen(false)}
              >
                <RouteIcon routeId={route.id} />
                <span>{routeLabel(route.id)}</span>
              </a>
            )
          })}
        </nav>

        <div className="v2-sidebar-divider" />
        <button type="button" className="v2-nav-item v2-nav-item--module" onClick={() => goTo(findRoute(MODULE_MAP_ROUTE_ID).hash)}>
          <MapTrifold size={17} aria-hidden="true" />
          <span>模块地图</span>
        </button>

        <div className="v2-sidebar-spaces">
          <div className="v2-sidebar-section-heading">
            <span>知识空间{spacesState === 'ready' ? ` · ${spaces.length}` : ''}</span>
            <button
              type="button"
              className="v2-plain-icon-button"
              aria-label="前往知识库新建空间"
              title="前往知识库新建空间"
              onClick={() => goTo('#/knowledge')}
            >
              <Plus size={14} aria-hidden="true" />
            </button>
          </div>
          {spacesState === 'loading' ? (
            <StatePanel state="loading" message="正在读取已授权知识空间。" />
          ) : spacesState === 'permission-denied' ? (
            <StatePanel state="permission-denied" message="当前主体无权读取知识空间列表。" />
          ) : spacesState === 'error' ? (
            <StatePanel state="error" message="知识空间读取失败，未使用本地缓存替代。" />
          ) : spaces.length === 0 ? (
            <StatePanel state="empty" message="当前账号还没有已授权的知识空间。" />
          ) : (
            <div className="v2-space-list">
              {visibleSpaces.map((space) => {
                const isActive = routeId === 'knowledge' && activeKbId === space.id
                return (
                  <button
                    type="button"
                    key={space.id}
                    className={isActive ? 'v2-space-item v2-space-item--active' : 'v2-space-item'}
                    aria-current={isActive ? 'true' : undefined}
                    title={space.description || space.name}
                    onClick={() => goTo(`#/knowledge?kb=${encodeURIComponent(space.id)}`)}
                  >
                    <Stack size={15} aria-hidden="true" />
                    <span className="v2-space-item-name">{space.name}</span>
                    <span className="v2-space-item-count">{space.documentCount}</span>
                  </button>
                )
              })}
              {hiddenSpaceCount > 0 ? (
                <button type="button" className="v2-space-more" onClick={() => goTo('#/knowledge')}>
                  <span>查看全部 {spaces.length} 个空间</span>
                  <CaretRight size={13} aria-hidden="true" />
                </button>
              ) : null}
            </div>
          )}
        </div>

        <div className="v2-sidebar-bottom">
          <div className="v2-plan-card">
            <strong>空间用量</strong>
            {spacesState === 'loading' ? (
              <StatePanel state="loading" message="正在统计真实用量。" />
            ) : spacesState === 'error' || spacesState === 'permission-denied' ? (
              <StatePanel state={spacesState} message="用量统计不可用。" />
            ) : (
              <div className="v2-plan-usage">
                <div>
                  <span>知识库</span>
                  <strong>{usage.spaceCount}</strong>
                </div>
                <div>
                  <span>文档</span>
                  <strong>{usage.documentCount}</strong>
                </div>
              </div>
            )}
          </div>
          <button type="button" className="v2-help-link" onClick={() => goTo(findRoute(MODULE_MAP_ROUTE_ID).hash)}>
            <Question size={15} aria-hidden="true" />
            <span>帮助与支持</span>
          </button>
          <div className="v2-sidebar-profile">
            <span className="v2-user-avatar">{session.user.name.slice(0, 1).toUpperCase() || 'U'}</span>
            <span className="v2-sidebar-profile-copy">
              <strong>{session.user.name}</strong>
              <small>{session.user.email}</small>
            </span>
          </div>
        </div>
      </aside>

      <div className="v2-global-content">
        <header className="v2-global-header">
          <div className="v2-header-left">
            <button
              type="button"
              className="v2-mobile-menu-button"
              aria-label={mobileNavigationOpen ? '关闭导航' : '打开导航'}
              aria-expanded={mobileNavigationOpen}
              onClick={toggleMobileNavigation}
            >
              <List size={18} aria-hidden="true" />
            </button>
            <span className="v2-breadcrumb-home"><BrandMark compact /></span>
            <span className="v2-breadcrumb-separator">/</span>
            <strong>{title}</strong>
          </div>
          <div className="v2-header-center">
            <SearchField
              value={searchValue}
              onChange={setSearchValue}
              placeholder="搜索知识库、文档、AI 助手…"
              shortcut="⌘ K"
              ariaLabel="全局搜索"
            />
          </div>
          <UtilityControls session={session} onLogout={onLogout} />
        </header>
        <main className="v2-page-content">{children}</main>
      </div>
    </div>
  )
}
