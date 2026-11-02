#!/usr/bin/env python3
"""Disposable images and mocked devices only; no physical disk access."""
import os
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import tempfile
import time
import unittest

ROOT=Path(__file__).resolve().parents[1]
DIAG=ROOT/'diagnostics'
sys.path.insert(0,str(DIAG))
from mbr_read_patch import patch

@unittest.skipUnless(all(shutil.which(x) for x in ('as','ld','objcopy')), 'GNU binutils required')
class ReadDiagnosticTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='mbr-read-test-')
        self.base=Path(self.temp.name)
        subprocess.run(['as','--32',str(DIAG/'mbr-read.S'),'-o',str(self.base/'code.o')],check=True)
        subprocess.run(['ld','-m','elf_i386','-Ttext','0x7c00','-e','_start',str(self.base/'code.o'),'-o',str(self.base/'code.elf')],check=True)
        subprocess.run(['objcopy','-O','binary','-j','.text',str(self.base/'code.elf'),str(self.base/'code.bin')],check=True)
        self.code=(self.base/'code.bin').read_bytes()
        self.sectors=32*16*63
        self.sector=bytearray(512)
        self.sector[440:446]=b'ID1234'
        self.sector[446:462]=bytes([0x80,0,33,2,6,4,63,5])+struct.pack('<II',2048,4096)
        self.sector[510:]=b'\x55\xaa'
    def tearDown(self): self.temp.cleanup()

    def test_validation_and_preservation(self):
        self.assertEqual(len(self.code),440)
        result=patch(self.sector,self.code,self.sectors)
        self.assertEqual(result[440:],self.sector[440:])
        for mutation in ('signature','no_active','two_active','flag','type','sector','head','start','size','outside'):
            with self.subTest(mutation=mutation):
                bad=bytearray(self.sector)
                if mutation=='signature': bad[510:]=b'\x00\x00'
                elif mutation=='no_active': bad[446]=0
                elif mutation=='two_active': bad[462]=0x80
                elif mutation=='flag': bad[462]=1
                elif mutation=='type': bad[450]=0
                elif mutation=='sector': bad[448]=0xc0
                elif mutation=='head': bad[447]=255
                elif mutation=='start': bad[454:458]=bytes(4)
                elif mutation=='size': bad[458:462]=bytes(4)
                else: bad[454:458]=struct.pack('<I',self.sectors)
                with self.assertRaises(ValueError): patch(bad,self.code,self.sectors)

    def test_writer_on_mocked_regular_device(self):
        image=self.base/'disk'; original=bytes(self.sector)+b'partition-content'*100
        image.write_bytes(original)
        bindir=self.base/'bin'; bindir.mkdir()
        programs={
            'lsblk':'case "$1" in -dn) case "$3" in TYPE) echo disk;; MOUNTPOINTS) [ "$SCENARIO" != mounted ] || echo /mounted;; *) echo fixture;; esac;; -nrpo) echo "$FIXTURE";; *) exit 1;; esac',
            'swapon':'[ "$*" = "--show --noheadings --raw --output NAME" ] || exit 2; case "$SCENARIO" in swap) echo "$FIXTURE";; fail) exit 1;; esac',
            'blockdev':'case "$1" in --getss) echo 512;; --getsz) echo 32256;; *) exit 1;; esac',
        }
        for name, body in programs.items():
            file=bindir/name; file.write_text('#!/bin/sh\n'+body+'\n'); file.chmod(0o755)
        script=(DIAG/'write-mbr-read.sh').read_text()
        script=script.replace('dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)',f'dir={DIAG}')
        script=script.replace("((EUID == 0)) || die 'Run as root.'",': # test: no privilege')
        script=script.replace('[[ -b "$target" ]]', '[[ -f "$target" ]]')
        script=script.replace("[[ -t 0 ]] || die 'Destructive confirmation requires an interactive terminal.'",': # test: piped confirmation')
        env=dict(os.environ,PATH=str(bindir)+':'+os.environ['PATH'],FIXTURE=str(image))
        for scenario, confirmation, success in (('ok',f'WRITE {image}\n',True),('swap',f'WRITE {image}\n',False),('mounted',f'WRITE {image}\n',False),('fail',f'WRITE {image}\n',False),('ok','NO\n',False)):
            with self.subTest(scenario=scenario,confirmation=confirmation):
                image.write_bytes(original)
                result=subprocess.run(['bash','-c',script,'diagnostic',str(image)],input=confirmation,text=True,capture_output=True,env=dict(env,SCENARIO=scenario))
                self.assertEqual(result.returncode==0,success,result.stdout+result.stderr)
                data=image.read_bytes()
                self.assertEqual(data[440:],original[440:])
                if success: self.assertEqual(data[:440],self.code)
                else: self.assertEqual(data,original)
        invalid=bytearray(original); invalid[446]=0; image.write_bytes(invalid)
        result=subprocess.run(['bash','-c',script,'diagnostic',str(image)],input=f'WRITE {image}\n',text=True,capture_output=True,env=dict(env,SCENARIO='ok'))
        self.assertNotEqual(result.returncode,0)
        self.assertIn('Exactly one active partition',result.stderr)
        self.assertEqual(image.read_bytes(),invalid)

    @unittest.skipUnless(shutil.which('qemu-system-i386'), 'QEMU unavailable')
    def test_qemu_chs_read_and_halt(self):
        image=self.base/'disk.img'
        with image.open('wb') as stream:
            stream.truncate(self.sectors*512)
            stream.seek(0); stream.write(patch(self.sector,self.code,self.sectors))
            stream.seek(2048*512); stream.write(b'ACTIVE-PARTITION-FIRST-SECTOR')
        cmd=['qemu-system-i386','-machine','pc','-cpu','486','-m','16',
             '-drive',f'file={image},format=raw,if=none,id=d',
             '-device','ide-hd,drive=d,bus=ide.0,unit=0,cyls=32,heads=16,secs=63,bios-chs-trans=none',
             '-boot','c','-snapshot','-display','none','-monitor','stdio']
        env=dict(os.environ,TMPDIR=str(self.base))
        proc=subprocess.Popen(cmd,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,env=env)
        time.sleep(3)
        out,err=proc.communicate(f'pmemsave 0xb8000 4000 "{self.base}/vga.bin"\npmemsave 0x600 32 "{self.base}/buffer.bin"\ninfo registers\nquit\n',timeout=15)
        self.assertEqual(proc.returncode,0,err)
        self.assertTrue((self.base/'vga.bin').exists(),out+err)
        screen=(self.base/'vga.bin').read_bytes()[::2].decode('cp437')
        self.assertIn('MBR START',screen)
        self.assertIn('READING...',screen)
        self.assertIn('DISK READ OK',screen)
        self.assertTrue((self.base/'buffer.bin').read_bytes().startswith(b'ACTIVE-PARTITION-FIRST-SECTOR'))
        self.assertIn('HLT=1',out)

    @unittest.skipUnless(shutil.which('qemu-system-i386'), 'QEMU unavailable')
    def test_qemu_displays_bios_error_and_halts(self):
        bad=bytearray(self.sector); bad[447]=31  # Outside the emulated BIOS's 16 heads.
        image=self.base/'bad-chs.img'
        with image.open('wb') as stream:
            stream.truncate(self.sectors*512)
            stream.seek(0); stream.write(patch(bad,self.code,self.sectors))
        cmd=['qemu-system-i386','-machine','pc','-cpu','486','-m','16',
             '-drive',f'file={image},format=raw,if=none,id=d',
             '-device','ide-hd,drive=d,bus=ide.0,unit=0,cyls=32,heads=16,secs=63,bios-chs-trans=none',
             '-boot','c','-snapshot','-display','none','-monitor','stdio']
        proc=subprocess.Popen(cmd,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,env=dict(os.environ,TMPDIR=str(self.base)))
        time.sleep(3)
        out,err=proc.communicate(f'pmemsave 0xb8000 4000 "{self.base}/error-vga.bin"\ninfo registers\nquit\n',timeout=15)
        self.assertEqual(proc.returncode,0,err)
        screen=(self.base/'error-vga.bin').read_bytes()[::2].decode('cp437')
        self.assertIn('READING...',screen)
        self.assertRegex(screen,r'DISK READ ERROR AH=[0-9A-F]{2}')
        self.assertIn('HLT=1',out)

if __name__=='__main__': unittest.main()
