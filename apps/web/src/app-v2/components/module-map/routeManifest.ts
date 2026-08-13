import { MODULE_MAP_ROUTE_ID, ROUTES } from '../../routes'

export function getModuleMapRoutes() {
  return ROUTES.filter((route) => route.id !== MODULE_MAP_ROUTE_ID)
}
