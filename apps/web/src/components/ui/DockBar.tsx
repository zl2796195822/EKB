import {
  BookOpen,
  ChatCircle,
  Gauge,
  MagnifyingGlass,
} from '@phosphor-icons/react'
import type { FC } from 'react'
import type { GalaxyPageId } from '../../hooks/useHashPage'

export type DockBarItem = {
  id: GalaxyPageId
  label: string
  Icon: typeof ChatCircle
}

export const DOCK_ITEMS: DockBarItem[] = [
  { id: 'qa', label: '问答助手', Icon: ChatCircle },
  { id: 'kb', label: '知识库', Icon: BookOpen },
  { id: 'search', label: '检索中心', Icon: MagnifyingGlass },
  { id: 'ops', label: '运营看板', Icon: Gauge },
]

export type DockBarProps = {
  activePage: GalaxyPageId
  onChange: (page: GalaxyPageId) => void
}

export const DockBar: FC<DockBarProps> = ({ activePage, onChange }) => {
  return (
    <nav className="dock-bar" role="tablist" aria-label="Dock 快捷导航">
      {DOCK_ITEMS.map(({ id, label, Icon }) => {
        const isOn = activePage === id
        return (
          <button
            key={id}
            type="button"
            role="tab"
            aria-selected={isOn}
            aria-label={label}
            className={isOn ? 'on' : undefined}
            onClick={() => onChange(id)}
          >
            <Icon className="dock-icon" weight={isOn ? 'fill' : 'regular'} aria-hidden />
            <span className="dock-tip">{label}</span>
          </button>
        )
      })}
    </nav>
  )
}
