# SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
# SPDX-License-Identifier: GPL-2.0-or-later

import contextlib
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import run_pes_image_test as runner
import run_pes_startup_test as startup


class ImageTest(unittest.TestCase):
    def test_validation_failure_never_launches_the_game(self):
        with patch.object(startup, 'require_idle'), \
                patch.object(runner.baseline, 'verify_installed'), \
                patch.object(runner.validate, 'launch', side_effect=RuntimeError('failed')), \
                patch.object(startup, 'run') as launch:
            with self.assertRaisesRegex(RuntimeError, 'failed'):
                runner.run_validated(Path('/unused'))
            launch.assert_not_called()

    def test_missing_images_cannot_pass_and_launch_is_fresh(self):
        result = {'frames_capture_passed': True, 'settings_unchanged': True,
                  'launcher_unchanged': True, 'screenshots_complete': False}
        with patch.object(startup, 'require_idle'), \
                patch.object(runner.baseline, 'verify_installed') as baseline, \
                patch.object(runner.validate, 'launch'), \
                patch.object(startup, 'run', return_value=result) as launch:
            with self.assertRaisesRegex(RuntimeError, 'Image capture failed'):
                runner.run_validated(Path('/unused'))
            launch.assert_called_once_with(Path('/unused'), profile='frames', screenshots=True)
            self.assertEqual(baseline.call_count, 2)

    def test_only_new_title_images_are_collected_and_originals_remain(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            root = home / '.local/share/shadPS4/screenshots'
            root.mkdir(parents=True)
            work = home / 'capture'
            work.mkdir()
            png = b'\x89PNG\r\n\x1a\n' + b'\0\0\0\0IEND\xaeB`\x82'
            old = 'CUSA18676_20261004_120000_000_game_000000.png'
            names = [old, 'CUSA18676_20261004_130000_000_game_000001.png',
                     'CUSA18676_20261004_130000_001_hud_000002.png',
                     'CUSA36843_20261004_130000_000_game_000003.png']
            for name in names:
                (root / name).write_bytes(png)
            found = startup.collect_screenshots(home, work, {old})
            self.assertEqual({Path(item['file']).name for item in found}, set(names[1:3]))
            self.assertEqual({p.name for p in root.iterdir()}, set(names))

    def test_partial_png_is_not_reported_as_captured(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            root = home / '.local/share/shadPS4/screenshots'
            root.mkdir(parents=True)
            (root / 'CUSA18676_20261004_130000_000_game_000001.png').write_bytes(
                b'\x89PNG\r\n\x1a\n' + bytes(40))
            with self.assertRaisesRegex(RuntimeError, 'Incomplete'):
                startup.collect_screenshots(home, home, set())


if __name__ == '__main__':
    unittest.main()
