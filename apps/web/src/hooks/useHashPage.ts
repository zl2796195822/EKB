import { useCallback, useEffect, useState } from 'react'

export type GalaxyPageId = 'qa' | 'kb' | 'search' | 'ops'

export const GALAXY_PAGE_ORDER: GalaxyPageId[] = ['qa', 'kb', 'search', 'ops']

const DEFAULT_PAGE: GalaxyPageId = 'qa'

function isValidPage(raw: string): raw is GalaxyPageId {
  return GALAXY_PAGE_ORDER.includes(raw as GalaxyPageId)
}

function readPageFromHash(): GalaxyPageId {
  if (typeof window === 'undefined') return DEFAULT_PAGE
  const raw = window.location.hash.replace(/^#\/?/, '').trim()
  if (raw && isValidPage(raw)) return raw
  return DEFAULT_PAGE
}

export function usePrefersReducedMotion(): boolean {
  const [reduced, setReduced] = useState<boolean>(() => {
    if (typeof window === 'undefined' || !window.matchMedia) return false
    return window.matchMedia('(prefers-reduced-motion: reduce)').matches
  })

  useEffect(() => {
    if (typeof window === 'undefined' || !window.matchMedia) return
    const media = window.matchMedia('(prefers-reduced-motion: reduce)')
    const onChange = (event: MediaQueryListEvent) => setReduced(event.matches)
    media.addEventListener('change', onChange)
    return () => media.removeEventListener('change', onChange)
  }, [])

  return reduced
}

export type UseHashPageReturn = readonly [
  GalaxyPageId,
  (page: GalaxyPageId) => void,
]

export function useHashPage(): UseHashPageReturn {
  const [activePage, setActivePage] = useState<GalaxyPageId>(() => readPageFromHash())

  const goToPage = useCallback((page: GalaxyPageId) => {
    if (!isValidPage(page)) return
    if (typeof window !== 'undefined') {
      const nextHash = `#/${page}`
      if (window.location.hash !== nextHash) {
        window.history.pushState(null, '', nextHash)
      }
    }
    setActivePage(page)
    window.dispatchEvent(new HashChangeEvent('hashchange'))
  }, [])

  useEffect(() => {
    const onHashChange = () => {
      setActivePage((current) => {
        const next = readPageFromHash()
        return next === current ? current : next
      })
    }
    window.addEventListener('hashchange', onHashChange)
    window.addEventListener('popstate', onHashChange)
    return () => {
      window.removeEventListener('hashchange', onHashChange)
      window.removeEventListener('popstate', onHashChange)
    }
  }, [])

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      const modifier = event.metaKey || event.ctrlKey
      if (!modifier) return
      const key = event.key
      if (!/^[1-4]$/.test(key)) return
      const index = Number(key) - 1
      const next = GALAXY_PAGE_ORDER[index]
      if (!next) return
      event.preventDefault()
      goToPage(next)
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [goToPage])

  return [activePage, goToPage] as const
}
