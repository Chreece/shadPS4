# SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
# SPDX-License-Identifier: GPL-2.0-or-later
import json
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import threading
import unittest
from unittest.mock import patch

import collect_graphics_evidence as capture


START = 'GRAPHICS_DIAG ms=0 event=enabled count=1 fixture=1\n'
GROWTH = ('GRAPHICS_DIAG ms=500 event=depth-growth count=1 address=2b4000000 '
          'src-layers=5 dst-layers=6 src-mips=1 dst-mips=1 src-samples=1 dst-samples=1 '
          'upload-recorded=1 copy-returned=1\n')
LAUNCHER = ('#!/usr/bin/env bash\n# SHADPS4_SESSION_GUARD_V1\n'
            '# SHADPS4_DEFAULT_MAIN_V1\n'
            'unset SHADPS4_GRAPHICS_DIAGNOSTICS\n'
            'exec /some/core --game "$game" --fullscreen true\n').encode()


class CaptureTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.home = Path(self.temp.name)
        self.addCleanup(self.temp.cleanup)

    def analyze(self, text):
        log = self.home / 'emulator.log'
        log.write_text(text)
        return capture.analyze(log)

    def test_quiet_log_is_not_success(self):
        report = self.analyze('Game started\n')
        self.assertFalse(report['graphics_diagnostics_started'])
        self.assertEqual(report['depth_growth_result'], 'NOT_OBSERVED')
        self.assertEqual(report['visual_result'], 'UNVERIFIED_REQUIRES_USER_FEEDBACK')

    def test_success_marker_and_errors_are_independent(self):
        report = self.analyze(START + GROWTH + '[Render_Vulkan] <Error> VUID-test-1234\n')
        self.assertTrue(report['graphics_diagnostics_started'])
        self.assertEqual(report['depth_growth_result'], 'INITIALIZATION_PATH_OBSERVED')
        self.assertEqual(report['valid_initialization_records'], 1)
        self.assertEqual(report['renderer_error_records'], 1)
        self.assertEqual(report['validation_vuids'], {'VUID-test-1234': 1})

    def test_failed_and_malformed_markers(self):
        failed = GROWTH.replace('event=depth-growth ', 'event=depth-growth-uninitialized ')
        self.assertEqual(self.analyze(START + failed)['depth_growth_result'],
                         'UNINITIALIZED_PATH_OBSERVED')
        self.assertEqual(self.analyze(START + GROWTH.replace('upload-recorded=1',
                         'upload-recorded=0'))['depth_growth_result'], 'INVALID_EVIDENCE_RECORD')

    def test_launcher_quoting_and_conflict_preservation(self):
        modified = capture.instrument(LAUNCHER, self.home / "one' script.py", self.home / 'capture')
        subprocess.run(['bash', '-n'], input=modified, check=True)
        self.assertIn(b'unset SHADPS4_GRAPHICS_DIAGNOSTICS\n', modified)
        wrapper = self.home / 'wrapper'
        wrapper.write_bytes(modified)
        self.assertTrue(capture.restore(wrapper, modified, LAUNCHER, 0o700))
        self.assertEqual(wrapper.read_bytes(), LAUNCHER)
        wrapper.write_bytes(b'independent edit')
        self.assertFalse(capture.restore(wrapper, modified, LAUNCHER, 0o700))
        self.assertEqual(wrapper.read_bytes(), b'independent edit')

    def test_installed_revision_and_hash_must_match(self):
        core = self.home / 'Applications/shadps4/shadps4'
        core.parent.mkdir(parents=True)
        core.write_bytes(b'fixture-binary')
        state = self.home / '.local/state/shadps4-default-main/install-test/state.json'
        state.parent.mkdir(parents=True)
        record = {'revision': 'a' * 40, 'home': str(self.home), 'binary': str(core),
                  'binary_sha256': capture.digest(core)}
        state.write_text(json.dumps(record))
        self.assertEqual(capture.selected_install(self.home, 'a' * 40)['binary'], str(core))
        with self.assertRaises(RuntimeError):
            capture.selected_install(self.home, 'b' * 40)
        core.write_bytes(b'changed')
        with self.assertRaises(RuntimeError):
            capture.selected_install(self.home, 'a' * 40)

    def run_session(self, identity_error=False):
        wrapper = self.home / '.local/bin/shadps4-esde'
        wrapper.parent.mkdir(parents=True)
        wrapper.write_bytes(LAUNCHER)
        core = str(Path(sys.executable).resolve())
        selected = {'revision': 'a' * 40, 'binary': core, 'binary_sha256': capture.digest(core)}
        identity = ({'identity_error': 'fixture: proc access denied'} if identity_error else
                    {'running_executable': core, 'running_binary_sha256': selected['binary_sha256']})
        workers = []
        failures = []
        real_write = capture.atomic_write

        def launch(work):
            try:
                code = ('import os,time; assert os.environ["SHADPS4_GRAPHICS_DIAGNOSTICS"]=="1"; '
                        'print(' + repr(START + GROWTH) + ',flush=True); time.sleep(0.2)')
                capture.run_game(work, [core, '-c', code])
            except BaseException as error:
                failures.append(error)

        def on_write(path, content, mode):
            real_write(path, content, mode)
            if path == wrapper and content != LAUNCHER:
                work = next(self.home.glob('shadps4-graphics-evidence-*'))
                worker = threading.Thread(target=launch, args=(work,))
                workers.append(worker)
                worker.start()

        with patch.object(capture, 'games', return_value={}), \
                patch.object(capture, 'selected_install', return_value=selected), \
                patch.object(capture, 'running_identity', return_value=identity), \
                patch.object(capture, 'atomic_write', side_effect=on_write):
            if identity_error:
                with self.assertRaises(RuntimeError):
                    capture.collect(self.home, 'a' * 40)
            else:
                capture.collect(self.home, 'a' * 40)
        for worker in workers:
            worker.join(timeout=5)
        self.assertEqual(failures, [])
        self.assertEqual(wrapper.read_bytes(), LAUNCHER)
        archive = next(self.home.glob('*.tar.gz'))
        with tarfile.open(archive) as stream:
            member = next(m for m in stream.getmembers() if m.name.endswith('/summary.json'))
            report = json.load(stream.extractfile(member))
        self.assertEqual(report['capture_complete'], not identity_error)
        self.assertEqual(report['exact_running_binary_verified'], not identity_error)
        self.assertTrue(report['launcher_restored'])
        self.assertEqual(report['session_exit'], 'NORMAL')
        self.assertGreaterEqual(report['report_created_unix'], report['exit']['ended_unix'])
        self.assertEqual(report['depth_growth_result'], 'INITIALIZATION_PATH_OBSERVED')
        self.assertEqual(report['visual_result'], 'UNVERIFIED_REQUIRES_USER_FEEDBACK')

    def test_real_child_launch_to_exit_and_archive(self):
        self.run_session()

    def test_inaccessible_running_identity_keeps_capture_incomplete(self):
        self.run_session(identity_error=True)


if __name__ == '__main__':
    unittest.main()
