# SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
# SPDX-License-Identifier: GPL-2.0-or-later
import contextlib
import io
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


if __name__ == '__main__':
    unittest.main()
