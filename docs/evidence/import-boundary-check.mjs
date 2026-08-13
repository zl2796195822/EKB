#!/usr/bin/env node
// 生产 import 边界检查（可复核）
// 1) 解析 apps/web/src 下每个文件的每条相对 import，确认没有任何一条落回旧 UI 路径。
// 2) 确认只有 app-v2/adapters/** 可以引用 lib/api.ts 与 types/api.ts。
// 3) 确认 pages/components/layouts 不出现 fetch(。
// 用法：node docs/evidence/import-boundary-check.mjs
import { readFileSync, readdirSync, statSync, existsSync } from 'node:fs'
import { dirname, join, relative, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const repoRoot = resolve(dirname(fileURLToPath(import.meta.url)), '../..')
const srcRoot = join(repoRoot, 'apps/web/src')
const appV2Root = join(srcRoot, 'app-v2')

// 旧 UI 在 M7 之前所处的位置。
const LEGACY_PATHS = [
  join(srcRoot, 'App.tsx'),
  join(srcRoot, 'pages'),
  join(srcRoot, 'components'),
  join(srcRoot, 'hooks'),
  join(srcRoot, 'utils'),
  join(srcRoot, 'styles.css'),
  join(srcRoot, 'styles.css.bak'),
]

// 允许被 adapters 引用的非 UI 后端能力层。
const CAPABILITY_MODULES = [join(srcRoot, 'lib/api'), join(srcRoot, 'types/api')]

const EXTENSIONS = ['', '.ts', '.tsx', '.css', '/index.ts', '/index.tsx']
const SOURCE_RE = /\.(ts|tsx)$/

function walk(dir) {
  const out = []
  for (const entry of readdirSync(dir)) {
    const full = join(dir, entry)
    if (statSync(full).isDirectory()) out.push(...walk(full))
    else out.push(full)
  }
  return out
}

function importSpecifiers(code) {
  const specs = []
  const re = /(?:from|import)\s+['"]([^'"]+)['"]/g
  let match
  while ((match = re.exec(code)) !== null) specs.push(match[1])
  return specs
}

function resolveSpecifier(fromFile, spec) {
  const base = resolve(dirname(fromFile), spec)
  for (const ext of EXTENSIONS) {
    if (existsSync(base + ext)) return base + ext
  }
  return base
}

function isUnder(target, root) {
  return target === root || target.startsWith(root + '/')
}

const violations = []
const capabilityImporters = new Set()
const files = walk(srcRoot).filter((file) => SOURCE_RE.test(file))

for (const file of files) {
  const code = readFileSync(file, 'utf8')
  const rel = relative(repoRoot, file)

  for (const spec of importSpecifiers(code)) {
    if (!spec.startsWith('.')) continue
    const target = resolveSpecifier(file, spec)

    for (const legacy of LEGACY_PATHS) {
      if (isUnder(target, legacy)) {
        violations.push(`[legacy-ui] ${rel} imports '${spec}' -> ${relative(repoRoot, target)}`)
      }
    }

    for (const capability of CAPABILITY_MODULES) {
      if (target === capability || target === capability + '.ts') {
        capabilityImporters.add(rel)
        const inAdapters = isUnder(file, join(appV2Root, 'adapters'))
        const inTests = isUnder(file, join(appV2Root, 'tests'))
        const isEntry = file === join(srcRoot, 'main.tsx')
        // 能力层内部互相引用（lib/api.ts -> types/api.ts）不属于 UI 越界。
        const inCapabilityLayer = isUnder(file, join(srcRoot, 'lib')) || isUnder(file, join(srcRoot, 'types'))
        if (!inAdapters && !inTests && !isEntry && !inCapabilityLayer) {
          violations.push(`[boundary] ${rel} imports the capability layer '${spec}' outside adapters/`)
        }
      }
    }
  }

  const inUiLayer =
    isUnder(file, join(appV2Root, 'pages')) ||
    isUnder(file, join(appV2Root, 'components')) ||
    isUnder(file, join(appV2Root, 'layouts'))
  if (inUiLayer && /\bfetch\(/.test(code)) {
    violations.push(`[boundary] ${rel} calls fetch( inside the UI layer`)
  }
}

console.log(`scanned files: ${files.length}`)
console.log(`legacy UI paths checked: ${LEGACY_PATHS.map((p) => relative(repoRoot, p)).join(', ')}`)
console.log(`legacy UI paths still present on disk: ${LEGACY_PATHS.filter((p) => existsSync(p)).length}`)
console.log('capability-layer importers:')
for (const importer of [...capabilityImporters].sort()) console.log(`  - ${importer}`)
console.log('')

if (violations.length > 0) {
  console.log('VIOLATIONS:')
  for (const violation of violations) console.log(`  - ${violation}`)
  console.log('')
  console.log('Import boundary check: FAILED')
  process.exit(1)
}

console.log('Import boundary check: PASSED')
