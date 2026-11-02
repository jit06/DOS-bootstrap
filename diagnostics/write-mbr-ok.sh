#!/usr/bin/env bash
# Destructive standalone BIOS diagnostic. Writes exactly LBA 0.
set -euo pipefail
dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
die() { printf '[ERROR] %s\n' "$*" >&2; exit 1; }
[[ $# == 1 ]] || die "Usage: $0 WHOLE_BLOCK_DEVICE"
((EUID == 0)) || die 'Run as root.'
for tool in readlink lsblk swapon blockdev flock mktemp dd cmp stat od tr rm; do
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
tmpdir=$(mktemp -d "${TMPDIR:-/tmp}/mbr-ok.XXXXXXXX") || die 'Cannot create temporary directory.'
trap 'rm -rf -- "$tmpdir"' EXIT
if command -v nasm >/dev/null; then
    nasm -f bin "$dir/mbr-ok.asm" -o "$tmpdir/mbr.bin"
elif command -v as >/dev/null && command -v ld >/dev/null && command -v objcopy >/dev/null; then
    as --32 "$dir/mbr-ok.S" -o "$tmpdir/mbr.o"
    ld -m elf_i386 -Ttext 0x7c00 -e _start "$tmpdir/mbr.o" -o "$tmpdir/mbr.elf"
    objcopy -O binary -j .text "$tmpdir/mbr.elf" "$tmpdir/mbr.bin"
else
    die 'Install NASM or GNU binutils (as, ld and objcopy).'
fi
[[ $(stat -c %s "$tmpdir/mbr.bin") == 512 ]] || die 'Assembled sector must be exactly 512 bytes.'
[[ $(od -An -tx1 -j 510 -N 2 "$tmpdir/mbr.bin" | tr -d ' \n') == 55aa ]] || die 'Assembled sector has an invalid boot signature.'
printf 'Target: %s\n' "$target"
lsblk -dn -o NAME,SIZE,MODEL,SERIAL "$target"
printf 'WARNING: LBA 0, including the existing MBR and partition table, will be overwritten.\n'
printf 'Type exactly ERASE %s to continue: ' "$target"
IFS= read -r answer || die 'Confirmation was not provided.'
[[ "$answer" == "ERASE $target" ]] || die 'Confirmation did not match; no write performed.'
check_busy
dd if="$tmpdir/mbr.bin" of="$target" bs=512 count=1 conv=notrunc,fsync status=none || die 'LBA 0 write failed; disk may be partially modified.'
dd if="$target" of="$tmpdir/readback.bin" bs=512 count=1 iflag=fullblock status=none || die 'LBA 0 readback failed.'
cmp -s "$tmpdir/mbr.bin" "$tmpdir/readback.bin" || die 'LBA 0 readback does not match the diagnostic sector.'
printf '[ OK ] Diagnostic sector written and verified on %s.\n' "$target"
