import type { ComponentType } from 'react'
import type { AuthSession } from './auth'
import type { V2Services } from './services'

export type RouteId =
  | 'dashboard'
  | 'knowledge'
  | 'knowledge-uploads'
  | 'assistant'
  | 'documents'
  | 'team'
  | 'analytics'
  | 'apps'
  | 'recycle'
  | 'profile'
  | 'modules'

export type LayoutKind = 'global' | 'knowledge' | 'assistant' | 'module-map'

export type MilestoneId =
  | 'M0'
  | 'M1'
  | 'M2'
  | 'M3'
  | 'M4'
  | 'M5'
  | 'M6'
  | 'M7'

export interface RouteDefinition {
  readonly id: RouteId
  readonly hash: string
  readonly pageName: string
  readonly layout: LayoutKind
  readonly milestone: MilestoneId
}

export interface V2PageProps {
  readonly route: RouteDefinition
  readonly session: AuthSession
  readonly services: V2Services
  readonly state?: import('./view-state').PageState
}

export type V2PageComponent = ComponentType<V2PageProps>

export interface UnknownRouteDefinition {
  readonly id: 'unknown'
  readonly hash: string
  readonly pageName: string
  readonly layout: 'global'
  readonly milestone: 'M0'
}

export type ResolvedRoute = RouteEntry | UnknownRouteDefinition

export interface RouteEntry extends RouteDefinition {
  readonly page: V2PageComponent
}
