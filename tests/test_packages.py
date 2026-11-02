#!/usr/bin/env python3
"""Package tests use temporary disk images and real mtools; no physical devices."""
import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import struct
import sys
import tempfile
import unittest
from unittest import mock
import zipfile

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'helpers'))
import package_common

@unittest.skipUnless(all(shutil.which(c) for c in ('mcopy','mdir','mmd','mformat','mattrib','mkfs.fat','sfdisk')), 'mtools and FAT tools required')
class PackageTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(prefix='dos-package-test-')
        self.base=Path(self.tmp.name)
        bindir=self.base/'bin'; bindir.mkdir()
        stub=bindir/'ms-sys'; stub.write_text('#!/bin/sh\nexit 0\n'); stub.chmod(0o755)
        self.env=dict(os.environ, PATH=str(bindir)+':'+os.environ['PATH'], TMP_DOWNLOAD_DIR=str(self.base/'download cache'))
        self.disk=self.base/'disk.img'
        with self.disk.open('wb') as stream: stream.truncate(128*1024*1024)
        self.config=self.base/'partitions.config'; self.config.write_text('C:fat16:32M\nD:\n')
        self.call('setup_disk.sh','-d',str(self.disk),'-c',str(self.config))
        self.desc=self.base/'descs'; self.desc.mkdir()
        self.list=self.base/'packages.list'
        self.src=self.base/'source'; self.src.mkdir()
        (self.src/'ONE.TXT').write_text('first')

    def tearDown(self): self.tmp.cleanup()

    def call(self, script, *args, success=True):
        result=subprocess.run([str(ROOT/script),*args],env=self.env,text=True,capture_output=True)
        self.assertEqual(result.returncode==0,success,result.stdout+result.stderr)
        return result

    def install(self, success=True, extra=()):
        return self.call('package.sh','-d',str(self.disk),'-c',str(self.config),'-l',str(self.list),'-p',str(self.desc),*extra,success=success)

    def zip(self, name, entries):
        path=self.base/name
        with zipfile.ZipFile(path,'w') as z:
            for key,value in entries.items(): z.writestr(key,value)
        return path

    def floppy(self, name, entries, hidden=()):
        image=self.base/name
        with image.open('wb') as stream: stream.truncate(1440*1024)
        subprocess.run(['mformat','-i',str(image),'-f','1440','::'],env=self.env,check=True,capture_output=True)
        for filename,contents in entries.items():
            path=self.base/'floppy-input'; path.write_text(contents)
            subprocess.run(['mcopy','-o','-i',str(image),str(path),'::/'+filename],env=self.env,check=True,capture_output=True)
        for filename in hidden:
            subprocess.run(['mattrib','-i',str(image),'+h','+s','::/'+filename],env=self.env,check=True,capture_output=True)
        return image

    def kwaj(self, data):
        # KWAJ method 2: SZDD/LZSS literal groups, with an uncompressed length.
        payload=b''.join(b'\xff'+data[i:i+8] for i in range(0,len(data),8))
        return b'KWAJ\x88\xf0\x27\xd1'+struct.pack('<HHHI',2,18,1,len(data))+payload

    def mcopy(self, volume, source, destination, success=True):
        offset={'C':2048*512,'D':67584*512}[volume]
        result=subprocess.run(['mcopy','-o','-i',str(self.disk)+'@@'+str(offset),str(source),str(destination)],env=self.env,text=True,capture_output=True)
        self.assertEqual(result.returncode==0,success,result.stdout+result.stderr)
        return result

    def read(self, volume, path):
        out=self.base/'readback'
        self.mcopy(volume,'::/'+path,out)
        return out.read_text()

    def digest(self):
        h=hashlib.sha256()
        with self.disk.open('rb') as stream:
            for part in iter(lambda:stream.read(4*1024*1024),b''): h.update(part)
        return h.digest()

    def test_physical_device_swap_listing_and_fail_closed(self):
        # Exercise the real block-device branch against a disposable image,
        # with all device-inspection commands mocked.
        child=str(self.disk)+'p1'
        for scenario in ('clear', 'swap', 'mounted', 'query_failure'):
            with self.subTest(scenario=scenario):
                commands=[]
                def fake_run(args, **kwargs):
                    commands.append(args)
                    if args[0]=='swapon':
                        self.assertEqual(args,['swapon','--show','--noheadings','--raw','--output','NAME'])
                        if scenario=='query_failure': raise subprocess.CalledProcessError(255,args)
                        output=child+'\n' if scenario=='swap' else ''
                    elif args[:3]==['lsblk','-dn','-o']:
                        output='disk\n' if args[3]=='TYPE' else ('/mounted\n' if scenario=='mounted' and args[-1]==child else '')
                    elif args[:2]==['lsblk','-nrpo']:
                        output=str(self.disk)+'\n'+child+'\n'
                    elif args[:2]==['blockdev','--getss']:
                        output='512\n'
                    elif args[:2]==['blockdev','--getsize64']:
                        output=str(self.disk.stat().st_size)+'\n'
                    else:
                        self.fail(f'Unexpected command: {args}')
                    return subprocess.CompletedProcess(args,0,stdout=output)
                before=self.digest()
                with mock.patch.object(package_common.stat,'S_ISREG',return_value=False), \
                     mock.patch.object(package_common.stat,'S_ISBLK',return_value=True), \
                     mock.patch.object(package_common.subprocess,'run',side_effect=fake_run):
                    if scenario=='clear':
                        self.assertEqual(set(package_common.checked_partitions(self.disk,self.config)),{'C','D'})
                    else:
                        with self.assertRaisesRegex(package_common.PackageError,
                                'Command failed: swapon' if scenario=='query_failure' else 'mounted or busy'):
                            package_common.checked_partitions(self.disk,self.config)
                self.assertTrue(any(command[0]=='swapon' for command in commands))
                self.assertEqual(before,self.digest())

    def test_generate_zip_and_directory(self):
        archive=self.zip('files.zip',{'sub/My File.txt':'zip'})
        self.list.write_text(f'first {archive}\nsecond {self.src}\n')
        self.call('generate_package.sh','-l',str(self.list),'-p',str(self.desc))
        self.assertIn('C:first/sub/My File.txt',(self.desc/'first.desc').read_text())
        self.assertIn('C:second/ONE.TXT',(self.desc/'second.desc').read_text())
        self.install()
        self.assertEqual(self.read('C','first/sub/My File.txt'),'zip')
        self.assertEqual(self.read('C','second/ONE.TXT'),'first')

    def test_generate_standalone_file(self):
        self.list.write_text(f'one {self.src / "ONE.TXT"}\n')
        self.call('generate_package.sh','-l',str(self.list),'-p',str(self.desc))
        self.install()
        self.assertEqual(self.read('C','one/ONE.TXT'),'first')

    def test_kwaj_decompress_override_and_post(self):
        (self.src/'FIRST.EX_').write_bytes(self.kwaj(b'first'))
        (self.src/'SECOND.SY_').write_bytes(self.kwaj(b'second'))
        (self.src/'MAGIC.DAT').write_bytes(self.kwaj(b'magic'))
        (self.src/'PLAIN.EX_').write_text('plain')
        other=self.zip('later.zip',{'OVERRIDE.BIN':self.kwaj(b'overridden')})
        self.list.write_text(f'first {self.src}\nsecond {other}\n')
        (self.desc/'first.desc').write_text('FIRST.EX_ C:DOS/FILE.EXE\nSECOND.SY_ C:DOS/OTHER.SYS\nMAGIC.DAT C:DOS/MAGIC.BIN\nPLAIN.EX_ C:DOS/PLAIN.EX_\n')
        (self.desc/'second.desc').write_text('OVERRIDE.BIN C:DOS/FILE.EXE\n')
        post=self.base/'post.sh'; post.write_text('test "$(cat "$STAGING_ROOT/C/DOS/OTHER.SYS")" = second\nprintf post >> "$STAGING_ROOT/C/DOS/FILE.EXE"\n')
        result=self.install(extra=('--post-script',str(post)))
        for message in ('[INFO] Target:', '[INFO] Starting package installation',
                        '[INFO] Extracting component first', '[ OK ] Extracted component first',
                        '[INFO] Staging component first', '[ OK ] Decompressed 3 KWAJ file(s) for first',
                        '[ OK ] Decompressed 1 KWAJ file(s) for second', '[ OK ] Staging post-script completed',
                        '[INFO] Transferring staged files', '[ OK ] FAT16 file transfer completed',
                        '[ OK ] Package installation completed'):
            self.assertIn(message, result.stdout)
        self.assertEqual(self.read('C','DOS/FILE.EXE'),'overriddenpost')
        self.assertEqual(self.read('C','DOS/OTHER.SYS'),'second')
        self.assertEqual(self.read('C','DOS/MAGIC.BIN'),'magic')
        self.assertEqual(self.read('C','DOS/PLAIN.EX_'),'plain')
        self.mcopy('C','::/DOS/SECOND.SY_',self.base/'missing',success=False)

    def test_kwaj_corruption_and_ambiguous_name_fail_before_transfer(self):
        for content, destination in ((self.kwaj(b'data')[:-4],'C:FILE.EXE'),
                                     (self.kwaj(b'data'),'C:FILE.EX_'),
                                     (b'SZDD\x88\xf0\x27\x33','C:FILE.EXE')):
            with self.subTest(destination=destination,content=content):
                (self.src/'FILE.EX_').write_bytes(content)
                self.list.write_text(f'one {self.src}\n')
                (self.desc/'one.desc').write_text(f'FILE.EX_ {destination}\n')
                before=self.digest(); result=self.install(success=False)
                self.assertTrue(any(x in result.stderr for x in ('KWAJ','SZDD')))
                self.assertEqual(before,self.digest())

    def test_missing_libmspack_fails_before_transfer(self):
        fake=self.base/'fake-python'/'ctypes'; fake.mkdir(parents=True)
        (fake/'__init__.py').write_text('')
        (fake/'util.py').write_text('def find_library(name): return None\n')
        self.env['PYTHONPATH']=str(fake.parent)
        self.env['PYTHONDONTWRITEBYTECODE']='1'
        self.list.write_text(f'one {self.src}\n')
        (self.desc/'one.desc').write_text('ONE.TXT C:FILE.TXT\n')
        before=self.digest(); result=self.install(success=False)
        self.assertIn('libmspack',result.stderr)
        self.assertEqual(before,self.digest())

    def test_fat12_generation_and_hidden_system_files(self):
        image=self.floppy('floppy.img',{'IO.SYS':'io','MSDOS.SYS':'kernel','COMMAND.COM':'command'},('IO.SYS','MSDOS.SYS'))
        subprocess.run(['mmd','-i',str(image),'::/TOOLS'],env=self.env,check=True,capture_output=True)
        subprocess.run(['mcopy','-i',str(image),str(self.src/'ONE.TXT'),'::/TOOLS/ONE.TXT'],env=self.env,check=True,capture_output=True)
        self.list.write_text(f'floppy {image}\n')
        self.call('generate_package.sh','-l',str(self.list),'-p',str(self.desc))
        generated=(self.desc/'floppy.desc').read_text()
        self.assertIn('IO.SYS',generated); self.assertIn('MSDOS.SYS',generated)
        self.install()
        self.assertEqual(self.read('C','floppy/IO.SYS'),'io')
        self.assertEqual(self.read('C','floppy/MSDOS.SYS'),'kernel')
        self.assertEqual(self.read('C','floppy/TOOLS/ONE.TXT'),'first')

    def test_multiple_floppies_overrides_post_and_preserves_existing(self):
        first=self.floppy('first.img',{'DATA.TXT':'first','IO.SYS':'hidden'},('IO.SYS',))
        second=self.floppy('second.ima',{'DATA.TXT':'second'})
        self.list.write_text(f'first {first}\nsecond {second}\n')
        (self.desc/'first.desc').write_text('DATA.TXT C:DATA.TXT\nIO.SYS C:IO.SYS\n')
        (self.desc/'second.desc').write_text('DATA.TXT C:DATA.TXT\n')
        keep=self.base/'keep'; keep.write_text('keep'); self.mcopy('C',keep,'::/KEEP.TXT')
        post=self.base/'post.sh'; post.write_text('printf post >> "$STAGING_ROOT/C/DATA.TXT"\n')
        self.install(extra=('--post-script',str(post)))
        self.assertEqual(self.read('C','DATA.TXT'),'secondpost')
        self.assertEqual(self.read('C','IO.SYS'),'hidden')
        self.assertEqual(self.read('C','KEEP.TXT'),'keep')

    def test_invalid_fat12_image_is_rejected_without_disk_changes(self):
        image=self.base/'invalid.ima'; image.write_bytes(b'invalid floppy')
        self.list.write_text(f'bad {image}\n')
        (self.desc/'bad.desc').write_text('FILE.TXT C:FILE.TXT\n')
        before=self.digest()
        result=self.install(success=False)
        self.assertIn('FAT12',result.stderr)
        self.assertEqual(before,self.digest())
        self.call('generate_package.sh','-l',str(self.list),'-p',str(self.desc),success=False)

    def test_fat16_image_is_rejected_as_floppy_source(self):
        image=self.base/'fat16.img'
        subprocess.run(['mkfs.fat','-C','-F','16',str(image),'32768'],check=True,capture_output=True)
        self.list.write_text(f'bad {image}\n')
        (self.desc/'bad.desc').write_text('FILE.TXT C:FILE.TXT\n')
        before=self.digest(); result=self.install(success=False)
        self.assertIn('FAT12',result.stderr)
        self.assertEqual(before,self.digest())

    def test_http_fat12_image(self):
        image=self.floppy('remote.img',{'FILE.TXT':'remote floppy'})
        fake=self.base/'bin'/'wget'; fake.write_text('#!/bin/sh\ncp "$WGET_SOURCE" "$3"\n'); fake.chmod(0o755)
        self.env['WGET_SOURCE']=str(image)
        self.list.write_text(f'floppy https://example.invalid/{self.base.name}/remote.img\n')
        self.call('generate_package.sh','-l',str(self.list),'-p',str(self.desc))
        result=self.install(extra=('--clean-on-close',))
        self.assertIn('[INFO] Using cached source for floppy',result.stdout)
        self.assertEqual(self.read('C','floppy/FILE.TXT'),'remote floppy')

    def test_transfer_override_post_script_multiple_volumes_and_preserve(self):
        archive=self.zip('files.zip',{'DATA.TXT':'second','OTHER.TXT':'other'})
        self.list.write_text(f'first {self.src}\nsecond {archive}\n')
        (self.desc/'first.desc').write_text('"ONE.TXT" "C:TOOLS/FILE.TXT"\n')
        (self.desc/'second.desc').write_text('"DATA.TXT" "C:tools/FILE.TXT"\n"OTHER.TXT" "D:DATA/OTHER.TXT"\n')
        local=self.base/'keep.txt'; local.write_text('keep')
        self.mcopy('C',local,'::/KEEP.TXT')
        post=self.base/'post.sh'; post.write_text('printf post > "$STAGING_ROOT/C/TOOLS/FILE.TXT"\n')
        self.install(extra=('--post-script',str(post)))
        self.assertEqual(self.read('C','tools/FILE.TXT'),'post')
        self.assertEqual(self.read('D','DATA/OTHER.TXT'),'other')
        self.assertEqual(self.read('C','KEEP.TXT'),'keep')

    def test_later_override_without_post(self):
        archive=self.zip('files.zip',{'DATA.TXT':'later'})
        self.list.write_text(f'first {self.src}\nsecond {archive}\n')
        (self.desc/'first.desc').write_text('ONE.TXT C:FILE.TXT\n')
        (self.desc/'second.desc').write_text('DATA.TXT C:file.txt\n')
        self.install()
        self.assertEqual(self.read('C','FILE.TXT'),'later')

    def test_short_alias_does_not_overwrite_unrelated_long_name(self):
        original=self.base/'original.txt'; original.write_text('keep long name')
        self.mcopy('C',original,'::/Long Filename.txt')
        self.list.write_text(f'one {self.src}\n')
        (self.desc/'one.desc').write_text('ONE.TXT C:LONGFI~1.TXT\n')
        result=self.install(success=False)
        self.assertIn('short-name alias',result.stderr)
        self.assertEqual(self.read('C','Long Filename.txt'),'keep long name')

    def test_invalid_descriptions_leave_disk_unchanged(self):
        self.list.write_text(f'one {self.src}\n')
        before=self.digest()
        for line in ('ONE.TXT C:../evil', 'ONE.TXT C:/absolute', 'ONE.TXT C:a/../../evil',
                     'ONE.TXT E:FILE.TXT', 'ONE.TXT C:bad*.txt', 'ONE.TXT C:a third',
                     '$(touch /tmp/unwanted) C:X'):
            with self.subTest(line=line):
                (self.desc/'one.desc').write_text(line+'\n')
                self.install(success=False)
                self.assertEqual(before,self.digest())

    def test_missing_source_or_bad_zip_leave_disk_unchanged(self):
        before=self.digest()
        (self.desc/'one.desc').write_text('ONE.TXT C:FILE.TXT\n')
        for source in (self.base/'missing.zip', self.base/'bad.zip', self.src):
            if source.name=='bad.zip': source.write_bytes(b'not a zip')
            if source==self.src: (self.desc/'one.desc').write_text('missing C:FILE.TXT\n')
            self.list.write_text(f'one {source}\n')
            self.install(success=False)
            self.assertEqual(before,self.digest())

    def test_zip_traversal_rejected(self):
        archive=self.zip('evil.zip',{'../escape':'bad'})
        self.list.write_text(f'one {archive}\n')
        (self.desc/'one.desc').write_text('escape C:escape\n')
        before=self.digest(); self.install(success=False); self.assertEqual(before,self.digest())
        self.assertFalse((self.base/'escape').exists())

    def test_post_script_symlink_rejected(self):
        self.list.write_text(f'one {self.src}\n')
        (self.desc/'one.desc').write_text('ONE.TXT C:FILE.TXT\n')
        post=self.base/'post.sh'; post.write_text('ln -s /etc/passwd "$STAGING_ROOT/C/leak"\n')
        before=self.digest(); self.install(success=False,extra=('--post-script',str(post))); self.assertEqual(before,self.digest())

    def test_mismatched_configuration_rejected(self):
        self.list.write_text(f'one {self.src}\n')
        (self.desc/'one.desc').write_text('ONE.TXT C:FILE.TXT\n')
        self.config.write_text('C:\n')
        before=self.digest(); self.install(success=False); self.assertEqual(before,self.digest())

    def test_corrupt_fat_metadata_rejected(self):
        self.list.write_text(f'one {self.src}\n')
        (self.desc/'one.desc').write_text('ONE.TXT C:FILE.TXT\n')
        with self.disk.open('r+b') as stream:
            stream.seek(2048*512+28); stream.write(b'\x00\x00\x00\x00')
        before=self.digest(); self.install(success=False); self.assertEqual(before,self.digest())

    def test_http_source_is_cached(self):
        archive=self.zip('http.zip',{'HTTP.TXT':'remote'})
        fake=self.base/'bin'/'wget'
        fake.write_text('#!/bin/sh\nprintf "call\\n" >> "$WGET_LOG"\ncp "$WGET_SOURCE" "$3"\n')
        fake.chmod(0o755)
        self.env.update(WGET_LOG=str(self.base/'wget.log'),WGET_SOURCE=str(archive))
        url='https://example.invalid/'+self.base.name+'/http.zip'
        self.list.write_text(f'one {url}\n')
        self.call('generate_package.sh','-l',str(self.list),'-p',str(self.desc))
        self.install(extra=('--clean-on-close',))
        self.assertEqual(self.read('C','one/HTTP.TXT'),'remote')
        self.assertEqual((self.base/'wget.log').read_text().splitlines(),['call'])

    def test_download_failure_preserves_disk(self):
        fake=self.base/'bin'/'wget'; fake.write_text('#!/bin/sh\nexit 8\n'); fake.chmod(0o755)
        self.list.write_text(f'one https://example.invalid/{self.base.name}/missing.zip?token=secret\n')
        (self.desc/'one.desc').write_text('file C:FILE.TXT\n')
        before=self.digest(); result=self.install(success=False)
        self.assertIn('wget',result.stderr)
        self.assertNotIn('secret',result.stdout+result.stderr)
        self.assertNotIn('[ OK ] Downloaded',result.stdout)
        self.assertEqual(before,self.digest())

    def test_download_progress_does_not_expose_url(self):
        archive=self.zip('download.zip',{'FILE.TXT':'downloaded'})
        fake=self.base/'bin'/'wget'; fake.write_text('#!/bin/sh\ncp "$WGET_SOURCE" "$3"\n'); fake.chmod(0o755)
        self.env['WGET_SOURCE']=str(archive)
        self.list.write_text(f'one https://example.invalid/{self.base.name}/download.zip?token=secret\n')
        (self.desc/'one.desc').write_text('FILE.TXT C:FILE.TXT\n')
        result=self.install(extra=('--clean-on-close',))
        self.assertIn('[INFO] Downloading source for one',result.stdout)
        self.assertIn('[ OK ] Downloaded source for one',result.stdout)
        self.assertNotIn('secret',result.stdout+result.stderr)
        self.assertEqual(self.read('C','FILE.TXT'),'downloaded')

    def test_transfer_failure_reports_partial_update(self):
        (self.src/'TWO.TXT').write_text('second')
        self.list.write_text(f'one {self.src}\n')
        (self.desc/'one.desc').write_text('ONE.TXT C:ONE.TXT\nTWO.TXT C:TWO.TXT\n')
        real=shutil.which('mcopy')
        fake=self.base/'bin'/'mcopy'
        fake.write_text('#!/bin/sh\nif [ -e "$MCOPY_MARKER" ]; then exit 1; fi\ntouch "$MCOPY_MARKER"\nexec "$MCOPY_REAL" "$@"\n')
        fake.chmod(0o755)
        self.env.update(MCOPY_MARKER=str(self.base/'mcopy-marker'),MCOPY_REAL=real)
        result=self.install(success=False)
        self.assertIn('partially updated',result.stderr)
        self.assertNotIn('[ OK ] FAT16 file transfer completed',result.stdout)
        self.assertNotIn('[ OK ] Package installation completed',result.stdout)
        fake.unlink()
        self.assertEqual(self.read('C','ONE.TXT'),'first')

    def test_named_package(self):
        base=self.base/'packages with spaces'; (base/'demo').mkdir(parents=True)
        (base/'demo.list').write_text(f'one {self.src}\n')
        (base/'demo'/'one.desc').write_text('ONE.TXT C:FILE.TXT\n')
        result=subprocess.run([str(ROOT/'package.sh'),'-d',str(self.disk),'-c',str(self.config),'-i','demo'],cwd=self.base,env=dict(self.env,PACKAGES_BASE_DIR=str(base)),text=True,capture_output=True)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        self.assertIn('[ OK ] Package demo installed',result.stdout)
        self.assertEqual(self.read('C','FILE.TXT'),'first')

    def test_configuration_defaults_and_paths_with_spaces(self):
        env=dict(self.env)
        env.pop('PACKAGES_BASE_DIR',None)
        probe='SCRIPT_DIR=$1; source "$1/main.config"; printf "%s\\n%s\\n" "$PACKAGES_BASE_DIR" "$TMP_DOWNLOAD_DIR"'
        result=subprocess.run(['bash','-c',probe,'config',str(ROOT)],cwd=self.base,env=env,text=True,capture_output=True,check=True)
        self.assertEqual(result.stdout.splitlines(),[str(ROOT/'packages'),str(self.base/'download cache')])
        env['PACKAGES_BASE_DIR']=str(self.base/'custom packages')
        result=subprocess.run(['bash','-c',probe,'config',str(ROOT)],cwd=self.base,env=env,text=True,capture_output=True,check=True)
        self.assertEqual(result.stdout.splitlines(),[str(self.base/'custom packages'),str(self.base/'download cache')])

if __name__=='__main__': unittest.main()
