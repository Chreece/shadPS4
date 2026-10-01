#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
# SPDX-License-Identifier: GPL-2.0-or-later
"""Exercise real Linux process/lock races with a disposable stand-in executable."""

import contextlib
import io
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

import session_guard as guard


@unittest.skipUnless(sys.platform == 'linux', 'Linux /proc and flock required')
class SessionGuardTests(unittest.TestCase):
    def setUp(self):
        if guard.cores():
            self.skipTest('Do not interfere with a real shadPS4 process')
        self.temporary = tempfile.TemporaryDirectory(prefix='shadps4 guard ')
        self.addCleanup(self.temporary.cleanup)
        self.home = Path(self.temporary.name)
        self.wrapper, self.helper, self.state = guard.paths(self.home)
        self.wrapper.parent.mkdir(parents=True)
        self.bin = self.home / 'bin'
        self.bin.mkdir()
        for name in ('bash', 'python3', 'sleep'):
            (self.bin / name).symlink_to(shutil.which(name))
        self.core = self.home / 'shadps4'
        shutil.copy2(shutil.which('sleep'), self.core)
        self.count = self.home / 'launches'
        self.arguments = self.home / 'arguments'
        self.inheritance = self.home / 'inheritance'
        inspection = (
            'import os; from pathlib import Path; '
            'paths=[]; '
            'paths=[os.readlink(p) for p in Path("/proc/self/fd").iterdir() if p.exists()]; '
            f'Path({str(self.inheritance)!r}).write_text(repr(paths)+"\\n"+'
            'str(os.environ.get("SHADPS4_GUARD_PARENT_PID")))'
        )
        self.original = (
            '#!/usr/bin/env bash\n'
            f'printf "start\\n" >> {shlex.quote(str(self.count))}\n'
            f'printf "%s\\n" "$@" > {shlex.quote(str(self.arguments))}\n'
            f'python3 -c {shlex.quote(inspection)}\n'
            'sleep 0.4\n'
            f'{shlex.quote(str(self.core))} 1.4\n'
        ).encode()
        self.wrapper.write_bytes(self.original)
        self.wrapper.chmod(0o750)
        with contextlib.redirect_stdout(io.StringIO()):
            guard.install(self.home)
        self.env = dict(os.environ, HOME=str(self.home), PATH=str(self.bin))
        for key in ('DISPLAY', 'WAYLAND_DISPLAY', 'SHADPS4_GUARD_PARENT_PID'):
            self.env.pop(key, None)
        self.children = []
        self.addCleanup(self.cleanup_children)

    def cleanup_children(self):
        # Only test-created processes/executables are eligible for cleanup.
        for child in self.children:
            if child.poll() is None:
                child.terminate()
        for core in guard.cores():
            if core['executable'] == str(self.core) and guard.alive(core):
                os.kill(core['pid'], 15)
        for child in self.children:
            child.wait(timeout=5)

    def launch(self, *arguments):
        child = subprocess.Popen(['bash', str(self.wrapper), *arguments], env=self.env,
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.children.append(child)
        return child

    def wait_for_core(self):
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            found = [core for core in guard.cores() if core['executable'] == str(self.core)]
            if found:
                return found[0]
            time.sleep(0.02)
        self.fail('Test executable did not start')

    def test_simultaneous_launches_start_once_and_preserve_arguments(self):
        arguments = ('game with spaces.ps4', 'literal $HOME; $(false)')
        children = [self.launch(*arguments) for _ in range(5)]
        for child in children:
            self.assertEqual(child.wait(timeout=10), 0)
        self.assertEqual(self.count.read_text(), 'start\n')
        self.assertEqual(self.arguments.read_text().splitlines(), list(arguments))
        inherited = self.inheritance.read_text()
        self.assertNotIn('session.lock', inherited)
        self.assertTrue(inherited.endswith('\nNone'))

    def test_existing_unguarded_game_is_reused(self):
        core = subprocess.Popen([str(self.core), '1.2'])
        self.children.append(core)
        self.wait_for_core()
        follower = self.launch('unused.ps4')
        time.sleep(0.2)
        self.assertIsNone(follower.poll())
        self.assertIsNone(core.poll())
        self.assertFalse(self.count.exists())
        self.assertEqual(follower.wait(timeout=5), 0)
        core.wait(timeout=5)

    def test_reconnect_after_supervisor_exit_does_not_start_again(self):
        first = self.launch()
        core = self.wait_for_core()
        first.terminate()
        first.wait(timeout=5)
        self.assertTrue(guard.alive(core))
        second = self.launch()
        self.assertEqual(second.wait(timeout=5), 0)
        self.assertEqual(self.count.read_text(), 'start\n')

    def test_completed_game_releases_lock_for_next_launch(self):
        self.assertEqual(self.launch().wait(timeout=5), 0)
        self.assertEqual(self.launch().wait(timeout=5), 0)
        self.assertEqual(self.count.read_text(), 'start\nstart\n')

    def test_install_is_idempotent_and_uninstall_preserves_original(self):
        installed = self.wrapper.read_bytes()
        with contextlib.redirect_stdout(io.StringIO()):
            guard.install(self.home)
            self.assertEqual(self.wrapper.read_bytes(), installed)
            guard.uninstall(self.home)
        self.assertEqual(self.wrapper.read_bytes(), self.original)
        self.assertEqual(self.wrapper.stat().st_mode & 0o777, 0o750)
        self.assertEqual(next(self.state.glob('shadps4-esde.before-*')).read_bytes(), self.original)


if __name__ == '__main__':
    unittest.main()
