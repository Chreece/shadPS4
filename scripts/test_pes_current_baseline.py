# SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
# SPDX-License-Identifier: GPL-2.0-or-later

import contextlib
import io
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import Mock, patch

import pes_current_baseline as baseline
import run_pes_frame_test as runner
import trace_video_progress as trace

MANIFEST = {'schema': 1,
            'upstream': {'repository': 'shadps4-emu/shadPS4', 'revision': 'a' * 40},
            'pending_fixes': [{'number': 5228, 'head': 'b' * 40}]}


class CurrentBaselineTests(unittest.TestCase):
    def test_remote_main_and_pending_heads_must_all_match(self):
        for main, pr, passed in [('a' * 40, 'b' * 40, True),
                                 ('c' * 40, 'b' * 40, False),
                                 ('a' * 40, 'c' * 40, False)]:
            with self.subTest(main=main, pr=pr):
                result = subprocess.CompletedProcess([], 0,
                    f'{main}\trefs/heads/main\n{pr}\trefs/pull/5228/head\n', '')
                with patch.object(baseline.subprocess, 'run', return_value=result) as run, \
                        contextlib.redirect_stdout(io.StringIO()):
                    if passed:
                        baseline.require_current(MANIFEST)
                    else:
                        with self.assertRaisesRegex(RuntimeError, 'outdated'):
                            baseline.require_current(MANIFEST)
                    self.assertEqual(run.call_args.kwargs['timeout'], 45)

    def test_stale_baseline_stops_before_validation_or_game(self):
        with patch.object(runner.baseline, 'verify_installed', side_effect=RuntimeError('outdated')), \
                patch.object(runner.validate, 'launch') as validate, \
                patch.object(runner.startup, 'run') as game:
            with self.assertRaisesRegex(RuntimeError, 'outdated'):
                runner.run_validated(Path('/unused'))
            validate.assert_not_called()
            game.assert_not_called()

    def fixture(self, home):
        root = home / 'Applications/shadps4'
        previous = root / 'releases/old/shadps4'
        previous.parent.mkdir(parents=True)
        previous.write_bytes(b'previous binary')
        core = root / 'shadps4'
        core.symlink_to(previous)
        wrapper = home / '.local/bin/shadps4-esde'
        wrapper.parent.mkdir(parents=True)
        wrapper.write_bytes(b'unchanged launcher')
        config = home / '.local/share/shadPS4/config.json'
        config.parent.mkdir(parents=True)
        config.write_text('{"Audio":{"channels":8}}')
        built = home / 'compiled-binary'
        built.write_bytes(b'new compiled binary')
        return core, previous, config, built

    def test_success_keeps_old_binary_and_settings_and_verifies_next_test(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            core, previous, config, built = self.fixture(home)
            before = baseline.preserved_files(home)
            with patch.object(baseline, 'require_current') as current, \
                    patch.object(baseline, 'require_no_game'), \
                    patch.object(trace, 'REVISION'), patch.object(trace, 'BINARY_SHA256'), \
                    patch.object(trace, 'BASELINE_METADATA', None, create=True), \
                    contextlib.redirect_stdout(io.StringIO()):
                record = baseline.prepare(home, 'c' * 40, 'integration', MANIFEST, Mock(return_value=built))
                self.assertEqual(baseline.verify_installed(home), record)
                self.assertEqual(trace.REVISION, 'c' * 40)
                self.assertEqual(trace.BINARY_SHA256, baseline.digest(built))
                self.assertEqual(trace.BASELINE_METADATA['manifest'], MANIFEST)
            self.assertEqual(current.call_count, 3)
            self.assertEqual(previous.read_bytes(), b'previous binary')
            self.assertEqual(core.read_bytes(), built.read_bytes())
            self.assertEqual(baseline.preserved_files(home), before)

    def test_failed_build_or_postbuild_freshness_keeps_selection(self):
        for scenario in ('build', 'freshness', 'settings'):
            with self.subTest(scenario=scenario), tempfile.TemporaryDirectory() as directory:
                home = Path(directory)
                core, previous, config, built = self.fixture(home)
                def build(*args):
                    if scenario == 'build':
                        raise RuntimeError('build failed')
                    if scenario == 'settings':
                        config.write_text('{"changed":true}')
                    return built
                checks = [None, RuntimeError('outdated')] if scenario == 'freshness' else None
                with patch.object(baseline, 'require_current', side_effect=checks), \
                        patch.object(baseline, 'require_no_game'), \
                        contextlib.redirect_stdout(io.StringIO()):
                    with self.assertRaises(RuntimeError):
                        baseline.prepare(home, 'c' * 40, 'integration', MANIFEST, build)
                self.assertEqual(core.resolve(), previous)
                self.assertFalse((home / baseline.STATE).exists())

    def test_state_write_failure_rolls_back_selection(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            core, previous, config, built = self.fixture(home)
            write = baseline.atomic_json
            def fail_state(path, value):
                if path == home / baseline.STATE:
                    raise OSError('disk full')
                write(path, value)
            with patch.object(baseline, 'require_current'), \
                    patch.object(baseline, 'require_no_game'), \
                    patch.object(baseline, 'atomic_json', side_effect=fail_state):
                with self.assertRaisesRegex(OSError, 'disk full'):
                    baseline.prepare(home, 'c' * 40, 'integration', MANIFEST, Mock(return_value=built))
            self.assertEqual(core.resolve(), previous)
            self.assertEqual(previous.read_bytes(), b'previous binary')

    def test_changed_binary_cannot_pass_as_verified_baseline(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            core, previous, config, built = self.fixture(home)
            with patch.object(baseline, 'require_current'), \
                    patch.object(baseline, 'require_no_game'), \
                    contextlib.redirect_stdout(io.StringIO()):
                baseline.prepare(home, 'c' * 40, 'integration', MANIFEST, Mock(return_value=built))
                core.write_bytes(b'changed')
                with self.assertRaisesRegex(RuntimeError, 'differs'):
                    baseline.verify_installed(home)

    def test_same_revision_legacy_binary_is_preserved_when_rebuild_differs(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            core, previous, config, built = self.fixture(home)
            legacy = core.parent / 'releases' / ('baseline-' + 'c' * 12) / 'shadps4'
            legacy.parent.mkdir()
            legacy.write_bytes(b'first build of same revision')
            core.unlink()
            core.symlink_to(legacy)
            before = baseline.preserved_files(home)
            with patch.object(baseline, 'require_current'), patch.object(baseline, 'require_no_game'), \
                    contextlib.redirect_stdout(io.StringIO()):
                record = baseline.prepare(home, 'c' * 40, 'integration', MANIFEST, Mock(return_value=built))
            self.assertNotEqual(core.resolve(), legacy)
            self.assertEqual(core.read_bytes(), built.read_bytes())
            self.assertEqual(legacy.read_bytes(), b'first build of same revision')
            self.assertEqual(record['previous_binary'], str(legacy))
            self.assertEqual(record['previous_sha256'], baseline.digest(legacy))
            self.assertEqual(baseline.preserved_files(home), before)

    def test_rebuilt_content_gets_separate_release_and_identical_content_is_reused(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            core, previous, config, built = self.fixture(home)
            with patch.object(baseline, 'require_current'), patch.object(baseline, 'require_no_game'), \
                    contextlib.redirect_stdout(io.StringIO()):
                first = baseline.prepare(home, 'c' * 40, 'integration', MANIFEST, Mock(return_value=built))
                first_path = Path(first['binary'])
                built.write_bytes(b'second build of the same source')
                second = baseline.prepare(home, 'c' * 40, 'integration', MANIFEST, Mock(return_value=built))
                second_path = Path(second['binary'])
                before = second_path.stat()
                third = baseline.prepare(home, 'c' * 40, 'integration', MANIFEST, Mock(return_value=built))
            self.assertNotEqual(first_path, second_path)
            self.assertEqual(first_path.read_bytes(), b'new compiled binary')
            self.assertEqual(second_path.read_bytes(), built.read_bytes())
            self.assertEqual(second['previous_binary'], str(first_path))
            self.assertEqual(third['binary'], second['binary'])
            self.assertEqual(second_path.stat().st_ino, before.st_ino)
            self.assertEqual(second_path.stat().st_mtime_ns, before.st_mtime_ns)
            self.assertEqual(previous.read_bytes(), b'previous binary')

    def test_content_addressed_release_conflict_is_still_rejected(self):
        for symlink in (False, True):
            with self.subTest(symlink=symlink), tempfile.TemporaryDirectory() as directory:
                home = Path(directory)
                core, previous, config, built = self.fixture(home)
                dest = core.parent / 'releases' / ('baseline-' + 'c' * 12 + '-' + baseline.digest(built))
                dest.mkdir()
                target = dest / 'shadps4'
                if symlink:
                    target.symlink_to(previous)
                else:
                    target.write_bytes(b'unexpected existing content')
                with patch.object(baseline, 'require_current'), patch.object(baseline, 'require_no_game'):
                    with self.assertRaisesRegex(RuntimeError, 'different binary'):
                        baseline.prepare(home, 'c' * 40, 'integration', MANIFEST, Mock(return_value=built))
                self.assertEqual(core.resolve(), previous)
                self.assertEqual(previous.read_bytes(), b'previous binary')
                self.assertEqual(target.is_symlink(), symlink)
                if not symlink:
                    self.assertEqual(target.read_bytes(), b'unexpected existing content')


if __name__ == '__main__':
    unittest.main()
