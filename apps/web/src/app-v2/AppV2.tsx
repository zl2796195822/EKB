import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { StatePanel } from './components'
import {
  createAppV2ApiClient,
  createAuthAdapter,
} from './adapters/auth'
import {
  createConversationsAdapter,
  createAdminAdapter,
  createDocumentsAdapter,
  createFavoritesAdapter,
  createFeedbackAdapter,
  createIdentityAdapter,
  createKnowledgeAdapter,
  createProfileAdapter,
  createQaStreamAdapter,
  createSearchAdapter,
  createTrashAdapter,
  createAnalyticsAdapter,
  createAppsAdapter,
  createLLMAdapter,
} from './adapters'
import { LoginPage } from './pages/LoginPage'
import { AppV2Layout } from './layouts'
import { findRoute, resolveRoute } from './routes'
import type { AuthNotice, AuthPhase, AuthSession, RouteEntry, V2Services } from './types'
import './styles/globals.css'

interface AppV2Props {
  readonly initialHash?: string
}

function readCurrentHash(initialHash?: string): string {
  if (initialHash) return initialHash
  if (typeof window === 'undefined') return ''
  return window.location.hash
}

function AuthLoadingState() {
  return (
    <main className="v2-auth-loading" role="status" aria-live="polite">
      <div className="v2-auth-loading-card">
        <span className="v2-loading-mark" aria-hidden="true" />
        <p className="v2-eyebrow">EKB / SESSION</p>
        <h1>正在恢复会话</h1>
        <p>正在通过真实认证服务校验当前会话，请稍候。</p>
      </div>
    </main>
  )
}

export function AppV2({ initialHash }: AppV2Props = {}) {
  const apiClient = useMemo(() => createAppV2ApiClient(), [])
  const auth = useMemo(() => createAuthAdapter(apiClient), [apiClient])
  const services = useMemo<V2Services>(
    () => ({
      conversations: createConversationsAdapter(apiClient),
      knowledge: createKnowledgeAdapter(apiClient),
      documents: createDocumentsAdapter(apiClient),
      search: createSearchAdapter(apiClient),
      qaStream: createQaStreamAdapter(apiClient),
      feedback: createFeedbackAdapter(apiClient),
      admin: createAdminAdapter(apiClient),
      identity: createIdentityAdapter(apiClient),
      profile: createProfileAdapter(apiClient),
      trash: createTrashAdapter(apiClient),
      analytics: createAnalyticsAdapter(apiClient),
      apps: createAppsAdapter(apiClient),
      favorites: createFavoritesAdapter(apiClient),
      llm: createLLMAdapter(apiClient),
    }),
    [apiClient],
  )
  const initialRouteHash = readCurrentHash(initialHash)
  const [currentHash, setCurrentHash] = useState(initialRouteHash)
  const [authPhase, setAuthPhase] = useState<AuthPhase>('restoring')
  const [session, setSession] = useState<AuthSession | null>(null)
  const [authNotice, setAuthNotice] = useState<AuthNotice | null>(null)
  const restorePromise = useRef<Promise<AuthSession | null> | null>(null)
  const pendingHash = useRef(initialRouteHash)

  const clearSession = useCallback(
    (notice: AuthNotice | null = null) => {
      auth.clear()
      setSession(null)
      setAuthPhase('unauthenticated')
      setAuthNotice(notice)
    },
    [auth],
  )

  useEffect(() => {
    if (typeof window === 'undefined') return

    const handleHashChange = () => {
      const nextHash = window.location.hash
      setCurrentHash(nextHash)
      if (resolveRoute(nextHash).id !== 'unknown') pendingHash.current = nextHash
    }

    window.addEventListener('hashchange', handleHashChange)
    return () => window.removeEventListener('hashchange', handleHashChange)
  }, [])

  useEffect(() => {
    const handleAuthFailure = () => {
      clearSession({ tone: 'expired', message: '登录已过期，请重新登录。' })
    }

    auth.setOnAuthFailure(handleAuthFailure)
    return () => auth.setOnAuthFailure(null)
  }, [auth, clearSession])

  useEffect(() => {
    restorePromise.current ??= auth.restore()
    let active = true

    void restorePromise.current.then((restoredSession) => {
      if (!active) return
      if (restoredSession) {
        setSession(restoredSession)
        setAuthPhase('authenticated')
        setAuthNotice(null)
      } else {
        setAuthPhase('unauthenticated')
      }
    })

    return () => {
      active = false
    }
  }, [auth])

  const handleLogin = useCallback(
    async (email: string, password: string) => {
      setAuthPhase('authenticating')
      setAuthNotice(null)
      try {
        const nextSession = await auth.login(email, password)
        setSession(nextSession)
        setAuthPhase('authenticated')
        const destination = pendingHash.current || findRoute('dashboard').hash
        if (typeof window !== 'undefined' && window.location.hash !== destination) {
          window.location.hash = destination
        } else {
          setCurrentHash(destination)
        }
      } catch {
        auth.clear()
        setSession(null)
        setAuthPhase('unauthenticated')
        setAuthNotice({
          tone: 'error',
          message: '登录失败，请检查邮箱和密码或稍后重试。',
        })
      }
    },
    [auth],
  )

  const handleLogout = useCallback(async () => {
    const result = await auth.logout()
    setSession(null)
    setAuthPhase('unauthenticated')
    setAuthNotice(result.error ? { tone: 'error', message: result.error } : null)
  }, [auth])

  if (authPhase === 'restoring') return <AuthLoadingState />

  if (!session || authPhase === 'unauthenticated' || authPhase === 'authenticating') {
    return (
      <LoginPage
        isSubmitting={authPhase === 'authenticating'}
        notice={authNotice}
        onSubmit={handleLogin}
      />
    )
  }

  const resolvedRoute = resolveRoute(currentHash)

  if (resolvedRoute.id === 'unknown') {
    return (
      <div className="v2-root" data-v2-entrypoint="parallel">
        <AppV2Layout
          kind={resolvedRoute.layout}
          pageTitle={resolvedRoute.pageName}
          routeId={resolvedRoute.id}
          session={session}
          services={services}
          onLogout={handleLogout}
        >
          <section className="v2-page-placeholder" aria-labelledby="v2-unknown-route">
            <p className="v2-eyebrow">M1 / route boundary</p>
            <h1 id="v2-unknown-route">{resolvedRoute.pageName}</h1>
            <StatePanel state="unavailable" message="该 hash 不在十页 route manifest 中。" />
          </section>
        </AppV2Layout>
      </div>
    )
  }

  const route = resolvedRoute as RouteEntry
  const Page = route.page

  return (
    <div className="v2-root" data-v2-entrypoint="parallel">
      <AppV2Layout
        kind={route.layout}
        pageTitle={route.pageName}
        routeId={route.id}
        session={session}
        services={services}
        onLogout={handleLogout}
      >
        <Page route={route} session={session} services={services} />
      </AppV2Layout>
    </div>
  )
}

export default AppV2
