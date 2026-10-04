# SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
# SPDX-License-Identifier: GPL-2.0-or-later

import contextlib
import io
import json
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch

import pes_graphics_launch as native
import pes_vulkan_validation as validation
import run_pes_startup_test as startup
import run_pes_vulkan_test as runner


class ValidationTests(unittest.TestCase):
    def test_real_launch_applies_validation_after_launcher_unset_and_restores(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            binary = home / 'Applications/shadps4/shadps4'
            binary.parent.mkdir(parents=True)
            binary.write_text('#!' + sys.executable + '\nimport os,json\nprint(json.dumps(dict(os.environ)))\n')
            binary.chmod(0o700)
            wrapper = home / 'launcher'
            original = ('#!/bin/sh\n# SHADPS4_SESSION_GUARD_V1\n# SHADPS4_DEFAULT_MAIN_V1\n'
                        'unset VK_INSTANCE_LAYERS VK_KHRONOS_VALIDATION_VALIDATE_SYNC\n'
                        f'exec {binary} --game "$game" --fullscreen true\n').encode()
            wrapper.write_bytes(original)
            wrapper.chmod(0o700)
            with native.enabled_launch(home, wrapper, startup.checksum(wrapper), home, validation=True):
                result = subprocess.run([wrapper], capture_output=True, check=True, timeout=5)
                env = json.loads(result.stdout)
                for key, value in validation.ENVIRONMENT.items():
                    self.assertEqual(env[key], value)
            self.assertEqual(wrapper.read_bytes(), original)

    def test_layer_mapping_required_in_addition_to_environment(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            proc = root / '123'
            proc.mkdir()
            (proc / 'stat').write_text('123 (fixture) ' + ' '.join(['0'] * 19 + ['456']))
            env = dict(validation.ENVIRONMENT, SHADPS4_GRAPHICS_DIAGNOSTICS='1')
            (proc / 'environ').write_bytes(b'\0'.join((k + '=' + v).encode() for k, v in env.items()))
            (proc / 'maps').write_text('libvulkan.so.1\n')
            identity = {'pid': 123, 'start_ticks': '456'}
            with self.assertRaisesRegex(RuntimeError, 'not loaded'):
                native.verify_environment(identity, root, validation=True)
            (proc / 'maps').write_text('/usr/lib/libVkLayer_khronos_validation.so\n')
            native.verify_environment(identity, root, validation=True)

    def test_failed_canary_prevents_game_launch_and_preserves_archive(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            wrapper = home / 'launcher'
            wrapper.write_text('untouched')
            with patch.object(startup, 'require_idle'), \
                    patch.object(startup, 'selected_launch', return_value=(wrapper, startup.checksum(wrapper))), \
                    patch.object(startup, 'desktop_environment', return_value=({}, 'fixture')), \
                    patch.object(startup, 'debugger_prefix', return_value=[]), \
                    patch.object(startup, 'settings', return_value={'Audio': '7.1'}), \
                    patch.object(validation, 'preflight', side_effect=RuntimeError('canary failed')), \
                    patch.object(startup, 'launch_game') as launch, \
                    contextlib.redirect_stdout(io.StringIO()):
                result = startup.run(home, profile='frames', graphics=True, validation=True)
            launch.assert_not_called()
            self.assertIn('RuntimeError: canary failed', result['errors'])
            self.assertTrue(result['settings_unchanged'])
            self.assertTrue(result['launcher_unchanged'])
            with tarfile.open(next(home.glob('*.tar.gz'))) as archive:
                self.assertIn('startup.json', archive.getnames())

    def test_stale_baseline_prevents_capture(self):
        with patch.object(runner.startup, 'require_idle'), \
                patch.object(runner.baseline, 'verify_installed', side_effect=RuntimeError('stale')), \
                patch.object(runner.startup, 'run') as capture:
            with self.assertRaisesRegex(RuntimeError, 'stale'):
                runner.run(Path('/fixture'))
            capture.assert_not_called()


if __name__ == '__main__':
    unittest.main()
