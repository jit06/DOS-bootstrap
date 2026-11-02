#!/usr/bin/env python3
"""Destructive tests against temporary regular files only; never a block device."""
import hashlib
import os
from pathlib import Path
import shutil
import struct
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / 'setup_disk.sh'
REAL_MS_SYS = shutil.which('ms-sys')

class SetupDiskTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='dos-bootstrap-tests-')
        self.base = Path(self.temp.name)
        bindir = self.base / 'bin'
        bindir.mkdir()
        # A stand-in makes the image tests independent of a locally installed ms-sys.
        stub = bindir / 'ms-sys'
        stub.write_text('#!/bin/sh\nprintf "%s\\n" "$*" >> "$MS_SYS_LOG"\n')
        stub.chmod(0o755)
        self.env = dict(os.environ, PATH=f'{bindir}:{os.environ["PATH"]}', MS_SYS_LOG=str(self.base / 'mbr.log'))
        self.image = self.base / 'disk.img'
        with self.image.open('wb') as stream:
            stream.truncate(512 * 1024 * 1024)
        self.config = self.base / 'partitions.config'

    def tearDown(self):
        self.temp.cleanup()

    def run_setup(self, config, success=True, extra=()):
        self.config.write_text(config)
        result = subprocess.run([str(SCRIPT), '-d', str(self.image), '-c', str(self.config), *extra], env=self.env, text=True, capture_output=True)
        self.assertEqual(result.returncode == 0, success, result.stdout + result.stderr)
        return result

    def verify(self, expected, mocked=True, heads=None, spt=63):
        if heads is None:
            heads = next(h for h in (16, 32, 64, 128, 255) if self.image.stat().st_size // 512 <= 1024 * h * spt)
        def decode_chs(raw):
            return raw[2] | ((raw[1] & 0xc0) << 2), raw[0], raw[1] & 0x3f
        def expected_chs(lba):
            cylinder, remainder = divmod(lba, heads * spt)
            head, sector = divmod(remainder, spt)
            return cylinder, head, sector + 1
        with self.image.open('rb') as stream:
            mbr = stream.read(512)
            self.assertEqual(mbr[510:512], b'\x55\xaa')
            entries = [mbr[446+i*16:462+i*16] for i in range(4)]
            self.assertEqual(sum(bool(e[4]) for e in entries), len(expected))
            for idx, (start, size, active) in enumerate(expected):
                entry = entries[idx]
                self.assertEqual(entry[0], 0x80 if active else 0)
                self.assertEqual(entry[4], 0x04 if size < 65536 else 0x06)
                self.assertEqual(struct.unpack_from('<II', entry, 8), (start, size))
                self.assertEqual(decode_chs(entry[1:4]), expected_chs(start))
                self.assertEqual(decode_chs(entry[5:8]), expected_chs(start + size - 1))
                stream.seek(start * 512)
                boot = stream.read(512)
                self.assertEqual(boot[510:512], b'\x55\xaa')
                self.assertEqual(struct.unpack_from('<H', boot, 11)[0], 512)
                self.assertEqual(struct.unpack_from('<I', boot, 28)[0], start)
                self.assertEqual(struct.unpack_from('<HH', boot, 24), (spt, heads))
                total = struct.unpack_from('<H', boot, 19)[0] or struct.unpack_from('<I', boot, 32)[0]
                self.assertEqual(total, size)
                spc = boot[13]
                reserved = struct.unpack_from('<H', boot, 14)[0]
                root = struct.unpack_from('<H', boot, 17)[0]
                fatsize = struct.unpack_from('<H', boot, 22)[0]
                clusters = (size - reserved - boot[16] * fatsize - (root * 32 + 511) // 512) // spc
                self.assertTrue(4085 <= clusters <= 65524, clusters)
        if mocked:
            self.assertEqual((self.base / 'mbr.log').read_text().splitlines(),
                             [f'--force --mbrsyslinux {self.image}'])

    def test_default(self):
        result = subprocess.run([str(SCRIPT), '-d', str(self.image)], env=self.env, text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('Automatic geometry is a heuristic', result.stdout + result.stderr)
        self.assertIn('32 heads, 63 sectors/track (automatic)', result.stdout)
        self.verify([(2048, 1046528, True)])

    @unittest.skipUnless(REAL_MS_SYS, 'real ms-sys not installed')
    def test_real_ms_sys_installs_mbr_code(self):
        self.env['PATH'] = os.environ['PATH']
        self.run_setup('C:\n')
        self.verify([(2048, 1046528, True)], mocked=False)
        with self.image.open('rb') as stream:
            self.assertNotEqual(stream.read(440), bytes(440))
        self.assertFalse((self.base / 'mbr.log').exists())

    def test_multiple_and_explicit_active(self):
        self.run_setup('# comment\nC:fat16:128M\nD:fat16:128M:boot\nE:fat16:\n')
        self.verify([(2048, 262144, False), (264192, 262144, True), (526336, 522240, False)])

    def test_explicit_bios_geometry(self):
        result=self.run_setup('C:\n', extra=('--heads', '32', '--sectors', '63'))
        self.assertIn('32 heads, 63 sectors/track (explicit)', result.stdout)
        self.verify([(2048, 1046528, True)], heads=32, spt=63)

    def test_automatic_geometry_profile_boundaries(self):
        for size_sectors, heads in ((1024*16*63,16), (1024*16*63+2,32),
                                    (1024*32*63+2,64), (1024*64*63+2,128),
                                    (1024*128*63+2,255)):
            with self.subTest(heads=heads):
                with self.image.open('r+b') as stream: stream.truncate(size_sectors*512)
                result=self.run_setup('C:fat16:32M\n')
                self.assertIn(f'{heads} heads, 63 sectors/track (automatic)',result.stdout)
                self.verify([(2048,65536,True)],mocked=False,heads=heads)

    def test_auto_geometry_disk_exceeds_chs_capacity(self):
        with self.image.open('r+b') as stream:
            stream.truncate((1024*255*63+2)*512)
            stream.seek(0); stream.write(b'original data')
        result=self.run_setup('C:fat16:32M\n',success=False)
        self.assertIn('exceeds conventional 1024-cylinder CHS capacity',result.stderr)
        with self.image.open('rb') as stream: self.assertEqual(stream.read(13),b'original data')
        self.assertFalse((self.base/'mbr.log').exists())

    def test_odd_sector_target_rejected_before_writes(self):
        with self.image.open('r+b') as stream:
            stream.truncate((1024*16*63+1)*512)
            stream.seek(0); stream.write(b'original data')
        result=self.run_setup('C:fat16:32M\n',success=False)
        self.assertIn('odd number of sectors',result.stderr)
        with self.image.open('rb') as stream: self.assertEqual(stream.read(13),b'original data')
        self.assertFalse((self.base/'mbr.log').exists())

    def test_explicit_geometry_multiple_partitions(self):
        self.run_setup('C:fat16:128M\nD:fat16:128M:boot\nE:\n', extra=('--heads', '32', '--sectors', '63'))
        self.verify([(2048, 262144, False), (264192, 262144, True),
                     (526336, 522240, False)], heads=32, spt=63)

    def test_explicit_geometry_overrides_automatic_profile(self):
        result=self.run_setup('C:\n', extra=('--heads', '64', '--sectors', '63'))
        self.assertIn('64 heads, 63 sectors/track (explicit)',result.stdout)
        self.verify([(2048,1046528,True)],heads=64,spt=63)

    def test_invalid_geometry_does_not_write(self):
        def digest():
            h = hashlib.sha256()
            with self.image.open('rb') as stream:
                for chunk in iter(lambda: stream.read(4 * 1024 * 1024), b''): h.update(chunk)
            return h.digest()
        before = digest()
        for options in (('--heads', '32'), ('--sectors', '63'), ('--heads', '0', '--sectors', '63'),
                        ('--heads', '256', '--sectors', '63'), ('--heads', '32', '--sectors', '0'),
                        ('--heads', '32', '--sectors', '64'), ('--heads', 'bad', '--sectors', '63'),
                        ('--heads', '16', '--sectors', '63')):
            with self.subTest(options=options):
                self.run_setup('C:\n', success=False, extra=options)
                self.assertEqual(digest(), before)
        self.assertFalse((self.base / 'mbr.log').exists())

    def test_four_partitions(self):
        self.run_setup('C:fat16:32M\nD:fat16:32M\nE:fat16:32M\nF:fat16:\n')
        self.verify([(2048, 65536, True), (67584, 65536, False),
                     (133120, 65536, False), (198656, 849920, False)])

    def test_omitted_fields_with_boot(self):
        self.run_setup('C::32M\nD:::boot\n')
        self.verify([(2048, 65536, False), (67584, 980992, True)])

    def test_near_two_gib_fat16_limit(self):
        with self.image.open('r+b') as stream:
            stream.truncate(2048 * 1024**2)
        self.run_setup('C:\n')
        self.verify([(2048, 4192256, True)])

    def test_exactly_two_gib_is_rejected_when_formatter_cannot_represent_it(self):
        with self.image.open('r+b') as stream:
            stream.truncate(1024 * 1024 + 2 * 1024**3)
        self.run_setup('C:\n', success=False)
        with self.image.open('rb') as stream:
            self.assertEqual(stream.read(512), bytes(512))

    @unittest.skipUnless(shutil.which('fsck.fat'), 'fsck.fat not installed')
    def test_fat_filesystem_check(self):
        self.run_setup('C:fat16:32M\n')
        partition = self.base / 'partition.img'
        with self.image.open('rb') as src, partition.open('wb') as dst:
            src.seek(2048 * 512)
            remaining = 32 * 1024 * 1024
            while remaining:
                chunk = src.read(min(remaining, 1024 * 1024))
                self.assertTrue(chunk)
                dst.write(chunk)
                remaining -= len(chunk)
        result = subprocess.run(['fsck.fat', '-n', str(partition)], text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_invalid_and_capacity_preserve_image(self):
        # Sample the entire initial image, including the partition and filesystem areas.
        def digest():
            h = hashlib.sha256()
            with self.image.open('rb') as stream:
                while chunk := stream.read(4 * 1024 * 1024): h.update(chunk)
            return h.digest()
        before = digest()
        for config in ('C:fat32:\n', 'C:\nD:fat16:10M\n', 'C:fat16:32M:boot\nD:fat16::boot\n',
                       'C:fat16:600M\n', 'C:fat16:1M\n', 'D:\n',
                       'C:fat16:0M\n', 'C:fat16:32MB\n', 'C:fat16:32M:extra\n',
                       'C!:fat16:32M\n', 'C:fat16:32M:\n', 'C:fat16:32M:BOOT\n',
                       'C:fat16:32M\nD:fat16:32M\nE:fat16:32M\nF:fat16:32M\nG:\n'):
            with self.subTest(config=config):
                self.run_setup(config, success=False)
                self.assertEqual(digest(), before)
        self.assertFalse((self.base / 'mbr.log').exists())

    def test_above_fat16_limit_preserves_image(self):
        with self.image.open('r+b') as stream:
            stream.truncate(3 * 1024**3)
        self.run_setup('C:\n', success=False)
        with self.image.open('rb') as stream:
            self.assertEqual(stream.read(512), bytes(512))
        self.assertFalse((self.base / 'mbr.log').exists())

    def test_missing_dependency(self):
        result = subprocess.run(['/bin/bash', '-c', 'source "$1/functions.sh"; __DEPENDENCIES__=(not_a_real_dos_tool); check_dependencies', '_', str(ROOT)], text=True, capture_output=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('not_a_real_dos_tool', result.stdout + result.stderr)

    def test_present_dependency(self):
        result = subprocess.run(['/bin/bash', '-c', 'source "$1/functions.sh"; __DEPENDENCIES__=(bash); check_dependencies', '_', str(ROOT)], text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_physical_device_swap_and_mount_checks(self):
        # Execute the actual inspection block with fake tools and temporary paths;
        # do not access any physical device or run the partitioning commands.
        script = SCRIPT.read_text()
        start = script.index('    devices=$(lsblk -nrpo NAME "$DISK_PATH")')
        end = script.index('    done <<< "$devices"', start) + len('    done <<< "$devices"')
        block = script[start:end]
        bindir = self.base / 'bin'
        lsblk = bindir / 'lsblk'
        lsblk.write_text('#!/bin/sh\ncase "$1" in -nrpo) printf "%s\\n" "$TEST_DEVICE";; -dn) printf "%s" "$TEST_MOUNT";; *) exit 1;; esac\n')
        lsblk.chmod(0o755)
        swapon = bindir / 'swapon'
        swapon.write_text('#!/bin/sh\n[ "$*" = "--show --noheadings --raw --output NAME" ] || exit 2\ncase "$TEST_SWAP" in fail) exit 1;; active) printf "%s\\n" "$TEST_DEVICE";; esac\n')
        swapon.chmod(0o755)
        env = dict(self.env, TEST_DEVICE=str(self.image), DISK_PATH=str(self.image))
        for mode, mount, expected in (('none', '', ''), ('active', '', 'Device is used for swap'),
                                      ('fail', '', 'Cannot inspect swap devices'), ('none', '/mnt/test', 'Mounted device')):
            with self.subTest(mode=mode, mount=mount):
                result = subprocess.run(['/bin/bash', '-c', 'source "$1/functions.sh"\n' + block, '_', str(ROOT)],
                                        env=dict(env, TEST_SWAP=mode, TEST_MOUNT=mount), text=True, capture_output=True)
                self.assertEqual(result.returncode == 0, not expected, result.stdout + result.stderr)
                if expected: self.assertIn(expected, result.stderr)

if __name__ == '__main__':
    unittest.main()
