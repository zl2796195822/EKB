import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { AppV2 } from './app-v2/AppV2'

type ThemePreference = 'light' | 'dark' | 'auto'

const THEME_STORAGE_KEY = 'v2.theme'
const SYSTEM_THEME_QUERY = '(prefers-color-scheme: dark)'

function readThemePreference(): ThemePreference {
  if (typeof window === 'undefined') return 'light'
  const stored = window.localStorage?.getItem(THEME_STORAGE_KEY)
  return stored === 'dark' || stored === 'auto' ? stored : 'light'
}

function resolveTheme(preference: ThemePreference, media: MediaQueryList | null): 'light' | 'dark' {
  return preference === 'auto' ? (media?.matches ? 'dark' : 'light') : preference
}

if (typeof document !== 'undefined' && typeof window !== 'undefined') {
  const media = window.matchMedia?.(SYSTEM_THEME_QUERY) ?? null
  const applyTheme = () => {
    document.documentElement.dataset.theme = resolveTheme(readThemePreference(), media)
  }

  applyTheme()
  if (media) {
    const handleSystemThemeChange = () => {
      if (readThemePreference() === 'auto') applyTheme()
    }
    media.addEventListener?.('change', handleSystemThemeChange)
    // Safari versions without addEventListener use the legacy MediaQueryList API.
    if (!media.addEventListener) media.addListener?.(handleSystemThemeChange)
    window.addEventListener('storage', (event) => {
      if (event.key === THEME_STORAGE_KEY) applyTheme()
    })
  }
}

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <AppV2 />
  </StrictMode>,
)
