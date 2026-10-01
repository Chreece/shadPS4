# SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
# SPDX-License-Identifier: GPL-2.0-or-later

import contextlib
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from types import SimpleNamespace
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
        with self.assertRaisesRegex(RuntimeError, 'not ' + test.REVISION[:8]):
            test.apply(self.home)
        self.assertFalse(self.profile.exists())

    def validation_session(self, original=None):
        if original is not None:
            self.profile.parent.mkdir(exist_ok=True)
            self.profile.write_bytes(original)
        collector = Path(__file__).with_name('collect_graphics.py')
        with mock.patch.object(test, 'require_validation_layer'), \
                contextlib.redirect_stdout(io.StringIO()) as output:
            test.apply(self.home, validation=True, collector=collector)
        records = list((self.home / '.local/state/shadps4-graphics-readbacks').glob('*/state.json'))
        self.assertEqual(len(records), 1)
        self.assertIn('--finish-validation ', output.getvalue())
        self.assertEqual(test.digest((records[0].parent / 'collect_graphics.py').read_bytes()),
                         test.COLLECTOR_SHA256)
        return records[0]

    def test_validation_preserves_readbacks_audio_and_restores_exact_profile(self):
        original = (b'{ "GPU":{"readbacks_mode":2},"Audio":{"channels":8},'
                    b'"Vulkan":{"vkvalidation_gpu_enabled":true,"gpu_id":0}}')
        record = self.validation_session(original)
        expected = json.loads(original)
        expected['Vulkan'].update(test.VALIDATION_SETTINGS)
        self.assertEqual(json.loads(self.profile.read_bytes()), expected)
        self.assertEqual(self.global_path.read_bytes(), self.global_bytes)
        with mock.patch.object(test.subprocess, 'run', return_value=SimpleNamespace(returncode=0)):
            test.finish_validation(self.home, record)
        self.assertEqual(self.profile.read_bytes(), original)

    def test_missing_validation_layer_does_not_change_configuration(self):
        with mock.patch.object(test, 'require_validation_layer', side_effect=RuntimeError('missing')):
            with self.assertRaisesRegex(RuntimeError, 'missing'):
                test.apply(self.home, validation=True,
                           collector=Path(__file__).with_name('collect_graphics.py'))
        self.assertFalse(self.profile.exists())
        self.assertFalse((self.home / '.local/state/shadps4-graphics-readbacks').exists())

    def test_collection_failure_still_restores_absent_profile(self):
        record = self.validation_session()
        with mock.patch.object(test.subprocess, 'run', return_value=SimpleNamespace(returncode=1)):
            with self.assertRaisesRegex(RuntimeError, 'collection failed'):
                test.finish_validation(self.home, record)
        self.assertFalse(self.profile.exists())

    def test_changed_collector_cannot_be_executed(self):
        record = self.validation_session()
        (record.parent / 'collect_graphics.py').write_text('raise RuntimeError("changed")')
        with mock.patch.object(test.subprocess, 'run') as run:
            with self.assertRaisesRegex(RuntimeError, 'checksum mismatch'):
                test.finish_validation(self.home, record)
            run.assert_not_called()
        test.restore(self.home, record)
        self.assertFalse(self.profile.exists())


if __name__ == '__main__':
    unittest.main()
