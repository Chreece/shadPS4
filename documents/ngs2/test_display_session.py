# SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
# SPDX-License-Identifier: GPL-2.0-or-later
import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

import deploy_test as deploy
import display_session as display
import repair_display_launch as repair
import session_guard


class DisplayResolutionTests(unittest.TestCase):
    def test_valid_inherited_session_is_unchanged(self):
        env = {'DISPLAY': ':0', 'XAUTHORITY': '/existing', 'PULSE_SINK': 'surround71'}
        with mock.patch.object(display, 'probe', return_value=True), mock.patch.object(
                display, 'session_candidates') as scan:
            result, source = display.resolve_environment(env)
        self.assertEqual((result, source), (env, 'inherited'))
        scan.assert_not_called()

    def test_stale_auth_uses_verified_same_display_and_preserves_audio(self):
        env = {'DISPLAY': ':0.0', 'XAUTHORITY': '/stale', 'PULSE_SINK': 'surround71'}
        candidates = [(':1', '/other', 'other'), (':0', '/active', 'xorg pid=123')]
        with mock.patch.object(display, 'session_candidates', return_value=candidates), mock.patch.object(
                display, 'probe', side_effect=lambda e: e.get('XAUTHORITY') == '/active') as check:
            result, source = display.resolve_environment(env)
        self.assertEqual(source, 'xorg pid=123')
        self.assertEqual(result, dict(env, XAUTHORITY='/active'))
        self.assertEqual(env['XAUTHORITY'], '/stale')
        self.assertEqual(check.call_count, 2)

    def test_does_not_redirect_remote_or_ambiguous_display(self):
        with self.assertRaisesRegex(RuntimeError, 'remote display'):
            display.resolve_environment({'DISPLAY': 'localhost:10.0'})
        with mock.patch.object(display, 'session_candidates', return_value=[
                (':0', '/one', 'one'), (':1', '/two', 'two')]):
            with self.assertRaisesRegex(RuntimeError, 'unique local display'):
                display.resolve_environment({})

    def test_failed_authentication_stops_without_changing_environment(self):
        env = {'DISPLAY': ':0', 'XAUTHORITY': '/stale'}
        with mock.patch.object(display, 'session_candidates', return_value=[(':0', '/active', 'xorg')]), mock.patch.object(
                display, 'probe', return_value=False):
            with self.assertRaisesRegex(RuntimeError, 'Cannot authenticate'):
                display.resolve_environment(env)
        self.assertEqual(env, {'DISPLAY': ':0', 'XAUTHORITY': '/stale'})

    def test_xorg_authority_must_belong_to_this_users_session(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            proc = root / '123'
            proc.mkdir()
            auth = root / 'serverauth'
            auth.write_bytes(b'test-only-cookie')
            auth.chmod(0o600)
            (proc / 'comm').write_text('Xorg\n')
            (proc / 'cmdline').write_bytes(b'/usr/lib/xorg/Xorg\0:0\0-auth\0' + os.fsencode(auth) + b'\0')
            (proc / 'cgroup').write_text('0::/user.slice/user-1000.slice/user@1000.service/headless-x.service\n')
            actual_stat = Path.stat
            def process_owner(path, *args, **kwargs):
                return SimpleNamespace(st_uid=0) if path == proc else actual_stat(path, *args, **kwargs)
            with mock.patch.object(display.os, 'getuid', return_value=1000), mock.patch.object(
                    Path, 'stat', process_owner):
                self.assertEqual(display.session_candidates(root), [(':0', str(auth), 'xorg pid=123')])
                (proc / 'cgroup').write_text('0::/user.slice/user-1001.slice/user@1001.service/\n')
                self.assertEqual(display.session_candidates(root), [])
            link = root / 'auth-link'
            link.symlink_to(auth)
            self.assertFalse(display.readable_authority(str(link)))
            self.assertEqual(auth.read_bytes(), b'test-only-cookie')


class DisplayRepairTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.home = Path(temporary.name)
        self.wrapper = self.home / '.local/bin/shadps4-esde'
        self.wrapper.parent.mkdir(parents=True)
        self.helper = self.home / '.local/lib/shadps4-session-guard/guard.py'
        self.helper.parent.mkdir(parents=True)
        self.old_guard = Path(session_guard.__file__).read_bytes()
        self.helper.write_bytes(self.old_guard)
        self.helper.chmod(0o700)
        self.binary = self.home / 'Applications/shadps4/releases/ngs2-286d0cca/shadps4'
        self.binary.parent.mkdir(parents=True)
        self.binary.write_bytes(b'verified existing binary')
        self.binary.chmod(0o755)
        prefix = '#!/usr/bin/env bash\n' + session_guard.prefix(self.helper)
        self.original = (prefix + '# NGS2 isolated core selection: ' + repair.WORKING +
                         '\npython3 ' + str(self.home / 'Applications/shadps4/releases/ngs2-6e00d2cc/run_diagnostic.py') + '\n').encode()
        self.selected = self.original.replace(repair.WORKING.encode(), repair.TARGET.encode()).replace(b'ngs2-6e00d2cc/', b'ngs2-286d0cca/')
        self.wrapper.write_bytes(self.original)
        self.wrapper.chmod(0o775)
        state = self.home / '.local/state/shadps4-ngs2/fixture/deployment.json'
        state.parent.mkdir(parents=True)
        state.write_text(json.dumps({'commit': repair.TARGET, 'wrapper': str(self.wrapper),
                                    'binary': str(self.binary), 'binary_sha256': deploy.digest(self.binary.read_bytes()),
                                    'original_wrapper_sha256': deploy.digest(self.original),
                                    'installed_wrapper_sha256': deploy.digest(self.selected)}))
        self.config = self.home / '.Xauthority'
        self.config.write_bytes(b'unchanged-existing-cookie')
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(mock.patch.object(deploy, 'no_running_core'))
        self.stack.enter_context(mock.patch.object(display, 'resolve_environment',
                                                   return_value=({'DISPLAY': ':0'}, 'xorg pid=123')))
        self.stack.enter_context(contextlib.redirect_stdout(io.StringIO()))

    def test_reuses_binary_preserves_guard_and_restores_exact_files(self):
        repair.install(self.home)
        self.assertEqual(self.wrapper.read_bytes(), self.selected)
        self.assertEqual(self.helper.read_bytes(), repair.patched_guard(self.old_guard))
        self.assertEqual(self.config.read_bytes(), b'unchanged-existing-cookie')
        self.assertEqual(self.binary.read_bytes(), b'verified existing binary')
        record = next(self.home.glob('.local/state/shadps4-display-launcher/repair-*/state.json'))
        self.assertTrue((record.parent / 'repair_display_launch.py').is_file())
        repair.restore(self.home, record)
        self.assertEqual(self.wrapper.read_bytes(), self.original)
        self.assertEqual(self.helper.read_bytes(), self.old_guard)
        self.assertFalse(self.helper.with_name('display_session.py').exists())

    def test_repair_is_idempotent(self):
        repair.install(self.home)
        repair.install(self.home)
        self.assertEqual(len(list(self.home.glob('.local/state/shadps4-display-launcher/repair-*'))), 1)

    def test_patched_guard_passes_repaired_display_to_child_and_blocks_duplicates(self):
        if session_guard.cores():
            self.skipTest('A real emulator is running')
        repair.install(self.home)
        (self.home / '.local/state/shadps4-session-guard').mkdir(parents=True)
        module = self.helper.with_name('display_session.py')
        module.write_text("def launch_environment(env):\n"
                          "    return dict(env, DISPLAY=':0', XAUTHORITY='/verified-test-auth')\n")
        output = self.home / 'child-environment.jsonl'
        runner = self.binary.with_name('run_diagnostic.py')
        runner.write_text('import json, os, time\nfrom pathlib import Path\n'
                          f'with Path({str(output)!r}).open("a") as f:\n'
                          '    f.write(json.dumps({k: os.environ.get(k) for k in '
                          '("DISPLAY", "XAUTHORITY", "PULSE_SINK", "SHADPS4_GUARD_PARENT_PID")})+"\\n")\n'
                          'time.sleep(0.8)\n')
        env = dict(os.environ, HOME=str(self.home), DISPLAY=':0', XAUTHORITY='/stale',
                   PULSE_SINK='surround71')
        children = []
        try:
            for _ in range(3):
                children.append(subprocess.Popen(['bash', str(self.wrapper)], env=env,
                                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE))
            for child in children:
                stdout, stderr = child.communicate(timeout=8)
                self.assertEqual(child.returncode, 0, (stdout, stderr))
        finally:
            for child in children:
                if child.poll() is None:
                    child.terminate()
                child.wait(timeout=3)
        rows = output.read_text().splitlines()
        self.assertEqual(len(rows), 1)
        self.assertEqual(json.loads(rows[0]), {'DISPLAY': ':0', 'XAUTHORITY': '/verified-test-auth',
                                             'PULSE_SINK': 'surround71', 'SHADPS4_GUARD_PARENT_PID': None})

    def test_unknown_launcher_or_binary_is_preserved(self):
        edited = self.original + b'# user edit\n'
        self.wrapper.write_bytes(edited)
        with self.assertRaisesRegex(RuntimeError, 'recorded test deployment'):
            repair.install(self.home)
        self.assertEqual(self.wrapper.read_bytes(), edited)
        self.wrapper.write_bytes(self.original)
        self.binary.write_bytes(b'changed binary')
        with self.assertRaisesRegex(RuntimeError, 'Cannot verify'):
            repair.install(self.home)
        self.assertEqual(self.helper.read_bytes(), self.old_guard)

    def test_auth_failure_changes_no_files(self):
        with mock.patch.object(display, 'resolve_environment', side_effect=RuntimeError('Cannot authenticate')):
            with self.assertRaisesRegex(RuntimeError, 'Cannot authenticate'):
                repair.install(self.home)
        self.assertEqual(self.wrapper.read_bytes(), self.original)
        self.assertEqual(self.helper.read_bytes(), self.old_guard)
        self.assertFalse(self.helper.with_name('display_session.py').exists())

    def test_modified_guard_is_preserved(self):
        self.helper.write_bytes(self.old_guard + b'# edited\n')
        with self.assertRaisesRegex(RuntimeError, 'guard differs'):
            repair.install(self.home)
        self.assertEqual(self.wrapper.read_bytes(), self.original)

    def test_restore_preserves_later_user_edits(self):
        repair.install(self.home)
        self.wrapper.write_bytes(self.selected + b'# later edit\n')
        record = next(self.home.glob('.local/state/shadps4-display-launcher/repair-*/state.json'))
        with self.assertRaisesRegex(RuntimeError, 'file changed'):
            repair.restore(self.home, record)
        self.assertEqual(self.wrapper.read_bytes(), self.selected + b'# later edit\n')


if __name__ == '__main__':
    unittest.main()
