import { defineConfig } from 'vitest/config'

export default defineConfig({
  test: {
    environment: 'node',
    include: [
      'src/app-v2/tests/v3*.test.{ts,tsx}',
      'src/app-v2/tests/m3.test.ts',
      'src/app-v2/tests/assistant-*.test.tsx',
    ],
    coverage: {
      include: ['src/app-v2/components/assistant/**'],
    },
  },
})
