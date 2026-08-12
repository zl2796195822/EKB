#!/bin/bash
set -euo pipefail

export PATH="/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:/opt/homebrew/bin:${PATH}"
export COPYFILE_DISABLE=1  # macOS: 禁止 tar 创建 ._ AppleDouble 文件

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${HERE}/../.." && pwd)"
cd "${REPO_ROOT}"

# DEPLOY_HOST and DEPLOY_USER are required at runtime. Optional password injection
# is read only by scripts/deploy/lib/ssh.sh through the runtime environment.
source "${HERE}/lib/ssh.sh"
ekb_require_deploy_config

# Step 1: 打包后端 ekb_api 目录（排除 macOS 扩展属性和缓存）
echo ">>> [1/6] 打包后端代码..."
tar czf /tmp/ekb_api_update.tar.gz \
  --exclude='__pycache__' --exclude='*.pyc' --exclude='*.pyo' \
  --exclude='.venv' --exclude='*.db' --exclude='*.sqlite3' \
  --exclude='backups/' --exclude='.pytest_cache/' \
  --exclude='._*' \
  -C apps/api ekb_api
echo "    打包完成: $(du -sh /tmp/ekb_api_update.tar.gz | awk '{print $1}')"

# Step 2: 上传后端代码包
echo ">>> [2/6] 上传后端代码到服务器..."
ekb_scp /tmp/ekb_api_update.tar.gz "${DEPLOY_USER}@${DEPLOY_HOST}:/tmp/"
echo "    上传完成"

# Step 3: 解压 + 更新容器代码 + 重启
echo ">>> [3/6] 更新容器代码 + 重启..."
ekb_ssh 'bash -s' <<'REMOTE_SCRIPT'
set -euo pipefail

cd /tmp
rm -rf ekb_api_update && mkdir ekb_api_update
tar xzf ekb_api_update.tar.gz -C ekb_api_update
# 清理可能残留的 ._ 文件
find ekb_api_update -name '._*' -delete 2>/dev/null || true
echo "[remote] 解压完成，文件数: $(find ekb_api_update -type f -not -name '._*' | wc -l)"

# 用 docker cp 逐个更新关键文件（避免目录嵌套和权限问题）
# 先更新修改的文件
for f in \
  migrations/v3_001_identity.py \
  migrations/v3_002_content.py \
  migrations/v3_003_analytics.py \
  migrations/v3_004_apps.py \
  migrations/v3_005_analytics_compat.py \
  migrations/v3_005_llm.py \
  migrations/v3_006_apps_compat.py \
  migrations/v3_007_content_governance_compat.py \
  migrations/v3_fullstack.py \
  services/__init__.py \
  services/llm_provider_catalog.py \
  services/v3_llm.py \
  services/v3_analytics.py \
  services/v3_content_governance.py \
  services/v3_identity.py \
  services/v3_profile.py \
  services/v3_trash.py \
  routers/__init__.py \
  routers/admin.py \
  routers/analytics.py \
  routers/apps.py \
  routers/auth.py \
  routers/content_governance.py \
  routers/conversations.py \
  routers/feedback.py \
  routers/identity_v3.py \
  routers/kb.py \
  routers/llm.py \
  routers/me.py \
  routers/qa.py \
  routers/search.py \
  routers/trash.py \
  core/__init__.py \
  core/alerting.py \
  core/audit.py \
  core/auth.py \
  core/authorization.py \
  core/cache.py \
  core/circuit_breaker.py \
  core/config.py \
  core/db.py \
  core/egress.py \
  core/errors.py \
  core/logging.py \
  core/metrics.py \
  core/security.py \
  core/tracing.py \
  models.py \
  main.py \
  domain.py \
  llm.py \
  embedding.py \
  chunking.py \
  parsing.py \
  ranking.py \
  retrieval.py \
  schemas.py \
  store.py \
  __init__.py; do
  if [ -f "ekb_api_update/ekb_api/${f}" ]; then
    docker cp "ekb_api_update/ekb_api/${f}" "ekb-api:/app/ekb_api/${f}"
    echo "[remote] 更新: ${f}"
  fi
done

# 清理容器内 .pyc 缓存（以 root 权限）
docker exec -u 0 ekb-api find /app/ekb_api -name '__pycache__' -type d -exec rm -rf {} + 2>/dev/null || true
echo "[remote] .pyc 缓存已清理"

# 验证关键文件已更新
echo "[remote] 验证文件..."
docker exec -u 0 ekb-api grep -c "INSERT OR IGNORE" /app/ekb_api/migrations/v3_003_analytics.py && echo "[remote] v3_003 INSERT OR IGNORE 已生效" || echo "[remote] WARNING: v3_003 未更新"
docker exec -u 0 ekb-api test -f /app/ekb_api/migrations/v3_005_llm.py && echo "[remote] v3_005_llm.py 存在" || echo "[remote] WARNING: v3_005_llm.py 不存在"
docker exec -u 0 ekb-api test -f /app/ekb_api/services/v3_llm.py && echo "[remote] v3_llm.py 存在" || echo "[remote] WARNING: v3_llm.py 不存在"
docker exec -u 0 ekb-api test -f /app/ekb_api/routers/llm.py && echo "[remote] routers/llm.py 存在" || echo "[remote] WARNING: routers/llm.py 不存在"

# 重启容器
echo "[remote] 重启 ekb-api 容器..."
docker restart ekb-api
echo "[remote] 等待容器启动..."
sleep 3

# 健康检查
for i in $(seq 1 20); do
  if docker exec ekb-api curl -fsS --max-time 3 http://127.0.0.1:8000/healthz 2>/dev/null; then
    echo ""
    echo "[remote] /healthz OK（第 ${i} 次轮询）"
    break
  fi
  echo -n "."
  sleep 2
  if [ $i -eq 20 ]; then
    echo ""
    echo "[remote] /healthz 40s 内未就绪；日志保留在受管运行环境中" >&2
    exit 1
  fi
done
REMOTE_SCRIPT
echo "    后端部署完成"

# Step 4: 上传前端 dist
echo ">>> [4/6] 上传前端 dist..."
TIMESTAMP=$(date +%Y%m%d-%H%M%S)
RELEASE_TAG="${TIMESTAMP}-llm-update"

ekb_ssh "mkdir -p /opt/ekb/web/releases/${RELEASE_TAG}/dist"

ekb_rsync \
  --exclude='._*' \
  apps/web/dist/ ${DEPLOY_USER}@${DEPLOY_HOST}:/opt/ekb/web/releases/${RELEASE_TAG}/dist/
echo "    前端上传完成"

# Step 5: 切换 web current + nginx reload
echo ">>> [5/6] 切换前端 release + nginx reload..."
ekb_ssh "bash -s" <<REMOTE_SCRIPT
set -euo pipefail
ln -sfn /opt/ekb/web/releases/${RELEASE_TAG} /opt/ekb/web/current
echo "[remote] web current 已切换到 ${RELEASE_TAG}"
nginx -t && systemctl reload nginx
echo "[remote] nginx reload 完成"
# 清理旧 release（保留最近3个）
ls -1t /opt/ekb/web/releases | tail -n +4 | while read old; do
  rm -rf "/opt/ekb/web/releases/\${old}"
  echo "[remote] 清理旧 web release: \${old}"
done
REMOTE_SCRIPT
echo "    前端切换完成"

# Step 6: 公网健康检查
echo ">>> [6/6] 公网健康检查..."
sleep 2
PUBLIC_URL="http://${DEPLOY_HOST}"
CODE=$(curl --max-time 8 -s -o /dev/null -w '%{http_code}' "${PUBLIC_URL}/" || echo 000)
echo "    首页: HTTP ${CODE}"
HEALTH_CODE=$(curl --max-time 8 -s -o /dev/null -w '%{http_code}' "${PUBLIC_URL}/api/healthz" || echo 000)
echo "    /api/healthz: HTTP ${HEALTH_CODE}"

echo ""
echo "===== 部署完成 ====="
echo "   访问地址: ${PUBLIC_URL}"
echo "   登录凭据: 由运行时受管配置提供（不会打印）"
