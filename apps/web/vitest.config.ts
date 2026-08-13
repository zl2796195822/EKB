import { defineConfig } from 'vitest/config'

export default defineConfig({
  test: {
    environment: 'node',
    include: ['src/app-v2/tests/v3*.test.ts'],
  },
})
