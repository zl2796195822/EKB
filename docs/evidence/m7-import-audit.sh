#!/usr/bin/env bash
# M7 旧 UI 生产 import 审计脚本（可复核）
# 用法：bash docs/evidence/m7-import-audit.sh
set -uo pipefail
cd "$(dirname "$0")/../.." || exit 1

run() {
  echo "\$ $*"
  eval "$@"
  local code=$?
  if [ "$code" -ne 0 ]; then echo "(no matches)"; fi
  echo
}

echo "# M7 旧 UI 生产 import 审计"
echo
echo "生成时间: $(date '+%Y-%m-%d %H:%M:%S %z')"
echo

echo '## 1. 生产入口'
echo '```'
run "cat apps/web/src/main.tsx"
echo '```'

echo '## 2. 按真实路径解析每条相对 import（纯正则会误判 app-v2 自身的 pages/components，故用解析器）'
echo '```'
run "node docs/evidence/import-boundary-check.mjs"
echo '```'

echo "## 3. 全仓对旧样式表的任何引用"
echo '```'
run "rg -n 'styles\\.css' apps/web/src apps/web/index.html apps/web/vite.config.ts"
echo '```'

echo "## 4. 旧 UI 组件名/hook 在生产代码中的任何残留引用"
echo '```'
run "rg -n 'ChatPanel|ConversationHistory|DocVersionPanel|KnowledgeSidebar|StatusBadge|GalaxyCard|GalaxyHero|DockBar|PillNavTabs|HitCard|useHashPage' apps/web/src"
echo '```'

echo "## 5. src 目录现状（旧 UI 文件已不存在）"
echo '```'
run "ls -1 apps/web/src"
run "ls -1 apps/web/src/lib apps/web/src/types"
echo '```'

echo "## 6. 构建产物中不再出现旧页面 chunk"
echo '```'
run "ls -1 apps/web/dist/assets"
echo '```'
