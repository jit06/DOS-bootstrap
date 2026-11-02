#!/usr/bin/env python3
"""Exercise shell and Python status messages with real terminals and pipes."""
import errno
import os
from pathlib import Path
import pty
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[1]

class ConsoleColorsTests(unittest.TestCase):
    def run_output(self, command, terminal, extra_env=None):
        env = dict(os.environ, TERM='xterm', PYTHONDONTWRITEBYTECODE='1')
        env['PYTHONPATH']=str(ROOT/'helpers')
        env.pop('NO_COLOR', None)
        env.update(extra_env or {})
        if not terminal:
            result = subprocess.run(command, cwd=ROOT, env=env, capture_output=True)
            return result.returncode, result.stdout + result.stderr
        master, slave = pty.openpty()
        try:
            process = subprocess.Popen(command, cwd=ROOT, env=env, stdout=slave, stderr=slave)
            os.close(slave); slave = -1
            output = bytearray()
            while True:
                try:
                    block = os.read(master, 65536)
                except OSError as exc:
                    if exc.errno == errno.EIO: break
                    raise
                if not block: break
                output.extend(block)
            return process.wait(), bytes(output)
        finally:
            os.close(master)
            if slave != -1: os.close(slave)

    def test_shell_and_python_colors_and_redirection(self):
        commands = [
            ['bash', '-c', 'source ./functions.sh; log_info info; log_success ok; log_error error'],
            ['python3', '-c', 'import package_common as p; p.info("info"); p.success("ok"); p.sys.argv=["x","install","--disk","/nonexistent","--config","/nonexistent","--base","/nonexistent","--cache","/nonexistent","--functions","/nonexistent","--install","missing"]; p.sys.exit(p.main())'],
        ]
        for command in commands:
            with self.subTest(command=command[0]):
                code, output = self.run_output(command, True)
                self.assertNotEqual(code, 0)
                for prefix in (b'\x1b[33m[INFO]\x1b[0m', b'\x1b[32m[ OK ]\x1b[0m', b'\x1b[31m[ERROR]\x1b[0m'):
                    self.assertIn(prefix, output)
                for terminal, env in ((False, {}), (True, {'NO_COLOR': ''}), (True, {'TERM': 'dumb'})):
                    _, output = self.run_output(command, terminal, env)
                    self.assertNotIn(b'\x1b[', output)
                    for prefix in (b'[INFO]', b'[ OK ]', b'[ERROR]'): self.assertIn(prefix, output)

    def test_each_stream_is_checked_independently(self):
        env = dict(os.environ, TERM='xterm', PYTHONDONTWRITEBYTECODE='1')
        env['PYTHONPATH']=str(ROOT/'helpers')
        env.pop('NO_COLOR', None)
        commands = [
            ['bash', '-c', 'source ./functions.sh; log_info info; log_error error'],
            ['python3', '-c', 'import package_common as p; p.info("info"); p.sys.argv=["x","install","--disk","/nonexistent","--config","/nonexistent","--base","/nonexistent","--cache","/nonexistent","--functions","/nonexistent","--install","missing"]; p.sys.exit(p.main())'],
        ]
        for command in commands:
            for tty_stdout in (True, False):
                with self.subTest(command=command[0],tty_stdout=tty_stdout):
                    master, slave = pty.openpty()
                    try:
                        result = subprocess.run(command,cwd=ROOT,env=env,
                            stdout=slave if tty_stdout else subprocess.PIPE,
                            stderr=subprocess.PIPE if tty_stdout else slave)
                        os.close(slave); slave = -1
                        tty_output = os.read(master,65536)
                        pipe_output = result.stderr if tty_stdout else result.stdout
                        self.assertIn(b'\x1b[',tty_output)
                        self.assertNotIn(b'\x1b[',pipe_output)
                    finally:
                        os.close(master)
                        if slave != -1: os.close(slave)

if __name__ == '__main__': unittest.main()
