#!/usr/bin/env bash
# Stage package descriptions and transfer them to existing FAT16 partitions.
set -eo pipefail

# Resolve project-owned code and configuration independently of the caller's cwd.
SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
source "$SCRIPT_DIR/main.config"
source "$SCRIPT_DIR/functions.sh"
__DEPENDENCIES__=(python3 wget mcopy mdir mmd mattrib losetup lsblk swapon blockdev)
usage() {
    echo "Usage: $0 --disk DEVICE_OR_IMAGE (--install NAME[,NAME...] | --package-list FILE --packages-path DIR) [--config FILE] [--post-script FILE] [--clean-on-close]"
}

# Parse package selection and explicit target options.
DISK= INSTALL= LIST= PACKAGES= POST= CONFIG="$SCRIPT_DIR/partitions.config" CLEAN=false
while (($#)); do
    case "$1" in
        -d|--disk|-i|--install|-l|--package-list|-p|--packages-path|-s|--post-script|-c|--config)
            (($# >= 2)) || log_error "Missing value for $1"
            case "$1" in
                -d|--disk) DISK=$2;; -i|--install) INSTALL=$2;; -l|--package-list) LIST=$2;;
                -p|--packages-path) PACKAGES=$2;; -s|--post-script) POST=$2;; -c|--config) CONFIG=$2;;
            esac
            shift 2 ;;
        --clean-on-close) CLEAN=true; shift ;;
        -h|--help) usage; exit 0 ;;
        *) log_error "Unknown option: $1" ;;
    esac
done

# The Python installer stages components and validates the target before transfer.
[[ -n "$DISK" ]] || { usage; exit 1; }
check_dependencies
args=(install --disk "$DISK" --config "$CONFIG" --base "$PACKAGES_BASE_DIR" --cache "$TMP_DOWNLOAD_DIR" --functions "$SCRIPT_DIR/functions.sh")
[[ -n "$INSTALL" ]] && args+=(--install "$INSTALL")
[[ -n "$LIST" ]] && args+=(--list "$LIST")
[[ -n "$PACKAGES" ]] && args+=(--packages "$PACKAGES")
[[ -n "$POST" ]] && args+=(--post "$POST")
[[ "$CLEAN" == true ]] && args+=(--clean)
python3 "$SCRIPT_DIR/helpers/package_common.py" "${args[@]}"
