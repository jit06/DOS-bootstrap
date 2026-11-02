#!/usr/bin/env python3
"""Test the DOS MBR writer on mocked devices backed by disposable files."""
import os
from pathlib import Path
import shutil
import struct
import subprocess
import tempfile
import time
import unittest

ROOT=Path(__file__).resolve().parents[1]
DIAG=ROOT/'diagnostics'

@unittest.skipUnless(shutil.which('ms-sys'), 'ms-sys required')
class DosMbrTests(unittest.TestCase):
    def test_writer_and_restore_preserve_noncode(self):
        with tempfile.TemporaryDirectory(prefix='mbr-dos-test-') as tmp:
            base=Path(tmp); disk=base/'disk'; bindir=base/'bin'; bindir.mkdir()
            first=bytearray(512); first[440:446]=b'ABCDEF'; first[510:]=b'\x55\xaa'
            first[446:462]=bytes([0x80,0,33,2,6,4,63,5])+struct.pack('<II',2048,4096)
            original=bytes(first)+bytes(range(256))*40
            disk.write_bytes(original)
            programs={
                'lsblk':'case "$1" in -dn) case "$3" in TYPE) echo disk;; MOUNTPOINTS) [ "$SCENARIO" != mounted ] || echo /mounted;; *) echo fixture;; esac;; -nrpo) echo "$FIXTURE";; *) exit 1;; esac',
                'swapon':'[ "$*" = "--show --noheadings --raw --output NAME" ] || exit 2; case "$SCENARIO" in swap) echo "$FIXTURE";; fail) exit 1;; esac',
                'blockdev':'case "$1" in --getss) echo 512;; --getsz) echo 32256;; *) exit 1;; esac',
            }
            for name,body in programs.items():
                file=bindir/name; file.write_text('#!/bin/sh\n'+body+'\n'); file.chmod(0o755)
            script=(DIAG/'write-mbr-dos.sh').read_text()
            script=script.replace('dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)',f'dir={DIAG}')
            script=script.replace("((EUID == 0)) || die 'Run as root.'",': # test only')
            script=script.replace('[[ -b "$target" ]]','[[ -f "$target" ]]')
            script=script.replace("[[ -t 0 ]] || die 'Destructive confirmation requires an interactive terminal.'",': # test only')
            env=dict(os.environ,PATH=str(bindir)+':'+os.environ['PATH'],FIXTURE=str(disk),PYTHONDONTWRITEBYTECODE='1')
            def invoke(*args,scenario='ok',answer=None):
                return subprocess.run(['bash','-c',script,'diagnostic',*args,str(disk)],input=answer or f'WRITE {disk}\n',text=True,capture_output=True,env=dict(env,SCENARIO=scenario))
            for scenario in ('swap','mounted','fail'):
                result=invoke(scenario=scenario)
                self.assertNotEqual(result.returncode,0,result.stdout+result.stderr)
                self.assertEqual(disk.read_bytes(),original)
            invalid=bytearray(original); invalid[446]=0; disk.write_bytes(invalid)
            result=invoke(); self.assertNotEqual(result.returncode,0)
            self.assertIn('Exactly one active partition',result.stderr)
            self.assertEqual(disk.read_bytes(),invalid)
            disk.write_bytes(original)
            result=invoke(answer='NO\n')
            self.assertNotEqual(result.returncode,0)
            self.assertEqual(disk.read_bytes(),original)
            result=invoke(); self.assertEqual(result.returncode,0,result.stdout+result.stderr)
            dos=disk.read_bytes()
            self.assertEqual(dos[440:],original[440:])
            self.assertTrue(dos.startswith(b'\xfa\x33\xc0'))
            result=invoke('--restore-syslinux'); self.assertEqual(result.returncode,0,result.stdout+result.stderr)
            restored=disk.read_bytes()
            self.assertEqual(restored[440:],original[440:])
            self.assertNotEqual(restored[:440],dos[:440])
            result=invoke(); self.assertEqual(result.returncode,0,result.stdout+result.stderr)
            self.assertEqual(disk.read_bytes(),dos)

    def test_requires_device_argument(self):
        result=subprocess.run([str(DIAG/'write-mbr-dos.sh')],text=True,capture_output=True)
        self.assertNotEqual(result.returncode,0)
        self.assertIn('Usage:',result.stderr)

    @unittest.skipUnless(all(shutil.which(x) for x in ('qemu-system-i386','as','ld','objcopy')), 'QEMU and GNU binutils required')
    def test_qemu_mbr_hands_off_to_active_vbr(self):
        with tempfile.TemporaryDirectory(prefix='mbr-dos-qemu-') as tmp:
            base=Path(tmp); disk=base/'disk.img'; mbr=base/'mbr.bin'
            first=bytearray(512); first[510:]=b'\x55\xaa'
            first[446:462]=bytes([0x80,0,33,2,6,4,63,5])+struct.pack('<II',2048,4096)
            mbr.write_bytes(first)
            subprocess.run(['ms-sys','--force','--mbrdos',str(mbr)],check=True,capture_output=True)
            subprocess.run(['as','--32',str(DIAG/'mbr-ok.S'),'-o',str(base/'vbr.o')],check=True)
            subprocess.run(['ld','-m','elf_i386','-Ttext','0x7c00','-e','_start',str(base/'vbr.o'),'-o',str(base/'vbr.elf')],check=True)
            subprocess.run(['objcopy','-O','binary','-j','.text',str(base/'vbr.elf'),str(base/'vbr.bin')],check=True)
            with disk.open('wb') as stream:
                stream.truncate(32*16*63*512)
                stream.seek(0); stream.write(mbr.read_bytes())
                stream.seek(2048*512); stream.write((base/'vbr.bin').read_bytes())
            command=['qemu-system-i386','-machine','pc','-cpu','486','-m','16',
                '-drive',f'file={disk},format=raw,if=none,id=d',
                '-device','ide-hd,drive=d,bus=ide.0,unit=0,cyls=32,heads=16,secs=63,bios-chs-trans=none',
                '-boot','c','-snapshot','-display','none','-monitor','stdio']
            proc=subprocess.Popen(command,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,env=dict(os.environ,TMPDIR=tmp))
            time.sleep(3)
            out,err=proc.communicate(f'pmemsave 0xb8000 4000 "{base}/vga.bin"\ninfo registers\nquit\n',timeout=15)
            self.assertEqual(proc.returncode,0,err)
            self.assertIn('MBR OK',(base/'vga.bin').read_bytes()[::2].decode('cp437'))
            self.assertIn('HLT=1',out)

if __name__=='__main__': unittest.main()
