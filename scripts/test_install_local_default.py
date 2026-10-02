# SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
# SPDX-License-Identifier: GPL-2.0-or-later
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock
import install_local_default as deploy


class DefaultInstallTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name)
        self.root = self.home / 'Applications/shadps4'
        self.release = self.root / 'releases/ngs2-286d0cca'
        self.release.mkdir(parents=True)
        self.old = self.release / 'shadps4'
        self.old.write_bytes(b'\x7fELF\x02previous core')
        self.old.chmod(0o755)
        (self.root / 'shadps4').symlink_to(self.old)
        self.guard = self.home / '.local/lib/shadps4-session-guard/guard.py'
        self.guard.parent.mkdir(parents=True)
        self.guard.write_text('# existing guard\n')
        self.prefix = (deploy.GUARD_START +
                       'if [[ "${SHADPS4_GUARD_PARENT_PID:-}" != "$PPID" ]]; then\n'
                       f'    exec python3 {self.guard} run "$0" "$@"\n'
                       'fi\nunset SHADPS4_GUARD_PARENT_PID\n' + deploy.GUARD_END)
        self.wrapper = self.home / '.local/bin/shadps4-esde'
        self.wrapper.parent.mkdir(parents=True)
        self.wrapper.write_text('#!/usr/bin/env bash\n' + self.prefix +
                                '# NGS2 isolated core selection: '
                                '286d0cca483ce80f9d4a4fe98d4620b6b003e0ca\n')
        self.wrapper.chmod(0o755)
        self.roms = self.home / 'roms/ps4'
        self.roms.mkdir(parents=True)
        self.normal = self.roms / 'Red Dead Redemption.ps4'
        self.normal.write_text('CUSA36843\n')
        self.trace = self.roms / 'Red Dead Redemption [NGS2 trace].ps4'
        self.trace.write_text('CUSA36843|ngs2probe\n')
        self.xml = self.home / 'ES-DE/custom_systems/es_systems.xml'
        self.xml.parent.mkdir(parents=True)
        self.xml.write_text('<systemList><system><name>ps4</name><path>' + str(self.roms) +
                            '</path><command>' + str(self.wrapper) +
                            ' %ROM%</command></system></systemList>')
        self.gamelist = self.roms / 'gamelist.xml'
        self.gamelist.write_text('<gameList><!-- keep metadata --><game><path>./' +
                                self.normal.name + '</path><favorite>true</favorite></game>'
                                '<game><path>./' + self.trace.name + '</path></game></gameList>')
        self.data = self.release / 'user/savedata/save.bin'
        self.data.parent.mkdir(parents=True)
        self.data.write_bytes(b'precious save')
        self.config = self.home / '.local/share/shadPS4/custom_configs/CUSA36843.json'
        self.config.parent.mkdir(parents=True)
        self.config.write_text('{"Audio": {"channels": 8}, "GPU": {"readbacks": 2}}')
        self.built = self.home / 'build/shadps4'
        self.built.parent.mkdir()
        self.built.write_bytes(b'\x7fELF\x02new core')
        self.built.chmod(0o755)
        self.revision = 'a' * 40

    def install(self):
        with mock.patch.object(deploy, 'no_running_apps'), \
             mock.patch.object(deploy.shutil, 'which', return_value='/usr/bin/tool'), \
             mock.patch.object(deploy, 'build', return_value=self.built):
            deploy.install(self.home, self.revision)

    def test_failed_build_keeps_entire_existing_installation(self):
        before = self.wrapper.read_bytes()
        with mock.patch.object(deploy, 'no_running_apps'), \
             mock.patch.object(deploy.shutil, 'which', return_value='/usr/bin/tool'), \
             mock.patch.object(deploy, 'build', side_effect=RuntimeError('compile failed')):
            with self.assertRaisesRegex(RuntimeError, 'compile failed'):
                deploy.install(self.home, self.revision)
        self.assertEqual(self.wrapper.read_bytes(), before)
        self.assertTrue(self.old.exists())
        self.assertTrue(self.trace.exists())
        self.assertEqual((self.root / 'shadps4').resolve(), self.old)

    def test_default_cleanup_and_rollback_preserve_data_and_guard(self):
        config = self.config.read_bytes()
        guard = self.guard.read_bytes()
        self.install()
        states = list((self.home / '.local/state/shadps4-default-main').glob('install-*/state.json'))
        self.assertEqual(len(states), 1)
        state = json.loads(states[0].read_text())
        self.assertEqual(state['cleanup'], 'complete')
        self.assertFalse(self.old.exists())
        self.assertFalse(self.trace.exists())
        self.assertTrue(self.normal.exists())
        self.assertEqual(self.data.read_bytes(), b'precious save')
        self.assertEqual(self.config.read_bytes(), config)
        self.assertEqual(self.guard.read_bytes(), guard)
        self.assertIn('<favorite>true</favorite>', self.gamelist.read_text())
        self.assertIn('keep metadata', self.gamelist.read_text())
        self.assertNotIn('NGS2 trace', self.gamelist.read_text())
        self.assertEqual(deploy.digest(self.root / 'shadps4'), deploy.digest(self.built))
        with mock.patch.object(deploy.Path, 'home', return_value=self.home), \
             mock.patch.object(deploy, 'no_running_apps'):
            deploy.restore(states[0])
        self.assertEqual((self.root / 'shadps4').read_bytes(), b'\x7fELF\x02previous core')
        self.assertEqual((self.root / 'shadps4').resolve().name, 'shadps4')

    def test_only_test_entry_becomes_ordinary_entry(self):
        self.normal.unlink()
        self.install()
        self.assertEqual(self.normal.read_text(), 'CUSA36843\n')
        self.assertFalse(self.trace.exists())

    def test_unknown_entries_stop_before_build_or_cleanup(self):
        (self.roms / 'unknown.ps4').write_text('unexpected format\n')
        with mock.patch.object(deploy, 'no_running_apps'), \
             mock.patch.object(deploy.shutil, 'which', return_value='/usr/bin/tool'), \
             mock.patch.object(deploy, 'build') as build:
            with self.assertRaisesRegex(RuntimeError, 'Unrecognized PS4 entry'):
                deploy.install(self.home, self.revision)
            build.assert_not_called()
        self.assertTrue(self.old.exists())

    def test_release_symlink_cannot_remove_outside_files(self):
        outside = self.home / 'unrelated'
        outside.mkdir()
        file = outside / 'shadps4'
        file.write_bytes(b'\x7fELF\x02unrelated')
        (self.root / 'releases/external').symlink_to(outside, target_is_directory=True)
        self.install()
        self.assertTrue(file.exists())

    def test_failed_atomic_switch_restores_original_default(self):
        original = self.wrapper.read_bytes()
        real_write = deploy.atomic_write

        def fail(path, data, mode=0o700):
            if path == self.wrapper and b'DEFAULT_MAIN_V1' in data:
                raise OSError('injected switch failure')
            return real_write(path, data, mode)

        with mock.patch.object(deploy, 'atomic_write', side_effect=fail):
            with self.assertRaisesRegex(OSError, 'injected switch failure'):
                self.install()
        self.assertEqual(self.wrapper.read_bytes(), original)
        self.assertEqual((self.root / 'shadps4').resolve(), self.old)
        self.assertTrue(self.trace.exists())

    def test_normal_launch_passes_exact_game_id_with_spaces_and_no_trace(self):
        # Skip the real guard only in this fixture; bash's PPID is this test process.
        result_path = self.home / 'invocation.json'
        self.old.write_text('#!' + sys_executable() + '\nimport os,json,sys\n'
                            'from pathlib import Path\nPath(' + repr(str(result_path)) +
                            ').write_text(json.dumps([sys.argv[1:],os.getcwd(),'
                            'os.environ.get("SHADPS4_GRAPHICS_DIAGNOSTICS")]))\n')
        self.wrapper.write_bytes(deploy.launcher(self.home, self.prefix))
        env = dict(os.environ, SHADPS4_GUARD_PARENT_PID=str(os.getpid()),
                   SHADPS4_GRAPHICS_DIAGNOSTICS='1')
        subprocess.run(['bash', str(self.wrapper), str(self.normal)], env=env, check=True)
        args, cwd, diag = json.loads(result_path.read_text())
        self.assertEqual(args, ['--game', 'CUSA36843', '--fullscreen', 'true'])
        self.assertEqual(cwd, str(self.home))
        self.assertIsNone(diag)
        result = subprocess.run(['bash', str(self.wrapper), str(self.trace)], env=env,
                                capture_output=True)
        self.assertEqual(result.returncode, 2)


def sys_executable():
    import sys
    return sys.executable


if __name__ == '__main__':
    unittest.main()
