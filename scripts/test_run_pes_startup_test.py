# SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
# SPDX-License-Identifier: GPL-2.0-or-later
"""Launcher checks use disposable processes; they do not emulate PES or validate GDB."""

import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch

import run_pes_startup_test as startup


class DesktopTests(unittest.TestCase):
    def test_esde_environment_overrides_ssh_forwarding_without_copying_secrets(self):
        env, source = startup.choose_desktop([
            (1, 'sunshine:100', {'DISPLAY': ':2'}),
            (0, 'es-de:200', {'DISPLAY': ':0', 'XAUTHORITY': '/session/Xauthority'}),
        ], {'DISPLAY': 'localhost:10.0', 'HOME': '/unchanged', 'PATH': '/bin'})
        self.assertEqual(source, 'es-de:200')
        self.assertEqual(env['DISPLAY'], ':0')
        self.assertEqual(env['HOME'], '/unchanged')

    def test_ambiguous_or_ssh_only_display_is_rejected(self):
        with self.assertRaisesRegex(RuntimeError, 'Multiple desktop'):
            startup.choose_desktop([(0, 'a', {'DISPLAY': ':0'}),
                                    (0, 'b', {'DISPLAY': ':1'})], {})
        with self.assertRaisesRegex(RuntimeError, 'local desktop'):
            startup.choose_desktop([], {'DISPLAY': 'localhost:10.0'})

    def test_existing_emulator_is_preserved(self):
        with patch.object(startup, 'running_emulators', return_value=[42]):
            with self.assertRaisesRegex(RuntimeError, 'Close the current game normally'):
                startup.require_idle()


class LauncherTests(unittest.TestCase):
    def test_background_child_captures_first_output_and_preserves_parent_session(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            wrapper = home / 'fixture-wrapper'
            wrapper.write_text('#!' + sys.executable + '\n'
                               'import json,os,sys\n'
                               'print(json.dumps({"sid":os.getsid(0), "pid":os.getpid(),'
                               '"argument":sys.argv[1], "input":sys.stdin.read()}),flush=True)\n')
            wrapper.chmod(0o700)
            before = (os.getsid(0), os.getpgrp())
            with (home / 'console').open('wb') as log:
                child = startup.launch_game(wrapper, dict(os.environ), home, log)
            self.assertEqual(child.wait(timeout=5), 0)
            data = json.loads((home / 'console').read_text())
            self.assertEqual(data['sid'], data['pid'])
            self.assertNotEqual(data['sid'], before[0])
            self.assertEqual(data['argument'], 'CUSA18676')
            self.assertEqual(data['input'], '')
            self.assertEqual((os.getsid(0), os.getpgrp()), before)

    def test_wrong_install_is_rejected_before_launch(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            binary = home / 'Applications/shadps4/shadps4'
            binary.parent.mkdir(parents=True)
            binary.write_bytes(b'wrong binary')
            with self.assertRaisesRegex(RuntimeError, 'pinned build'):
                startup.selected_launch(home)
            self.assertEqual(binary.read_bytes(), b'wrong binary')

    def test_boot_failure_preserves_log_and_settings_in_archive(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            wrapper = home / 'fixture-wrapper'
            wrapper.write_text('unchanged launcher')
            sha = startup.checksum(wrapper)
            config = {'Audio': {'output': '7.1'}}

            def launch(wrapper, env, home, log):
                log.write(b'first startup message\nfixture boot failed\n')
                log.flush()
                return subprocess.Popen([sys.executable, '-c', 'pass'],
                                        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                        stderr=subprocess.DEVNULL, start_new_session=True)

            def await_game(child):
                child.wait(timeout=5)
                raise RuntimeError('fixture startup failure')

            with patch.object(startup, 'require_idle'), \
                    patch.object(startup, 'selected_launch', return_value=(wrapper, sha)), \
                    patch.object(startup, 'desktop_environment', return_value=({}, 'fixture')), \
                    patch.object(startup, 'debugger_prefix', return_value=[]), \
                    patch.object(startup, 'settings', return_value=config), \
                    patch.object(startup, 'launch_game', side_effect=launch), \
                    patch.object(startup, 'await_game', side_effect=await_game), \
                    contextlib.redirect_stdout(io.StringIO()):
                startup.run(home)
            archive = next(home.glob('*.tar.gz'))
            with tarfile.open(archive) as data:
                result = json.load(data.extractfile('startup.json'))
                self.assertEqual(result['errors'], ['RuntimeError: fixture startup failure'])
                self.assertTrue(result['settings_unchanged'])
                self.assertTrue(result['launcher_unchanged'])
                self.assertIn(b'fixture boot failed', data.extractfile('console.0.log').read())
                self.assertNotIn('emulator.log', data.getnames())
            self.assertEqual(wrapper.read_text(), 'unchanged launcher')


if __name__ == '__main__':
    unittest.main()
