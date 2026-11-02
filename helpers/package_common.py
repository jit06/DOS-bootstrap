#!/usr/bin/env python3
"""Safe package-list, archive and FAT16 operations used by the shell scripts."""
import argparse
import contextlib
import ctypes
import ctypes.util
import fcntl
import hashlib
import os
from pathlib import Path, PurePosixPath
import re
import shlex
import shutil
import stat
import struct
import subprocess
import sys
import tempfile
import urllib.parse
import zipfile
import dos622_boot

class PackageError(Exception):
    pass

KWAJ_MAGIC = b'KWAJ\x88\xf0\x27\xd1'

def load_mspack():
    """Use the distribution's libmspack; its msexpand example is not packaged everywhere."""
    name = ctypes.util.find_library('mspack')
    if not name:
        fail('Required library libmspack not found. Please install libmspack.')
    try:
        lib = ctypes.CDLL(name)
        lib.mspack_create_kwaj_decompressor.argtypes = [ctypes.c_void_p]
        lib.mspack_create_kwaj_decompressor.restype = ctypes.c_void_p
        lib.mspack_destroy_kwaj_decompressor.argtypes = [ctypes.c_void_p]
        lib.mspack_destroy_kwaj_decompressor.restype = None
        return lib
    except (OSError, AttributeError) as exc:
        fail(f'Cannot load required libmspack KWAJ support: {exc}')

def decompress_kwaj(lib, source, output):
    with source.open('rb') as stream:
        header = stream.read(18)
    if len(header) < 18 or header[:8] != KWAJ_MAGIC:
        fail(f'Corrupt KWAJ header: {source}')
    method, offset, flags = struct.unpack_from('<HHH', header, 8)
    if method not in (0, 1, 2, 3):
        fail(f'Unsupported KWAJ compression method {method}: {source}')
    if not flags & 1:
        fail(f'Unsupported KWAJ file without an uncompressed length: {source}')
    if offset < 18 or offset > source.stat().st_size:
        fail(f'Corrupt KWAJ data offset: {source}')
    expected = struct.unpack_from('<I', header, 14)[0]
    instance = lib.mspack_create_kwaj_decompressor(None)
    if not instance:
        fail('libmspack could not create a KWAJ decompressor')
    try:
        # mskwaj_decompressor exposes open, close, extract, decompress, last_error.
        methods = ctypes.cast(instance, ctypes.POINTER(ctypes.c_void_p))
        decompress = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_void_p, ctypes.c_char_p, ctypes.c_char_p)(methods[3])
        code = decompress(instance, os.fsencode(source), os.fsencode(output))
    finally:
        lib.mspack_destroy_kwaj_decompressor(instance)
    if code != 0 or not output.is_file() or output.stat().st_size != expected:
        fail(f'KWAJ decompression failed or produced an incorrect length: {source} (libmspack error {code})')

def fail(message):
    raise PackageError(message)

def info(message):
    prefix = '\x1b[33m[INFO]\x1b[0m' if sys.stdout.isatty() and os.environ.get('TERM') != 'dumb' and 'NO_COLOR' not in os.environ else '[INFO]'
    print(f'{prefix} {message}', flush=True)

def success(message):
    prefix = '\x1b[32m[ OK ]\x1b[0m' if sys.stdout.isatty() and os.environ.get('TERM') != 'dumb' and 'NO_COLOR' not in os.environ else '[ OK ]'
    print(f'{prefix} {message}', flush=True)

def run(args, **kwargs):
    try:
        return subprocess.run(args, check=True, **kwargs)
    except subprocess.CalledProcessError as exc:
        fail(f"Command failed: {args[0]} (exit status {exc.returncode})")
    except OSError as exc:
        fail(f"Command failed: {args[0]}: {exc}")

def lines(path):
    try:
        with open(path, encoding='utf-8') as stream:
            for number, text in enumerate(stream, 1):
                try:
                    parts = shlex.split(text, comments=True, posix=True)
                except ValueError as exc:
                    fail(f'{path}:{number}: {exc}')
                if parts:
                    yield number, parts
    except OSError as exc:
        fail(f'Cannot read {path}: {exc}')

def package_entries(path):
    for number, fields in lines(path):
        if len(fields) != 2 or not re.fullmatch(r'[A-Za-z0-9_.-]+', fields[0]) or fields[0] in ('.', '..'):
            fail(f'{path}:{number}: expected component name and source')
        yield fields

def checked_path(value, *, root_allowed=False, fat=False):
    if '\\' in value or '\x00' in value or value.startswith('/'):
        fail(f'Invalid path: {value!r}')
    value = value.rstrip('/')
    if not value or value == '.':
        if root_allowed:
            return ()
        fail('Empty path')
    parts = value.split('/')
    if any(p in ('', '.', '..') for p in parts):
        fail(f'Invalid path: {value!r}')
    if fat:
        for p in parts:
            if (any(ord(c) < 32 or c in '<>:"|?*[]' for c in p) or p.endswith((' ', '.'))
                or re.fullmatch(r'(?i)(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?', p)):
                fail(f'Invalid FAT destination: {value!r}')
    return tuple(parts)

def desc_entries(path):
    for number, fields in lines(path):
        if len(fields) != 2:
            fail(f'{path}:{number}: expected exactly two quoted paths')
        src, dest = fields
        source = checked_path(src, root_allowed=True)
        match = re.fullmatch(r'([C-F]):(.*)', dest)
        if not match:
            fail(f'{path}:{number}: invalid destination {dest!r}')
        yield source, match.group(1), checked_path(match.group(2), root_allowed=True, fat=True)

def acquire(source, cache, label=None):
    parsed = urllib.parse.urlsplit(source)
    if parsed.scheme in ('http', 'https'):
        name = Path(urllib.parse.unquote(parsed.path)).name
        if not name or name in ('.', '..'):
            fail(f'Invalid download URL: {source}')
        cache.mkdir(parents=True, exist_ok=True)
        # Incorporate the complete URL to avoid collisions between different servers.
        import hashlib
        key = hashlib.sha256(source.encode()).hexdigest()[:16]
        target = cache / (key + '-' + name)
        if not target.is_file():
            if label: info(f'Downloading source for {label}')
            fd, tmp = tempfile.mkstemp(prefix='.download-', dir=cache)
            os.close(fd)
            try:
                run(['wget', '-q', '-O', tmp, source])
                os.replace(tmp, target)
                if label: success(f'Downloaded source for {label}')
            finally:
                if os.path.exists(tmp): os.unlink(tmp)
        elif label:
            info(f'Using cached source for {label}')
        return target, True, name
    if parsed.scheme:
        fail(f'Unsupported source protocol: {parsed.scheme}')
    path = Path(source).resolve()
    if not path.is_file() and not path.is_dir():
        fail(f'Local source does not exist: {source}')
    return path, False, path.name

def extract(source, target, source_name):
    target.mkdir(parents=True)
    if source.is_dir():
        for root, dirs, files in os.walk(source, followlinks=False):
            for item in dirs + files:
                entry = Path(root) / item
                if entry.is_symlink() or not (entry.is_dir() or entry.is_file()):
                    fail(f'Unsupported source entry: {entry}')
        shutil.copytree(source, target, dirs_exist_ok=True)
        return
    if source.suffix.lower() in ('.img', '.ima'):
        extract_floppy(source, target)
        return
    if source.suffix.lower() in ('.iso', '.lha', '.lzh', '.adf', '.7z', '.tar', '.gz'):
        fail(f'Unsupported archive or image format: {source}')
    if source.suffix.lower() != '.zip':
        checked_path(source_name)
        shutil.copyfile(source, target / source_name)
        return
    try:
        with zipfile.ZipFile(source) as archive:
            for item in archive.infolist():
                parts = checked_path(item.filename, root_allowed=False)
                mode = item.external_attr >> 16
                if stat.S_ISLNK(mode) or (stat.S_IFMT(mode) and not (stat.S_ISREG(mode) or stat.S_ISDIR(mode))):
                    fail(f'Unsupported ZIP member: {item.filename}')
                dest = target.joinpath(*parts)
                if item.is_dir():
                    dest.mkdir(parents=True, exist_ok=True)
                else:
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    with archive.open(item) as src, open(dest, 'wb') as dst:
                        shutil.copyfileobj(src, dst)
    except (OSError, zipfile.BadZipFile, RuntimeError) as exc:
        fail(f'Cannot extract {source}: {exc}')

def extract_floppy(source, target):
    """Validate a superfloppy FAT12 BPB, then copy every entry including hidden files."""
    if '@@' in str(source):
        fail('Floppy image path cannot contain @@')
    with source.open('rb') as stream:
        boot = stream.read(512)
    if len(boot) != 512 or boot[510:] != b'\x55\xaa':
        fail(f'Invalid FAT12 floppy boot sector: {source}')
    bps = struct.unpack_from('<H', boot, 11)[0]
    spc = boot[13]
    reserved = struct.unpack_from('<H', boot, 14)[0]
    fats = boot[16]
    roots = struct.unpack_from('<H', boot, 17)[0]
    sectors = struct.unpack_from('<H', boot, 19)[0] or struct.unpack_from('<I', boot, 32)[0]
    fat_size = struct.unpack_from('<H', boot, 22)[0]
    hidden = struct.unpack_from('<I', boot, 28)[0]
    if (bps != 512 or spc not in (1, 2, 4, 8, 16, 32, 64) or not reserved
            or fats not in (1, 2) or not roots or not fat_size or hidden
            or sectors * bps != source.stat().st_size):
        fail(f'Invalid or unsupported FAT12 floppy metadata: {source}')
    data = sectors - reserved - fats * fat_size - (roots * 32 + bps - 1) // bps
    clusters = data // spc
    if data <= 0 or not 1 <= clusters < 4085 or (fat_size * bps * 2 // 3) < clusters + 2:
        fail(f'Image is not a valid FAT12 filesystem: {source}')
    try:
        listing = subprocess.run(['mdir', '-a', '-b', '-/', '-i', str(source), '::/'],
                                 check=True, text=True, capture_output=True).stdout
        for entry in listing.splitlines():
            if not entry.startswith('::/'):
                fail(f'Unexpected mdir output: {entry!r}')
            parts = checked_path(entry[3:], fat=True)
            dest = target.joinpath(*parts)
            if entry.endswith('/'):
                dest.mkdir(parents=True, exist_ok=True)
            else:
                dest.parent.mkdir(parents=True, exist_ok=True)
                subprocess.run(['mcopy', '-i', str(source), entry, str(dest)],
                               check=True, capture_output=True)
    except (OSError, subprocess.CalledProcessError) as exc:
        fail(f'Cannot read FAT12 floppy image {source}: {exc}')

def resolve_stage(root, parts, is_dir):
    node = root
    for index, part in enumerate(parts):
        node.mkdir(parents=True, exist_ok=True)
        found = [p for p in node.iterdir() if p.name.casefold() == part.casefold()]
        if len(found) > 1: fail(f'Ambiguous staged path: {part}')
        node = found[0] if found else node / part
        if index < len(parts)-1 or is_dir:
            if node.exists() and not node.is_dir(): fail(f'File blocks staged directory: {node}')
    return node

def stage_source(src, root, parts, mspack):
    if src.is_symlink(): fail(f'Symbolic link is not supported: {src}')
    if src.is_dir():
        dest = resolve_stage(root, parts, True)
        dest.mkdir(parents=True, exist_ok=True)
        decompressed = 0
        for child in sorted(src.iterdir(), key=lambda p: p.name.casefold()):
            checked_path(child.name, fat=True)
            decompressed += stage_source(child, root, parts + (child.name,), mspack)
        return decompressed
    elif src.is_file():
        if not parts: fail('A file cannot target a volume root')
        dest = resolve_stage(root, parts, False)
        if dest.is_dir(): fail(f'Directory blocks staged file: {dest}')
        with src.open('rb') as stream:
            signature = stream.read(8)
        if signature.startswith(b'KWAJ'):
            if dest.name.endswith('_'):
                fail(f'KWAJ does not reliably encode the original filename; map {src.name} to its expanded filename in the .desc')
            fd, tmp = tempfile.mkstemp(prefix='.expand-', dir=dest.parent)
            os.close(fd)
            try:
                decompress_kwaj(mspack, src, Path(tmp))
                os.replace(tmp, dest)
            finally:
                with contextlib.suppress(FileNotFoundError): os.unlink(tmp)
            return 1
        elif signature.startswith(b'SZDD'):
            fail(f'Unsupported Microsoft SZDD compressed file: {src}')
        else:
            shutil.copyfile(src, dest)
            return 0
    else:
        fail(f'Described source does not exist: {src}')

def get_tasks(args):
    if args.install:
        if args.list or args.packages: fail('--install cannot be combined with --package-list or --packages-path')
        tasks=[]
        for name in args.install.split(','):
            if not re.fullmatch(r'[A-Za-z0-9_.-]+', name) or name in ('.', '..'): fail('Invalid package name')
            tasks.append((Path(args.base) / (name + '.list'), Path(args.base) / name))
        return tasks
    if not args.list or not args.packages: fail('Use --install or both --package-list and --packages-path')
    return [(Path(args.list), Path(args.packages))]

def generate(args):
    cache = Path(args.cache)
    entries = list(package_entries(args.list))
    if not entries: fail('Package list is empty')
    output = Path(args.packages)
    output.mkdir(parents=True, exist_ok=True)
    downloaded=[]
    with tempfile.TemporaryDirectory(prefix='dos-package-generate-') as work:
        for index, (name, source) in enumerate(entries):
            archive, remote, source_name = acquire(source, cache)
            if remote: downloaded.append(archive)
            folder = Path(work) / str(index)
            extract(archive, folder, source_name)
            files = sorted(p for p in folder.rglob('*') if p.is_file())
            rows=[]
            for path in files:
                rel = path.relative_to(folder).as_posix()
                checked_path(rel, fat=True)
                # POSIX shell quoting is accepted by shlex and remains editable.
                rows.append(f'{shlex.quote(rel)} {shlex.quote("C:" + name + "/" + rel)}\n')
            (output / (name + '.desc')).write_text(''.join(rows), encoding='utf-8')
            print(f'Generated {output / (name + ".desc")}')
    if args.clean:
        for archive in downloaded:
            with contextlib.suppress(FileNotFoundError): archive.unlink()

def partition_config(path):
    values=[]; active=0
    try: text = Path(path).read_text(encoding='utf-8')
    except OSError as exc: fail(f'Cannot read partition configuration: {exc}')
    for line in text.splitlines():
        line=line.split('#',1)[0].strip()
        if not line: continue
        m=re.fullmatch(r'([C-F]):([A-Za-z0-9]*)(?::([0-9]*[kKmMgG]?)(?::(boot))?)?',line)
        if not m or m.group(1) != chr(67+len(values)) or m.group(2).lower() not in ('','fat16'):
            fail(f'Invalid partition configuration: {line}')
        if values and values[-1] is None: fail('Only the last partition can use remaining space')
        size=m.group(3) or ''
        if size:
            size_match=re.fullmatch(r'([0-9]+)([kKmMgG])',size)
            if not size_match: fail(f'Invalid partition size: {size}')
            size=int(size_match.group(1))*{'k':2,'m':2048,'g':2097152}[size_match.group(2).lower()]
            if not 0 < size <= 4194304: fail('Partition size is out of range')
        else: size=None
        values.append(size)
        active += bool(m.group(4))
    if not 1 <= len(values) <= 4 or active > 1: fail('Invalid partition configuration')
    return values

def checked_partitions(disk, config):
    sizes=partition_config(config)
    mode=os.stat(disk).st_mode
    if not (stat.S_ISREG(mode) or stat.S_ISBLK(mode)): fail('Disk must be a regular image or whole block device')
    if stat.S_ISREG(mode):
        result=run(['losetup','-j',str(disk)],stdout=subprocess.PIPE,text=True)
        if result.stdout.strip(): fail('Image is attached to a loop device')
        disk_bytes=os.stat(disk).st_size
    else:
        kind=run(['lsblk','-dn','-o','TYPE',str(disk)],stdout=subprocess.PIPE,text=True).stdout.strip()
        if kind != 'disk': fail('Target must be a whole disk')
        devices=run(['lsblk','-nrpo','NAME',str(disk)],stdout=subprocess.PIPE,text=True).stdout.splitlines()
        swaps=run(['swapon','--show','--noheadings','--raw','--output','NAME'],stdout=subprocess.PIPE,text=True).stdout.splitlines()
        swaps={os.path.realpath(x) for x in swaps}
        for dev in devices:
            mounts=run(['lsblk','-dn','-o','MOUNTPOINTS',dev],stdout=subprocess.PIPE,text=True).stdout.strip()
            if mounts or os.path.realpath(dev) in swaps or any((Path('/sys/class/block') / Path(dev).name / 'holders').glob('*')):
                fail(f'Device is mounted or busy: {dev}')
        if int(run(['blockdev','--getss',str(disk)],stdout=subprocess.PIPE,text=True).stdout) != 512: fail('Only 512-byte logical sectors are supported')
        disk_bytes=int(run(['blockdev','--getsize64',str(disk)],stdout=subprocess.PIPE,text=True).stdout)
    if disk_bytes % 512: fail('Disk is not sector-aligned')
    result={}
    with open(disk,'rb',buffering=0) as stream:
        mbr=stream.read(512)
        if len(mbr)!=512 or mbr[510:]!=b'\x55\xaa': fail('Invalid MBR')
        entries=[mbr[446+16*i:462+16*i] for i in range(4)]
        if sum(e[4]!=0 for e in entries)!=len(sizes): fail('Partition count differs from configuration')
        next_sector=2048
        for i, configured in enumerate(sizes):
            entry=entries[i]; start,length=struct.unpack_from('<II',entry,8)
            expected_start=((next_sector+2047)//2048)*2048
            expected_size=configured if configured is not None else ((disk_bytes//512-expected_start)//2)*2
            if entry[4] not in (4,6) or start!=expected_start or length!=expected_size or start+length>disk_bytes//512:
                fail(f'Partition {chr(67+i)} does not match configuration or is not FAT16')
            stream.seek(start*512); boot=stream.read(512)
            if len(boot)!=512 or boot[510:]!=b'\x55\xaa': fail('Invalid FAT boot sector')
            bps=struct.unpack_from('<H',boot,11)[0]; spc=boot[13]; reserved=struct.unpack_from('<H',boot,14)[0]
            fats=boot[16]; roots=struct.unpack_from('<H',boot,17)[0]; fatsize=struct.unpack_from('<H',boot,22)[0]
            hidden=struct.unpack_from('<I',boot,28)[0]; total=struct.unpack_from('<H',boot,19)[0] or struct.unpack_from('<I',boot,32)[0]
            if bps!=512 or spc not in (1,2,4,8,16,32,64) or not reserved or not fats or not roots or not fatsize or hidden!=start or total!=length:
                fail(f'Invalid FAT16 metadata on {chr(67+i)}')
            clusters=(length-reserved-fats*fatsize-(roots*32+511)//512)//spc
            if not 4085<=clusters<=65524 or fatsize*512//2 < clusters+2: fail('Invalid FAT16 cluster count or FAT size')
            result[chr(67+i)]=f'{disk}@@{start*512}'
            next_sector=start+length
    return result

def validate_staging(root, partitions):
    for volume in root.iterdir():
        if volume.is_symlink() or not volume.is_dir() or volume.name not in partitions:
            fail(f'Invalid staging volume: {volume.name}')
        for parent, dirs, files in os.walk(volume, followlinks=False):
            for name in dirs+files:
                path=Path(parent)/name
                checked_path(path.relative_to(volume).as_posix(), fat=True)
                if path.is_symlink() or not (path.is_dir() or path.is_file()): fail(f'Invalid staged entry: {path}')

def transfer(root, partitions, exclude_system_files=False):
    for volume in sorted(root.iterdir()):
        image=partitions[volume.name]
        # Check that mtools can read every destination before transferring anything.
        run(['mdir','-i',image,'::/'],stdout=subprocess.DEVNULL,stderr=subprocess.PIPE)

    def check_name(image, rel, name):
        # FAT short aliases can refer to a different long name. Never let -o
        # overwrite an unrelated existing file through such an alias.
        parent='::/' if str(rel)=='.' else '::/'+rel.as_posix()+'/'
        listing=run(['mdir','-b','-i',image,parent],stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True).stdout
        names=[line.rstrip('/').rsplit('/',1)[-1] for line in listing.splitlines()]
        if any(existing.casefold()==name.casefold() for existing in names):
            return True
        target=parent+name
        found=subprocess.run(['mdir','-b','-i',image,target],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        if found.returncode==0:
            fail(f'Destination matches an existing FAT short-name alias: {target}')
        return False

    for volume in sorted(root.iterdir()):
        image=partitions[volume.name]
        for parent, dirs, files in os.walk(volume):
            dirs.sort(); files.sort()
            rel=Path(parent).relative_to(volume)
            for name in dirs:
                dest='::/' + (rel/name).as_posix()
                if not check_name(image,rel,name): run(['mmd','-i',image,dest])
            for name in files:
                if exclude_system_files and volume.name == 'C' and str(rel) == '.' and name.upper() in dos622_boot.NAMES:
                    continue
                dest='::/' + (rel/name).as_posix()
                check_name(image,rel,name)
                run(['mcopy','-o','-i',image,str(Path(parent)/name),dest])

def install(args):
    mspack=load_mspack()
    tasks=get_tasks(args)
    plan=[]
    dos_tasks=0
    for listing, descriptions in tasks:
        is_dos=listing.name.lower() == 'dos622.list'
        dos_tasks += is_dos
        for name, source in package_entries(listing):
            desc=descriptions/(name+'.desc')
            mappings=list(desc_entries(desc))
            plan.append((name,source,mappings,is_dos))
    if not plan: fail('No package components selected')
    if dos_tasks > 1: fail('dos622 can only be selected once')
    disk=Path(args.disk).resolve(strict=True)
    if '@@' in str(disk): fail('Disk path cannot contain @@')
    if not os.access(disk, os.W_OK): fail(f'Disk is not writable: {disk}')
    info(f'Target: {disk}')
    info('Starting package installation')
    cache=Path(args.cache)
    downloaded=[]; originals={}; disk1=None
    with tempfile.TemporaryDirectory(prefix='dos-package-install-') as work:
        work=Path(work); stage=work/'staging'; stage.mkdir()
        for index,(name,source,mappings,is_dos) in enumerate(plan):
            archive, remote, source_name=acquire(source,cache,label=name)
            if remote: downloaded.append(archive)
            info(f'Extracting component {name}')
            extracted=work/str(index); extract(archive,extracted,source_name)
            success(f'Extracted component {name}')
            if is_dos and name == 'Dos622-Disk1':
                if disk1 is not None: fail('Dos622-Disk1 must occur exactly once')
                disk1=archive
                for system_name in dos622_boot.NAMES:
                    system_file=extracted/system_name
                    if not system_file.is_file(): fail(f'Missing original system file: {system_name}')
                    originals[system_name]=hashlib.sha256(system_file.read_bytes()).digest()
            info(f'Staging component {name}')
            decompressed=0
            for source_parts, volume, destination in mappings:
                src=extracted.joinpath(*source_parts)
                decompressed += stage_source(src,stage/volume,destination,mspack)
            if decompressed: success(f'Decompressed {decompressed} KWAJ file(s) for {name}')
            success(f'Staged component {name}')
        if args.post:
            info('Running staging post-script')
            post=Path(args.post).resolve(strict=True)
            env=dict(os.environ, STAGING_ROOT=str(stage))
            # The existing post-script is intentionally sourced as user-provided shell code.
            run(['bash','-c','set -e; source "$1"; source "$2"','post-script',args.functions,str(post)],cwd=stage,env=env)
            success('Staging post-script completed')
        if dos_tasks:
            expected=dos622_boot.staged_files(stage,originals)
            if disk1 is None: fail('Native MS-DOS requires the Dos622-Disk1 floppy image')
            info('Validating native MS-DOS 6.22 floppy boot code')
            boot_record=dos622_boot.native_boot_record(disk1,originals)
            success('Native MS-DOS 6.22 floppy boot code verified')
        with open(disk,'rb') as lock:
            try: fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
            except OSError: fail('Disk is locked by another process')
            partitions=checked_partitions(disk,args.config)
            validate_staging(stage,partitions)
            try:
                if dos_tasks:
                    if 'C' not in partitions: fail('DOS installation requires a C: partition')
                    image=partitions['C']; start=int(image.rsplit('@@',1)[1])
                    info('Installing and verifying DOS system files')
                    dos622_boot.install_system_files(disk,image,start,expected,stage)
                    success('DOS system files verified')
                info('Transferring staged files to FAT16 partitions')
                transfer(stage,partitions,exclude_system_files=bool(dos_tasks))
                success('FAT16 file transfer completed')
                if dos_tasks:
                    info('Installing Native MS-DOS 6.22 VBR')
                    dos622_boot.install_vbr(disk,start,boot_record,expected)
                    success('Native MS-DOS 6.22 VBR installed and verified')
            except (PackageError, dos622_boot.BootError, OSError) as exc:
                fail(f'Transfer failed; the target may be partially updated: {exc}')
        if args.clean:
            for archive in downloaded:
                with contextlib.suppress(FileNotFoundError): archive.unlink()
    for listing, _ in tasks:
        success(f'Package {listing.stem} installed')
    success('Package installation completed')

def main():
    parser=argparse.ArgumentParser()
    sub=parser.add_subparsers(dest='action',required=True)
    g=sub.add_parser('generate'); g.add_argument('--list',required=True); g.add_argument('--packages',required=True); g.add_argument('--cache',required=True); g.add_argument('--clean',action='store_true')
    p=sub.add_parser('install'); p.add_argument('--disk',required=True); p.add_argument('--config',required=True); p.add_argument('--base',required=True); p.add_argument('--cache',required=True); p.add_argument('--functions',required=True); p.add_argument('--install'); p.add_argument('--list'); p.add_argument('--packages'); p.add_argument('--post'); p.add_argument('--clean',action='store_true')
    args=parser.parse_args()
    try:
        (generate if args.action=='generate' else install)(args)
    except (PackageError, dos622_boot.BootError, OSError, ValueError) as exc:
        prefix = '\x1b[31m[ERROR]\x1b[0m' if sys.stderr.isatty() and os.environ.get('TERM') != 'dumb' and 'NO_COLOR' not in os.environ else '[ERROR]'
        print(f'{prefix} {exc}',file=sys.stderr,flush=True); return 1
    return 0

if __name__=='__main__': sys.exit(main())
