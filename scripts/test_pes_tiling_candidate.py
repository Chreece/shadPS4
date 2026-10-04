# SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
# SPDX-License-Identifier: GPL-2.0-or-later

from pathlib import Path
import unittest
from unittest.mock import patch

import run_pes_tiling_candidate as candidate


class CandidateGateTests(unittest.TestCase):
    def test_disabled_uniform_capability_and_malformed_modules_are_rejected(self):
        header = [0x07230203, 0x00010600, 0, 10, 0]
        shader = [(2 << 16) | 17, 1]
        candidate.check_spirv(header + shader)
        for invalid in ([], header + [0], header + [(3 << 16) | 17, 1],
                        header + shader + [(2 << 16) | 17, 4434]):
            with self.assertRaises(RuntimeError):
                candidate.check_spirv(invalid)

    def test_build_failure_never_runs_pes(self):
        with patch.object(candidate.baseline, 'prepare', side_effect=RuntimeError('build failed')), \
                patch.object(candidate.images, 'run_validated') as game:
            with self.assertRaisesRegex(RuntimeError, 'build failed'):
                candidate.run(Path('/unused'), {})
            game.assert_not_called()

    def test_successful_build_precedes_automatic_capture(self):
        events = []
        with patch.object(candidate.baseline, 'prepare', side_effect=lambda *a: events.append('build')), \
                patch.object(candidate.images, 'run_validated', side_effect=lambda *a: events.append('capture')):
            candidate.run(Path('/unused'), {})
        self.assertEqual(events, ['build', 'capture'])


if __name__ == '__main__':
    unittest.main()
