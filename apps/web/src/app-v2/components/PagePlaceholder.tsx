import type { V2PageProps } from '../types'
import { StatePanel } from './StatePanel'

interface PagePlaceholderProps extends V2PageProps {
  readonly title: string
  readonly description: string
}

export function PagePlaceholder({
  route,
  state = 'unavailable',
  title,
  description,
}: PagePlaceholderProps) {
  return (
    <section className="v2-page-placeholder" data-route-id={route.id} aria-labelledby={'v2-page-' + route.id}>
      <p className="v2-eyebrow">M0 / {route.hash}</p>
      <h1 id={'v2-page-' + route.id}>{title}</h1>
      <p className="v2-page-description">{description}</p>
      <StatePanel state={state} />
    </section>
  )
}
