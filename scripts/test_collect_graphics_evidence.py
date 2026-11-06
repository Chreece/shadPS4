# SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
# SPDX-License-Identifier: GPL-2.0-or-later
import json
import os
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

    def test_waiting_does_not_consume_the_game_capture_limit(self):
        self.assertIsNone(capture.timeout_reason(7200, None, 0, 1800))
        self.assertIsNone(capture.timeout_reason(7500, 300, 0, 1800))
        self.assertIsNotNone(capture.timeout_reason(9000, 1800, 0, 1800))
        self.assertIsNotNone(capture.timeout_reason(180, None, 180, 1800))
        self.assertIsNone(capture.timeout_reason(100000, 90000, 0, 0))

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

    def log_fixture(self, content=b'previous launch: old error\n'):
        path = self.home / 'shared.log'
        path.write_bytes(content)
        return path, self.home / 'captured.log', capture.snapshot_renderer_log(path)

    def test_appended_log_excludes_all_historical_bytes(self):
        path, dest, before = self.log_fixture()
        with path.open('ab') as out:
            out.write(b'current launch only\n')
        result = capture.capture_renderer_log(path, dest, before)
        self.assertEqual(result['status'], 'captured')
        self.assertEqual(result['boundary'], 'verified_append')
        self.assertEqual(dest.read_bytes(), b'current launch only\n')
        self.assertEqual(result['sha256'], capture.digest(dest))
        self.assertEqual(path.read_bytes(), b'previous launch: old error\ncurrent launch only\n')

    def test_touching_old_log_does_not_make_it_fresh(self):
        path, dest, before = self.log_fixture()
        os.utime(path, ns=(before['mtime_ns'], before['mtime_ns'] + 2_000_000_000))
        result = capture.capture_renderer_log(path, dest, before)
        self.assertEqual(result['status'], 'unchanged')
        self.assertFalse(dest.exists())

    def test_truncated_or_rewritten_log_is_not_claimed_as_fresh(self):
        for content, reason in [(b'short\n', 'truncated'),
                                (b'X' * len(b'previous launch: old error\n'), 'rewritten_prefix'),
                                (b'replacement launch\n' * 10, 'rewritten_prefix')]:
            with self.subTest(reason=reason, length=len(content)):
                path, dest, before = self.log_fixture()
                path.write_bytes(content)
                result = capture.capture_renderer_log(path, dest, before)
                self.assertEqual(result['reason'], reason)
                self.assertFalse(dest.exists())
                self.assertEqual(path.read_bytes(), content)

    def test_rotation_is_omitted_even_when_replacement_has_the_same_prefix(self):
        path, dest, before = self.log_fixture()
        rotated = path.with_suffix('.old')
        path.rename(rotated)
        path.write_bytes(rotated.read_bytes() + b'other session\n')
        result = capture.capture_renderer_log(path, dest, before)
        self.assertEqual(result['reason'], 'rotated_or_replaced')
        self.assertFalse(dest.exists())
        self.assertEqual(rotated.read_bytes(), b'previous launch: old error\n')

    def test_new_file_captures_the_whole_session(self):
        path = self.home / 'new.log'
        dest = self.home / 'captured.log'
        before = capture.snapshot_renderer_log(path)
        path.write_bytes(b'new session\n')
        result = capture.capture_renderer_log(path, dest, before)
        self.assertEqual(result['boundary'], 'new_file')
        self.assertEqual(dest.read_bytes(), b'new session\n')

    def test_missing_or_unreadable_boundary_never_includes_an_old_file(self):
        path, dest, before = self.log_fixture()
        path.unlink()
        self.assertEqual(capture.capture_renderer_log(path, dest, before)['reason'],
                         'missing_at_exit')
        path.write_bytes(b'unrelated old file\n')
        result = capture.capture_renderer_log(path, dest, {'status': 'omitted'})
        self.assertEqual(result['reason'], 'no_verified_start_boundary')
        self.assertFalse(dest.exists())

    def test_size_limit_preserves_source_and_does_not_write_partial_capture(self):
        path, dest, before = self.log_fixture(b'old\n')
        with path.open('ab') as out:
            out.write(b'new\n' * 10)
        with patch.object(capture, 'MAX_RENDERER_LOG_BYTES', 8):
            result = capture.capture_renderer_log(path, dest, before)
            self.assertEqual(result['reason'], 'size_limit')
            self.assertEqual(capture.snapshot_renderer_log(path)['reason'], 'size_limit')
        self.assertFalse(dest.exists())
        self.assertEqual(path.read_bytes(), b'old\n' + b'new\n' * 10)

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
        log_root = self.home / '.local/share/shadPS4/log'
        log_root.mkdir(parents=True)
        startup = log_root / 'shadps4.log'
        game_log = log_root / 'shad_log.txt'
        startup.write_text('previous launch: Game ID or file path not found\n')
        game_log.write_text('previous renderer error\n')
        current_startup = 'THIS SESSION startup\n'

        def launch(work):
            try:
                code = ('import os,time; assert os.environ["SHADPS4_GRAPHICS_DIAGNOSTICS"]=="1"; '
                        'from pathlib import Path; '
                        'f=Path(' + repr(str(startup)) + ').open("a"); '
                        'f.write(' + repr(current_startup) + '); f.close(); '
                        'Path(' + repr(str(game_log)) + ').write_text(' + repr(START + GROWTH) + '); '
                        'print(' + repr(START + GROWTH) + ',flush=True); time.sleep(0.2)')
                capture.run_game(work, [core, '-c', code])
                # Collection/archive happens later; it must use the worker's
                # frozen copy, not pick up another launch from this live file.
                with startup.open('a') as out:
                    out.write('FOLLOWING SESSION error\n')
            except BaseException as error:
                failures.append(error)

        def on_write(path, content, mode):
            real_write(path, content, mode)
            if path == wrapper and content != LAUNCHER:
                work = next(self.home.glob('shadps4-graphics-evidence-*'))
                with startup.open('a') as out:
                    out.write('AFTER ARMING but before this launch\n')
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
            current = next(m for m in stream.getmembers()
                           if m.name.endswith('/renderer-shadps4.log'))
            self.assertEqual(stream.extractfile(current).read(), current_startup.encode())
            self.assertFalse(any(m.name.endswith('/renderer-shad_log.txt')
                                 for m in stream.getmembers()))
            direct = next(m for m in stream.getmembers() if m.name.endswith('/emulator.log'))
            self.assertIn(GROWTH.encode(), stream.extractfile(direct).read())
        self.assertEqual(report['capture_complete'], not identity_error)
        self.assertEqual(report['exact_running_binary_verified'], not identity_error)
        self.assertTrue(report['launcher_restored'])
        self.assertEqual(report['session_exit'], 'NORMAL')
        self.assertGreaterEqual(report['report_created_unix'], report['exit']['ended_unix'])
        self.assertEqual(report['depth_growth_result'], 'INITIALIZATION_PATH_OBSERVED')
        self.assertEqual(report['visual_result'], 'UNVERIFIED_REQUIRES_USER_FEEDBACK')
        self.assertEqual(report['fresh_renderer_logs'], ['shadps4.log'])
        self.assertEqual(report['renderer_log_capture']['shad_log.txt']['reason'],
                         'rewritten_prefix')

    def test_real_child_launch_to_exit_and_archive(self):
        self.run_session()

    def test_inaccessible_running_identity_keeps_capture_incomplete(self):
        self.run_session(identity_error=True)

    def test_interrupted_capture_restores_launcher_without_copying_shared_logs(self):
        wrapper = self.home / '.local/bin/shadps4-esde'
        wrapper.parent.mkdir(parents=True)
        wrapper.write_bytes(LAUNCHER)
        startup = self.home / '.local/share/shadPS4/log/shadps4.log'
        startup.parent.mkdir(parents=True)
        startup.write_text('old error must not appear in this report\n')
        selected = {'revision': 'a' * 40, 'binary': '/fixture/shadps4',
                    'binary_sha256': 'fixture'}
        real_write = capture.atomic_write

        def on_write(path, content, mode):
            real_write(path, content, mode)
            if path == wrapper and content != LAUNCHER:
                work = next(self.home.glob('shadps4-graphics-evidence-*'))
                (work / 'emulator.log').write_text(START)
                capture.write_json(work / 'process.json', {
                    'pid': os.getpid(), 'started_unix': 0,
                    'running_executable': selected['binary'],
                    'running_binary_sha256': selected['binary_sha256']})

        with patch.object(capture, 'games', return_value={}), \
                patch.object(capture, 'selected_install', return_value=selected), \
                patch.object(capture, 'atomic_write', side_effect=on_write), \
                patch.object(capture, 'timeout_reason', return_value='fixture: session limit'):
            with self.assertRaisesRegex(RuntimeError, 'Capture incomplete'):
                capture.collect(self.home, 'a' * 40)
        self.assertEqual(wrapper.read_bytes(), LAUNCHER)
        self.assertEqual(startup.read_text(), 'old error must not appear in this report\n')
        with tarfile.open(next(self.home.glob('*.tar.gz'))) as stream:
            members = stream.getmembers()
            report = json.load(stream.extractfile(
                next(m for m in members if m.name.endswith('/summary.json'))))
            self.assertFalse(any('/renderer-' in m.name for m in members))
        self.assertFalse(report['capture_complete'])
        self.assertEqual(report['session_exit'], 'NOT_OBSERVED')
        self.assertEqual(report['fresh_renderer_logs'], [])
        self.assertEqual(report['renderer_log_capture'], {})


if __name__ == '__main__':
    unittest.main()
