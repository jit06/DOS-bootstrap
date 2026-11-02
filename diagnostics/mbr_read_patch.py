#!/usr/bin/env python3
"""Validate an MBR and replace only its executable boot-code bytes."""
import struct
import sys
from pathlib import Path

def patch(sector, code, disk_sectors):
    if len(sector) != 512 or sector[510:512] != b'\x55\xaa':
        raise ValueError('No valid MBR signature; recreate the disk layout first')
    if len(code) != 440:
        raise ValueError('Diagnostic boot code must be exactly 440 bytes')
    entries = [sector[446+i*16:462+i*16] for i in range(4)]
    if any(entry[0] not in (0, 0x80) for entry in entries):
        raise ValueError('Invalid partition boot flag; recreate or repair the layout first')
    active = [entry for entry in entries if entry[0] == 0x80]
    if len(active) != 1:
        raise ValueError('Exactly one active partition is required; recreate the disk layout first')
    entry = active[0]
    start, size = struct.unpack_from('<II', entry, 8)
    if not entry[4] or not (entry[2] & 0x3f) or entry[1] == 255 or not start or not size or start + size > disk_sectors:
        raise ValueError('Invalid active partition type, CHS start, or LBA boundaries; recreate or repair the layout first')
    result = code + sector[440:]
    if result[440:] != sector[440:]:
        raise ValueError('Preserved MBR bytes changed')
    return result

def main():
    if len(sys.argv) != 5:
        sys.exit('Usage: mbr_read_patch.py ORIGINAL CODE DISK_SECTORS OUTPUT')
    try:
        original, code = Path(sys.argv[1]).read_bytes(), Path(sys.argv[2]).read_bytes()
        result = patch(original, code, int(sys.argv[3]))
        Path(sys.argv[4]).write_bytes(result)
    except (OSError, ValueError) as exc:
        sys.exit(f'[ERROR] {exc}')

if __name__ == '__main__': main()
