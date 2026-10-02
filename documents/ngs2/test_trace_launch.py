#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
# SPDX-License-Identifier: GPL-2.0-or-later
import contextlib
import io
import json
from pathlib import Path
import tarfile
import tempfile
import unittest
from unittest.mock import Mock, patch

import trace_launch as capture


class LaunchCaptureTests(unittest.TestCase):
    def test_child_environment_isolated_and_audio_preserved(self):
        original = {'DISPLAY': ':0', 'PULSE_SINK': 'surround71', 'SECRET': 'private',
                    'SHADPS4_GUARD_PARENT_PID': '456'}
        baseline = capture.launch_environment(original, False)
        test = capture.launch_environment(original, True)
        self.assertNotIn('RADV_DEBUG', baseline)
        self.assertNotIn('RADV_DEBUG', original)
        self.assertEqual(test['RADV_DEBUG'], 'syncshaders')
        self.assertEqual(test['PULSE_SINK'], 'surround71')
        self.assertEqual(test['SECRET'], 'private')
        self.assertNotIn('SHADPS4_GUARD_PARENT_PID', test)
        public = capture.public_processes([{'environment': test}])[0]['environment']
        self.assertNotIn('SECRET', public)
        self.assertEqual(public['RADV_DEBUG'], 'syncshaders')

    def test_existing_debug_flag_is_not_overwritten(self):
        env = {'RADV_DEBUG': 'nohiz'}
        with self.assertRaisesRegex(RuntimeError, 'already supplies'):
            capture.launch_environment(env, True)
        self.assertEqual(capture.launch_environment(env, False), env)

    def run_capture(self, home, processes, popen, sync=True):
        with patch.object(capture.os, 'geteuid', return_value=1000), \
                patch.object(capture.Path, 'home', return_value=home), \
                patch.object(capture, 'processes', side_effect=processes), \
                patch.object(capture.subprocess, 'Popen', popen), \
                patch.object(capture.time, 'sleep'), contextlib.redirect_stdout(io.StringIO()):
            capture.main(sync)
        archive = next(home.glob('ngs2-launch-check-*.tar.gz'))
        with tarfile.open(archive) as bundle:
            files = {m.name: bundle.extractfile(m).read() for m in bundle.getmembers()}
        return json.loads(files['launch-report.json']), files

    def test_running_core_prevents_second_launch(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            core = {'pid': 99, 'name': 'shadps4:main', 'exe': '/test/shadps4',
                    'cwd': directory, 'environment': {}}
            popen = Mock()
            report, _ = self.run_capture(home, [[core], [core]], popen)
            popen.assert_not_called()
            self.assertIn('already running', report['launch_note'])

    def test_no_frontend_does_not_launch(self):
        with tempfile.TemporaryDirectory() as directory:
            popen = Mock()
            report, _ = self.run_capture(Path(directory), [[], []], popen)
            popen.assert_not_called()
            self.assertIn('No live ES-DE', report['launch_note'])

    def test_one_guarded_launch_collects_after_exit_without_install_changes(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            wrapper = home / '.local/bin/shadps4-esde'
            helper = home / '.local/lib/shadps4-session-guard/guard.py'
            wrapper.parent.mkdir(parents=True)
            helper.parent.mkdir(parents=True)
            helper.write_text('retained guard')
            wrapper.write_text(
                '#!/usr/bin/env bash\n# SHADPS4_SESSION_GUARD_V1\n'
                'if [[ "${SHADPS4_GUARD_PARENT_PID:-}" != "$PPID" ]]; then\n'
                f'    exec python3 {helper} run "$0" "$@"\n'
                'fi\nunset SHADPS4_GUARD_PARENT_PID\n# END SHADPS4_SESSION_GUARD_V1\n'
                '# NGS2_PROBE_DISPATCH_V1\n# NGS2 isolated core selection: ' + capture.REVISION)
            originals = {p: p.read_bytes() for p in (wrapper, helper)}
            frontend = {'pid': 10, 'name': 'es-de', 'exe': '/test/es-de', 'cwd': directory,
                        'environment': {'DISPLAY': ':0', 'PULSE_SINK': 'surround71'}}
            core = {'pid': 12, 'name': 'shadps4:main', 'cwd': directory,
                    'exe': str(home / 'Applications/shadps4/releases/ngs2-286d0cca/shadps4'),
                    'environment': dict(frontend['environment'], RADV_DEBUG='syncshaders')}
            child = Mock(pid=11)
            child.poll.return_value = None
            def exit_game():
                (home / 'ngs2-diagnostic-286d0cca.log').write_text('completed trace\nexit_code=0\n')
                child.poll.return_value = 0
                return 0
            child.wait.side_effect = exit_game
            popen = Mock(return_value=child)
            report, files = self.run_capture(home, [[frontend], [frontend, core], [frontend]], popen)
            popen.assert_called_once()
            args, kwargs = popen.call_args
            self.assertEqual(args[0][:2], ['/bin/bash', str(wrapper)])
            self.assertEqual(Path(args[0][2]).read_text(), 'CUSA36843|ngs2probe\n')
            self.assertEqual(kwargs['env']['RADV_DEBUG'], 'syncshaders')
            self.assertEqual(kwargs['env']['PULSE_SINK'], 'surround71')
            self.assertTrue(kwargs['start_new_session'])
            child.wait.assert_called_once()
            child.terminate.assert_not_called()
            child.kill.assert_not_called()
            self.assertEqual(report['launcher_exit_code'], 0)
            self.assertTrue(report['sync_observed_in_core'])
            self.assertTrue(report['trace_changed'])
            self.assertIn(b'completed trace\nexit_code=0\n', files.values())
            for path, data in originals.items():
                self.assertEqual(path.read_bytes(), data)


if __name__ == '__main__':
    unittest.main()
