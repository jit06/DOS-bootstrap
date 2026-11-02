#!/usr/bin/env python3
"""MS-DOS 6.22 FAT16 system files and native VBR."""
import hashlib
import os
from pathlib import Path
import struct
import subprocess
NAMES = ('IO.SYS', 'MSDOS.SYS', 'COMMAND.COM')
SHORT = (b'IO      SYS', b'MSDOS   SYS', b'COMMAND COM')
NATIVE_CODE_SHA256 = '1b3eb75ed61fb87d93d1ac398a59f96e02eb492b79be0065e8e67cbe5d03936b'
NATIVE_FILE_SHA256 = {
    'IO.SYS': 'e443d6fb7303cd341d59a260262ae2aa20029a54d8e8daac83c1d4efa9042d75',
    'MSDOS.SYS': '6db133420bf04ddbafe310a720fab934db3e7dc9f5a53fc6692d5a01cbbc0d4e',
    'COMMAND.COM': '65fa71b0a34e91feffc31297fb5e24d40767c72fb513e46aaac721bda7836110',
}

class BootError(Exception):
    pass

def fail(message):
    raise BootError(message)

def command(args):
    try:
        subprocess.run(args, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    except (OSError, subprocess.CalledProcessError) as exc:
        detail = exc.stderr.decode(errors='replace').strip() if isinstance(exc, subprocess.CalledProcessError) and exc.stderr else str(exc)
        fail(f'{args[0]} failed: {detail}')

def native_boot_record(path, originals):
    """Accept only the verified 1.44 MB Microsoft 6.22 disk-1 variant."""
    path = Path(path)
    if not path.is_file() or path.stat().st_size != 1474560:
        fail('Native MS-DOS boot requires the original 1.44 MB MS-DOS 6.22 disk 1 floppy image')
    with path.open('rb') as stream:
        boot=stream.read(512)
        fat1=stream.read(9*512); fat2=stream.read(9*512)
        root=stream.read(224*32)
    if (len(boot)!=512 or boot[:3]!=b'\xeb\x3c\x90' or boot[3:11]!=b'MSDOS5.0'
        or boot[510:]!=b'\x55\xaa' or struct.unpack_from('<HBHBHHBHHHII',boot,11)
        != (512,1,1,2,224,2880,0xf0,9,18,2,0,0)
        or boot[38]!=0x29 or boot[54:62]!=b'FAT12   '
        or len(fat1)!=9*512 or fat1!=fat2 or fat1[:3]!=b'\xf0\xff\xff'
        or root[:11]!=SHORT[0] or root[32:43]!=SHORT[1]
        or hashlib.sha256(boot[62:510]).hexdigest()!=NATIVE_CODE_SHA256
        or any(originals.get(n,b'').hex()!=NATIVE_FILE_SHA256[n] for n in NAMES)):
        fail('Floppy is not the supported original MS-DOS 6.22 disk 1 or its boot code/system files are invalid')
    return boot

def staged_files(stage, originals):
    result = {}
    for name in NAMES:
        path = stage / 'C' / name
        if not path.is_file() or path.is_symlink() or name not in originals:
            fail(f'Missing staged MS-DOS system file: C:{name}')
        content = path.read_bytes()
        if hashlib.sha256(content).digest() != originals[name] or (name == 'IO.SYS' and len(content) < 1536):
            fail(f'Staged C:{name} does not match the original disk 1 file')
        result[name] = content
    return result

class FAT16:
    def __init__(self, disk, start):
        self.disk = Path(disk)
        self.start = start
        with self.disk.open('rb', buffering=0) as f:
            mbr = f.read(512)
            if mbr[446] != 0x80 or any(mbr[446+i*16] != 0 for i in range(1, 4)):
                fail('DOS installation requires C: to be the only active MBR partition')
            f.seek(start)
            self.vbr = f.read(512)
            b = self.vbr
            if len(b) != 512 or b[510:] != b'\x55\xaa': fail('Invalid C: VBR')
            self.bps = struct.unpack_from('<H', b, 11)[0]
            self.spc = b[13]
            self.reserved = struct.unpack_from('<H', b, 14)[0]
            self.fats = b[16]
            self.roots = struct.unpack_from('<H', b, 17)[0]
            self.fat_size = struct.unpack_from('<H', b, 22)[0]
            self.sectors = struct.unpack_from('<H', b, 19)[0] or struct.unpack_from('<I', b, 32)[0]
            hidden = struct.unpack_from('<I', b, 28)[0]
            spt, heads = struct.unpack_from('<HH', b, 24)
            if (self.bps != 512 or not self.spc or not self.reserved or self.fats not in (1, 2)
                    or not self.roots or not self.fat_size or hidden * 512 != start
                    or b[0x24] != 0x80 or b[0x26] != 0x29 or not 1 <= heads <= 255 or not 1 <= spt <= 63):
                fail('C: BPB does not contain supported FAT16 geometry, hidden sectors and BIOS drive 0x80')
            self.root_sectors = (self.roots * 32 + 511) // 512
            self.root_sector = self.reserved + self.fats * self.fat_size
            self.data_sector = self.root_sector + self.root_sectors
            self.clusters = (self.sectors - self.data_sector) // self.spc
            if not 4085 <= self.clusters <= 65524 or self.fat_size * 256 < self.clusters + 2:
                fail('Invalid FAT16 cluster layout')
            f.seek(start + self.reserved * 512)
            self.fat = f.read(self.fat_size * 512)
            if len(self.fat) != self.fat_size * 512: fail('Cannot read FAT')
            for _ in range(1, self.fats):
                if f.read(len(self.fat)) != self.fat: fail('FAT copies differ; refusing DOS installation')
            f.seek(start + self.root_sector * 512)
            self.root = f.read(self.root_sectors * 512)
            if len(self.root) != self.root_sectors * 512: fail('Cannot read FAT root directory')
        self.entries = {}
        self.free_root = 0
        for offset in range(0, self.roots * 32, 32):
            entry = self.root[offset:offset+32]
            if entry[0] in (0, 0xe5):
                self.free_root += 1
                continue
            if entry[11] == 0x0f: continue
            key = entry[:11]
            if key in SHORT:
                if key in self.entries or entry[11] & 0x18: fail('Ambiguous or invalid DOS system file entry')
                self.entries[key] = entry

    def chain(self, entry):
        cluster = struct.unpack_from('<H', entry, 26)[0]
        size = struct.unpack_from('<I', entry, 28)[0]
        needed = (size + self.spc * 512 - 1) // (self.spc * 512)
        if needed == 0: fail('Empty DOS system file')
        seen=set(); clusters=[]
        while True:
            if not 2 <= cluster < self.clusters + 2 or cluster in seen: fail('Invalid DOS system file FAT chain')
            seen.add(cluster); clusters.append(cluster)
            nxt = struct.unpack_from('<H', self.fat, cluster * 2)[0]
            if nxt >= 0xfff8: break
            if nxt < 2 or nxt >= 0xfff0: fail('Invalid DOS system file FAT chain')
            cluster=nxt
            if len(clusters) > needed: fail('DOS system file FAT chain is longer than its size')
        if len(clusters) != needed: fail('DOS system file FAT chain does not match its size')
        return clusters, size

    def verify(self, expected):
        if set(self.entries) != set(SHORT): fail('DOS system files are missing from the FAT16 root')
        if self.root[:11] != SHORT[0] or self.root[32:43] != SHORT[1]:
            fail('Native MS-DOS requires IO.SYS and MSDOS.SYS in the first two root-directory entries')
        all_clusters=set()
        with self.disk.open('rb', buffering=0) as stream:
            for name, short in zip(NAMES, SHORT):
                entry=self.entries[short]
                attrs=entry[11]
                if name != 'COMMAND.COM' and attrs & 0x07 != 0x07: fail(f'{name} is missing read-only, hidden or system attributes')
                if name == 'COMMAND.COM' and attrs & 0x16: fail('COMMAND.COM has invalid hidden, system or directory attributes')
                clusters, size=self.chain(entry)
                if size != len(expected[name]): fail(f'{name} size does not match the staged file')
                if all_clusters.intersection(clusters): fail('DOS system files share FAT clusters')
                all_clusters.update(clusters)
                if name == 'IO.SYS':
                    # The native loader reads the first three sectors sequentially.
                    sectors=[]
                    for cluster in clusters:
                        sectors.extend(range(self.data_sector + (cluster-2)*self.spc,
                                             self.data_sector + (cluster-2)*self.spc + self.spc))
                        if len(sectors) >= 3: break
                    if len(sectors) < 3 or sectors[:3] != list(range(sectors[0], sectors[0]+3)):
                        fail('The first three sectors of IO.SYS are not contiguous')
                content=bytearray()
                for cluster in clusters:
                    stream.seek(self.start + (self.data_sector + (cluster-2)*self.spc)*512)
                    part=stream.read(self.spc*512)
                    if len(part) != self.spc*512: fail(f'Cannot read {name} data')
                    content.extend(part)
                if bytes(content[:size]) != expected[name]: fail(f'{name} contents differ from the staged original')

    def ensure_room(self, expected):
        if self.entries:
            if set(self.entries) != set(SHORT): fail('Existing DOS system files are incomplete; refusing to overwrite them')
            self.verify(expected)
            return False
        if self.root[0] not in (0,0xe5) or self.root[32] not in (0,0xe5):
            fail('Native MS-DOS requires the first two root-directory entries to be free; existing files will not be relocated')
        if self.free_root < 3: fail('Not enough free root-directory entries for DOS system files')
        required=sum((len(expected[name])+self.spc*512-1)//(self.spc*512) for name in NAMES)
        free=[n for n in range(2, self.clusters+2) if struct.unpack_from('<H', self.fat, n*2)[0] == 0]
        if len(free) < required: fail('Not enough free FAT clusters for DOS system files')
        needed=(3+self.spc-1)//self.spc
        free_set=set(free)
        if not any(all(c+i in free_set for i in range(needed)) for c in free):
            fail('No contiguous free space for the first three IO.SYS sectors')
        return True

def install_system_files(disk, image, start, expected, stage):
    fat=FAT16(disk,start)
    if not fat.ensure_room(expected):
        return
    for name in NAMES:
        command(['mcopy', '-i', image, str(stage/'C'/name), '::/'+name])
        if name != 'COMMAND.COM':
            command(['mattrib', '-i', image, '+r', '+h', '+s', '::/'+name])
        else:
            command(['mattrib', '-i', image, '-r', '-h', '-s', '+a', '::/'+name])
    FAT16(disk,start).verify(expected)

def install_vbr(disk, start, boot_record, expected):
    fat=FAT16(disk,start)
    fat.verify(expected)
    original=fat.vbr
    patched=bytearray(original)
    patched[:3]=b'\xeb\x3c\x90'
    patched[0x3e:0x1fe]=boot_record[0x3e:0x1fe]
    try:
        with open(disk, 'r+b', buffering=0) as stream:
            stream.seek(start)
            if stream.write(patched) != 512: raise OSError('Short VBR write')
            os.fsync(stream.fileno())
            stream.seek(start)
            if stream.read(512) != patched: raise OSError('VBR read-back verification failed')
    except OSError as exc:
        try:
            with open(disk, 'r+b', buffering=0) as stream:
                stream.seek(start)
                if stream.write(original) != 512: raise OSError('Short rollback write')
                os.fsync(stream.fileno())
                stream.seek(start)
                if stream.read(512) != original: raise OSError('VBR rollback verification failed')
        except OSError as rollback:
            fail(f'VBR write failed ({exc}); rollback failed ({rollback})')
        fail(f'VBR write failed and original VBR was restored: {exc}')
