import type { ReactNode } from 'react'
import { GlobalShell } from './GlobalShell'
import type { AuthSession, V2Services } from '../types'

interface ModuleMapLayoutProps {
  readonly pageTitle: string
  readonly session: AuthSession
  readonly services: V2Services
  readonly onLogout: () => Promise<void>
  readonly children: ReactNode
}

export function ModuleMapLayout({ pageTitle, session, services, onLogout, children }: ModuleMapLayoutProps) {
  return (
    <GlobalShell pageTitle={pageTitle} routeId="modules" session={session} services={services} onLogout={onLogout}>
      <div className="v2-module-map-content">{children}</div>
    </GlobalShell>
  )
}
