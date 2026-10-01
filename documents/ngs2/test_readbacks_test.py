# SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
# SPDX-License-Identifier: GPL-2.0-or-later

import contextlib
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

import readbacks_test as test


class ReadbackComparisonTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name)
        self.root = self.home / '.local/share/shadPS4'
        self.root.mkdir(parents=True)
        self.global_path = self.root / 'config.json'
        self.global_bytes = b'{"GPU":{"readbacks_mode":0},"Audio":{"channels":8}}\n'
        self.global_path.write_bytes(self.global_bytes)
        self.profile = test.profile_path(self.home)
        self.wrapper = self.home / '.local/bin/shadps4-esde'
        self.wrapper.parent.mkdir(parents=True)
        self.wrapper.write_text('# NGS2 isolated core selection: ' + test.REVISION + '\n')
        self.addCleanup(mock.patch.stopall)
        self.running = mock.patch.object(test, 'no_running_core').start()
        mock.patch.dict(os.environ, {'XDG_DATA_HOME': str(self.home / '.local/share')}).start()

    def apply(self):
        with contextlib.redirect_stdout(io.StringIO()) as output:
            test.apply(self.home)
        records = list((self.home / '.local/state/shadps4-graphics-readbacks').glob('*/state.json'))
        self.assertEqual(len(records), 1)
        self.assertIn('RESTORE=python3 ', output.getvalue())
        return records[0]

    def test_absent_profile_adds_only_readback_override_and_restores_absence(self):
        wrapper = self.wrapper.read_bytes()
        state = self.apply()
        self.assertEqual(json.loads(self.profile.read_text()), {'GPU': {'readbacks_mode': 2}})
        self.assertEqual(self.global_path.read_bytes(), self.global_bytes)
        self.assertEqual(self.wrapper.read_bytes(), wrapper)
        test.restore(self.home, state)
        self.assertFalse(self.profile.exists())
        test.restore(self.home, state)

    def test_existing_profile_preserves_audio_and_unknown_fields_then_restores_bytes(self):
        original = b'{ "GPU": {"readbacks_mode":1,"other":true}, "Audio":{"channels":8}, "Future":7 }'
        self.profile.parent.mkdir()
        self.profile.write_bytes(original)
        self.profile.chmod(0o640)
        state = self.apply()
        expected = json.loads(original)
        expected['GPU']['readbacks_mode'] = 2
        self.assertEqual(json.loads(self.profile.read_text()), expected)
        test.restore(self.home, state)
        self.assertEqual(self.profile.read_bytes(), original)
        self.assertEqual(self.profile.stat().st_mode & 0o777, 0o640)

    def test_restore_refuses_to_overwrite_later_user_edit(self):
        state = self.apply()
        changed = b'{"GPU":{"readbacks_mode":2},"Input":{"changed":true}}'
        self.profile.write_bytes(changed)
        with self.assertRaisesRegex(RuntimeError, 'settings changed'):
            test.restore(self.home, state)
        self.assertEqual(self.profile.read_bytes(), changed)

    def test_running_game_blocks_install_before_any_config_write(self):
        self.running.side_effect = RuntimeError('Close shadPS4 normally first')
        with self.assertRaisesRegex(RuntimeError, 'Close shadPS4'):
            test.apply(self.home)
        self.assertFalse(self.profile.exists())
        self.assertEqual(self.global_path.read_bytes(), self.global_bytes)

    def test_symlink_profile_and_wrong_revision_are_preserved(self):
        self.profile.parent.mkdir()
        self.profile.symlink_to(self.global_path)
        with self.assertRaisesRegex(RuntimeError, 'symlink'):
            test.apply(self.home)
        self.assertEqual(self.global_path.read_bytes(), self.global_bytes)
        self.profile.unlink()
        self.wrapper.write_text('# other build\n')
        with self.assertRaisesRegex(RuntimeError, 'not 10ff9e19'):
            test.apply(self.home)
        self.assertFalse(self.profile.exists())


if __name__ == '__main__':
    unittest.main()
