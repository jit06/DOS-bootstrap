#!/usr/bin/env bash
# Generate editable descriptions from a package list; never writes to the target disk.
set -eo pipefail

# Use the same package cache and dependency checks as installation.
SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
source "$SCRIPT_DIR/main.config"
source "$SCRIPT_DIR/functions.sh"
__DEPENDENCIES__=(python3 wget mdir mcopy)
usage() {
    echo "Usage: $0 --package-list FILE --packages-path DIRECTORY [--clean-on-close]"
    echo "Generates editable C: mappings for ZIP archives, FAT12 images, files and local directories."
}

# Package generation only writes editable descriptions, not a target disk.
LIST_PATH= PKG_DEST_DIR= CLEAN_UP=false
while (($#)); do
    case "$1" in
        -l|--package-list|-p|--packages-path)
            (($# >= 2)) || log_error "Missing value for $1"
            if [[ "$1" == -l || "$1" == --package-list ]]; then LIST_PATH=$2; else PKG_DEST_DIR=$2; fi
            shift 2 ;;
        --clean-on-close) CLEAN_UP=true; shift ;;
        -h|--help) usage; exit 0 ;;
        *) log_error "Unknown option: $1" ;;
    esac
done

[[ -n "$LIST_PATH" && -n "$PKG_DEST_DIR" ]] || { usage; exit 1; }
check_dependencies
args=(generate --list "$LIST_PATH" --packages "$PKG_DEST_DIR" --cache "$TMP_DOWNLOAD_DIR")
[[ "$CLEAN_UP" == true ]] && args+=(--clean)
python3 "$SCRIPT_DIR/helpers/package_common.py" "${args[@]}"
