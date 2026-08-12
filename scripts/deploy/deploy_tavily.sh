#!/usr/bin/env bash
# Tavily 联网搜索接入的热部署脚本（复用 deploy/lib 的 SSH/rsync 封装，更可靠）
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${HERE}/../.." && pwd)"
cd "${REPO_ROOT}"

export COPYFILE_DISABLE=1

# shellcheck source=scripts/deploy/lib/log.sh
source "${REPO_ROOT}/scripts/deploy/lib/log.sh" 2>/dev/null || {
  log_ok()   { echo "[OK]   $*"; }
  log_info() { echo "[INFO] $*"; }
  log_step() { echo ""; echo "=== $* ==="; }
  log_warn() { echo "[WARN] $*"; }
  die()      { echo "[FAIL] $*" >&2; exit 1; }
}
# shellcheck source=scripts/deploy/lib/ssh.sh
source "${REPO_ROOT}/scripts/deploy/lib/ssh.sh"

ekb_require_deploy_config

# --- Step 0: 前端构建（已有产物时跳过 build，用时间戳检查）---
log_step "Step 0：前端构建（若需要）"
WEB_DIR="${REPO_ROOT}/apps/web"
NEED_BUILD=1
if [ -f "${WEB_DIR}/dist/index.html" ] && [ -n "$(find "${WEB_DIR}/src" -newer "${WEB_DIR}/dist/index.html" 2>/dev/null | head -1)" ]; then
  NEED_BUILD=1
elif [ ! -f "${WEB_DIR}/dist/index.html" ]; then
  NEED_BUILD=1
fi
if [ "${NEED_BUILD}" = "1" ]; then
  log_info "正在 vite build ..."
  ( cd "${WEB_DIR}" && npx --yes tsc -b >/dev/null 2>&1 && npx --yes vite build --logLevel warn 2>&1 | tail -5 )
  [ -f "${WEB_DIR}/dist/index.html" ] || die "vite build 未产出 dist/index.html"
  log_ok "前端构建完成"
else
  log_ok "前端 dist 已最新，跳过 build"
fi

# --- Step 1: 打包后端 ---
log_step "Step 1：打包后端 ekb_api + pyproject.toml"
STAGE_TMP="/tmp/ekb_tavily_stage"
rm -rf "${STAGE_TMP}" && mkdir -p "${STAGE_TMP}"
cp -R "${REPO_ROOT}/apps/api/ekb_api" "${STAGE_TMP}/"
cp "${REPO_ROOT}/apps/api/pyproject.toml" "${STAGE_TMP}/"
tar czf /tmp/ekb_tavily_update.tar.gz \
  --exclude='__pycache__' --exclude='*.pyc' --exclude='*.pyo' \
  --exclude='.venv' --exclude='*.db' --exclude='*.sqlite3' \
  --exclude='backups/' --exclude='.pytest_cache/' \
  --exclude='._*' --exclude='*.egg-info' \
  -C "${STAGE_TMP}" ekb_api pyproject.toml
log_ok "打包完成：$(du -sh /tmp/ekb_tavily_update.tar.gz | awk '{print $1}')"

# --- Step 2: SSH 连通测试 + 上传 ---
log_step "Step 2：SSH 连通 + 上传 tarball"
if ! ekb_ssh_test; then
  ekb_ensure_host_key
  ekb_ssh_test || die "SSH 仍不通，终止部署"
fi
log_ok "SSH connectivity OK"

ekb_scp /tmp/ekb_tavily_update.tar.gz \
  "${DEPLOY_USER}@${DEPLOY_HOST}:/tmp/ekb_tavily_update.tar.gz"
log_ok "tarball 上传"

# --- Step 3: 远程容器热更新 ---
log_step "Step 3：容器侧更新（docker cp + httpx + TAVILY env + restart + ensure_admin）"
ekb_ssh "bash -s" <<'REMOTE_SCRIPT'
set -euo pipefail

cd /tmp
rm -rf ekb_tavily_update && mkdir ekb_tavily_update
tar xzf ekb_tavily_update.tar.gz -C ekb_tavily_update
find ekb_tavily_update -name '._*' -delete 2>/dev/null || true

# -- (A) 停+禁用 systemd ekb-api，避免和 docker 容器端口冲突 --
systemctl stop ekb-api.service 2>/dev/null || true
systemctl disable ekb-api.service 2>/dev/null || true
echo "  [A] systemd ekb-api.service 已停止+禁用（project memory 要求）"

# -- (B) 容器存在性检查 --
if ! docker ps -a --format '{{.Names}}' | grep -q '^ekb-api$'; then
  echo "  [B] FAIL: ekb-api 容器不存在，请先确认容器部署。退出。"
  exit 2
fi
echo "  [B] ekb-api 容器存在，继续。"

# -- (C) docker cp 更新文件 --
for f in \
  routers/qa.py \
  core/web_search.py \
  core/metrics.py \
  schemas.py \
  __init__.py; do
  if [ -f "ekb_tavily_update/ekb_api/${f}" ]; then
    docker cp "ekb_tavily_update/ekb_api/${f}" "ekb-api:/app/ekb_api/${f}"
    echo "  [C] 更新: ekb_api/${f}"
  fi
done
docker cp "ekb_tavily_update/pyproject.toml" "ekb-api:/app/pyproject.toml"
echo "  [C] 更新: pyproject.toml"

# -- (D) 清理 .pyc 缓存 --
docker exec -u 0 ekb-api find /app/ekb_api -name '__pycache__' -type d -exec rm -rf {} + 2>/dev/null || true
echo "  [D] __pycache__ 清理"

# -- (E) 安装 httpx 依赖 --
echo "  [E] 检查/安装 httpx >=0.27,<1 ..."
docker exec -u 0 ekb-api sh -c "
  set -e
  if ! python3 -c 'import httpx' 2>/dev/null; then
    echo '    httpx 未安装 -> pip install ...'
    pip install --quiet 'httpx>=0.27,<1' >/dev/null 2>&1
  else
    CUR=\$(python3 -c 'import httpx; print(httpx.__version__)')
    echo '    httpx 已安装（v'\${CUR}'）'
  fi
"

# -- (F) Require runtime TAVILY configuration without reading or printing values --
echo "  [F] 检查运行时 TAVILY 配置 ..."
docker exec -u 0 ekb-api sh -c "
  ENV_FILE=/app/.env
  if ! grep -Eq '^EKB_TAVILY_API_KEY=.+$' \${ENV_FILE} 2>/dev/null || ! grep -Eq '^EKB_TAVILY_BASE_URL=https?://[^[:space:]]+$' \${ENV_FILE} 2>/dev/null; then
    echo '    FAIL: required runtime TAVILY settings are missing; refusing provider setup'
    exit 78
  fi
  if ! grep -q '^EKB_WEB_SEARCH_ENABLED=' \${ENV_FILE} 2>/dev/null; then
    echo 'EKB_WEB_SEARCH_ENABLED=true' >> \${ENV_FILE}
    echo '    +EKB_WEB_SEARCH_ENABLED=true'
  fi
  echo '    runtime TAVILY configuration present (values withheld)'
"

# -- (G) 重启容器 + healthz 循环 --
echo "  [G] 重启 ekb-api 容器并等待 /healthz ..."
docker restart ekb-api >/dev/null
sleep 2
for i in $(seq 1 20); do
  if docker exec ekb-api curl -fsS --max-time 3 http://127.0.0.1:8000/healthz 2>/dev/null; then
    echo "    /healthz OK（第 $i 次）"
    break
  fi
  echo -n "."
  sleep 2
  if [ $i -eq 20 ]; then
    echo ""
    echo "    FAIL: /healthz 40s 内未就绪；日志保留在受管运行环境中"
    exit 3
  fi
done

# -- (H) ensure_admin：凭据来自运行时受管配置，不在日志中打印 --
echo "  [H] ensure_admin 使用运行时受管凭据 ..."
docker exec -u 0 ekb-api sh -c "
  set -a; . /app/.env 2>/dev/null; set +a || true
  python /app/ensure_admin.py >/dev/null 2>&1
" || { echo "    [H] FAIL: ensure_admin refused; deployment stopped" >&2; exit 79; }

# -- (I) 能力接口 quick check（容器内 curl）--
echo "  [I] 快速检查 /qa/capabilities 的 web_search_enabled 字段 ..."
docker exec ekb-api sh -c "
  # 先用未登录方式抓一次看看是否返回结构（会 401 也没关系，说明接口存在）
  OUT=\$(curl -fsS --max-time 5 -H 'Accept: application/json' http://127.0.0.1:8000/api/v1/qa/capabilities 2>&1 || true)
  echo \"    capabilities response received; body withheld\"
  # 如果返回 capabilities 就解析 web_search_enabled
  if echo \"\${OUT}\" | grep -q 'web_search_enabled'; then
    VAL=\$(echo \"\${OUT}\" | python3 -c 'import sys,json
try: d=json.load(sys.stdin)
except: print(\"JSON_PARSE_FAIL\"); sys.exit(0)
print(d.get(\"capabilities\",{}).get(\"web_search_enabled\",\"MISSING\"))
')
    echo \"    web_search_enabled field present\"
  else
    echo \"    capabilities endpoint did not expose the field without authentication\"
  fi
"

echo "  容器侧更新完成 ✓"
REMOTE_SCRIPT
log_ok "后端容器热更新完成"

# --- Step 4: 前端上传 + 切换 ---
log_step "Step 4：前端 dist 上传 + nginx 切换"
TIMESTAMP="$(date +%Y%m%d-%H%M%S)"
RELEASE_TAG="${TIMESTAMP}-tavily"
ekb_ssh "mkdir -p /opt/ekb/web/releases/${RELEASE_TAG}/dist"
ekb_rsync \
  --bwlimit=1500 \
  "${WEB_DIR}/dist/" \
  "${DEPLOY_USER}@${DEPLOY_HOST}:/opt/ekb/web/releases/${RELEASE_TAG}/dist/"
log_ok "前端 dist 上传"

ekb_ssh "bash -s" <<REMOTE_SCRIPT
set -euo pipefail
ln -sfn "/opt/ekb/web/releases/${RELEASE_TAG}" /opt/ekb/web/current
echo "  web/current 已切到 ${RELEASE_TAG}"
nginx -t >/dev/null 2>&1 && systemctl reload nginx && echo "  nginx reload ✓"
ls -1t /opt/ekb/web/releases | tail -n +4 | while read -r OLD; do
  rm -rf "/opt/ekb/web/releases/\${OLD}"
  echo "  清理旧 web release: \${OLD}"
done
REMOTE_SCRIPT
log_ok "前端切换 + nginx reload 完成"

# --- Step 5: 公网健康检查 + SSH 端口转发提示 ---
log_step "Step 5：验收"
PUBLIC_URL="http://${DEPLOY_HOST}"
echo "  首页 HTTP: $(curl --max-time 8 -s -o /dev/null -w '%{http_code}' "${PUBLIC_URL}/" || echo 000)"
echo "  /api/healthz: HTTP $(curl --max-time 8 -s -o /dev/null -w '%{http_code}' "${PUBLIC_URL}/api/healthz" || echo 000)"

echo ""
echo "╔══════════════════════════════════════════════════════════════════╗"
echo "║  Tavily 联网搜索接入：部署完成                                    ║"
echo "╠══════════════════════════════════════════════════════════════════╣"
echo "║  🌐 访问：  ${PUBLIC_URL}                                         ║"
echo "║  👤 登录：  使用运行时受管凭据（不会打印）                         ║"
echo "╠══════════════════════════════════════════════════════════════════╣"
echo "║  ⚠️  联网搜索配置必须由受管运行时配置提供（不在脚本中填入）：       ║"
echo "║                                                                  ║"
echo "║  验证能力：登录后 → AI 助手 → 左下角「联网搜索」按钮变亮           ║"
echo "║  （按钮 title 变成“开启联网搜索…”，不再 disabled）                ║"
echo "║  打开后提问，SSE 事件中会出现：                                   ║"
echo "║    web_search_started → web_search_completed → retrieval_*       ║"
echo "╠══════════════════════════════════════════════════════════════════╣"
echo "║  本地端口转发（DNS/备案未就绪前用）：                              ║"
echo "║  ssh -fNL 30080:127.0.0.1:80 -p ${DEPLOY_SSH_PORT} ${DEPLOY_USER}@${DEPLOY_HOST}"
echo "║  然后浏览器打开 http://localhost:30080                            ║"
echo "╚══════════════════════════════════════════════════════════════════╝"
