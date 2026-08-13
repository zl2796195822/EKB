interface UserAvatarProps {
  readonly name: string
  readonly compact?: boolean
}

export function UserAvatar({ name, compact = false }: UserAvatarProps) {
  const initial = name.trim().slice(0, 1).toUpperCase() || 'U'

  return (
    <span className={compact ? 'v2-user-avatar v2-user-avatar--compact' : 'v2-user-avatar'} aria-hidden="true">
      {initial}
    </span>
  )
}
