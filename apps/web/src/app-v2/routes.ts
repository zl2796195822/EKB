import {
  AnalyticsPage,
  AppsPage,
  AssistantPage,
  DashboardPage,
  DocumentsPage,
  KnowledgePage,
  ModuleMapPage,
  ProfilePage,
  RecyclePage,
  TeamPage,
  UploadTaskPage,
} from './pages'
import type { ResolvedRoute, RouteEntry, RouteId, UnknownRouteDefinition } from './types'

export const ROUTES = [
  {
    id: 'dashboard',
    hash: '#/dashboard',
    pageName: '工作台 / Dashboard',
    layout: 'global',
    milestone: 'M2',
    page: DashboardPage,
  },
  {
    id: 'knowledge',
    hash: '#/knowledge',
    pageName: '知识库 / Knowledge Base',
    layout: 'knowledge',
    milestone: 'M3',
    page: KnowledgePage,
  },
  {
    id: 'knowledge-uploads',
    hash: '#/knowledge/uploads',
    pageName: '上传任务中心 / Upload Task Center',
    layout: 'global',
    milestone: 'M3',
    page: UploadTaskPage,
  },
  {
    id: 'assistant',
    hash: '#/assistant',
    pageName: 'AI 助手 / AI Assistant',
    layout: 'assistant',
    milestone: 'M4',
    page: AssistantPage,
  },
  {
    id: 'documents',
    hash: '#/documents',
    pageName: '文档中心 / Document Center',
    layout: 'global',
    milestone: 'M3',
    page: DocumentsPage,
  },
  {
    id: 'team',
    hash: '#/team',
    pageName: '团队与权限 / Team & Permissions',
    layout: 'global',
    milestone: 'M5',
    page: TeamPage,
  },
  {
    id: 'analytics',
    hash: '#/analytics',
    pageName: '数据看板 / Analytics',
    layout: 'global',
    milestone: 'M6',
    page: AnalyticsPage,
  },
  {
    id: 'apps',
    hash: '#/apps',
    pageName: '应用中心 / App Center',
    layout: 'global',
    milestone: 'M6',
    page: AppsPage,
  },
  {
    id: 'recycle',
    hash: '#/recycle',
    pageName: '回收站 / Recycle Bin',
    layout: 'global',
    milestone: 'M6',
    page: RecyclePage,
  },
  {
    id: 'profile',
    hash: '#/profile',
    pageName: '个人中心 / Settings',
    layout: 'global',
    milestone: 'M5',
    page: ProfilePage,
  },
  {
    id: 'modules',
    hash: '#/modules',
    pageName: '模块地图 / Module Map',
    layout: 'module-map',
    milestone: 'M2',
    page: ModuleMapPage,
  },
] as const satisfies readonly RouteEntry[]

export const ROUTE_HASHES = ROUTES.map((route) => route.hash)

/**
 * The primary shell navigation is deliberately derived from the ten-route manifest.
 * Module map remains a separate entry so it can keep its dedicated return action.
 */
export const PRIMARY_ROUTE_IDS = [
  'dashboard',
  'knowledge',
  'assistant',
  'documents',
  'team',
  'analytics',
  'apps',
  'recycle',
  'profile',
] as const satisfies readonly RouteId[]

export const MODULE_MAP_ROUTE_ID: RouteId = 'modules'

const DEFAULT_ROUTE = ROUTES[0]

const UNKNOWN_ROUTE: UnknownRouteDefinition = {
  id: 'unknown',
  hash: '',
  pageName: '不可用路由',
  layout: 'global',
  milestone: 'M0',
}

function normalizeHash(rawHash: string): string {
  const hash = rawHash.trim()
  if (!hash) return DEFAULT_ROUTE.hash
  return hash.split('?')[0]
}

export function findRoute(routeId: RouteId): RouteEntry {
  return ROUTES.find((route) => route.id === routeId) ?? DEFAULT_ROUTE
}

export function resolveRoute(rawHash?: string): ResolvedRoute {
  const currentHash =
    rawHash ?? (typeof window === 'undefined' ? DEFAULT_ROUTE.hash : window.location.hash)
  const normalizedHash = normalizeHash(currentHash)
  return ROUTES.find((route) => route.hash === normalizedHash) ?? {
    ...UNKNOWN_ROUTE,
    hash: normalizedHash,
  }
}
