import type { ReactNode } from 'react'
import type { AuthSession, LayoutKind, RouteId, V2Services } from '../types'
import { AssistantLayout } from './AssistantLayout'
import { GlobalShell } from './GlobalShell'
import { KnowledgeLayout } from './KnowledgeLayout'
import { ModuleMapLayout } from './ModuleMapLayout'

interface AppV2LayoutProps {
  readonly kind: LayoutKind
  readonly pageTitle: string
  readonly routeId: RouteId | 'unknown'
  readonly session: AuthSession
  readonly services: V2Services
  readonly onLogout: () => Promise<void>
  readonly children: ReactNode
}

export function AppV2Layout({
  kind,
  pageTitle,
  routeId,
  session,
  services,
  onLogout,
  children,
}: AppV2LayoutProps) {
  switch (kind) {
    case 'knowledge':
      return (
        <KnowledgeLayout pageTitle={pageTitle} session={session} services={services} onLogout={onLogout}>
          {children}
        </KnowledgeLayout>
      )
    case 'assistant':
      return (
        <AssistantLayout pageTitle={pageTitle} session={session} services={services} onLogout={onLogout}>
          {children}
        </AssistantLayout>
      )
    case 'module-map':
      return (
        <ModuleMapLayout pageTitle={pageTitle} session={session} services={services} onLogout={onLogout}>
          {children}
        </ModuleMapLayout>
      )
    case 'global':
      return (
        <GlobalShell
          pageTitle={pageTitle}
          routeId={routeId}
          session={session}
          services={services}
          onLogout={onLogout}
        >
          {children}
        </GlobalShell>
      )
  }
}
