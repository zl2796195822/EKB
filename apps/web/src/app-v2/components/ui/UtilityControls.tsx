import { Bell, GearSix, Question, SignOut, UserCircle } from '@phosphor-icons/react'
import { useCallback, useState } from 'react'
import type { AuthSession } from '../../types'
import { UserAvatar } from './UserAvatar'

interface UtilityControlsProps {
  readonly session: AuthSession
  readonly onLogout: () => Promise<void>
  readonly compact?: boolean
}

type UtilityPanel = 'notifications' | 'help' | 'settings' | 'user' | null

export function UtilityControls({ session, onLogout, compact = false }: UtilityControlsProps) {
  const [openPanel, setOpenPanel] = useState<UtilityPanel>(null)
  const [isLoggingOut, setIsLoggingOut] = useState(false)

  const togglePanel = (panel: Exclude<UtilityPanel, null>) => {
    setOpenPanel((current) => (current === panel ? null : panel))
  }

  const handleLogout = useCallback(async () => {
    setIsLoggingOut(true)
    try {
      await onLogout()
    } finally {
      setIsLoggingOut(false)
      setOpenPanel(null)
    }
  }, [onLogout])

  return (
    <div className={compact ? 'v2-utility-controls v2-utility-controls--compact' : 'v2-utility-controls'}>
      <button
        type="button"
        className="v2-icon-button v2-icon-button--notice"
        aria-label="通知"
        aria-expanded={openPanel === 'notifications'}
        title="通知"
        onClick={() => togglePanel('notifications')}
      >
        <Bell size={17} aria-hidden="true" />
        <span className="v2-notice-dot" aria-hidden="true" />
      </button>
      <button
        type="button"
        className="v2-icon-button"
        aria-label="帮助"
        aria-expanded={openPanel === 'help'}
        title="帮助"
        onClick={() => togglePanel('help')}
      >
        <Question size={17} aria-hidden="true" />
      </button>
      <button
        type="button"
        className="v2-icon-button"
        aria-label="设置"
        aria-expanded={openPanel === 'settings'}
        title="设置"
        onClick={() => togglePanel('settings')}
      >
        <GearSix size={17} aria-hidden="true" />
      </button>
      <div className="v2-user-control-wrap">
        <button
          type="button"
          className="v2-user-control"
          aria-label="打开用户菜单"
          aria-expanded={openPanel === 'user'}
          onClick={() => togglePanel('user')}
        >
          <UserAvatar name={session.user.name} compact />
          <span className="v2-user-control-copy">
            <strong>{session.user.name}</strong>
            <small>{session.user.email}</small>
          </span>
          <UserCircle size={15} aria-hidden="true" />
        </button>
        {openPanel === 'user' ? (
          <div className="v2-utility-popover v2-utility-popover--user" role="menu">
            <div className="v2-utility-popover-heading">
              <UserAvatar name={session.user.name} compact />
              <span>
                <strong>{session.user.name}</strong>
                <small>{session.tenants[0]?.name ?? '当前主体'}</small>
              </span>
            </div>
            <button type="button" role="menuitem" disabled={isLoggingOut} onClick={handleLogout}>
              <SignOut size={15} aria-hidden="true" />
              {isLoggingOut ? '正在退出…' : '退出登录'}
            </button>
          </div>
        ) : null}
      </div>
      {openPanel && openPanel !== 'user' ? (
        <div className="v2-utility-popover" role="status">
          {openPanel === 'notifications' ? '当前没有可显示的通知。' : null}
          {openPanel === 'help' ? '帮助中心入口将在后续里程碑接入。' : null}
          {openPanel === 'settings' ? '设置入口请从个人中心访问。' : null}
        </div>
      ) : null}
    </div>
  )
}
