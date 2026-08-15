#!/usr/bin/env bash
# EKB API entrypoint: migration/verify/admin configuration are hard gates.
# It never prints credentials, DSNs, SQL bodies or ensure_admin output.

set -euo pipefail

APP_DIR="${EKB_APP_DIR:-/app}"
cd "${APP_DIR}"

log() { printf '[entrypoint] %s\n' "$*"; }
fail() {
  log "FAIL category=$1"
  exit 78
}

environment="${EKB_ENV:-development}"
database_url="${EKB_DATABASE_URL:-${DATABASE_URL:-}}"
if [[ -z "${database_url}" ]]; then
  database_url="sqlite:///./ekb_dev.db"
fi

environment_lower="$(printf '%s' "${environment}" | tr '[:upper:]' '[:lower:]')"
if [[ "${environment_lower}" == "production" ]]; then
  [[ -n "${EKB_TOKEN_SECRET:-}" && "${EKB_TOKEN_SECRET}" != *REPLACE_WITH* ]] || fail "missing_token_secret"
  [[ -n "${EKB_DEV_USER_EMAIL:-}" ]] || fail "missing_runtime_admin_email"
  [[ -n "${EKB_DEV_USER_NAME:-}" ]] || fail "missing_runtime_admin_name"
  [[ -n "${EKB_DEV_PASSWORD:-}" ]] || fail "missing_runtime_admin_password"
  [[ -n "${EKB_APPS_MASTER_KEY:-}" && "${EKB_APPS_MASTER_KEY}" != *REPLACE_WITH* ]] || fail "missing_runtime_master_key"
  case "${database_url}" in
    sqlite://*|sqlite+*) fail "production_sqlite_database_forbidden" ;;
  esac
  admin_email_lower="$(printf '%s' "${EKB_DEV_USER_EMAIL}" | tr '[:upper:]' '[:lower:]')"
  admin_name_lower="$(printf '%s' "${EKB_DEV_USER_NAME}" | tr '[:upper:]' '[:lower:]')"
  case "${admin_email_lower}" in
    admin|admin@example.com) fail "default_admin_identity" ;;
  esac
  case "${admin_name_lower}" in
    admin|ekb\ admin) fail "default_admin_identity" ;;
  esac
  case "${EKB_DEV_PASSWORD}" in
    admin|change-me-local-only) fail "default_admin_password" ;;
  esac
fi

if [[ "${database_url}" == sqlite://* ]]; then
  sqlite_path="${database_url#sqlite:///}"
  if [[ "${sqlite_path}" == /* ]]; then
    data_dir="$(dirname "${sqlite_path}")"
  else
    data_dir="$(dirname "${APP_DIR}/${sqlite_path}")"
  fi
  mkdir -p "${data_dir}" 2>/dev/null || fail "database_directory"
  chmod 700 "${data_dir}" 2>/dev/null || fail "database_directory_permissions"
  write_probe="${data_dir}/.write_test"
  touch "${write_probe}" 2>/dev/null || fail "database_directory_not_writable"
  command rm -f "${write_probe}" 2>/dev/null || fail "database_directory_cleanup"
fi

log "migration_apply_verify_start"
if ! python3 -m ekb_api.migrations.v4_fullstack \
  --database-url "${database_url}" \
  --verify >/dev/null 2>&1; then
  fail "migration_or_verify"
fi
log "migration_apply_verify_pass"

log "runtime_admin_initialization_start"
if ! python3 "${APP_DIR}/ensure_admin.py" >/dev/null 2>&1; then
  fail "ensure_admin"
fi
log "runtime_admin_initialization_pass"

log "uvicorn_start"
exec "$@"
