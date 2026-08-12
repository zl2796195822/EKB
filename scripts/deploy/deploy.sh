#!/usr/bin/env bash
# EKB 一键部署（每轮开发完成后执行本脚本 → 验证→打包→rsync→部署→重启→公网健康检查→打印验收信息）
#
# 用法：
#   export DEPLOY_HOST='deploy.example.invalid'  # required runtime target; do not commit a real host
#   export DEPLOY_USER='runtime-deploy-user'        # required runtime SSH user
#   bash scripts/deploy/deploy.sh                # 常规每轮部署（默认校验 vitest/tsc）
#   bash scripts/deploy/deploy.sh --init-server  # 首次：只把初始化脚本+tmpl 传上去并远程执行 setup_server.sh
#   bash scripts/deploy/deploy.sh --skip-tests   # 跳过 vitest/tsc（本地已跑过，节省重复时间）
#   bash scripts/deploy/deploy.sh --help

set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${HERE}/../.." && pwd)"
cd "${REPO_ROOT}"

# shellcheck source=scripts/deploy/lib/log.sh
source "${HERE}/lib/log.sh"
# shellcheck source=scripts/deploy/lib/ssh.sh
source "${HERE}/lib/ssh.sh"

# ---- Flags ----
SKIP_TESTS=0
INIT_SERVER=0
VERBOSE=0
for arg in "$@"; do
  case "${arg}" in
    --skip-tests)   SKIP_TESTS=1 ;;
    --init-server)  INIT_SERVER=1 ;;
    -v|--verbose)   VERBOSE=1 ;;
    -h|--help)
      cat <<'EOF'
用法: bash scripts/deploy/deploy.sh [OPTIONS]

  --init-server   首次部署：把 setup_server.sh + tmpl 上传并执行（目录/nginx/systemd/venv）
  --skip-tests    跳过 vitest / tsc（本地已跑过，节省重复时间）
  -v, --verbose   显示 rsync / ssh 输出
  -h, --help      帮助
EOF
      exit 0 ;;
    *) die "未知参数：${arg}（用 -h 看帮助）" ;;
  esac
done

ekb_require_deploy_config

if [[ "${VERBOSE}" == "1" ]]; then
  log_info "verbose mode enabled; command tracing remains disabled to protect runtime values"
fi

# =============================================================================
#  STEP 0 · SSH 可达性 / sshpass 自检
# =============================================================================
log_step "Step 0：SSH 连通性自检"
if ! command -v ssh >/dev/null 2>&1; then
  die "本系统没有 ssh 命令，请先安装 OpenSSH client。"
fi
if ! command -v rsync >/dev/null 2>&1; then
  die "本系统没有 rsync，请先安装 rsync（brew install rsync / apt install rsync）。"
fi

if ! ekb_ssh_test; then
  log_warn "SSH 连接失败。尝试自动吸收 host key 后重试..."
  ekb_ensure_host_key
  if ekb_ssh_test; then
    log_ok "SSH 连通性 OK。"
  else
    cat <<EOF
请先在受管运行环境验证 SSH 密钥或配置受控密码注入：

  ssh -p ${DEPLOY_SSH_PORT} ${DEPLOY_USER}@${DEPLOY_HOST}

连不上的常见原因：
  · 运行时凭据未配置或无效
  · sshpass 不可用时不会回退到交互式密码输入
  · 服务器安全组未放行 ${DEPLOY_SSH_PORT}

EOF
    die "SSH 不通，部署终止。"
  fi
fi
log_ok "SSH connectivity OK"

# =============================================================================
#  STEP 0.5 · 远程注入 HTTP_PROXY（可选）：通过 SSH -R 反向隧道 + 本地 ekb_local_proxy 出网
# =============================================================================
# 若本地已启动 SSH -R 127.0.0.1:3128 反向隧道到本地 3128 代理（且该代理可访问外网），
# 则在服务器 /etc/profile.d 写入 HTTP_PROXY 变量，使 dnf/pip/curl 全部自动走代理，
# 无需在安全组放开任何出站规则。
if [[ -z "${EKB_SKIP_INJECT_PROXY:-}" ]]; then
  log_info "写入可选的受控代理配置（值不会输出）"
  ekb_ssh "cat > /etc/profile.d/ekb-http-proxy.sh <<'PROXYEOF'
export HTTP_PROXY=http://127.0.0.1:3128
export HTTPS_PROXY=http://127.0.0.1:3128
export ALL_PROXY=http://127.0.0.1:3128
export http_proxy=\$HTTP_PROXY
export https_proxy=\$HTTPS_PROXY
export no_proxy=localhost,127.0.0.1,10.0.0.0/8,172.16.0.0/12,192.168.0.0/16,/var/run/docker.sock
export NO_PROXY=\$no_proxy
PROXYEOF
chmod 644 /etc/profile.d/ekb-http-proxy.sh"
  # 同时给 dnf 显式写一次 proxy（有些发行版 profile.d 对非交互 bash 不生效）
  ekb_ssh "mkdir -p /etc/dnf && if [ ! -f /etc/dnf/dnf.conf ] || ! grep -q '^proxy=' /etc/dnf/dnf.conf 2>/dev/null; then if grep -q '^\\[main\\]' /etc/dnf/dnf.conf 2>/dev/null; then sed -i '/^\\[main\\]/a proxy=http://127.0.0.1:3128' /etc/dnf/dnf.conf 2>/dev/null || true; else echo -e '[main]\\nproxy=http://127.0.0.1:3128' >> /etc/dnf/dnf.conf; fi; fi; cat /etc/dnf/dnf.conf 2>/dev/null | head -5 || true"
fi

# =============================================================================
#  BRANCH A · --init-server
# =============================================================================
if [[ "${INIT_SERVER}" == "1" ]]; then
  log_step "运行服务器一次性初始化（setup_server.sh）"
  STAGING="$(mktemp -d)"
  cp -R "${HERE}/remote/." "${STAGING}/"
  # 给脚本加可执行权限
  chmod +x "${STAGING}/setup_server.sh"
  log_info "上传 setup 资产到服务器 /tmp/ekb-setup..."
  ekb_rsync "${STAGING}/" "${DEPLOY_USER}@${DEPLOY_HOST}:/tmp/ekb-setup/"
  log_info "远程执行 /tmp/ekb-setup/setup_server.sh..."
  ekb_ssh "set -a; . /etc/profile.d/ekb-http-proxy.sh 2>/dev/null || true; set +a; bash /tmp/ekb-setup/setup_server.sh"
  rm -rf "${STAGING}"
  log_ok "服务器初始化执行完毕，退出前清理 /tmp/ekb-setup。"
  ekb_ssh "rm -rf /tmp/ekb-setup" || true
  echo
  echo "下一步："
  echo "  export DEPLOY_USER='<runtime SSH user>'"
  echo "  bash scripts/deploy/deploy.sh    # 运行首轮完整部署（构建+上传+启动）"
  exit 0
fi

# =============================================================================
#  STEP 1 · 本地校验 + 构建
# =============================================================================
log_step "Step 1：本地校验（Python compileall + TS 类型 + Vitest + Vite build）"

# --- Python ---
API_DIR="${REPO_ROOT}/apps/api"
if [[ -x "${REPO_ROOT}/.venv/bin/python" ]]; then
  PY_BIN="${REPO_ROOT}/.venv/bin/python"
elif command -v python3 >/dev/null 2>&1; then
  PY_BIN="python3"
else
  die "找不到 python3 或 .venv/bin/python"
fi
log_info "后端 compileall ekb_api..."
( cd "${API_DIR}" && "${PY_BIN}" -m compileall -q ekb_api ) || {
  die "Python compileall 报错（存在语法错误）；先 fix 后再部署。"
}
log_ok "后端 compileall 通过"

# --- Frontend ---
WEB_DIR="${REPO_ROOT}/apps/web"
NPM_RUN() { ( cd "${WEB_DIR}" && npx --yes "$@" ); }

if [[ "${SKIP_TESTS}" == "0" ]]; then
  log_info "前端 tsc --noEmit..."
  NPM_RUN tsc --noEmit --pretty false >/dev/null || die "tsc --noEmit 失败。"
  log_ok "tsc --noEmit 通过"

  log_info "前端 Vitest..."
  NPM_RUN vitest run --reporter=verbose 2>&1 | tail -15
  if ( NPM_RUN vitest run >/tmp/ekb-vitest.log 2>&1 ); then
    log_ok "Vitest 全部通过"
  else
    tail -50 /tmp/ekb-vitest.log 1>&2 || true
    die "Vitest 失败；终止部署。"
  fi
else
  log_muted "--skip-tests 跳过 tsc & vitest。"
fi

log_info "前端 vite build..."
( cd "${WEB_DIR}" && npx --yes tsc -b >/dev/null && npx --yes vite build --logLevel warn 2>&1 ) | tail -10
DIST_DIR="${WEB_DIR}/dist"
if [[ ! -f "${DIST_DIR}/index.html" ]] || ! ls "${DIST_DIR}/assets/"*.js >/dev/null 2>&1; then
  die "vite build 未产出 ${DIST_DIR}/index.html + assets/*.js，终止部署。"
fi
log_ok "前端构建产物 ready（${DIST_DIR}）"

# =============================================================================
#  STEP 2 · 打包 staging
# =============================================================================
log_step "Step 2：生成 release staging 目录"

TIMESTAMP="$(date +%Y%m%d-%H%M%S)"
GIT_SHA="$(git rev-parse --short HEAD 2>/dev/null || echo local)"
RELEASE_TAG="${TIMESTAMP}-${GIT_SHA}"

STAGING_ROOT="${REPO_ROOT}/.deploy_staging"
rm -rf "${STAGING_ROOT}"
mkdir -p "${STAGING_ROOT}/api/${RELEASE_TAG}" "${STAGING_ROOT}/web/${RELEASE_TAG}"

# Web: 只上传 dist
cp -R "${DIST_DIR}" "${STAGING_ROOT}/web/${RELEASE_TAG}/dist"
# 写入 release 元信息（给页脚展示）
echo "{\"tag\":\"${RELEASE_TAG}\",\"ts\":\"${TIMESTAMP}\",\"sha\":\"${GIT_SHA}\"}" \
  > "${STAGING_ROOT}/web/${RELEASE_TAG}/dist/release.json"

# API: ekb_api + pyproject.toml + README（排除 __pycache__ / *.pyc / .db / .venv）
rsync -a --delete \
  --exclude='__pycache__' --exclude='*.pyc' --exclude='*.pyo' \
  --exclude='.venv' --exclude='*.db' --exclude='*.sqlite3' \
  --exclude='backups/' --exclude='.pytest_cache/' \
  "${API_DIR}/ekb_api" "${STAGING_ROOT}/api/${RELEASE_TAG}/"
[[ -f "${API_DIR}/pyproject.toml" ]] && cp "${API_DIR}/pyproject.toml" "${STAGING_ROOT}/api/${RELEASE_TAG}/"
[[ -f "${API_DIR}/README.md" ]] && cp "${API_DIR}/README.md" "${STAGING_ROOT}/api/${RELEASE_TAG}/" 2>/dev/null || true

# 额外把 ensure_admin.py 放到 release 根，部署时执行
cp "${HERE}/remote/ensure_admin.py.tmpl" "${STAGING_ROOT}/api/${RELEASE_TAG}/ensure_admin.py"
chmod +x "${STAGING_ROOT}/api/${RELEASE_TAG}/ensure_admin.py"

echo "${RELEASE_TAG}" > "${STAGING_ROOT}/api/${RELEASE_TAG}/RELEASE_TAG"
log_ok "Staging 已生成：release=${RELEASE_TAG}"
log_muted "  web: $(du -sh "${STAGING_ROOT}/web/${RELEASE_TAG}" | awk '{print $1}')"
log_muted "  api: $(du -sh "${STAGING_ROOT}/api/${RELEASE_TAG}" | awk '{print $1}')"

# =============================================================================
#  STEP 3 · rsync 上传
# =============================================================================
log_step "Step 3：上传代码到服务器 /opt/ekb/{api,web}/releases/${RELEASE_TAG}"
ekb_rsync "${STAGING_ROOT}/api/${RELEASE_TAG}/" \
  "${DEPLOY_USER}@${DEPLOY_HOST}:/opt/ekb/api/releases/${RELEASE_TAG}/"
ekb_rsync "${STAGING_ROOT}/web/${RELEASE_TAG}/" \
  "${DEPLOY_USER}@${DEPLOY_HOST}:/opt/ekb/web/releases/${RELEASE_TAG}/"
log_ok "上传完成"

# =============================================================================
#  STEP 4 · 远程部署（Python 装依赖 / .env 写密钥 / ln -sfn / systemctl restart / healthz）
# =============================================================================
log_step "Step 4：远程部署（切换 release → 运行时配置检查 → ensure_admin → 重启）"

# 注意：使用 here-doc 传远程执行脚本。$ 前缀变量需要判断是本地还是远程：
#  - 本地变量要展开：写 ${VAR}
#  - 远程变量：写 \${VAR} 或用 'EOF' 不展开。这里混合写法，用 \ 转义远程引用。
DEPLOY_SCRIPT=$(cat <<EOSCRIPT
set -euo pipefail
# === 自动加载 HTTP_PROXY（SSH 反向隧道 + 本地 ekb_local_proxy 出外网）===
if [ -f /etc/profile.d/ekb-http-proxy.sh ]; then
  set -a; . /etc/profile.d/ekb-http-proxy.sh; set +a
fi
TAG="${RELEASE_TAG}"
API_ROOT=/opt/ekb/api
WEB_ROOT=/opt/ekb/web
DATA_ROOT=/opt/ekb/data
VENV_PY="\${API_ROOT}/venv/bin/python3"
VENV_PIP="\${API_ROOT}/venv/bin/pip"

# pip 走代理（venv 的 pip 有时不继承 env，这里显式设 config set 更稳）
if [ -n "\${HTTPS_PROXY:-}" ]; then
  "\${VENV_PIP}" config set global.proxy "\${HTTPS_PROXY}" >/dev/null 2>&1 || true
fi

# --- (A) 若 pyproject.toml 与上次 current 版本不一致则 pip install -e . ---
PYPROJ_NEW="\${API_ROOT}/releases/\${TAG}/pyproject.toml"
PYPROJ_OLD="\${API_ROOT}/current/pyproject.toml"
FIRST_DEPLOY=0
if [[ ! -d "\${API_ROOT}/current/ekb_api" ]] || [[ ! -f "\${PYPROJ_OLD}" ]]; then
  echo "[remote] FIRST_DEPLOY=1；准备全量 pip install"
  FIRST_DEPLOY=1
fi

if [[ \${FIRST_DEPLOY} -eq 1 ]] || ! diff -q "\${PYPROJ_NEW}" "\${PYPROJ_OLD}" >/dev/null 2>&1; then
  echo "[remote] pyproject.toml 变更或首次 → pip install -e . (清华镜像源)"
  "\${VENV_PIP}" install --quiet --upgrade pip
  cd "\${API_ROOT}/releases/\${TAG}"
  "\${VENV_PIP}" install --quiet -e . 2>&1 | tail -5 || {
    # 常见失败：cryptography/tiktoken 需要 Rust/gcc，安装 build deps 后再试
    echo "[remote] 首次 pip 失败；尝试补齐 build deps 再装一次..." >&2
    apt-get install -y --no-install-recommends build-essential libssl-dev libffi-dev pkg-config >/dev/null 2>&1 || true
    "\${VENV_PIP}" install --quiet -e . 2>&1 | tail -10
  }
  echo "[remote] pip install 完成"
else
  echo "[remote] pyproject.toml 无变更，跳过 pip"
fi

# --- (B) .env 占位替换（一次性）---
ENV_FILE="\${API_ROOT}/.env"
if [[ ! -f "\${ENV_FILE}" ]]; then
  echo "[remote] .env 不存在 → 从模板复制"
  cp /opt/ekb/api/releases/\${TAG}/../../../../../../tmp/ekb-setup/server.env.tmpl "\${ENV_FILE}" 2>/dev/null || {
    echo "[remote] 找不到模板 → 拒绝继续：运行时配置模板缺失" >&2
    exit 78
  }
fi
for required_env in EKB_TOKEN_SECRET EKB_DEV_USER_EMAIL EKB_DEV_USER_NAME EKB_DEV_PASSWORD; do
  if ! grep -Eq "^\${required_env}=.+" "\${ENV_FILE}"; then
    echo "[remote] required runtime setting \${required_env} is missing; refusing deployment" >&2
    exit 78
  fi
done
chown ekb:ekb "\${ENV_FILE}" || true
chmod 640 "\${ENV_FILE}"

# --- (C) 切换 release 软链 ---
ln -sfn "\${API_ROOT}/releases/\${TAG}" "\${API_ROOT}/current"
ln -sfn "\${WEB_ROOT}/releases/\${TAG}" "\${WEB_ROOT}/current"

# --- (D) SQLite data 目录 + 权限强化 ---
# 注意：容器内 ekb 用户 uid=1000（Dockerfile 里 useradd），宿主机 ekb 用户 uid=989，
# 直接 chown ekb:ekb 会导致 bind mount 到容器时 Permission denied。
# 这里用“数字 uid=1000”统一，保证容器侧 /data 可读；同时 run_as_ekb 用 sudo -u#1000 兜底。
mkdir -p "\${DATA_ROOT}"
EKB_UID=\$(id -u ekb 2>/dev/null || echo 1000)
EKB_GID=\$(id -g ekb 2>/dev/null || echo 1000)
# 若与容器期望 uid 不一致，先尝试把宿主机 ekb 改为 1000:1000（破坏性最小，只改一次）
if [ "\${EKB_UID}" != "1000" ] || [ "\${EKB_GID}" != "1000" ]; then
  if command -v usermod >/dev/null 2>&1 && command -v groupmod >/dev/null 2>&1; then
    (groupmod -g 1000 ekb 2>/dev/null || true)
    (usermod -u 1000 -g 1000 ekb 2>/dev/null || true)
  fi
  # 兜底：仍按 1000:1000 直接 chown，容器可通
fi
chown -R 1000:1000 "\${DATA_ROOT}"
chmod 700 "\${DATA_ROOT}"
find "\${DATA_ROOT}" -maxdepth 1 -type f \\( -name "*.db" -o -name "*.sqlite3" \\) -exec chmod 600 {} \\;

# --- (E) ensure_admin：使用运行时受管凭据（优先 docker exec，保证数据库 URL/uid/env 一致）---
echo "[remote] ensure_admin.py：应用运行时受管管理员凭据"
if command -v docker >/dev/null 2>&1 && docker ps -q --filter name=ekb-api | grep -q .; then
  # 容器路径：DB URL 与运行时一致，文件系统权限也走容器内 ekb 用户，不受宿主机 uid 差异影响
  docker exec -u 0 ekb-api sh -c "
    set -a; . /app/.env 2>/dev/null; set +a || true
    python /app/ensure_admin.py
  " >/dev/null 2>&1 || { echo "[remote] ensure_admin failed; refusing deployment" >&2; exit 79; }
else
  run_as_ekb() { su ekb -s /bin/bash -c "cd \${API_ROOT}/current && \$*" || sudo -u ekb bash -c "cd \${API_ROOT}/current && \$*" || sudo -u '#'1000 bash -c "cd \${API_ROOT}/current && \$*"; }
  run_as_ekb "\${VENV_PY}" ensure_admin.py || {
    echo "[remote] ensure_admin failed; refusing deployment" >&2
    exit 79
  }
fi

# --- (F) 启动/重启 uvicorn systemd ---
systemctl daemon-reload 2>/dev/null || true
systemctl enable ekb-api.service >/dev/null 2>&1 || true
echo "[remote] restart ekb-api.service..."
systemctl restart ekb-api.service
sleep 2
if ! systemctl is-active --quiet ekb-api.service; then
    echo "[remote] 启动失败；日志保留在受管运行环境中" >&2
  exit 7
fi
echo "[remote] ekb-api.service active ✓"

# --- (G) 本地 127.0.0.1 healthz 循环（最多 30s）---
for i in {1..15}; do
  if curl -fsS --max-time 3 http://127.0.0.1:8000/healthz >/dev/null 2>&1; then
    echo "[remote] /healthz OK（第 \${i} 次轮询）"
    break
  fi
  sleep 2
  if [[ \${i} -eq 15 ]]; then
    echo "[remote] /healthz 30s 内未就绪；日志保留在受管运行环境中" >&2
    exit 8
  fi
done

# --- (H) Nginx reload + SPA 静态目录校验 ---
if [[ ! -d "\${WEB_ROOT}/current/dist" ]]; then
  echo "[remote] web/current/dist 不存在！nginx 将会 404。检查 rsync 与软链。" >&2
  ls -la "\${WEB_ROOT}/current/" >&2 || true
  exit 9
fi
nginx -t >/dev/null 2>&1 && systemctl reload nginx || {
  echo "[remote] nginx -t 失败：" >&2; nginx -t >&2 || true; exit 10
}
echo "[remote] nginx reload ✓"

# --- (I) Releases 清理：保留最近 3 个 ---
ls -1t "\${API_ROOT}/releases" | tail -n +4 | while read -r OLD; do
  echo "[remote] 清理旧 API release：\${OLD}"
  rm -rf "\${API_ROOT}/releases/\${OLD}"
done
ls -1t "\${WEB_ROOT}/releases" | tail -n +4 | while read -r OLD; do
  echo "[remote] 清理旧 WEB release：\${OLD}"
  rm -rf "\${WEB_ROOT}/releases/\${OLD}"
done

echo "[remote] === 远程部署完成 ==="
EOSCRIPT
)

# 把远程脚本喂给 ssh bash -s
# 注意这里用 bash -s：heredoc 已经在本地做过一次变量替换（只有 TAG 被替换）
if ! ekb_ssh "bash -s" <<<"${DEPLOY_SCRIPT}"; then
  # 远程步骤失败 → 提示回滚
  log_error "远程部署脚本退出非 0；如要回滚执行：bash scripts/deploy/rollback.sh"
  log_info "保留临时 staging：${STAGING_ROOT}，便于问题定位（成功后会自动清理）"
  die "部署失败；请检查上方远程日志。"
fi
log_ok "远程部署执行通过"

# =============================================================================
#  STEP 5 · 公网健康检查（浏览器级验收最后一步）
# =============================================================================
log_step "Step 5：公网验收（http://${DEPLOY_HOST}）"

PUBLIC_URL="http://${DEPLOY_HOST}"
pass=0
# (A) 静态 200
for i in {1..10}; do
  CODE=$(curl --max-time 8 -s -o /dev/null -w '%{http_code}' "${PUBLIC_URL}/" || echo 000)
  if [[ "${CODE}" == "200" ]]; then
    log_ok "首页 HTTP ${CODE}（第 ${i} 次轮询）"
    pass=$((pass+1))
    break
  fi
  log_muted "首页 still ${CODE} ..."
  sleep 2
done

# (B) /api/healthz → {"status":"ok", ...}
for i in {1..10}; do
  OUT=$(curl --max-time 8 -s "${PUBLIC_URL}/api/healthz" || echo '')
  if echo "${OUT}" | grep -q 'status.*ok'; then
    log_ok "/api/healthz returned an acceptable status"
    pass=$((pass+1))
    break
  fi
  log_muted "/api/healthz did not return an acceptable status ..."
  sleep 2
done

# (C) 登录接口不在 PH0 deploy smoke 中执行；凭据必须由运行时受管配置提供。
# 这里仅做一次弱校验：登录页包含 "登录" / "EKB" 字样
for i in {1..5}; do
  HTML=$(curl --max-time 8 -s "${PUBLIC_URL}/" || echo '')
  if echo "${HTML}" | grep -q 'index.html' || echo "${HTML}" | grep -qi 'script'; then
    log_ok "首页返回 SPA 入口 ✓"
    pass=$((pass+1))
    break
  fi
  sleep 2
done

# 清理 staging
rm -rf "${STAGING_ROOT}"

echo
if [[ ${pass} -ge 2 ]]; then
  log_ok "部署完成"
  echo
  echo -e "   🌐 访问地址：${BOLD}${CYAN}${PUBLIC_URL}${NC}"
  echo -e "   👤 登录凭据：由运行时受管配置提供（不会打印）"
  echo
  log_muted "提示：登录后可验证左侧 10 模块（工作台/知识库/AI 助手/回收站/个人中心/应用中心…）是否均可进入"
  log_muted "提示：如要配真实 LLM，改 /opt/ekb/api/.env 的 EKB_MODEL_PROVIDERS 后 systemctl restart ekb-api"
  log_muted "提示：回滚上一版 → bash scripts/deploy/rollback.sh"
  exit 0
else
  log_error "公网验收未通过（${pass}/3 检查点）。请先验证云服务商安全组是否放行了 80 端口。"
  exit 1
fi
