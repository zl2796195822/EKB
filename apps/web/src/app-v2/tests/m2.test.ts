import { getLocalGreeting } from '../components/dashboard/greeting'
import { getModuleMapRoutes } from '../components/module-map/routeManifest'
import { MODULE_MAP_ROUTE_ID, ROUTES } from '../routes'

function assert(condition: unknown, message: string): asserts condition {
  if (!condition) throw new Error(message)
}

export function runM2ContractTests(): void {
  assert(getLocalGreeting(new Date(2026, 0, 1, 7)) === '早上好', 'greeting should use the local morning hour')
  assert(getLocalGreeting(new Date(2026, 0, 1, 15)) === '下午好', 'greeting should use the local afternoon hour')
  assert(getLocalGreeting(new Date(2026, 0, 1, 21)) === '晚上好', 'greeting should use the local evening hour')

  const moduleRoutes = getModuleMapRoutes()
  assert(moduleRoutes.length === 9, 'module map should expose nine non-map routes')
  assert(!moduleRoutes.some((route) => route.id === MODULE_MAP_ROUTE_ID), 'module map should not link to itself')
  assert(
    moduleRoutes.every((route) => ROUTES.some((manifestRoute) => manifestRoute.id === route.id && manifestRoute.hash === route.hash)),
    'module map links should come from the route manifest',
  )
}

runM2ContractTests()
console.log('M2 contract tests: PASS')
