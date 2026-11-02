#!/usr/bin/env bash
# Shared console output and host dependency checks.

log_info() { if [[ -t 1 && ${TERM:-} != dumb && ! ${NO_COLOR+x} ]]; then printf '\033[33m[INFO]\033[0m %s\n' "$1"; else printf '[INFO] %s\n' "$1"; fi; }
log_success() { if [[ -t 1 && ${TERM:-} != dumb && ! ${NO_COLOR+x} ]]; then printf '\033[32m[ OK ]\033[0m %s\n' "$1"; else printf '[ OK ] %s\n' "$1"; fi; }
log_error() { if [[ -t 2 && ${TERM:-} != dumb && ! ${NO_COLOR+x} ]]; then printf '\033[31m[ERROR]\033[0m %s\n' "$1" >&2; else printf '[ERROR] %s\n' "$1" >&2; fi; exit 1; }
log_warn() { if [[ -t 2 && ${TERM:-} != dumb && ! ${NO_COLOR+x} ]]; then printf '\033[33m[WARN]\033[0m %s\n' "$1" >&2; else printf '[WARN] %s\n' "$1" >&2; fi; }

# Each entry point declares its required host commands in __DEPENDENCIES__.
check_dependencies() {
    local cmd
    for cmd in "${__DEPENDENCIES__[@]}"; do
        if ! command -v "$cmd" &> /dev/null; then
            log_error "Required command '$cmd' not found. Please install it."
        fi
    done
}
