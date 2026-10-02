# SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
# SPDX-License-Identifier: GPL-2.0-or-later
import contextlib
import io
import json
from pathlib import Path
import tarfile
import tempfile
import unittest
from unittest import mock
import recover_crash as recovery


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.home = Path(self.directory.name)
        self.wrapper = self.home / '.local/bin/shadps4-esde'
        self.wrapper.parent.mkdir(parents=True)
        self.states = self.home / '.local/state/shadps4-ngs2'
        self.states.mkdir(parents=True)
        self.binary = self.home / 'Applications/shadps4/releases/ngs2-f00bef80/shadps4'
        self.binary.parent.mkdir(parents=True)
        self.binary.write_bytes(b'working binary')
        self.binary.chmod(0o755)
        self.working = b'#!/bin/bash\necho working-test\n'
        self.diagnostic = b'#!/bin/bash\necho diagnostic-test\n'
        self.wrapper.write_bytes(self.diagnostic)
        self.wrapper.chmod(0o751)
        self.add_state('1', recovery.WORKING, b'#!/bin/bash\necho normal\n', self.working)
        self.add_state('2', 'ac36a0edd40409c3c9ed67dc68c630b7d2dcba7e',
                       self.working, self.diagnostic)
        self.log = self.home / '.local/share/shadPS4/log/shad_log.txt'
        self.log.parent.mkdir(parents=True)
        self.log.write_bytes(b'critical startup assertion\n')
        self.config = self.log.parent.parent / 'config.json'
        self.config.write_bytes(b'{"audioChannels":8}')
        stack = contextlib.ExitStack()
        self.addCleanup(stack.close)
        stack.enter_context(mock.patch.object(recovery, 'no_running_core'))
        stack.enter_context(mock.patch.object(recovery.shutil, 'which', return_value=None))
        stack.enter_context(mock.patch.dict(recovery.os.environ, {'XDG_DATA_HOME': ''}))
        stack.enter_context(contextlib.redirect_stdout(io.StringIO()))

    def add_state(self, name, commit, original, installed):
        directory = self.states / name
        directory.mkdir()
        (directory / 'shadps4-esde.before').write_bytes(original)
        (directory / 'deployment.json').write_text(json.dumps({
            'commit': commit, 'wrapper': str(self.wrapper), 'binary': str(self.binary),
            'binary_sha256': recovery.digest(self.binary.read_bytes()), 'original_mode': 0o751,
            'installed_wrapper_sha256': recovery.digest(installed),
            'original_wrapper_sha256': recovery.digest(original)}))

    def test_capture_before_restore_and_preserve_settings(self):
        recovery.recover(self.home)
        self.assertEqual(self.wrapper.read_bytes(), self.working)
        self.assertEqual(self.wrapper.stat().st_mode & 0o777, 0o751)
        self.assertEqual(self.config.read_bytes(), b'{"audioChannels":8}')
        self.assertEqual(self.log.read_bytes(), b'critical startup assertion\n')
        archive = next(self.home.glob('ngs2-startup-crash-*.tar.gz'))
        self.assertEqual(archive.stat().st_mode & 0o777, 0o600)
        with tarfile.open(archive) as bundle:
            self.assertEqual(bundle.extractfile('launcher.before-recovery').read(), self.diagnostic)
            self.assertEqual(bundle.extractfile('logs/0/shad_log.txt').read(), self.log.read_bytes())

    def test_follow_two_verified_backups_to_working_core(self):
        middle = b'#!/bin/bash\necho intermediate\n'
        (self.states / '2/deployment.json').unlink()
        self.add_state('3', 'ca67919dacf2917140fb957142dcd993737d9dd6', self.working, middle)
        self.add_state('4', 'ac36a0edd40409c3c9ed67dc68c630b7d2dcba7e', middle, self.diagnostic)
        recovery.recover(self.home)
        self.assertEqual(self.wrapper.read_bytes(), self.working)

    def test_user_edits_preserved_but_evidence_still_saved(self):
        edited = self.diagnostic + b'# user edit\n'
        self.wrapper.write_bytes(edited)
        with self.assertRaisesRegex(RuntimeError, 'No verified rollback chain'):
            recovery.recover(self.home)
        self.assertEqual(self.wrapper.read_bytes(), edited)
        self.assertTrue(list(self.home.glob('ngs2-startup-crash-*.tar.gz')))

    def test_corrupt_backup_or_binary_never_switches(self):
        backup = self.states / '2/shadps4-esde.before'
        backup.write_bytes(b'wrong')
        with self.assertRaisesRegex(RuntimeError, 'checksum'):
            recovery.recover(self.home)
        self.assertEqual(self.wrapper.read_bytes(), self.diagnostic)
        backup.write_bytes(self.working)
        self.binary.write_bytes(b'wrong')
        with self.assertRaisesRegex(RuntimeError, 'Cannot verify'):
            recovery.recover(self.home)
        self.assertEqual(self.wrapper.read_bytes(), self.diagnostic)

    def test_recovery_is_repeatable(self):
        recovery.recover(self.home)
        recovery.recover(self.home)
        self.assertEqual(self.wrapper.read_bytes(), self.working)

    def test_mip_test_restores_exact_guarded_6e00_launcher_and_captures_failure(self):
        working = '6e00d2ccadfa4ec1f5a7bbd46ad8a233857bbbfa'
        failed = '286d0cca483ce80f9d4a4fe98d4620b6b003e0ca'
        self.binary = self.home / 'Applications/shadps4/releases/ngs2-6e00d2cc/shadps4'
        self.binary.parent.mkdir(parents=True)
        self.binary.write_bytes(b'retained 6e00 executable')
        self.binary.chmod(0o755)
        guarded = b'#!/bin/bash\n# retained single-instance guard\necho previous-test\n'
        self.add_state('stable', working, self.working, guarded)
        self.add_state('mip', failed, guarded, self.diagnostic)
        trace = self.home / 'ngs2-diagnostic-286d0cca.log'
        trace.write_bytes(b'exit_code=-6\nsignal=SIGABRT\n')
        with mock.patch.object(recovery, 'WORKING', working), mock.patch.object(
                recovery, 'DIAGNOSTIC', {failed}):
            recovery.recover(self.home)
            self.assertEqual(self.wrapper.read_bytes(), guarded)
            self.assertEqual(self.config.read_bytes(), b'{"audioChannels":8}')
            self.assertEqual(self.binary.read_bytes(), b'retained 6e00 executable')
            archive = next(self.home.glob('ngs2-startup-crash-*.tar.gz'))
            with tarfile.open(archive) as bundle:
                self.assertEqual(bundle.extractfile('diagnostic-286d0cca.log').read(),
                                 trace.read_bytes())
                self.assertEqual(bundle.extractfile('launcher.before-recovery').read(),
                                 self.diagnostic)
            recovery.recover(self.home)
            self.assertEqual(self.wrapper.read_bytes(), guarded)


if __name__ == '__main__':
    unittest.main()
