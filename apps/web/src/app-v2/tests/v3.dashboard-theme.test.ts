import { readFileSync } from 'node:fs'
import { describe, expect, it } from 'vitest'

const rawColorLiteral = /#[0-9a-f]{3,8}\b|rgba?\s*\(/i

describe('Dashboard theme contract', () => {
  it('uses semantic variables instead of page-level raw color literals', () => {
    const dashboardSource = readFileSync(new URL('../pages/DashboardPage.tsx', import.meta.url), 'utf8')

    expect(dashboardSource).not.toMatch(rawColorLiteral)
  })
})
