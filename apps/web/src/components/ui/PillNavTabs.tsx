import type { FC } from 'react'
import type { GalaxyPageId } from '../../hooks/useHashPage'

export type PillNavTabItem = {
  id: GalaxyPageId
  label: string
  kbd: string
}

export const PILL_NAV_TABS: PillNavTabItem[] = [
  { id: 'qa', label: '问答助手', kbd: '⌘1' },
  { id: 'kb', label: '知识库', kbd: '⌘2' },
  { id: 'search', label: '检索中心', kbd: '⌘3' },
  { id: 'ops', label: '运营看板', kbd: '⌘4' },
]

export type PillNavTabsProps = {
  activePage: GalaxyPageId
  onChange: (page: GalaxyPageId) => void
}

export const PillNavTabs: FC<PillNavTabsProps> = ({ activePage, onChange }) => {
  return (
    <div className="pill-nav-shell">
      <div className="content-rail">
        <nav className="pill-nav" role="tablist" aria-label="主导航">
          {PILL_NAV_TABS.map((tab) => {
            const isOn = activePage === tab.id
            return (
              <button
                key={tab.id}
                type="button"
                role="tab"
                aria-selected={isOn}
                className={isOn ? 'on' : undefined}
                onClick={() => onChange(tab.id)}
              >
                <span>{tab.label}</span>
                <span className="nav-kbd">{tab.kbd}</span>
              </button>
            )
          })}
        </nav>
      </div>
    </div>
  )
}
