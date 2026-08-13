export interface AuthUser {
  readonly id: string
  readonly name: string
  readonly email: string
}

export interface AuthTenant {
  readonly id: string
  readonly name: string
  readonly role: string
}

export interface AuthSession {
  readonly subjectId: string
  readonly tenantId: string
  readonly accessTokenExpiresAt: string
  readonly user: AuthUser
  readonly tenants: readonly AuthTenant[]
  readonly capabilities: readonly string[]
  readonly policyVersion: number
}

export type AuthPhase = 'restoring' | 'unauthenticated' | 'authenticating' | 'authenticated'

export interface AuthNotice {
  readonly tone: 'error' | 'expired'
  readonly message: string
}
