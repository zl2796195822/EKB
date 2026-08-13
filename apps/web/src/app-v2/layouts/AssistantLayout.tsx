import type { ReactNode } from 'react'
import { GlobalShell } from './GlobalShell'
import type { AuthSession, V2Services } from '../types'

interface AssistantLayoutProps {
  readonly pageTitle: string
  readonly session: AuthSession
  readonly services: V2Services
  readonly onLogout: () => Promise<void>
  readonly children: ReactNode
}

export function AssistantLayout({ pageTitle, session, services, onLogout, children }: AssistantLayoutProps) {
  return (
    <GlobalShell pageTitle={pageTitle} routeId="assistant" session={session} services={services} onLogout={onLogout}>
      <div className="v2-assistant-main v2-page-content--flush">{children}</div>
    </GlobalShell>
  )
}
