import {
  ChartLineUp,
  FileText,
  FolderOpen,
  GearSix,
  House,
  Robot,
  ShieldCheck,
  SquaresFour,
  Trash,
  UserCircle,
  UsersThree,
} from '@phosphor-icons/react'
import type { RouteId } from '../../types'

export function RouteIcon({ routeId, size = 17 }: { readonly routeId: RouteId; readonly size?: number }) {
  switch (routeId) {
    case 'dashboard':
      return <House size={size} weight="regular" />
    case 'knowledge':
      return <FolderOpen size={size} weight="regular" />
    case 'assistant':
      return <Robot size={size} weight="regular" />
    case 'documents':
      return <FileText size={size} weight="regular" />
    case 'team':
      return <UsersThree size={size} weight="regular" />
    case 'analytics':
      return <ChartLineUp size={size} weight="regular" />
    case 'apps':
      return <SquaresFour size={size} weight="regular" />
    case 'recycle':
      return <Trash size={size} weight="regular" />
    case 'profile':
      return <UserCircle size={size} weight="regular" />
    case 'modules':
      return <GearSix size={size} weight="regular" />
  }
}

export function PermissionIcon({ size = 15 }: { readonly size?: number }) {
  return <ShieldCheck size={size} weight="regular" />
}
