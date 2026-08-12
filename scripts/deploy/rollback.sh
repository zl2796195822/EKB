#!/usr/bin/env bash
# EKB 回滚：把 API/Web 软链切回上一个 release，重启服务 + reload nginx，并做 healthz 验证。
#
# 用法：bash scripts/deploy/rollback.sh

set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${HERE}/../.." && pwd)"
source "${HERE}/lib/log.sh"
source "${HERE}/lib/ssh.sh"
ekb_require_deploy_config

log_step "EKB 回滚：切换到上一个 release"

rollback_one() {
  local what="$1"; local root="$2"
  local curlink target prev
  curlink=$(readlink "${root}/current" 2>/dev/null || echo '')
  [[ -n "${curlink}" ]] || { log_warn "${root}/current 无软链，跳过 ${what} 回滚"; return 0; }
  target="${curlink##*/}"  # 只取 basename，比如 20260811-xxxx-abcdef
  # ls 列表按时间倒序，找到 target 之后那一个（上一版）
  prev=$(ls -1t "${root}/releases" 2>/dev/null | awk -v t="${target}" '$0==t{found=1;next}found{print;exit}')
  if [[ -z "${prev}" ]]; then
    log_warn "${what}：没有可回滚的历史版本（当前已是最旧版本）"
    return 0
  fi
  log_info "${what}: ${target} → ${prev}"
  ekb_ssh "ln -sfn '${root}/releases/${prev}' '${root}/current' && echo ok"
}

rollback_one "API" "/opt/ekb/api"
rollback_one "Web" "/opt/ekb/web"

log_step "重启 ekb-api.service + reload nginx"
ekb_ssh "systemctl restart ekb-api.service && sleep 2 && systemctl is-active ekb-api.service >/dev/null && echo ok-api"
ekb_ssh "nginx -t >/dev/null 2>&1 && systemctl reload nginx && echo ok-nginx"

log_step "公网 healthz 验证"
for i in {1..15}; do
  H=$(curl --max-time 5 -s -o /dev/null -w '%{http_code}' "http://${DEPLOY_HOST}/" || echo 000)
  A=$(curl --max-time 5 -s "http://${DEPLOY_HOST}/api/healthz" 2>/dev/null | grep -o 'status.:.[a-z]*' || echo '')
  if [[ "${H}" == "200" ]] && echo "${A}" | grep -q 'ok'; then
    log_ok "回滚完成，HTTP ${H} + ${A}"
    exit 0
  fi
  sleep 2
done
log_error "回滚后 healthz 未恢复，请 SSH 登录查看 journalctl -u ekb-api.service"
exit 1
