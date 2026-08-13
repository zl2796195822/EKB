import { ArrowRight } from '@phosphor-icons/react'
import type { ReactNode } from 'react'
import type { RouteId } from '../../types'

interface ModuleTileProps {
  readonly index: number
  readonly routeId: RouteId
  readonly hash: string
  readonly title: string
  readonly subtitle: string
  readonly description: string
  readonly icon: ReactNode
}

export function ModuleTile({ index, routeId, hash, title, subtitle, description, icon }: ModuleTileProps) {
  return (
    <a className="v2-module-tile" href={hash} data-route-id={routeId}>
      <span className="v2-module-tile-top">
        <span className="v2-module-index" aria-hidden="true">
          {index}
        </span>
        <span aria-hidden="true">{icon}</span>
      </span>
      <h2>
        {title} <small>({subtitle})</small>
      </h2>
      <p>{description}</p>
      <span className="v2-module-tile-link">
        查看页面
        <ArrowRight size={13} aria-hidden="true" />
      </span>
    </a>
  )
}
