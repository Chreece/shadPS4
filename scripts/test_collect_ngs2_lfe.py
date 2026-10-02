# SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
# SPDX-License-Identifier: GPL-2.0-or-later
import contextlib
import io
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock
import collect_ngs2_lfe as capture


class CaptureTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.home = Path(self.directory.name)
        self.wrapper = self.home / '.local/bin/shadps4-esde'
        self.wrapper.parent.mkdir(parents=True)
        self.original = (b'#!/usr/bin/env bash\n# SHADPS4_SESSION_GUARD_V1\n'
                         b'if true; then :; fi\n# END SHADPS4_SESSION_GUARD_V1\n'
                         b'exec /existing/core --game "$game" --fullscreen true\n')
        self.wrapper.write_bytes(self.original)
        self.wrapper.chmod(0o751)

    def test_instrument_preserves_guard_prefix_and_targets_only_game(self):
        result = capture.instrument(self.original, self.home / 'log file')
        self.assertTrue(result.startswith(self.original.split(b'exec ')[0]))
        self.assertIn(b'export SHADPS4_NGS2_LFE_DIAGNOSTICS=1', result)
        self.assertIn(b" >'", result)

    def test_running_game_prevents_launcher_change(self):
        with mock.patch.object(capture, 'games', return_value={'42': '/existing/core'}):
            with self.assertRaisesRegex(RuntimeError, 'Exit the running'):
                capture.collect(self.home)
        self.assertEqual(self.wrapper.read_bytes(), self.original)

    def test_snapshot_failure_restores_actual_launcher_and_mode(self):
        with mock.patch.object(capture, 'games', side_effect=[{}, {'42': '/existing/core'}]), \
                mock.patch.object(capture, 'snapshot', side_effect=RuntimeError('snapshot failure')), \
                contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaisesRegex(RuntimeError, 'snapshot failure'):
                capture.collect(self.home)
        self.assertEqual(self.wrapper.read_bytes(), self.original)
        self.assertEqual(self.wrapper.stat().st_mode & 0o777, 0o751)

    def test_summary_counts_tiny_lfe_and_silent_windows(self):
        logfile = self.home / 'log'
        logfile.write_text('NGS2_LFE output system=1 grain=0 output=0 channels=8 frames=256 '
                           'peaks=0,0,0,1e-07,0,0,0,0 lfe-nonzero=2 nonfinite=0 final=0\n'
                           'NGS2_LFE output system=1 grain=1024 output=0 channels=8 frames=512 '
                           'peaks=0,0,0,0,0,0,0,0 lfe-nonzero=0 nonfinite=0 final=1\n')
        result = capture.summary(logfile)['outputs']['system=1 output=0 channels=8']
        self.assertEqual(result['frames_observed'], 768)
        self.assertEqual(result['lfe_nonzero_samples'], 2)
        self.assertEqual(result['maximum_peaks'][3], 1e-07)

    def installed_core(self):
        revision = 'f' * 40
        binary = self.home / 'Applications/shadps4/releases/main-ffffffffffff/shadps4'
        binary.parent.mkdir(parents=True)
        binary.write_bytes(b'test binary')
        (self.home / 'Applications/shadps4/shadps4').symlink_to(binary)
        record = {'home': str(self.home), 'revision': revision, 'binary': str(binary),
                  'binary_sha256': hashlib.sha256(binary.read_bytes()).hexdigest()}
        state = self.home / '.local/state/shadps4-default-main/install-test/state.json'
        state.parent.mkdir(parents=True)
        state.write_text(json.dumps(record))
        return revision, binary

    def test_audio_instrumentation_preserves_guard_and_enables_both_traces(self):
        result = capture.instrument(self.original, self.home / 'log file', True)
        self.assertTrue(result.startswith(self.original.split(b'exec ')[0]))
        self.assertIn(b'export SHADPS4_NGS2_DIAGNOSTICS=1\n', result)
        self.assertIn(b'export SHADPS4_NGS2_LFE_DIAGNOSTICS=1\n', result)
        self.assertEqual(result.count(b'exec /existing/core'), 1)

    def test_expected_install_requires_matching_revision_and_binary_hash(self):
        revision, binary = self.installed_core()
        self.assertEqual(capture.selected_install(self.home, revision)['binary'], str(binary))
        with self.assertRaises(RuntimeError):
            capture.selected_install(self.home, '0' * 40)
        binary.write_bytes(b'changed binary')
        with self.assertRaises(RuntimeError):
            capture.selected_install(self.home, revision)

    def test_wrong_revision_preserves_launcher_before_capture(self):
        self.installed_core()
        with mock.patch.object(capture, 'games', return_value={}):
            with self.assertRaises(RuntimeError):
                capture.collect(self.home, True, '0' * 40)
        self.assertEqual(self.wrapper.read_bytes(), self.original)
        self.assertFalse(list(self.home.glob('shadps4-audio-review-*')))

    def test_audio_summary_distinguishes_success_errors_and_nonfinite_output(self):
        logfile = self.home / 'audio.log'
        logfile.write_text(
            'NGS2_DIAG ms=0 control-stage voice=1 command=1000000a result=0 blocks=1\n'
            'NGS2_DIAG ms=0 control-stage voice=1 command=1000000a result=804a030f\n'
            'NGS2_DIAG ms=1 filter-rejected voice=1\n'
            'NGS2_DIAG ms=1 block-rejected voice=2\n'
            'NGS2_DIAG ms=1 control-error voice=2\n'
            'NGS2_DIAG ms=2 render-error system=1 result=804a0001\n'
            'NGS2_DIAG ms=2 decode-error code=6\n'
            'NGS2_DIAG ms=2 state-query voice=2 flags=10 queued=1\n'
            'NGS2_DIAG ms=2 state-query voice=2 flags=23 queued=0\n'
            'NGS2_DIAG ms=3 state-query voice=3 flags=3 queued=1\n'
            'NGS2_LFE output system=1 nonfinite=2 final=1\n')
        result = capture.audio_summary(logfile)
        self.assertEqual(result['diagnostic_records'], 10)
        self.assertEqual(result['nonfinite_output_samples_observed'], 2)
        self.assertFalse(result['diagnostic_budget_may_be_exhausted'])
        for count in result['record_counts'].values():
            self.assertEqual(count, 1)

    def test_audio_capture_waits_for_game_and_restores_before_creating_report(self):
        revision, binary = self.installed_core()

        def snapshot(work, number):
            self.assertNotEqual(self.wrapper.read_bytes(), self.original)
            (work / 'emulator.log').write_text(
                'NGS2_DIAG ms=0 control-stage command=1000000a result=0\n'
                'NGS2_LFE output system=1 grain=1 output=0 channels=8 frames=256 '
                'peaks=1,1,1,0,1,1,1,1 lfe-nonzero=0 nonfinite=0 final=1\n')

        with mock.patch.object(capture, 'games', side_effect=[{}, {'42': str(binary)}, {}]), \
                mock.patch.object(capture, 'snapshot', side_effect=snapshot), \
                mock.patch.object(capture.time, 'sleep'), \
                mock.patch.object(capture.time, 'monotonic', return_value=0), \
                contextlib.redirect_stdout(io.StringIO()) as output:
            capture.collect(self.home, True, revision)
        self.assertEqual(self.wrapper.read_bytes(), self.original)
        self.assertEqual(self.wrapper.stat().st_mode & 0o777, 0o751)
        self.assertIn('AUDIO_REPORT=', output.getvalue())
        self.assertEqual(len(list(self.home.glob('shadps4-audio-review-*.tar.gz'))), 1)
        report_file = next(self.home.glob('shadps4-audio-review-*/summary.json'))
        report = json.loads(report_file.read_text())
        self.assertTrue(report['observed_core_matches_expected'])
        self.assertEqual(report['selected_install']['revision'], revision)
        self.assertEqual(report['audio_review']['diagnostic_records'], 1)


if __name__ == '__main__':
    unittest.main()
