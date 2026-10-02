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

    def test_same_revision_reuses_verified_binary_without_rebuilding(self):
        self.install()
        core = (self.root / 'shadps4').resolve()
        wrapper = self.wrapper.read_bytes()
        state_root = self.home / '.local/state/shadps4-default-main'
        backups = list(state_root.glob('install-*/state.json'))
        self.built.write_bytes(b'different bytes from another build of the same revision')
        with mock.patch.object(deploy, 'build', side_effect=AssertionError('must not rebuild')), \
             mock.patch.object(deploy, 'no_running_apps', side_effect=AssertionError('read-only reuse')):
            deploy.install(self.home, self.revision)
        self.assertEqual((self.root / 'shadps4').resolve(), core)
        self.assertEqual(core.read_bytes(), b'\x7fELF\x02new core')
        self.assertEqual(self.wrapper.read_bytes(), wrapper)
        self.assertEqual(list(state_root.glob('install-*/state.json')), backups)

    def test_reuse_rejects_changed_binary_launcher_and_revision(self):
        self.install()
        self.assertIsNotNone(deploy.verified_selected_install(self.home, self.revision))
        self.assertIsNone(deploy.verified_selected_install(self.home, 'b' * 40))
        before = self.wrapper.read_bytes()
        self.wrapper.write_bytes(before + b'# independent edit\n')
        self.assertIsNone(deploy.verified_selected_install(self.home, self.revision))
        self.wrapper.write_bytes(before)
        core = (self.root / 'shadps4').resolve()
        core.write_bytes(b'different selected binary')
        self.assertIsNone(deploy.verified_selected_install(self.home, self.revision))

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

    def test_version_entries_and_old_selectors_are_retired_together(self):
        versions = []
        for version, selector in [('0.10.0', 'v0100'), ('0.11.0', '0.11.0'),
                                  ('0.12.0', 'v0.12.0'), ('0.18.0', None)]:
            entry = self.roms / ('Red Dead Redemption [shadPS4 ' + version + '].ps4')
            entry.write_text('CUSA36843' + ('|' + selector if selector else '') + '\n')
            versions.append(entry)
        extra = self.roms / 'Red Dead Redemption [older experiment].ps4'
        extra.write_text('CUSA36843|old-build\n')
        versions.append(extra)
        self.install()
        self.assertEqual(self.normal.read_text(), 'CUSA36843\n')
        self.assertTrue(all(not entry.exists() for entry in versions))
        state = next((self.home / '.local/state/shadps4-default-main').glob('install-*/state.json'))
        self.assertEqual(len(json.loads(state.read_text())['removed_entries']), 6)

    def test_version_only_game_gets_normal_entry_with_region_preserved(self):
        self.normal.unlink()
        self.trace.unlink()
        entry = self.roms / 'Red Dead Redemption [Europe] [shadPS4 0.10.0].ps4'
        entry.write_bytes(b'CUSA36843|v0100\r\n')
        self.install()
        self.assertFalse(entry.exists())
        self.assertEqual((self.roms / 'Red Dead Redemption [Europe].ps4').read_text(), 'CUSA36843\n')

    def test_ordinary_title_with_trace_word_is_not_a_test_entry(self):
        entry = self.roms / 'Trace [Europe].ps4'
        entry.write_text('CUSA12345\n')
        self.install()
        self.assertEqual(entry.read_text(), 'CUSA12345\n')

    def test_normalized_title_collision_keeps_both_game_ids(self):
        self.normal.unlink()
        self.trace.unlink()
        for version, game in [('0.10.0', 'CUSA36843'), ('0.11.0', 'CUSA12345')]:
            entry = self.roms / ('Red Dead Redemption [shadPS4 ' + version + '].ps4')
            entry.write_text(game + '|v' + version + '\n')
        self.install()
        self.assertEqual({p.read_text().strip() for p in self.roms.glob('*.ps4')},
                         {'CUSA36843', 'CUSA12345'})

    def test_version_label_does_not_authorize_unrecognized_payload(self):
        entry = self.roms / 'Red Dead Redemption [shadPS4 0.10.0].ps4'
        payload = 'CUSA36843|$(touch unwanted)\n'
        entry.write_text(payload)
        with mock.patch.object(deploy, 'no_running_apps'), \
             mock.patch.object(deploy.shutil, 'which', return_value='/usr/bin/tool'), \
             mock.patch.object(deploy, 'build') as build:
            with self.assertRaisesRegex(RuntimeError, 'Unrecognized PS4 entries'):
                deploy.install(self.home, self.revision)
            build.assert_not_called()
        self.assertEqual(entry.read_text(), payload)

    def test_manager_shortcut_survives_cleanup_and_launches_existing_gui(self):
        entry = self.roms / 'shadPS4 Manager.ps4'
        entry.write_text('GUI\n')
        manager = self.home / 'Applications/shadps4QtLauncher-latest.AppImage'
        result_path = self.home / 'manager-invocation.json'
        manager.write_text('#!' + sys_executable() + '\nimport os,json,sys\n'
                           'from pathlib import Path\nPath(' + repr(str(result_path)) +
                           ').write_text(json.dumps([sys.argv[1:],os.getcwd()]))\n')
        manager.chmod(0o755)
        self.gamelist.write_text('<gameList><game><path>./shadPS4 Manager.ps4</path>'
                                '<name>shadPS4 Manager</name></game></gameList>')
        self.install()
        self.assertEqual(entry.read_text(), 'GUI\n')
        self.assertIn('shadPS4 Manager', self.gamelist.read_text())
        env = dict(os.environ, SHADPS4_GUARD_PARENT_PID=str(os.getpid()))
        subprocess.run(['bash', str(self.wrapper), str(entry)], env=env, check=True)
        self.assertEqual(json.loads(result_path.read_text()), [[], str(self.home)])

    def test_unknown_entries_stop_before_build_or_cleanup(self):
        (self.roms / 'unknown.ps4').write_text('unexpected format\n')
        with mock.patch.object(deploy, 'no_running_apps'), \
             mock.patch.object(deploy.shutil, 'which', return_value='/usr/bin/tool'), \
             mock.patch.object(deploy, 'build') as build:
            with self.assertRaisesRegex(RuntimeError, 'Unrecognized PS4 entries'):
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
