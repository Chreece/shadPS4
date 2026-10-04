# SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
# SPDX-License-Identifier: GPL-2.0-or-later

import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from types import SimpleNamespace

import pes_graphics_launch as native
import run_pes_packet_test as packets


class NativeLaunchTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.home = Path(self.temporary.name)
        self.work = self.home / 'capture'
        self.work.mkdir()
        self.binary = self.home / 'Applications/shadps4/shadps4'
        self.binary.parent.mkdir(parents=True)
        self.binary.write_text('#!' + sys.executable + '\nimport json,os\n'
                               'print(json.dumps({"flag":os.getenv("SHADPS4_GRAPHICS_DIAGNOSTICS"),'
                               '"startup":os.getenv("SHADPS4_STARTUP_DIAGNOSTICS"),'
                               '"sid":os.getsid(0)}))\n')
        self.binary.chmod(0o700)
        self.wrapper = self.home / 'launcher'
        self.original = (f'#!/bin/sh\n# SHADPS4_SESSION_GUARD_V1\n'
                         f'# SHADPS4_DEFAULT_MAIN_V1\n'
                         f'unset SHADPS4_GRAPHICS_DIAGNOSTICS SHADPS4_STARTUP_DIAGNOSTICS\ngame="$1"\n'
                         f'exec {self.binary} --game "$game" --fullscreen true\n').encode()
        self.wrapper.write_bytes(self.original)
        self.wrapper.chmod(0o750)
        self.sha = hashlib.sha256(self.original).hexdigest()

    def test_real_child_gets_flag_after_unset_and_launcher_is_restored(self):
        with native.enabled_launch(self.home, self.wrapper, self.sha, self.work):
            result = subprocess.run([self.wrapper, 'CUSA18676'], start_new_session=True,
                                    capture_output=True, text=True, check=True, timeout=3)
            actual = json.loads(result.stdout)
            self.assertEqual(actual['flag'], '1')
            self.assertEqual(actual['startup'], '1')
            self.assertNotEqual(actual['sid'], os.getsid(0))
        self.assertEqual(self.wrapper.read_bytes(), self.original)
        self.assertEqual(stat.S_IMODE(self.wrapper.stat().st_mode), 0o750)

    def test_environment_verification_rejects_missing_flag_and_reused_pid(self):
        proc = self.work / '123'
        proc.mkdir()
        (proc / 'stat').write_text('123 (fixture) ' + ' '.join(['0'] * 19 + ['456']))
        (proc / 'environ').write_bytes(b'SHADPS4_GRAPHICS_DIAGNOSTICS=1\0SHADPS4_STARTUP_DIAGNOSTICS=1\0')
        native.verify_environment({'pid': 123, 'start_ticks': '456'}, self.work)
        with self.assertRaisesRegex(RuntimeError, 'identity changed'):
            native.verify_environment({'pid': 123, 'start_ticks': '455'}, self.work)
        (proc / 'environ').write_bytes(b'SHADPS4_GRAPHICS_DIAGNOSTICS=1\0')
        with self.assertRaisesRegex(RuntimeError, 'did not reach'):
            native.verify_environment({'pid': 123, 'start_ticks': '456'}, self.work)

    def test_boot_failure_restores_exact_launcher(self):
        with self.assertRaisesRegex(RuntimeError, 'boot failed'):
            with native.enabled_launch(self.home, self.wrapper, self.sha, self.work):
                raise RuntimeError('boot failed')
        self.assertEqual(self.wrapper.read_bytes(), self.original)

    def test_gpu_injection_is_quoted_scoped_to_child_and_restored(self):
        collector = SimpleNamespace(
            environment={'PES_FIXTURE_VALUE': 'path with spaces'},
            command=['env', 'PES_FIXTURE_CHILD=one frame'])
        self.binary.write_text('#!' + sys.executable + '\nimport json,os,sys\n'
            'print(json.dumps([os.getenv("PES_FIXTURE_VALUE"),'
            'os.getenv("PES_FIXTURE_CHILD"),sys.argv[1:]]))\n')
        with self.assertRaisesRegex(RuntimeError, 'after capture'):
            with native.enabled_launch(self.home, self.wrapper, self.sha, self.work,
                                       gpu_collector=collector):
                result = subprocess.run([self.wrapper, 'CUSA18676'], capture_output=True,
                                        text=True, check=True, timeout=3)
                self.assertEqual(json.loads(result.stdout), ['path with spaces', 'one frame',
                    ['--game', 'CUSA18676', '--fullscreen', 'true']])
                raise RuntimeError('after capture')
        self.assertEqual(self.wrapper.read_bytes(), self.original)
        self.assertNotIn('PES_FIXTURE_CHILD', os.environ)

    def test_concurrent_launcher_edit_is_preserved(self):
        with self.assertRaisesRegex(RuntimeError, 'edited externally'):
            with native.enabled_launch(self.home, self.wrapper, self.sha, self.work):
                self.wrapper.write_bytes(b'external edit')
        self.assertEqual(self.wrapper.read_bytes(), b'external edit')
        self.assertEqual((self.work / 'launcher.original').read_bytes(), self.original)

    def test_marker_absence_and_sample_lower_bounds(self):
        path = self.work / 'console.0.log'
        path.write_text('no markers\n')
        self.assertFalse(native.analyze([path])['started'])
        path.write_text('GRAPHICS_DIAG ms=0 event=enabled count=1 started\n'
                        'GRAPHICS_DIAG ms=40 event=query-result count=8 data\n'
                        'GRAPHICS_DIAG ms=70 event=query-result count=16 data\n')
        result = native.analyze([path])
        self.assertTrue(result['started'])
        self.assertEqual(result['event_count_lower_bounds']['query-result'], 16)

    def test_packet_runner_refuses_a_capture_without_native_marker(self):
        result = dict(errors=[], frames_capture_passed=True, screenshots_complete=True,
                      settings_unchanged=True, launcher_unchanged=True)
        with patch.object(packets.startup, 'require_idle'), \
                patch.object(packets.baseline, 'verify_installed'), \
                patch.object(packets.startup, 'run', return_value=result) as run:
            with self.assertRaisesRegex(RuntimeError, 'incomplete'):
                packets.run(self.home)
            run.assert_called_once_with(self.home, profile='frames', screenshots=True, graphics=True)


if __name__ == '__main__':
    unittest.main()
