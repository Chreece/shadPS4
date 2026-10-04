# SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
# SPDX-License-Identifier: GPL-2.0-or-later
"""Host-launcher checks; real GDB validation is performed by the Docker test."""

import contextlib
import io
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch

import trace_video_progress as trace
import validate_video_diagnostic as validate
import pes_frame_profile


class LauncherTests(unittest.TestCase):
    def test_frame_idle_result_reaches_profile_validator_without_shadowing_module(self):
        result = {'apis': {api: {'calls': 0} for api in pes_frame_profile.APIS},
                  'skipped_symbol_fragments': ['Rasterizer::DrawIndirect.cold.1',
                                               'Rasterizer::DrawIndirect.cold.2',
                                               'Rasterizer::DrawIndirect()::$_0::operator()() const',
                                               'Rasterizer::DrawIndirect()::$_1::operator()() const']}
        validate.validate_observations('idle', result, profile='frames')
        result['apis']['sceVideoOutSubmitFlip']['calls'] = 1
        with self.assertRaisesRegex(RuntimeError, 'Idle target recorded frame activity'):
            validate.validate_observations('idle', result, profile='frames')

    def exercise(self, returncode):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            commands = []
            actual_popen = subprocess.Popen
            group = validate.os.getpgrp()

            def command_run(command, **kwargs):
                commands.append(command)
                return subprocess.CompletedProcess(command, 0, '', '')

            def child(command, **kwargs):
                commands.append(command)
                self.assertTrue(kwargs['start_new_session'])
                return actual_popen([sys.executable, '-c',
                    'print("PES_DIAGNOSTIC_SELFTEST=PASS", flush=True); '
                    f'raise SystemExit({returncode})'], **kwargs)

            error = None
            with patch.object(Path, 'home', return_value=home), \
                    patch.object(validate.shutil, 'which', return_value='/fixture/docker'), \
                    patch.object(validate.subprocess, 'run', side_effect=command_run), \
                    patch.object(validate.subprocess, 'Popen', side_effect=child), \
                    contextlib.redirect_stdout(io.StringIO()):
                try:
                    validate.launch()
                except RuntimeError as caught:
                    error = caught
            self.assertEqual(validate.os.getpgrp(), group)
            archives = list(home.glob('*/selftest-evidence.tar.gz'))
            self.assertEqual(len(archives), 1)
            with tarfile.open(archives[0]) as archive:
                self.assertIn('selftest.log', archive.getnames())
            launch = next(c for c in commands if c[:2] == ['docker', 'run'])
            name = launch[launch.index('--name') + 1]
            self.assertIn(['docker', 'rm', '-f', name], commands)
            return error

    def test_success_and_cleanup(self):
        self.assertIsNone(self.exercise(0))

    def test_nonzero_container_exit_is_failure_even_with_pass_marker(self):
        self.assertIsNotNone(self.exercise(2))

    def test_only_fresh_results_directory_is_mounted(self):
        command = validate.docker_run_command([], 'image', 'name', Path('/tmp/new-results'))
        self.assertEqual(command.count('--mount'), 1)
        self.assertEqual(command[command.index('--mount') + 1],
                         'type=bind,src=/tmp/new-results,dst=/results')
        self.assertEqual(command[command.index('--network') + 1], 'none')
        self.assertNotIn('--pid', command)
        self.assertNotIn('--privileged', command)
        self.assertIn('--read-only', command)

    def test_live_probe_command_is_shared_with_collector(self):
        identity = {'executable': '/test/fixture'}
        command = trace.debugger_command(identity, Path('/results/idle/probe.py'))
        self.assertEqual(command[-4:], ['-se', '/test/fixture', '-ex',
                                       'source /results/idle/probe.py'])
        self.assertIn('set may-call-functions off', command)


if __name__ == '__main__':
    unittest.main()
