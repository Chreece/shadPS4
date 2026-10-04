# SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
# SPDX-License-Identifier: GPL-2.0-or-later

from pathlib import Path
import unittest
from unittest.mock import patch

import run_pes_refresh_candidate as candidate


class CandidateTests(unittest.TestCase):
    def test_failed_local_build_prevents_game_launch(self):
        with patch.object(candidate.baseline, 'prepare', side_effect=RuntimeError('build failed')), \
                patch.object(candidate.packets, 'run') as capture:
            with self.assertRaisesRegex(RuntimeError, 'build failed'):
                candidate.run(Path('/fixture'), {})
            capture.assert_not_called()

    def test_selection_precedes_capture_and_preserves_manifest(self):
        calls = []
        manifest = {'upstream': {'revision': 'upstream'}, 'pending_fixes': [5228]}
        with patch.object(candidate.baseline, 'prepare', side_effect=lambda *a: calls.append(a)), \
                patch.object(candidate.packets, 'run', side_effect=lambda *a: calls.append(a)):
            candidate.run(Path('/fixture'), manifest)
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0][:3], (Path('/fixture'), candidate.REVISION, candidate.BRANCH))
        self.assertEqual(calls[0][3]['pending_fixes'], [5228])
        self.assertEqual(calls[0][3]['candidate_fix']['revision'], candidate.FIX_REVISION)
        self.assertIs(calls[0][4], candidate.installer.build)
        self.assertEqual(calls[1], (Path('/fixture'),))
        self.assertNotIn('candidate_fix', manifest)


if __name__ == '__main__':
    unittest.main()
