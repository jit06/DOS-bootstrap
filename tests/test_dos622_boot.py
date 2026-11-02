#!/usr/bin/env python3
"""DOS boot installation tests: disposable images and synthetic system files only."""
import hashlib
import os
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'helpers'))
import dos622_boot

@unittest.skipUnless(all(shutil.which(x) for x in ('mcopy','mdir','mmd','mformat','mattrib','mkfs.fat','sfdisk')), 'mtools and FAT tools required')
class DOSBootTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='dos622-boot-test-')
        self.base = Path(self.temp.name)
        self.bin = self.base/'bin'; self.bin.mkdir()
        fake=self.bin/'ms-sys'; fake.write_text('#!/bin/sh\nexit 0\n'); fake.chmod(0o755)
        self.env=dict(os.environ, PATH=str(self.bin)+':'+os.environ['PATH'], PYTHONDONTWRITEBYTECODE='1')
        self.disk=self.base/'disk.img'
        with self.disk.open('wb') as f: f.truncate(33*1024*1024)
        self.config=self.base/'partitions.config'; self.config.write_text('C:\n')
        self.invoke([str(ROOT/'setup_disk.sh'),'-d',str(self.disk),'-c',str(self.config)])
        self.start=2048*512; self.image=str(self.disk)+'@@'+str(self.start)
        self.packages=self.base/'packages'; (self.packages/'dos622').mkdir(parents=True)
        self.source=self.base/'disk1'; self.source.mkdir()
        self.files={'IO.SYS':bytes(range(256))*32,'MSDOS.SYS':b'kernel'*450,'COMMAND.COM':b'command'*400}
        for name, data in self.files.items(): (self.source/name).write_bytes(data)
        self.floppy=self.base/'disk1.img'; self.use_real=False
        (self.packages/'dos622.list').write_text(f'Dos622-Disk1 {self.floppy}\n')
        (self.packages/'dos622'/'Dos622-Disk1.desc').write_text(''.join(f'"{n}" "C:{n}"\n' for n in self.files))
        self.binary=bytearray(512); self.binary[:3]=b'\xeb\x3c\x90'; self.binary[62:70]=b'BOOTCODE'; self.binary[510:]=b'\x55\xaa'

    def tearDown(self): self.temp.cleanup()

    def make_floppy(self):
        with self.floppy.open('wb') as f: f.truncate(1474560)
        self.invoke(['mformat','-i',str(self.floppy),'-f','1440','::'])
        for name in (*dos622_boot.NAMES, *(p.name for p in self.source.iterdir() if p.name not in dos622_boot.NAMES)):
            self.invoke(['mcopy','-i',str(self.floppy),str(self.source/name),'::/'+name])
        with self.floppy.open('r+b') as f:
            boot=bytearray(f.read(512)); boot[:3]=self.binary[:3]; boot[3:11]=b'MSDOS5.0'; boot[62:510]=self.binary[62:510]
            f.seek(0); f.write(boot)

    def invoke(self, argv, success=True, env=None):
        result=subprocess.run(argv,env=env or self.env,text=True,capture_output=True)
        self.assertEqual(result.returncode==0,success,result.stdout+result.stderr)
        return result

    def install(self, items='dos622', success=True, extra=()):
        argv=[sys.executable,str(ROOT/'helpers'/'package_common.py')]
        if not self.use_real:
            self.make_floppy()
            codehash=hashlib.sha256(self.binary[62:510]).hexdigest()
            filehash={n:hashlib.sha256(v).hexdigest() for n,v in self.files.items()}
            wrapper=('import sys; sys.path.insert(0,sys.argv.pop(1)); import dos622_boot as b; '
                     f'b.NATIVE_CODE_SHA256={codehash!r}; b.NATIVE_FILE_SHA256={filehash!r}; '
                     'import package_common; sys.exit(package_common.main())')
            argv=[sys.executable,'-c',wrapper,str(ROOT/'helpers')]
        argv+=['install','--disk',str(self.disk),'--config',str(self.config),'--base',str(self.packages),'--cache',str(self.base/'cache'),'--functions',str(ROOT/'functions.sh'),'--install',items,*extra]
        return self.invoke(argv,success)

    def mcopy(self, source, destination):
        self.invoke(['mcopy','-o','-i',self.image,str(source),str(destination)])

    def sector(self):
        with self.disk.open('rb') as f: f.seek(self.start); return f.read(512)

    def digest(self):
        h=hashlib.sha256()
        with self.disk.open('rb') as f:
            for chunk in iter(lambda:f.read(1024*1024),b''): h.update(chunk)
        return h.digest()

    def test_boot_files_attributes_vbr_preservation_and_reinstall(self):
        old=self.sector()
        with self.disk.open('rb') as f: mbr=f.read(512)
        self.install()
        new=self.sector()
        self.assertEqual(new[:3],b'\xeb\x3c\x90')
        self.assertEqual(new[3:0x3e],old[3:0x3e])
        self.assertEqual(new[0x3e:0x1fe],self.binary[0x3e:0x1fe])
        with self.disk.open('rb') as f: self.assertEqual(f.read(512),mbr)
        attrs=self.invoke(['mattrib','-i',self.image,'::/IO.SYS','::/MSDOS.SYS','::/COMMAND.COM']).stdout
        self.assertEqual(attrs.count('SHR'),2)
        keep=self.base/'keep'; keep.write_text('untouched'); self.mcopy(keep,'::/KEEP.TXT')
        keepout=self.base/'keepout'; self.mcopy('::/KEEP.TXT',keepout); self.assertEqual(keepout.read_text(),'untouched')
        fat=dos622_boot.FAT16(self.disk,self.start); fat.verify(self.files)
        entry=fat.entries[b'IO      SYS']; clusters,_=fat.chain(entry)
        self.assertEqual(clusters[:3],list(range(clusters[0],clusters[0]+3)))
        self.install()
        self.assertEqual(self.sector(),new)

    def test_unsafe_existing_system_file_does_not_modify_disk(self):
        old=self.base/'existing'; old.write_text('unrelated existing io'); self.mcopy(old,'::/IO.SYS')
        before=self.digest(); result=self.install(success=False)
        self.assertIn('incomplete',result.stderr); self.assertEqual(before,self.digest())

    def test_inactive_c_and_bad_bios_drive_rejected_before_writes(self):
        for offset, byte in ((446,b'\x00'),(self.start+0x24,b'\x81')):
            with self.subTest(offset=offset):
                with self.disk.open('r+b') as stream:
                    stream.seek(offset); original=stream.read(1); stream.seek(offset); stream.write(byte)
                before=self.digest(); self.install(success=False); self.assertEqual(before,self.digest())
                with self.disk.open('r+b') as stream: stream.seek(offset); stream.write(original)

    def test_fragmented_io_reinstall_fails_without_new_vbr(self):
        self.install()
        fat=dos622_boot.FAT16(self.disk,self.start)
        chain,_=fat.chain(fat.entries[b'IO      SYS'])
        self.assertEqual(fat.spc,1); self.assertGreaterEqual(len(chain),4)
        # Swap second and third clusters in the chain, keeping its length valid.
        with self.disk.open('r+b',buffering=0) as stream:
            for index in range(fat.fats):
                offset=self.start+(fat.reserved+index*fat.fat_size)*512
                for cluster,nxt in ((chain[0],chain[2]),(chain[2],chain[1]),(chain[1],chain[3])):
                    stream.seek(offset+cluster*2); stream.write(struct.pack('<H',nxt))
        before=self.digest(); result=self.install(success=False)
        self.assertIn('not contiguous',result.stderr); self.assertEqual(before,self.digest())

    def test_transfer_failure_leaves_original_vbr(self):
        (self.source/'EXTRA.TXT').write_text('ordinary file')
        with (self.packages/'dos622'/'Dos622-Disk1.desc').open('a') as stream:
            stream.write('EXTRA.TXT C:EXTRA.TXT\n')
        fake=self.bin/'mcopy'; real=shutil.which('mcopy')
        fake.write_text('#!/bin/sh\ncase "$*" in *"$MCOPY_TARGET"*EXTRA.TXT*) exit 1;; esac\nexec "$MCOPY_REAL" "$@"\n')
        fake.chmod(0o755); self.env.update(MCOPY_REAL=real,MCOPY_TARGET=str(self.disk))
        old=self.sector(); result=self.install(success=False)
        self.assertIn('partially updated',result.stderr)
        self.assertNotIn('[ OK ] Native MS-DOS 6.22 VBR installed',result.stdout)
        self.assertNotIn('[ OK ] Package dos622 installed',result.stdout)
        self.assertEqual(old,self.sector())

    def test_failed_vbr_write_restores_original(self):
        self.install()
        old=self.sector(); modified=bytearray(self.binary); modified[0x70] ^= 1
        actual_fsync=os.fsync; calls=[]
        def fail_once(fd):
            calls.append(fd)
            if len(calls)==1: raise OSError('simulated write failure')
            return actual_fsync(fd)
        with mock.patch.object(dos622_boot.os,'fsync',side_effect=fail_once):
            with self.assertRaisesRegex(dos622_boot.BootError,'original VBR was restored'):
                dos622_boot.install_vbr(self.disk,self.start,modified,self.files)
        self.assertEqual(old,self.sector())

    def test_missing_or_modified_staged_system_files_fail_before_writes(self):
        for action in ('rm "$STAGING_ROOT/C/IO.SYS"','printf bad > "$STAGING_ROOT/C/COMMAND.COM"'):
            with self.subTest(action=action):
                post=self.base/'post.sh'; post.write_text(action+'\n')
                before=self.digest(); result=self.install(success=False,extra=('--post',str(post)))
                self.assertIn('system file' if action.startswith('rm') else 'original disk 1',result.stderr)
                self.assertEqual(before,self.digest())

    def test_benchmark_remains_normal_package(self):
        (self.packages/'benchmark').mkdir()
        source=self.base/'benchmark'; source.mkdir(); (source/'BENCH.EXE').write_bytes(b'bench')
        (self.packages/'benchmark.list').write_text(f'benchmark {source}\n')
        (self.packages/'benchmark'/'benchmark.desc').write_text('BENCH.EXE C:BENCH/BENCH.EXE\n')
        self.install('dos622,benchmark')
        output=self.base/'readbench'; self.mcopy('::/BENCH/BENCH.EXE',output)
        self.assertEqual(output.read_bytes(),b'bench')

    def native_media(self):
        image=next((path for path in (ROOT/'media'/'dos-622-disk1.img', ROOT/'dos-622-disk1.img') if path.is_file()),None)
        if image is None: self.skipTest('user-supplied MS-DOS 6.22 disk 1 image unavailable')
        self.files={}
        for name in dos622_boot.NAMES:
            result=subprocess.run(['mcopy','-i',str(image),'::/'+name,'-'],env=self.env,capture_output=True,check=True)
            self.files[name]=result.stdout
        self.use_real=True
        (self.packages/'dos622.list').write_text(f'Dos622-Disk1 {image}\n')
        return image

    def test_native_install_preserves_bpb_and_reinstalls(self):
        image=self.native_media(); boot=image.read_bytes()[:512]
        old=self.sector(); result=self.install()
        self.assertIn('Native MS-DOS 6.22 VBR installed and verified',result.stdout)
        new=self.sector()
        self.assertEqual(new[:3],boot[:3]); self.assertEqual(new[3:62],old[3:62])
        self.assertEqual(new[62:510],boot[62:510]); self.assertEqual(new[510:],b'\x55\xaa')
        fat=dos622_boot.FAT16(self.disk,self.start); fat.verify(self.files)
        self.assertEqual(fat.root[:11],dos622_boot.SHORT[0]); self.assertEqual(fat.root[32:43],dos622_boot.SHORT[1])
        keep=self.base/'keep'; keep.write_text('unrelated'); self.mcopy(keep,'::/KEEP.TXT')
        self.install(); self.assertEqual(self.sector(),new)
        out=self.base/'keepout'; self.mcopy('::/KEEP.TXT',out); self.assertEqual(out.read_text(),'unrelated')

    def test_downloaded_and_local_floppy_use_same_native_path(self):
        image=self.native_media()
        fake=self.bin/'wget'; fake.write_text('#!/bin/sh\nprintf called >> "$WGET_LOG"\ncp "$WGET_SOURCE" "$3"\n'); fake.chmod(0o755)
        self.env.update(WGET_SOURCE=str(image),WGET_LOG=str(self.base/'wget.log'))
        (self.packages/'dos622.list').write_text('Dos622-Disk1 https://example.invalid/dos-622-disk1.img\n')
        result=self.install()
        self.assertIn('Downloaded source for Dos622-Disk1',result.stdout)
        self.assertIn('Native MS-DOS 6.22 VBR installed and verified',result.stdout)
        vbr=self.sector(); fat=dos622_boot.FAT16(self.disk,self.start); fat.verify(self.files)
        result=self.install()
        self.assertIn('Using cached source for Dos622-Disk1',result.stdout)
        self.assertEqual((self.base/'wget.log').read_text(),'called')
        (self.packages/'dos622.list').write_text(f'Dos622-Disk1 {image}\n')
        self.install()
        self.assertEqual(self.sector(),vbr)
        dos622_boot.FAT16(self.disk,self.start).verify(self.files)

    def test_native_rejects_occupied_root_without_writes(self):
        self.native_media(); keep=self.base/'keep'; keep.write_text('unrelated'); self.mcopy(keep,'::/KEEP.TXT')
        before=self.digest(); result=self.install(success=False)
        self.assertIn('first two root-directory entries',result.stderr); self.assertEqual(before,self.digest())

    def test_native_rejects_misordered_existing_files(self):
        self.native_media(); self.install()
        fat=dos622_boot.FAT16(self.disk,self.start)
        with self.disk.open('r+b') as stream:
            stream.seek(self.start+fat.root_sector*512); first=stream.read(64)
            stream.seek(self.start+fat.root_sector*512); stream.write(first[32:]+first[:32])
        before=self.digest(); result=self.install(success=False)
        self.assertIn('first two root-directory entries',result.stderr)
        self.assertEqual(before,self.digest())

    def test_native_source_validation(self):
        image=self.native_media()
        originals={n:hashlib.sha256(v).digest() for n,v in self.files.items()}
        good=dos622_boot.native_boot_record(image,originals)
        self.assertEqual(hashlib.sha256(good[62:510]).hexdigest(),dos622_boot.NATIVE_CODE_SHA256)
        for offset in (0,3,11,21,38,54,62,510,512,19*512):
            with self.subTest(offset=offset):
                data=bytearray(image.read_bytes()); data[offset]^=1
                bad=self.base/'bad.img'; bad.write_bytes(data)
                with self.assertRaises(dos622_boot.BootError): dos622_boot.native_boot_record(bad,originals)
        with self.assertRaises(dos622_boot.BootError): dos622_boot.native_boot_record(image,{})

    def test_native_bad_source_does_not_modify_target(self):
        image=self.native_media(); bad=self.base/'bad.img'
        data=bytearray(image.read_bytes()); data[70]^=1; bad.write_bytes(data)
        (self.packages/'dos622.list').write_text(f'Dos622-Disk1 {bad}\n')
        before=self.digest(); result=self.install(success=False)
        self.assertIn('not the supported original MS-DOS 6.22',result.stderr)
        self.assertEqual(before,self.digest())

    def test_native_vbr_write_failure_rolls_back(self):
        image=self.native_media(); self.install()
        original=self.sector(); boot=bytearray(image.read_bytes()[:512]); boot[70]^=1
        real_fsync=os.fsync; calls=[]
        def fail_once(fd):
            calls.append(fd)
            if len(calls)==1: raise OSError('simulated write failure')
            return real_fsync(fd)
        with mock.patch.object(dos622_boot.os,'fsync',side_effect=fail_once):
            with self.assertRaisesRegex(dos622_boot.BootError,'original VBR was restored'):
                dos622_boot.install_vbr(self.disk,self.start,boot,self.files)
        self.assertEqual(self.sector(),original)

    def test_native_rejects_corrupted_system_file(self):
        self.native_media(); self.install()
        fat=dos622_boot.FAT16(self.disk,self.start)
        cluster=struct.unpack_from('<H',fat.entries[dos622_boot.SHORT[0]],26)[0]
        offset=self.start+(fat.data_sector+(cluster-2)*fat.spc)*512
        with self.disk.open('r+b') as stream:
            stream.seek(offset); byte=stream.read(1); stream.seek(offset); stream.write(bytes([byte[0]^1]))
        before=self.digest(); result=self.install(success=False)
        self.assertIn('contents differ',result.stderr); self.assertEqual(before,self.digest())

    def test_native_rejects_fragmented_first_three_sectors(self):
        self.native_media(); self.install()
        fat=dos622_boot.FAT16(self.disk,self.start)
        chain,_=fat.chain(fat.entries[dos622_boot.SHORT[0]])
        self.assertEqual(fat.spc,1); self.assertGreaterEqual(len(chain),4)
        with self.disk.open('r+b',buffering=0) as stream:
            for index in range(fat.fats):
                offset=self.start+(fat.reserved+index*fat.fat_size)*512
                for cluster,nxt in ((chain[0],chain[2]),(chain[2],chain[1]),(chain[1],chain[3])):
                    stream.seek(offset+cluster*2); stream.write(struct.pack('<H',nxt))
        before=self.digest(); result=self.install(success=False)
        self.assertIn('not contiguous',result.stderr); self.assertEqual(before,self.digest())

if __name__ == '__main__': unittest.main()
