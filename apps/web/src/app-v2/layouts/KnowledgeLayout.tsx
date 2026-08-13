import type { ReactNode } from 'react'
import { GlobalShell } from './GlobalShell'
import type { AuthSession, V2Services } from '../types'

interface KnowledgeLayoutProps {
  readonly pageTitle: string
  readonly session: AuthSession
  readonly services: V2Services
  readonly onLogout: () => Promise<void>
  readonly children: ReactNode
}

export function KnowledgeLayout({ pageTitle, session, services, onLogout, children }: KnowledgeLayoutProps) {
  return (
    <GlobalShell pageTitle={pageTitle} routeId="knowledge" session={session} services={services} onLogout={onLogout}>
      <div className="v2-knowledge-page v2-page-content--flush">{children}</div>
    </GlobalShell>
  )
}
