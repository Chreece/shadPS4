#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
# SPDX-License-Identifier: GPL-2.0-or-later
"""Focused host-repair checks; never invokes sudo or changes a real service."""
import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import repair_sunshine_display as repair

WATCHDOG = b'#!/bin/bash\nrepair_x11()\n{\n    echo display\n}\n'
KMS = (b'#!/bin/bash\nset -euo pipefail\nexport DISPLAY=:0\n'
       b'export XAUTHORITY=/home/chreece/.Xauthority\necho display\n')


def gpu_config(home):
    auth = str(home / '.Xauthority')
    return ('[Service]\n# Keep existing capability and restart policies\nRestart=on-failure\n'
            'ExecStartPre=+/bin/bash -lc \'setcap cap_sys_admin,cap_sys_nice+p /usr/bin/sunshine\'\n'
            'ExecStartPre=/bin/bash -lc \'for i in $(seq 1 90); do [ -S /tmp/.X11-unix/X0 ] && '
            '[ -f ' + auth + ' ] && DISPLAY=:0 XAUTHORITY=' + auth +
            ' xset q >/dev/null 2>&1 && exit 0; sleep 2; done; echo "X11 display :0 not ready" >&2; exit 1\'\n'
            'ExecStartPre=/bin/bash -lc \'DISPLAY=:0 XAUTHORITY=' + auth +
            ' xrandr --output HDMI-A-0 --mode 1920x1080 --rate 60 --primary >/dev/null 2>&1 || true\'\n'
            'ExecStartPre=/bin/bash -lc \'DISPLAY=:0 XAUTHORITY=' + auth +
            ' xset -dpms s off s noblank >/dev/null 2>&1 || true\'\n').encode()


class RepairTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.home = Path(self.temporary.name)
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)

    def mock(self, name, **kwargs):
        return self.stack.enter_context(patch.object(repair, name, **kwargs))

    def scripts(self):
        self.mock('WATCHDOG_SHA', new=repair.digest(WATCHDOG))
        self.mock('KMS_SHA', new=repair.digest(KMS))
        return repair.patch_scripts(self.home, WATCHDOG, KMS)

    def fixture(self):
        self.scripts()
        for path, content in zip(repair.paths(self.home)[2:], (WATCHDOG, KMS)):
            repair.atomic(path, content, 0o775)
        override = self.home / 'system/sunshine.service.d/90-session-x11.conf'
        self.mock('DROPIN', new=override)
        self.mock('service_preflight', return_value=[])
        self.mock('no_games')
        self.mock('authority')
        self.mock('verify')
        gpu = self.home / 'system/sunshine.service.d/gpu-recovery.conf'
        repair.atomic(gpu, gpu_config(self.home), 0o644)
        self.mock('GPU_PRESTART', new=gpu)
        def sudo(*args):
            if args[0] == '/usr/bin/install' and args[-1] == gpu:
                repair.atomic(gpu, Path(args[-2]).read_bytes(), 0o644)
        self.mock('sudo', side_effect=sudo)
        self.mock('wait_service_state', return_value=True)
        self.mock('recovery_capture')
        self.activate = self.mock('activate')
        # Keep bash syntax checks real. Only simulate sudo -v.
        original_run = subprocess.run
        def run(command, **kwargs):
            if command == ['sudo', '-v']:
                return subprocess.CompletedProcess(command, 0)
            return original_run(command, **kwargs)
        self.stack.enter_context(patch.object(repair.subprocess, 'run', side_effect=run))
        def write_override(payload, backup):
            if payload is None:
                override.unlink()
            else:
                repair.atomic(override, payload, 0o644)
        self.mock('write_dropin', side_effect=write_override)
        original_regular = repair.regular
        self.mock('regular', side_effect=lambda path, root=False: original_regular(path))
        repair.directory(self.home / '.local/state/sunshine-display')
        return override

    def test_authority_rotates_without_changing_sunshine_environment(self):
        old, new = self.home / 'serverauth-old', self.home / 'serverauth-new'
        old.write_bytes(b'old-test-data')
        new.write_bytes(b'new-test-data')
        alias = self.home / '.local/state/sunshine-display/Xauthority'
        live = [old]
        def resolve(env):
            return dict(env, XAUTHORITY=str(live[0])), 'xorg'
        def probe(env):
            return Path(env['XAUTHORITY']).read_bytes() == live[0].read_bytes()
        with patch.object(repair.display_session, 'resolve_environment', side_effect=resolve), \
                patch.object(repair.display_session, 'probe', side_effect=probe):
            first, _ = repair.authority(self.home, {'PULSE_SINK': 'surround71', 'KEEP': 'yes'})
            self.assertEqual(os.readlink(alias), str(old))
            live[0] = new
            second, _ = repair.authority(self.home, first)
        self.assertEqual(first['XAUTHORITY'], second['XAUTHORITY'])
        self.assertEqual(os.readlink(alias), str(new))
        self.assertEqual(second['PULSE_SINK'], 'surround71')
        self.assertEqual(second['KEEP'], 'yes')
        self.assertNotIn('SDL_VIDEODRIVER', second)
        self.assertEqual(old.read_bytes(), b'old-test-data')

    def test_unrelated_authority_alias_is_preserved(self):
        directory = self.home / '.local/state/sunshine-display'
        repair.directory(directory)
        (directory / 'Xauthority').write_text('unrelated')
        with self.assertRaisesRegex(RuntimeError, 'alias'):
            repair.authority(self.home, {})
        self.assertEqual((directory / 'Xauthority').read_text(), 'unrelated')

    def test_failed_authority_does_not_disable_idle_cleanup_or_run_kms_commands(self):
        watchdog, kms = self.scripts()
        helper = repair.paths(self.home)[0]
        repair.atomic(helper, b'raise SystemExit(1)\n')
        result = subprocess.run(['bash'], input=watchdog + b'repair_x11\necho cleanup\n',
                                capture_output=True)
        self.assertEqual(result.stdout, b'cleanup\n')
        result = subprocess.run(['bash'], input=kms, capture_output=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout, b'')

    def test_unknown_script_is_not_patched(self):
        self.scripts()
        with self.assertRaisesRegex(RuntimeError, 'differ'):
            repair.patch_scripts(self.home, WATCHDOG + b'# later edit\n', KMS)

    def test_install_idempotence_and_exact_restore(self):
        override = self.fixture()
        with contextlib.redirect_stdout(io.StringIO()):
            repair.install(self.home)
            backup, = (self.home / '.local/state/sunshine-display').glob('backup-*')
            repair.install(self.home)
            self.assertEqual(self.activate.call_count, 1)
            repair.restore(self.home, backup)
        self.assertFalse(override.exists())
        self.assertFalse(repair.paths(self.home)[0].exists())
        self.assertFalse(repair.paths(self.home)[1].exists())
        for path, original in zip(repair.paths(self.home)[2:], (WATCHDOG, KMS)):
            self.assertEqual(path.read_bytes(), original)
            self.assertEqual(path.stat().st_mode & 0o777, 0o775)
        self.assertEqual(repair.GPU_PRESTART.read_bytes(), gpu_config(self.home))

    def test_failed_restart_restores_originals(self):
        override = self.fixture()
        self.activate.side_effect = [RuntimeError('test service start failure'), None]
        with contextlib.redirect_stdout(io.StringIO()), self.assertRaisesRegex(RuntimeError, 'start failure'):
            repair.install(self.home)
        self.assertFalse(override.exists())
        self.assertFalse(repair.paths(self.home)[0].exists())
        self.assertEqual(repair.paths(self.home)[2].read_bytes(), WATCHDOG)
        self.assertEqual(repair.paths(self.home)[3].read_bytes(), KMS)
        self.assertEqual(repair.GPU_PRESTART.read_bytes(), gpu_config(self.home))
        self.assertEqual(self.activate.call_count, 2)

    def test_restore_refuses_later_edits_before_restarting_services(self):
        self.fixture()
        with contextlib.redirect_stdout(io.StringIO()):
            repair.install(self.home)
            backup, = (self.home / '.local/state/sunshine-display').glob('backup-*')
            target = repair.paths(self.home)[2]
            target.write_bytes(target.read_bytes() + b'# new change\n')
            self.activate.reset_mock()
            with self.assertRaisesRegex(RuntimeError, 'changed after repair'):
                repair.restore(self.home, backup)
        self.activate.assert_not_called()

    def test_service_with_extra_arguments_is_not_overridden(self):
        info = {'User': repair.pwd.getpwuid(os.getuid()).pw_name, 'ActiveState': 'active',
                'ControlGroup': '',
                'KillMode': 'control-group', 'ExecStart': '{ path=/usr/bin/sunshine ; '
                'argv[]=/usr/bin/sunshine --config another.conf ; }'}
        watchdog = dict(info, ExecStart='{ path=' + str(repair.paths(self.home)[2]) + ' ; }')
        self.mock('show', side_effect=[info, watchdog])
        with self.assertRaisesRegex(RuntimeError, 'additional start arguments'):
            repair.service_preflight(self.home)

    def test_running_games_block_restart_but_frontend_pair_does_not(self):
        group = '/system.slice/sunshine.service'
        proc = self.home / 'proc'
        for pid, name in ((1, 'sunshine'), (2, 'es-de'), (3, 'es-de')):
            folder = proc / str(pid)
            folder.mkdir(parents=True)
            (folder / 'comm').write_text(name)
            (folder / 'cgroup').write_text('0::' + group + '\n')
        services = [{'ControlGroup': group}]
        repair.no_games(services, proc)
        (proc / '3/comm').write_text('rpcs3')
        with self.assertRaisesRegex(RuntimeError, 'Close running games'):
            repair.no_games(services, proc)

    def test_memfd_mount_worker_does_not_count_as_game_or_hide_one(self):
        group = '/system.slice/sunshine.service'
        proc = self.home / 'proc'
        worker = proc / '3144031'
        (worker / 'fd').mkdir(parents=True)
        (worker / 'comm').write_text('memfd:squashfus')
        (worker / 'cgroup').write_text('0::' + group + '\n')
        (worker / 'exe').symlink_to('/memfd:squashfuse (deleted)')
        (worker / 'fd/7').symlink_to('/dev/fuse')
        services = [{'ControlGroup': group}]
        repair.no_games(services, proc)
        game = proc / '3144032'
        game.mkdir()
        (game / 'comm').write_text('rpcs3')
        (game / 'cgroup').write_text('0::' + group + '\n')
        with self.assertRaisesRegex(RuntimeError, '3144032:rpcs3'):
            repair.no_games(services, proc)
        # A game outside Sunshine's cgroup must still stop shadPS4 deployment.
        (game / 'comm').write_text('shadPS4:Main')
        (game / 'cgroup').write_text('0::/user.slice/test.service\n')
        with self.assertRaisesRegex(RuntimeError, '3144032:shadps4:main'):
            repair.no_games(services, proc)

    def test_mount_worker_requires_executable_and_fuse_descriptor(self):
        process = self.home / 'process'
        (process / 'fd').mkdir(parents=True)
        (process / 'exe').symlink_to('/usr/bin/unrelated')
        (process / 'fd/7').symlink_to('/dev/fuse')
        self.assertFalse(repair.mount_helper(process, 'memfd:squashfus'))
        (process / 'exe').unlink()
        (process / 'exe').symlink_to('/memfd:squashfuse_ll (deleted)')
        self.assertTrue(repair.mount_helper(process, 'memfd:squashfus'))
        (process / 'fd/7').unlink()
        self.assertFalse(repair.mount_helper(process, 'memfd:squashfus'))

    def recovery_fixture(self):
        override = self.fixture()
        self.mock('RECOVERABLE_INSTALLERS', new={repair.digest(Path(repair.__file__).read_bytes())})
        with contextlib.redirect_stdout(io.StringIO()):
            repair.install(self.home)
        backup, = (self.home / '.local/state/sunshine-display').glob('backup-*')
        return override, backup

    def test_recovery_accepts_installed_and_partial_and_completed_rollback(self):
        override, backup = self.recovery_fixture()
        self.assertEqual(repair.recovery_plan(self.home)[0], backup)
        repair.paths(self.home)[0].unlink()
        repair.paths(self.home)[2].write_bytes(WATCHDOG)
        self.assertEqual(repair.recovery_plan(self.home)[0], backup)
        repair.paths(self.home)[1].unlink()
        repair.paths(self.home)[3].write_bytes(KMS)
        override.unlink()
        _, entries, dropin = repair.recovery_plan(self.home)
        self.assertIsNone(dropin)
        self.assertTrue(all(current == before for _, current, before, _ in entries))

    def test_recovery_preserves_modified_files_and_overrides(self):
        override, _ = self.recovery_fixture()
        original = override.read_bytes()
        override.write_bytes(original + b'# later admin edit\n')
        with self.assertRaisesRegex(RuntimeError, 'subsequently edited'):
            repair.recovery_plan(self.home)
        override.write_bytes(original)
        repair.paths(self.home)[2].write_bytes(b'# unrelated guard\n')
        with self.assertRaisesRegex(RuntimeError, 'subsequently edited'):
            repair.recovery_plan(self.home)

    def test_pending_start_is_not_reported_as_active(self):
        self.mock('show', return_value={'ActiveState': 'activating', 'MainPID': '123'})
        self.assertFalse(repair.wait_service_state(True, seconds=0))
        self.assertFalse(repair.wait_service_state(False, seconds=0))

    def test_recovery_does_not_require_units_to_be_active(self):
        override, _ = self.recovery_fixture()
        repair.paths(self.home)[0].unlink()
        repair.paths(self.home)[1].unlink()
        repair.paths(self.home)[2].write_bytes(WATCHDOG)
        repair.paths(self.home)[3].write_bytes(KMS)
        override.unlink()
        def show(unit):
            command = '/usr/bin/sunshine' if unit == repair.UNITS[0] else str(repair.paths(self.home)[2])
            return {'User': repair.pwd.getpwuid(os.getuid()).pw_name, 'KillMode': 'control-group',
                    'ControlGroup': '', 'ActiveState': 'inactive', 'SubState': 'dead',
                    'ExecStart': '{ argv[]=' + command + ' ; }'}
        self.mock('show', side_effect=show)
        self.mock('recovery_capture')
        commands = self.mock('recovery_command', return_value=True)
        self.mock('wait_service_state', return_value=False)
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            repair.recover(self.home)
        self.assertIn('SUNSHINE_RECOVERY_FILES=RESTORED', output.getvalue())
        self.assertIn('START_PENDING_OR_FAILED', output.getvalue())
        self.assertNotIn('SERVICES_ACTIVE', output.getvalue())
        starts = [call.args[1] for call in commands.call_args_list if 'start' in call.args[1]]
        self.assertEqual(len(starts), 2)
        self.assertTrue(all('--no-block' in command for command in starts))

    def test_startup_shell_and_children_are_not_apps_but_an_unrelated_game_is(self):
        group = '/system.slice/sunshine.service'
        proc = self.home / 'proc'
        for pid, name, parent in ((101, 'bash', 1), (102, 'sleep', 101)):
            folder = proc / str(pid)
            folder.mkdir(parents=True)
            (folder / 'comm').write_text(name)
            (folder / 'cgroup').write_text('0::' + group + '\n')
            (folder / 'stat').write_text(f'{pid} ({name}) S {parent} 0 0\n')
        service = {'ActiveState': 'activating', 'SubState': 'start-pre', 'MainPID': '0',
                   'ControlPID': '101', 'ControlGroup': group}
        repair.no_games([service], proc)
        game = proc / '103'
        game.mkdir()
        (game / 'comm').write_text('rpcs3')
        (game / 'cgroup').write_text('0::' + group + '\n')
        (game / 'stat').write_text('103 (rpcs3) S 1 0 0\n')
        with self.assertRaisesRegex(RuntimeError, '103:rpcs3'):
            repair.no_games([service], proc)

    def test_prestart_resolves_auth_before_existing_wait_and_preserves_capabilities(self):
        original = gpu_config(self.home)
        changed = repair.patch_prestart(self.home, original)
        self.assertLess(changed.index(b' --prepare\n'), changed.index(b'for i in $(seq 1 90)'))
        self.assertNotIn(str(self.home / '.Xauthority').encode(), changed)
        self.assertIn(b'setcap cap_sys_admin,cap_sys_nice+p /usr/bin/sunshine', changed)
        self.assertIn(b'Restart=on-failure\n', changed)
        self.assertEqual(repair.patch_prestart(self.home, changed), changed)
        with self.assertRaisesRegex(RuntimeError, 'differ'):
            repair.patch_prestart(self.home, original.replace(b'1920x1080', b'3840x2160'))


if __name__ == '__main__':
    unittest.main()
