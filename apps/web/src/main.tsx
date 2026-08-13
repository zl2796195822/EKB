import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { AppV2 } from './app-v2/AppV2'

if (typeof document !== 'undefined' && typeof window !== 'undefined') {
  const stored = window.localStorage?.getItem('v2.theme') ?? 'light'
  const resolved =
    stored === 'auto'
      ? window.matchMedia?.('(prefers-color-scheme: dark)').matches
        ? 'dark'
        : 'light'
      : stored === 'dark'
        ? 'dark'
        : 'light'
  document.documentElement.dataset.theme = resolved
}

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <AppV2 />
  </StrictMode>,
)
