#!/usr/bin/env bash
# Destructive standalone BIOS handoff diagnostic.
set -euo pipefail
dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
die() { printf '[ERROR] %s\n' "$*" >&2; exit 1; }
[[ $# == 3 && ( $1 == --backup-dir || $1 == --restore ) ]] || die "Usage: $0 (--backup-dir NEW_DIRECTORY | --restore BACKUP_DIRECTORY) WHOLE_BLOCK_DEVICE"
mode=install; [[ $1 == --restore ]] && mode=restore
backup=$2; requested_target=$3
((EUID == 0)) || die 'Run as root.'
for tool in readlink lsblk swapon blockdev flock mktemp dd cmp rm python3 as ld objcopy; do
    command -v "$tool" >/dev/null || die "Missing required command: $tool"
done
target=$(readlink -e -- "$requested_target") || die 'Cannot resolve target.'
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
tmpdir=$(mktemp -d "${TMPDIR:-/tmp}/mbr-vbr.XXXXXXXX") || die 'Cannot create temporary directory.'
trap 'rm -rf -- "$tmpdir"' EXIT
if [[ $mode == install ]]; then
    [[ ! -e $backup && ! -L $backup ]] || die 'Backup directory must not already exist.'
    as --32 "$dir/mbr-vbr-test-mbr.S" -o "$tmpdir/mbr.o"
    ld -m elf_i386 -Ttext 0x0600 -e _start "$tmpdir/mbr.o" -o "$tmpdir/mbr.elf"
    objcopy -O binary -j .text "$tmpdir/mbr.elf" "$tmpdir/mbr-code.bin"
    as --32 "$dir/mbr-vbr-test-vbr.S" -o "$tmpdir/vbr.o"
    ld -m elf_i386 -Ttext 0x7c00 -e _start "$tmpdir/vbr.o" -o "$tmpdir/vbr.elf"
    objcopy -O binary -j .text "$tmpdir/vbr.elf" "$tmpdir/vbr-code.bin"
else
    [[ -d $backup && ! -L $backup ]] || die 'Backup directory does not exist or is a symlink.'
fi
disk_sectors=$(blockdev --getsz "$target") || die 'Cannot determine disk capacity.'
PYTHONDONTWRITEBYTECODE=1 python3 "$dir/mbr_vbr_test.py" check "$target" "$disk_sectors" "$backup"
printf 'Target: %s\n' "$target"
lsblk -dn -o NAME,SIZE,MODEL,SERIAL "$target"
printf 'WARNING: This will %s BOTH the MBR and active FAT16 VBR on %s.\n' "$mode" "$target"
printf 'Backup directory: %s\n' "$backup"
printf 'Type exactly WRITE %s to continue: ' "$target"
IFS= read -r answer || die 'Confirmation was not provided.'
[[ "$answer" == "WRITE $target" ]] || die 'Confirmation did not match; no write performed.'
check_busy
if [[ $mode == install ]]; then
    PYTHONDONTWRITEBYTECODE=1 python3 "$dir/mbr_vbr_test.py" install "$target" "$disk_sectors" "$backup" "$tmpdir/mbr-code.bin" "$tmpdir/vbr-code.bin"
else
    PYTHONDONTWRITEBYTECODE=1 python3 "$dir/mbr_vbr_test.py" restore "$target" "$disk_sectors" "$backup"
fi
