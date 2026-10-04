import contextlib
import io
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch

import pes_test_cleanup as cleanup
import run_pes_startup_test as startup


class ProcessTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.proc = self.root / 'proc'
        self.proc.mkdir()
        self.children = []
        self.addCleanup(self.stop_children)

    def stop_children(self):
        for child in self.children:
            if child.poll() is None:
                child.kill()
            child.wait(timeout=3)
            if child.stdout:
                child.stdout.close()

    def spawn(self, token=None, ignore_term=False):
        code = ('import signal,time,os,json\n' +
                ('signal.signal(signal.SIGTERM,signal.SIG_IGN)\n' if ignore_term else '') +
                'print(json.dumps({"pid":os.getpid(),"session":os.getsid(0),'
                '"ticks":open("/proc/self/stat").read().rsplit(")",1)[1].split()[19]}),flush=True)\n'
                'time.sleep(60)\n')
        child = subprocess.Popen([sys.executable, '-c', code], stdout=subprocess.PIPE,
                                 start_new_session=True)
        self.children.append(child)
        identity = json.loads(child.stdout.readline())
        self.assertEqual(identity['pid'], child.pid)
        # The sandbox's proc snapshot does not reliably expose new child entries. Metadata
        # comes from the child; pidfds, signals, exit checks and parent preservation are native.
        proc = self.proc / str(child.pid)
        proc.mkdir()
        fields = ['S', str(os.getpid()), str(child.pid), str(identity['session'])] + ['0'] * 15 + [identity['ticks']]
        (proc / 'stat').write_text(str(child.pid) + ' (fixture) ' + ' '.join(fields))
        (proc / 'environ').write_bytes((cleanup.TOKEN_KEY + '=' + token).encode() if token else b'')
        return child

    def test_native_owned_processes_exit_decoy_and_parent_survive(self):
        owner = cleanup.OwnedLaunch(self.proc)
        launcher = self.spawn(owner.token)
        detached_child = self.spawn(owner.token)
        decoy = self.spawn('different-test')
        before = (os.getpid(), os.getppid(), os.getsid(0), os.getpgrp())
        owner.adopt_launcher(launcher)
        result = owner.close(grace=0.2)
        self.assertTrue(result['complete'], result)
        self.assertIsNotNone(launcher.poll())
        self.assertIsNotNone(detached_child.poll())
        self.assertIsNone(decoy.poll())
        self.assertEqual(before, (os.getpid(), os.getppid(), os.getsid(0), os.getpgrp()))
        self.assertEqual({p['pid'] for p in result['processes']}, {launcher.pid, detached_child.pid})

    def test_native_term_resistant_owned_process_is_reaped(self):
        owner = cleanup.OwnedLaunch(self.proc)
        child = self.spawn(owner.token, ignore_term=True)
        owner.adopt_launcher(child)
        result = owner.close(grace=0.2)
        self.assertTrue(result['complete'], result)
        self.assertEqual(child.returncode, -signal.SIGKILL)
        self.assertIn('SIGKILL', [item.get('signal') for item in result['actions']])

    def test_stale_identity_and_parent_are_never_registered(self):
        owner = cleanup.OwnedLaunch(self.proc)
        child = self.spawn()
        with self.assertRaisesRegex(RuntimeError, 'identity'):
            owner.remember(child.pid, 'stale')
        proc = self.proc / str(os.getpid())
        proc.mkdir()
        (proc / 'stat').write_text(Path('/proc/self/stat').read_text())
        with self.assertRaisesRegex(RuntimeError, 'isolated'):
            owner.remember(os.getpid())
        self.assertFalse(owner.owned)
        self.assertIsNone(child.poll())

    def test_startup_failure_closes_launch_before_writing_archive(self):
        owner = cleanup.OwnedLaunch(self.proc)
        child = self.spawn(owner.token)
        wrapper = self.root / 'launcher'
        wrapper.write_text('unchanged')
        close = owner.close
        with patch.object(cleanup, 'OwnedLaunch', return_value=owner), \
                patch.object(owner, 'close', side_effect=lambda identity, env: close(identity, env, grace=0.2)), \
                patch.object(startup, 'require_idle'), \
                patch.object(startup, 'selected_launch', return_value=(wrapper, startup.checksum(wrapper))), \
                patch.object(startup, 'desktop_environment', return_value=({}, 'fixture')), \
                patch.object(startup, 'debugger_prefix', return_value=[]), \
                patch.object(startup, 'settings', return_value={'Audio': {'channels': '7.1'}}), \
                patch.object(startup, 'launch_game', return_value=child), \
                patch.object(startup, 'await_game', side_effect=RuntimeError('fixture boot failure')), \
                contextlib.redirect_stdout(io.StringIO()):
            result = startup.run(self.root, close_after=True)
        self.assertIsNotNone(child.poll())
        self.assertTrue(result['process_cleanup']['complete'])
        with tarfile.open(next(self.root.glob('*.tar.gz'))) as archive:
            saved = json.load(archive.extractfile('startup.json'))
            self.assertTrue(saved['process_cleanup']['complete'])
            self.assertTrue(saved['settings_unchanged'])
            self.assertTrue(saved['launcher_unchanged'])

    def test_reused_user_session_cannot_enable_cleanup(self):
        with self.assertRaisesRegex(RuntimeError, 'fresh test-owned'):
            startup.run(self.root, reuse_existing=True, close_after=True)


if __name__ == '__main__':
    unittest.main()
