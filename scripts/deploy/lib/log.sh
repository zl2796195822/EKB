#!/usr/bin/env bash
# EKB deploy helpers: color logging
# Usage: source "$(dirname "$0")/lib/log.sh"

if [[ -z "${_EKB_LOG_LOADED:-}" ]]; then
  _EKB_LOG_LOADED=1

  NC=$'\033[0m'
  if [[ -t 1 ]]; then
    RED=$'\033[0;31m';    GREEN=$'\033[0;32m'; YELLOW=$'\033[1;33m'
    BLUE=$'\033[0;34m';   CYAN=$'\033[0;36m';  BOLD=$'\033[1m'
    GRAY=$'\033[0;90m'
  else
    RED=''; GREEN=''; YELLOW=''; BLUE=''; CYAN=''; BOLD=''; GRAY=''
  fi

  _ts() { date '+%H:%M:%S'; }

  log_info()    { echo -e "${BLUE}[$(_ts)] ℹ  $*${NC}"; }
  log_ok()      { echo -e "${GREEN}[$(_ts)] ✅ $*${NC}"; }
  log_warn()    { echo -e "${YELLOW}[$(_ts)] ⚠  $*${NC}" 1>&2; }
  log_error()   { echo -e "${RED}[$(_ts)] ❌ $*${NC}" 1>&2; }
  log_step()    { echo -e "\n${BOLD}${CYAN}[$(_ts)] ▶ $*${NC}"; }
  log_muted()   { echo -e "${GRAY}$(_ts) · $*${NC}"; }

  die() { log_error "$*"; exit 1; }
fi
