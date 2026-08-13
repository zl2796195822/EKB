import { MapTrifold } from '@phosphor-icons/react'

interface ModuleMapLinkProps {
  readonly active?: boolean
}

export function ModuleMapLink({ active = false }: ModuleMapLinkProps) {
  return (
    <a
      className={active ? 'v2-module-map-link v2-module-map-link--active' : 'v2-module-map-link'}
      href="#/modules"
      aria-current={active ? 'page' : undefined}
    >
      <MapTrifold size={15} weight="regular" aria-hidden="true" />
      <span>模块地图</span>
    </a>
  )
}
