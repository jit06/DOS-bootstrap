#!/usr/bin/env bash
# Destructive standalone BIOS diagnostic. Writes exactly LBA 0.
set -euo pipefail
dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
die() { printf '[ERROR] %s\n' "$*" >&2; exit 1; }
mode=--mbrdos
if [[ ${1:-} == --restore-syslinux ]]; then mode=--mbrsyslinux; shift; fi
[[ $# == 1 ]] || die "Usage: $0 [--restore-syslinux] WHOLE_BLOCK_DEVICE"
((EUID == 0)) || die 'Run as root.'
for tool in readlink lsblk swapon blockdev flock mktemp dd cmp rm python3 ms-sys cp; do
    command -v "$tool" >/dev/null || die "Missing required command: $tool"
done
target=$(readlink -e -- "$1") || die 'Cannot resolve target.'
[[ -b "$target" ]] || die 'Target must be a block device, not an image or file.'
[[ $(lsblk -dn -o TYPE "$target") == disk ]] || die 'Target must be a whole disk.'
[[ $(blockdev --getss "$target") == 512 ]] || die 'Target must have 512-byte logical sectors.'
[[ -t 0 ]] || die 'Destructive confirmation requires an interactive terminal.'

exec {disk_fd}<>"$target"
flock -n -x "$disk_fd" || die 'Target is locked by another process.'
check_busy() {
    local devices swaps dev swapdev mountpoints holder
    devices=$(lsblk -nrpo NAME "$target") || die 'Cannot inspect target devices.'
    [[ -n "$devices" ]] || die 'No target devices returned by lsblk.'
    swaps=$(swapon --show --noheadings --raw --output NAME) || die 'Cannot inspect swap devices.'
    while IFS= read -r dev; do
        [[ -n "$dev" ]] || continue
        mountpoints=$(lsblk -dn -o MOUNTPOINTS "$dev") || die "Cannot inspect mounts: $dev"
        [[ -z "$mountpoints" ]] || die "Device is mounted: $dev"
        while IFS= read -r swapdev; do
            [[ -n "$swapdev" ]] || continue
            swapdev=$(readlink -e -- "$swapdev") || die 'Cannot resolve swap device.'
            [[ "$swapdev" != "$dev" ]] || die "Device is used for swap: $dev"
        done <<< "$swaps"
        for holder in /sys/class/block/"${dev##*/}"/holders/*; do
            [[ ! -e "$holder" ]] || die "Device has an active holder: $dev"
        done
    done <<< "$devices"
}
check_busy
tmpdir=$(mktemp -d "${TMPDIR:-/tmp}/mbr-dos.XXXXXXXX") || die 'Cannot create temporary directory.'
trap 'rm -rf -- "$tmpdir"' EXIT
disk_sectors=$(blockdev --getsz "$target") || die 'Cannot determine disk capacity.'
dd if="$target" of="$tmpdir/original.bin" bs=512 count=1 iflag=fullblock status=none || die 'Cannot read LBA 0.'
PYTHONDONTWRITEBYTECODE=1 python3 - "$dir" "$tmpdir/original.bin" "$disk_sectors" <<'PY'
import sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from mbr_read_patch import patch
try:
    patch(Path(sys.argv[2]).read_bytes(), bytes(440), int(sys.argv[3]))
except (OSError, ValueError) as exc:
    sys.exit(f'[ERROR] {exc}')
PY
cp -- "$tmpdir/original.bin" "$tmpdir/mbr.bin"
ms-sys --force "$mode" "$tmpdir/mbr.bin" || die 'Could not prepare MBR code.'
python3 - "$tmpdir/original.bin" "$tmpdir/mbr.bin" <<'PY'
import sys
from pathlib import Path
before, after = (Path(path).read_bytes() for path in sys.argv[1:])
if len(before) != 512 or len(after) != 512 or before[440:] != after[440:]:
    sys.exit('[ERROR] Prepared MBR changed preserved bytes; no write performed.')
PY
printf 'Target: %s\n' "$target"
lsblk -dn -o NAME,SIZE,MODEL,SERIAL "$target"
printf 'WARNING: MBR boot code at LBA 0 will be replaced using ms-sys %s. The disk signature and partition table are preserved.\n' "$mode"
printf 'Type exactly WRITE %s to continue: ' "$target"
IFS= read -r answer || die 'Confirmation was not provided.'
[[ "$answer" == "WRITE $target" ]] || die 'Confirmation did not match; no write performed.'
check_busy
dd if="$target" of="$tmpdir/recheck.bin" bs=512 count=1 iflag=fullblock status=none || die 'Cannot recheck LBA 0.'
cmp -s "$tmpdir/original.bin" "$tmpdir/recheck.bin" || die 'LBA 0 changed since validation; no write performed.'
dd if="$tmpdir/mbr.bin" of="$target" bs=512 count=1 conv=notrunc,fsync status=none || die 'LBA 0 write failed; disk may be partially modified.'
dd if="$target" of="$tmpdir/readback.bin" bs=512 count=1 iflag=fullblock status=none || die 'LBA 0 readback failed.'
cmp -s "$tmpdir/mbr.bin" "$tmpdir/readback.bin" || die 'LBA 0 readback does not match the diagnostic sector.'
printf '[ OK ] Diagnostic sector written and verified on %s.\n' "$target"
