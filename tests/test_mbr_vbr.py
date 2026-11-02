#!/usr/bin/env python3
"""Handoff tests use disposable images and mocked device inspection only."""
import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

ROOT=Path(__file__).resolve().parents[1]
DIAG=ROOT/'diagnostics'
sys.path.insert(0,str(DIAG))
import mbr_vbr_test as handoff

@unittest.skipUnless(all(shutil.which(x) for x in ('as','ld','objcopy','sfdisk','mkfs.fat')), 'disk tools and binutils required')
class HandoffTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='handoff-test-'); self.base=Path(self.temp.name)
        self.bin=self.base/'bin'; self.bin.mkdir()
        fake=self.bin/'ms-sys'; fake.write_text('#!/bin/sh\nexit 0\n'); fake.chmod(0o755)
        self.env=dict(os.environ,PATH=str(self.bin)+':'+os.environ['PATH'],PYTHONDONTWRITEBYTECODE='1',TMPDIR=str(self.base))
        self.sectors=260*16*63; self.disk=self.base/'disk.img'
        with self.disk.open('wb') as f: f.truncate(self.sectors*512)
        r=subprocess.run([str(ROOT/'setup_disk.sh'),'-d',str(self.disk),'--heads','16','--sectors','63'],env=self.env,capture_output=True,text=True)
        self.assertEqual(r.returncode,0,r.stdout+r.stderr)
        for name,addr in (('mbr','0x600'),('vbr','0x7c00')):
            src=DIAG/f'mbr-vbr-test-{name}.S'
            subprocess.run(['as','--32',str(src),'-o',str(self.base/f'{name}.o')],check=True)
            subprocess.run(['ld','-m','elf_i386','-Ttext',addr,'-e','_start',str(self.base/f'{name}.o'),'-o',str(self.base/f'{name}.elf')],check=True)
            subprocess.run(['objcopy','-O','binary','-j','.text',str(self.base/f'{name}.elf'),str(self.base/f'{name}.bin')],check=True)
        self.backup=self.base/'backup'
    def tearDown(self): self.temp.cleanup()

    def sectors_and_rest(self):
        h=hashlib.sha256()
        with self.disk.open('rb') as f:
            mbr=f.read(512)
            h.update(f.read(2048*512-512))
            vbr=f.read(512)
            while data:=f.read(1024*1024): h.update(data)
        return mbr,vbr,h.digest()

    def install(self):
        handoff.run('install',self.disk,self.sectors,self.backup,self.base/'mbr.bin',self.base/'vbr.bin')

    def test_install_preserves_metadata_and_restore(self):
        original=self.sectors_and_rest(); self.install(); installed=self.sectors_and_rest()
        self.assertEqual(installed[0][440:],original[0][440:])
        self.assertEqual(installed[1][3:62],original[1][3:62])
        self.assertEqual(installed[1][:3],b'\xeb\x3c\x90')
        self.assertEqual(installed[1][510:],b'\x55\xaa')
        self.assertEqual(installed[2],original[2])
        self.assertEqual((self.backup/'mbr.bin').read_bytes(),original[0])
        self.assertEqual((self.backup/'vbr.bin').read_bytes(),original[1])
        backup_vbr=self.backup/'vbr.bin'; backup_vbr.write_bytes(bytes(512))
        with self.assertRaisesRegex(ValueError,'Backup does not match'):
            handoff.run('restore',self.disk,self.sectors,self.backup)
        self.assertEqual(self.sectors_and_rest(),installed)
        backup_vbr.write_bytes(original[1])
        handoff.run('restore',self.disk,self.sectors,self.backup)
        self.assertEqual(self.sectors_and_rest(),original)

    def test_actual_script_rejects_regular_file(self):
        before=self.sectors_and_rest()
        result=subprocess.run([str(DIAG/'write-mbr-vbr-test.sh'),'--backup-dir',str(self.backup),str(self.disk)],text=True,capture_output=True)
        self.assertNotEqual(result.returncode,0)
        self.assertIn('block device' if os.geteuid()==0 else 'Run as root',result.stderr)
        self.assertFalse(self.backup.exists())
        self.assertEqual(self.sectors_and_rest(),before)

    def test_invalid_layout_and_backup_refused_without_writes(self):
        original=self.sectors_and_rest()
        for mutation in ('mbr','bpb','geometry'):
            mbr=bytearray(original[0]); vbr=bytearray(original[1])
            if mutation=='mbr': mbr[446]=0
            if mutation=='bpb': vbr[11:13]=b'\0\0'
            if mutation=='geometry': mbr[448]=1
            with self.subTest(mutation=mutation):
                with self.assertRaises(ValueError): handoff.geometry(mbr,vbr,self.sectors)
        self.backup.mkdir()
        with self.assertRaises(FileExistsError): self.install()
        self.assertEqual(self.sectors_and_rest(),original)

    def test_readback_failure_is_explicit(self):
        with self.disk.open('r+b',buffering=0) as f, mock.patch.object(handoff.os,'pread',return_value=b'wrong'):
            with self.assertRaisesRegex(OSError,'readback verification failed'):
                handoff.write_checked(f.fileno(),bytes(512),0,'MBR')

    def test_failed_write_reports_recoverable_backup(self):
        original=self.sectors_and_rest()
        with mock.patch.object(handoff,'write_checked',side_effect=OSError('VBR readback verification failed')):
            with self.assertRaisesRegex(OSError,'partially changed; retain backup'):
                self.install()
        self.assertEqual((self.backup/'mbr.bin').read_bytes(),original[0])
        self.assertEqual((self.backup/'vbr.bin').read_bytes(),original[1])
        self.assertEqual(self.sectors_and_rest(),original)

    def test_shell_safety_checks_with_mocked_device(self):
        programs={
            'lsblk':'case "$1" in -dn) case "$3" in TYPE) echo disk;; MOUNTPOINTS) [ "$SCENARIO" != mounted ] || echo /mounted;; *) echo fixture;; esac;; -nrpo) echo "$FIXTURE";; *) exit 1;; esac',
            'swapon':'[ "$*" = "--show --noheadings --raw --output NAME" ] || exit 2; case "$SCENARIO" in swap) echo "$FIXTURE";; fail) exit 1;; esac',
            'blockdev':f'case "$1" in --getss) echo 512;; --getsz) echo {self.sectors};; *) exit 1;; esac',
        }
        for name,body in programs.items():
            f=self.bin/name; f.write_text('#!/bin/sh\n'+body+'\n'); f.chmod(0o755)
        script=(DIAG/'write-mbr-vbr-test.sh').read_text()
        script=script.replace('dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)',f'dir={DIAG}')
        script=script.replace("((EUID == 0)) || die 'Run as root.'",': # test only')
        script=script.replace('[[ -b "$target" ]]','[[ -f "$target" ]]')
        script=script.replace("[[ -t 0 ]] || die 'Destructive confirmation requires an interactive terminal.'",': # test only')
        original=self.sectors_and_rest()
        for scenario,answer in (('swap','x'),('mounted','x'),('fail','x'),('ok','NO')):
            result=subprocess.run(['bash','-c',script,'test','--backup-dir',str(self.backup),str(self.disk)],input=answer+'\n',text=True,capture_output=True,env=dict(self.env,FIXTURE=str(self.disk),SCENARIO=scenario))
            self.assertNotEqual(result.returncode,0,result.stdout+result.stderr)
            self.assertEqual(self.sectors_and_rest(),original)
            self.assertFalse(self.backup.exists())
        result=subprocess.run(['bash','-c',script,'test','--backup-dir',str(self.backup),str(self.disk)],input=f'WRITE {self.disk}\n',text=True,capture_output=True,env=dict(self.env,FIXTURE=str(self.disk),SCENARIO='ok'))
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        self.assertIn('MBR and VBR read back and verified',result.stdout)
        result=subprocess.run(['bash','-c',script,'test','--restore',str(self.backup),str(self.disk)],input=f'WRITE {self.disk}\n',text=True,capture_output=True,env=dict(self.env,FIXTURE=str(self.disk),SCENARIO='ok'))
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        self.assertEqual(self.sectors_and_rest(),original)

    @unittest.skipUnless(shutil.which('qemu-system-i386'), 'QEMU unavailable')
    def test_qemu_handoff_and_halt(self):
        self.install()
        cmd=['qemu-system-i386','-machine','pc','-cpu','486','-m','16',
             '-drive',f'file={self.disk},format=raw,if=none,id=d',
             '-device','ide-hd,drive=d,bus=ide.0,unit=0,cyls=260,heads=16,secs=63,bios-chs-trans=none',
             '-boot','c','-snapshot','-display','none','-monitor','stdio']
        proc=subprocess.Popen(cmd,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,env=self.env)
        time.sleep(3)
        out,err=proc.communicate(f'pmemsave 0xb8000 4000 "{self.base}/screen"\ninfo registers\nquit\n',timeout=15)
        self.assertEqual(proc.returncode,0,err)
        screen=(self.base/'screen').read_bytes()[::2].decode('cp437')
        positions=[screen.index(s) for s in ('MBR START','READING...','DISK READ OK','JUMPING TO VBR','VBR OK')]
        self.assertEqual(positions,sorted(positions))
        self.assertIn('HLT=1',out)
        self.assertIn('EDX=00000080',out)

if __name__=='__main__': unittest.main()
