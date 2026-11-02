#!/usr/bin/env python3
"""Diagnostic tests use temporary regular files only, never block devices."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
DIAG = ROOT / 'diagnostics'

@unittest.skipUnless(all(shutil.which(x) for x in ('as', 'ld', 'objcopy')), 'GNU binutils required')
class MbrDiagnosticTests(unittest.TestCase):
    def test_assembled_sector(self):
        with tempfile.TemporaryDirectory(prefix='mbr-ok-test-') as temp:
            temp = Path(temp)
            subprocess.run(['as','--32',str(DIAG/'mbr-ok.S'),'-o',str(temp/'mbr.o')],check=True)
            subprocess.run(['ld','-m','elf_i386','-Ttext','0x7c00','-e','_start',str(temp/'mbr.o'),'-o',str(temp/'mbr.elf')],check=True)
            subprocess.run(['objcopy','-O','binary','-j','.text',str(temp/'mbr.elf'),str(temp/'mbr.bin')],check=True)
            sector=(temp/'mbr.bin').read_bytes()
            self.assertEqual(len(sector),512)
            self.assertEqual(sector[510:],b'\x55\xaa')
            self.assertEqual(sector[446:510],bytes(64))
            self.assertIn(b'MBR OK\0',sector[:446])
            if shutil.which('nasm'):
                subprocess.run(['nasm','-f','bin',str(DIAG/'mbr-ok.asm'),'-o',str(temp/'nasm.bin')],check=True)
                self.assertEqual((temp/'nasm.bin').read_bytes(),sector)

    def test_writer_rejects_regular_file(self):
        with tempfile.TemporaryDirectory(prefix='mbr-ok-test-') as temp:
            target=Path(temp)/'image'; target.write_bytes(b'untouched')
            result=subprocess.run([str(DIAG/'write-mbr-ok.sh'),str(target)],capture_output=True,text=True)
            self.assertNotEqual(result.returncode,0)
            self.assertIn('block device' if os.geteuid()==0 else 'Run as root',result.stderr)
            self.assertEqual(target.read_bytes(),b'untouched')

    def test_writer_requires_target(self):
        result=subprocess.run([str(DIAG/'write-mbr-ok.sh')],capture_output=True,text=True)
        self.assertNotEqual(result.returncode,0)
        self.assertIn('Usage:',result.stderr)

if __name__ == '__main__': unittest.main()
