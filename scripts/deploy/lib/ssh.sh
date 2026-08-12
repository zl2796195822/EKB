#!/usr/bin/env bash
# EKB deploy helpers.  DEPLOY_HOST is intentionally supplied at runtime.
#
# Required environment:
#   DEPLOY_HOST       remote deployment host (required; no repository default)
# Optional environment:
#   DEPLOY_SSH_PORT   SSH port (default 22)
#   DEPLOY_USER       SSH user (required; no repository default)
#   EKB_DEPLOY_SSH_PASS optional password for sshpass -e; key authentication is preferred
#
# Provides: ekb_require_deploy_config, ekb_ssh, ekb_rsync, ekb_scp, ekb_ssh_test.

if [[ -z "${_EKB_SSH_LOADED:-}" ]]; then
  _EKB_SSH_LOADED=1

  DEPLOY_SSH_PORT="${DEPLOY_SSH_PORT:-22}"
  DEPLOY_USER="${DEPLOY_USER:-}"

  _have_sshpass() { command -v sshpass >/dev/null 2>&1; }
  _have_keyscan() { command -v ssh-keyscan >/dev/null 2>&1; }

  ekb_require_deploy_config() {
    if [[ -z "${DEPLOY_HOST:-}" ]]; then
      echo "[deploy] DEPLOY_HOST is required; refusing to run without an explicit target." >&2
      return 64
    fi
    if [[ "${DEPLOY_HOST}" == "__REQUIRED__" || "${DEPLOY_HOST}" == "[production host]" || "${DEPLOY_HOST}" == "deploy.example.invalid" || "${DEPLOY_HOST}" == "example.com" || "${DEPLOY_HOST}" == "example.invalid" || "${DEPLOY_HOST}" == *.invalid || "${DEPLOY_HOST}" == *"["* || "${DEPLOY_HOST}" == *"]"* || "${DEPLOY_HOST}" == *"<"* || "${DEPLOY_HOST}" == *">"* ]]; then
      echo "[deploy] DEPLOY_HOST contains a placeholder; refusing to run." >&2
      return 64
    fi
    if [[ "${DEPLOY_HOST}" == *"://"* || "${DEPLOY_HOST}" == */* || "${DEPLOY_HOST}" =~ [[:space:]] ]]; then
      echo "[deploy] DEPLOY_HOST must be a host value, not a URL, path, or whitespace-containing value." >&2
      return 64
    fi
    if [[ ! "${DEPLOY_SSH_PORT}" =~ ^[0-9]+$ ]] || (( DEPLOY_SSH_PORT < 1 || DEPLOY_SSH_PORT > 65535 )); then
      echo "[deploy] DEPLOY_SSH_PORT must be a valid numeric port." >&2
      return 64
    fi
    if [[ -z "${DEPLOY_USER}" ]]; then
      echo "[deploy] DEPLOY_USER is required; refusing to run without an explicit user." >&2
      return 64
    fi
    if [[ "${DEPLOY_USER}" == "root" || "${DEPLOY_USER}" == "admin" || "${DEPLOY_USER}" == "__REQUIRED__" ]]; then
      echo "[deploy] DEPLOY_USER contains a prohibited/default value; refusing to run." >&2
      return 64
    fi
  }

  _ssh_common_opts() {
    printf '%s\n' \
      -o StrictHostKeyChecking=accept-new \
      -o UserKnownHostsFile="${HOME}/.ssh/known_hosts" \
      -o ConnectTimeout=10
  }

  _run_with_sshpass_env() {
    # Disable xtrace while the secret is copied into the process environment.
    # sshpass -e reads SSHPASS from the environment and never receives it as an argument.
    local had_xtrace=0 status
    case "$-" in *x*) had_xtrace=1; set +x ;; esac
    if [[ -n "${EKB_DEPLOY_SSH_PASS:-}" ]] && ! _have_sshpass; then
      echo "[deploy] password injection was requested but sshpass is unavailable; refusing password fallback." >&2
      (( had_xtrace )) && set -x
      return 69
    fi
    if _have_sshpass && [[ -n "${EKB_DEPLOY_SSH_PASS:-}" ]]; then
      SSHPASS="${EKB_DEPLOY_SSH_PASS}" sshpass -e "$@"
      status=$?
    elif [[ -z "${EKB_DEPLOY_SSH_PASS:-}" ]]; then
      "$@"
      status=$?
    else
      (( had_xtrace )) && set -x
      return 69
    fi
    (( had_xtrace )) && set -x
    return "${status}"
  }

  ekb_ensure_host_key() {
    ekb_require_deploy_config || return
    local kh="${HOME}/.ssh/known_hosts"
    mkdir -p "$(dirname "${kh}")"
    touch "${kh}"
    if ! grep -Eq "^(\[[^]]+\]:)?${DEPLOY_HOST//./\\.}(:[0-9]+)?[[:space:]]" "${kh}"; then
      if _have_keyscan; then
        ssh-keyscan -p "${DEPLOY_SSH_PORT}" "${DEPLOY_HOST}" 2>/dev/null >>"${kh}"
      fi
    fi
  }

  ekb_ssh_test() {
    ekb_require_deploy_config || return
    local tmp status
    tmp=$(mktemp)
    if ekb_ssh 'printf ekb-ssh-ok' >"${tmp}" 2>/dev/null && grep -q 'ekb-ssh-ok' "${tmp}"; then
      rm -f "${tmp}"
      return 0
    fi
    rm -f "${tmp}"
    return 1
  }

  ekb_ssh() {
    ekb_require_deploy_config || return
    ekb_ensure_host_key || return
    local -a opts=()
    while IFS= read -r opt; do opts+=("${opt}"); done < <(_ssh_common_opts)
    _run_with_sshpass_env ssh "${opts[@]}" -p "${DEPLOY_SSH_PORT}" "${DEPLOY_USER}@${DEPLOY_HOST}" "$@"
  }

  ekb_rsync() {
    ekb_require_deploy_config || return
    ekb_ensure_host_key || return
    local xtrace=0 status rsh
    case "$-" in *x*) xtrace=1; set +x ;; esac
    rsh="ssh -p ${DEPLOY_SSH_PORT} -o StrictHostKeyChecking=accept-new -o ConnectTimeout=10 -o UserKnownHostsFile=${HOME}/.ssh/known_hosts"
    if [[ -n "${EKB_DEPLOY_SSH_PASS:-}" ]] && ! _have_sshpass; then
      echo "[deploy] password injection was requested but sshpass is unavailable; refusing password fallback." >&2
      (( xtrace )) && set -x
      return 69
    fi
    if _have_sshpass && [[ -n "${EKB_DEPLOY_SSH_PASS:-}" ]]; then
      rsh="sshpass -e ${rsh}"
      SSHPASS="${EKB_DEPLOY_SSH_PASS}" rsync -az --partial --progress --no-perms --no-owner --no-group \
        -e "${rsh}" "$@"
    elif [[ -z "${EKB_DEPLOY_SSH_PASS:-}" ]]; then
      rsync -az --partial --progress --no-perms --no-owner --no-group \
        -e "${rsh}" "$@"
    else
      (( xtrace )) && set -x
      return 69
    fi
    status=$?
    (( xtrace )) && set -x
    return "${status}"
  }

  ekb_scp() {
    ekb_require_deploy_config || return
    ekb_ensure_host_key || return
    local xtrace=0 status
    case "$-" in *x*) xtrace=1; set +x ;; esac
    if [[ -n "${EKB_DEPLOY_SSH_PASS:-}" ]] && ! _have_sshpass; then
      echo "[deploy] password injection was requested but sshpass is unavailable; refusing password fallback." >&2
      (( xtrace )) && set -x
      return 69
    fi
    if _have_sshpass && [[ -n "${EKB_DEPLOY_SSH_PASS:-}" ]]; then
      SSHPASS="${EKB_DEPLOY_SSH_PASS}" sshpass -e scp \
        -o StrictHostKeyChecking=accept-new -o ConnectTimeout=10 \
        -P "${DEPLOY_SSH_PORT}" "$@"
    elif [[ -z "${EKB_DEPLOY_SSH_PASS:-}" ]]; then
      scp -o StrictHostKeyChecking=accept-new -o ConnectTimeout=10 \
        -P "${DEPLOY_SSH_PORT}" "$@"
    else
      (( xtrace )) && set -x
      return 69
    fi
    status=$?
    (( xtrace )) && set -x
    return "${status}"
  }
fi
