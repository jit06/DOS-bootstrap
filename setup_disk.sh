#!/usr/bin/env bash
# Initialize an existing disk or raw image with an MBR and FAT16 partitions.
set -eo pipefail

# Load shared checks before processing disk and geometry arguments.
SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
source "$SCRIPT_DIR/functions.sh"
__DEPENDENCIES__=(sfdisk mkfs.fat ms-sys python3 blockdev lsblk losetup flock od stat truncate mktemp rm readlink swapon tr sync)

usage() {
    cat <<USAGE
Usage: $0 --disk DEVICE_OR_IMAGE [--config FILE] [--heads N --sectors N]

Initialize an existing whole block device or raw image with MBR and FAT16.
WARNING: Existing data on the target will be destroyed.
  -d, --disk    Whole block device or existing raw image
  -c, --config  Partition configuration (default: partitions.config)
      --heads   Target BIOS heads (1-255; requires --sectors)
      --sectors Target BIOS sectors per track (1-63; requires --heads)
  -h, --help    Show this help
Physical devices require root and interactive confirmation.
USAGE
}

# Explicit options take precedence over the repository partition configuration.
DISK_PATH= CONFIG_PATH="$SCRIPT_DIR/partitions.config"
HEADS_INPUT= SECTORS_INPUT= HEADS_SET=false SECTORS_SET=false
while (($#)); do
    case "$1" in
        -d|--disk|-c|--config|--heads|--sectors)
            (($# >= 2)) || log_error "Missing value for $1"
            case "$1" in
                -d|--disk) DISK_PATH=$2 ;;
                -c|--config) CONFIG_PATH=$2 ;;
                --heads) HEADS_INPUT=$2; HEADS_SET=true ;;
                --sectors) SECTORS_INPUT=$2; SECTORS_SET=true ;;
            esac
            shift 2 ;;
        -h|--help) usage; exit 0 ;;
        *) log_error "Unknown option: $1" ;;
    esac
done
[[ -n "$DISK_PATH" ]] || { usage; exit 1; }
[[ "$HEADS_SET" == "$SECTORS_SET" ]] || log_error "Specify --heads and --sectors together"
if [[ "$HEADS_SET" == true ]]; then
    [[ "$HEADS_INPUT" =~ ^[0-9]{1,3}$ && "$SECTORS_INPUT" =~ ^[0-9]{1,3}$ ]] || log_error "Geometry values must be decimal integers"
    heads=$((10#$HEADS_INPUT)); sectors_per_track=$((10#$SECTORS_INPUT))
    ((heads >= 1 && heads <= 255 && sectors_per_track >= 1 && sectors_per_track <= 63)) || log_error "Geometry must have 1-255 heads and 1-63 sectors per track"
fi
# Resolve and validate the target before constructing the layout.
check_dependencies
[[ -f "$CONFIG_PATH" && -r "$CONFIG_PATH" ]] || log_error "Cannot read configuration: $CONFIG_PATH"
DISK_PATH=$(readlink -e -- "$DISK_PATH") || log_error "Target does not exist"
[[ -b "$DISK_PATH" || -f "$DISK_PATH" ]] || log_error "Target must be a whole block device or a regular raw image"
[[ -w "$DISK_PATH" ]] || log_error "Target is not writable: $DISK_PATH"

is_device=false
if [[ -b "$DISK_PATH" ]]; then
    is_device=true
    [[ $EUID -eq 0 ]] || log_error "Run as root for a physical device"
    [[ $(lsblk -dn -o TYPE "$DISK_PATH") == disk ]] || log_error "Target must be a whole disk"
    [[ $(blockdev --getss "$DISK_PATH") == 512 ]] || log_error "Only 512-byte logical sectors are supported"
    disk_bytes=$(blockdev --getsize64 "$DISK_PATH")
else
    disk_bytes=$(stat -c %s -- "$DISK_PATH")
fi
((disk_bytes % 512 == 0)) || log_error "Target size must be a multiple of 512 bytes"
disk_sectors=$((disk_bytes / 512))
((disk_sectors > 2048 && disk_sectors <= 4294967295)) || log_error "Target size is outside supported MBR limits"
# mkfs.fat can add a sector to a filesystem on an odd-sector-sized target,
# even with an explicit block count, causing the BPB to exceed its partition.
((disk_sectors % 2 == 0)) || log_error "Target has an odd number of sectors; use a target with a size divisible by 1024 bytes"
if [[ "$HEADS_SET" == false ]]; then
    # Pick the smallest conventional translation that covers the entire disk.
    sectors_per_track=63
    heads=0
    for candidate in 16 32 64 128 255; do
        if ((disk_sectors <= 1024 * candidate * sectors_per_track)); then heads=$candidate; break; fi
    done
    ((heads > 0)) || log_error "Disk exceeds conventional 1024-cylinder CHS capacity; use a smaller target or layout and specify the target BIOS geometry"
    geometry_source=automatic
    log_warn "Automatic geometry is a heuristic; use --heads and --sectors for the target BIOS geometry."
else
    geometry_source=explicit
fi
((heads > 0 && heads <= 255 && sectors_per_track > 0 && sectors_per_track <= 63)) || log_error "Unsupported disk geometry"

# The optional fourth field marks the active partition. Letters identify positions.
declare -a letters sizes starts clusters
active=-1
line_number=0
while IFS= read -r line || [[ -n "$line" ]]; do
    ((line_number+=1))
    line=${line%%#*}
    line=${line#"${line%%[![:space:]]*}"}
    line=${line%"${line##*[![:space:]]}"}
    [[ -z "$line" ]] && continue
    [[ "$line" =~ ^([C-F]):([a-zA-Z0-9]*)(:([0-9]*[kKmMgG]?)(:(boot))?)?$ ]] || log_error "Invalid configuration at line $line_number"
    letter=${BASH_REMATCH[1]}; fs=${BASH_REMATCH[2],,}; size=${BASH_REMATCH[4]:-}; marker=${BASH_REMATCH[6]:-}
    index=${#letters[@]}
    ((index < 4)) || log_error "At most four partitions are supported"
    expected=$(printf "\\$(printf '%03o' $((67 + index)))")
    [[ "$letter" == "$expected" ]] || log_error "Partition letters must be sequential starting with C"
    [[ -z "$fs" || "$fs" == fat16 ]] || log_error "Unsupported filesystem: $fs"
    if [[ -n "$marker" ]]; then
        ((active == -1)) || log_error "Only one partition may be active"
        active=$index
    fi
    if ((index > 0)) && [[ -z "${sizes[index-1]}" ]]; then log_error "Only the last partition may use remaining space"; fi
    letters+=("$letter"); sizes+=("$size")
done < "$CONFIG_PATH"
((${#letters[@]} > 0)) || log_error "No partitions configured"
((active >= 0)) || active=0

# 1 MiB aligned starts. Explicit sizes are exact multiples of 1 KiB.
next=2048
for ((i=0; i<${#letters[@]}; i++)); do
    start=$(((next + 2047) / 2048 * 2048))
    spec=${sizes[i]}
    if [[ -z "$spec" ]]; then
        sectors=$(((disk_sectors - start) / 2 * 2))
    else
        [[ "$spec" =~ ^([0-9]+)([kKmMgG])$ ]] || log_error "Size must use K, M or G: $spec"
        number=${BASH_REMATCH[1]}; unit=${BASH_REMATCH[2],,}
        ((${#number} <= 9)) || log_error "Size is too large: $spec"
        number=$((10#$number))
        case "$unit" in k) factor=2;; m) factor=2048;; g) factor=2097152;; esac
        sectors=$((number * factor))
    fi
    ((sectors > 0 && sectors <= 4194304 && start + sectors <= disk_sectors)) || log_error "Partition ${letters[i]} does not fit or exceeds 2 GiB"
    ((start + sectors <= 1024 * heads * sectors_per_track)) || log_error "Partition ${letters[i]} exceeds the selected geometry's 1024-cylinder CHS limit; reduce the layout or use the correct target BIOS geometry"
    # Pick a DOS-compatible cluster size and verify the actual formatter output below.
    if ((sectors <= 65536)); then spc=1
    elif ((sectors <= 131072)); then spc=2
    elif ((sectors <= 262144)); then spc=4
    elif ((sectors <= 524288)); then spc=8
    elif ((sectors <= 1048576)); then spc=16
    elif ((sectors <= 2097152)); then spc=32
    else spc=64; fi
    starts+=("$start"); sizes[i]=$sectors; clusters+=("$spc")
    next=$((start + sectors))
done

# Lock first, then refuse devices already held, mounted, used for swap, or loop-backed.
exec {disk_fd}<>"$DISK_PATH"
flock -n -x "$disk_fd" || log_error "Target is locked by another process"
if [[ "$is_device" == true ]]; then
    devices=$(lsblk -nrpo NAME "$DISK_PATH") || log_error "Cannot inspect target devices"
    swap_devices=$(swapon --show --noheadings --raw --output NAME) || log_error "Cannot inspect swap devices"
    while IFS= read -r dev; do
        [[ -n "$dev" ]] || continue
        mountpoints=$(lsblk -dn -o MOUNTPOINTS "$dev") || log_error "Cannot inspect mount points for $dev"
        [[ -z "$mountpoints" ]] || log_error "Mounted device: $dev"
        while IFS= read -r swapdev; do
            [[ -z "$swapdev" ]] && continue
            swapdev=$(readlink -e -- "$swapdev") || log_error "Cannot resolve swap device"
            [[ "$swapdev" != "$dev" ]] || log_error "Device is used for swap: $dev"
        done <<< "$swap_devices"
        name=${dev##*/}
        for holder in /sys/class/block/"$name"/holders/*; do
            [[ ! -e "$holder" ]] || log_error "Device has an active holder: $dev"
        done
    done <<< "$devices"
else
    loops=$(losetup -j "$DISK_PATH") || log_error "Cannot inspect loop devices"
    [[ -z "$loops" ]] || log_error "Image is attached to a loop device"
fi

# Probe actual mkfs geometry on disposable sparse files before touching the target.
tmpdir=$(mktemp -d "${TMPDIR:-/tmp}/dos-bootstrap.XXXXXXXX")
cleanup() { rm -rf -- "$tmpdir"; }
trap cleanup EXIT
for ((i=0; i<${#letters[@]}; i++)); do
    probe="$tmpdir/probe"
    truncate -s "$((sizes[i] * 512))" "$probe"
    if ! mkfs.fat -a -F 16 -S 512 -s "${clusters[i]}" -g "$heads/$sectors_per_track" -h "${starts[i]}" "$probe" >/dev/null 2>&1; then
        log_error "Cannot create FAT16 filesystem for ${letters[i]}"
    fi
    read_le() { od -An -tu"$2" -j "$1" -N "$2" "$probe" | tr -d ' '; }
    reserved=$(read_le 14 2); fats=$(read_le 16 1); root_entries=$(read_le 17 2); fat_sectors=$(read_le 22 2)
    data_sectors=$((sizes[i] - reserved - fats * fat_sectors - (root_entries * 32 + 511) / 512))
    count=$((data_sectors / clusters[i]))
    ((count >= 4085 && count <= 65524)) || log_error "Partition ${letters[i]} cannot be represented as FAT16 ($count clusters)"
    rm -f -- "$probe"
done

printf 'Target: %s (%s bytes)\n' "$DISK_PATH" "$disk_bytes"
printf 'Target BIOS geometry: %s heads, %s sectors/track (%s)\n' "$heads" "$sectors_per_track" "$geometry_source"
printf 'Partition  Start sector  Sectors  Filesystem  Active\n'
for ((i=0; i<${#letters[@]}; i++)); do
    flag=no; ((i == active)) && flag=yes
    printf '%-10s %-13s %-8s %-11s %s\n' "${letters[i]}:" "${starts[i]}" "${sizes[i]}" fat16 "$flag"
done
if [[ "$is_device" == true ]]; then
    [[ -t 0 ]] || log_error "Physical disk confirmation requires a terminal"
    printf 'Type the full target path to erase it: '
    IFS= read -r confirmation
    [[ "$confirmation" == "$DISK_PATH" ]] || log_error "Confirmation did not match; no changes made"
fi

{
    printf 'label: dos\nunit: sectors\n\n'
    for ((i=0; i<${#letters[@]}; i++)); do
        type=6; ((sizes[i] < 65536)) && type=4
        printf 'start=%s, size=%s, type=%s' "${starts[i]}" "${sizes[i]}" "$type"
        ((i == active)) && printf ', bootable'
        printf '\n'
    done
} > "$tmpdir/layout"
sfdisk --wipe always --wipe-partitions always "$DISK_PATH" < "$tmpdir/layout"
# Install generic Syslinux MBR code only; never write a DOS volume boot record.
if [[ "$is_device" == true ]]; then
    ms-sys --mbrsyslinux "$DISK_PATH"
else
    ms-sys --force --mbrsyslinux "$DISK_PATH"
fi
# sfdisk does not accept the target BIOS geometry. Rewrite only the six CHS
# bytes in each entry after confirming its LBA boundaries and active flag.
part_specs=()
for ((i=0; i<${#letters[@]}; i++)); do part_specs+=("${starts[i]}:${sizes[i]}"); done
python3 - "$DISK_PATH" "$heads" "$sectors_per_track" "$active" "${part_specs[@]}" <<'PY'
import os, struct, sys

path, heads, sectors, active, *parts = sys.argv[1:]
heads, sectors, active = int(heads), int(sectors), int(active)
def chs(lba):
    cylinder, remainder = divmod(lba, heads * sectors)
    head, sector = divmod(remainder, sectors)
    if cylinder > 1023:
        raise ValueError('partition exceeds the BIOS CHS limit')
    return bytes((head, (sector + 1) | ((cylinder >> 2) & 0xc0), cylinder & 0xff))
try:
    with open(path, 'r+b', buffering=0) as disk:
        mbr = disk.read(512)
        if len(mbr) != 512 or mbr[510:] != b'\x55\xaa':
            raise ValueError('invalid MBR after partitioning')
        table = bytearray(mbr[446:510])
        for i, spec in enumerate(parts):
            start, length = map(int, spec.split(':'))
            offset = i * 16
            entry = table[offset:offset + 16]
            if (struct.unpack_from('<II', entry, 8) != (start, length)
                    or entry[0] != (0x80 if i == active else 0)
                    or entry[4] != (4 if length < 65536 else 6)):
                raise ValueError(f'partition {i + 1} differs from validated layout')
            table[offset + 1:offset + 4] = chs(start)
            table[offset + 5:offset + 8] = chs(start + length - 1)
        if any(table[i * 16 + 4] for i in range(len(parts), 4)):
            raise ValueError('unexpected extra partition')
        disk.seek(446)
        if disk.write(table) != len(table):
            raise OSError('short partition table write')
        os.fsync(disk.fileno())
        disk.seek(446)
        if disk.read(64) != table:
            raise OSError('partition CHS verification failed')
except (OSError, ValueError) as exc:
    sys.exit(f'ERROR: could not write partition CHS fields: {exc}')
PY
for ((i=0; i<${#letters[@]}; i++)); do
    mkfs.fat -a -I --mbr=n -F 16 -S 512 -s "${clusters[i]}" -g "$heads/$sectors_per_track" -h "${starts[i]}" --offset="${starts[i]}" "$DISK_PATH" "$((sizes[i] / 2))"
done
sync
log_success "Disk initialization complete."
