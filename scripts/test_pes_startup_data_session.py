import contextlib
import io
import json
from pathlib import Path
import subprocess
import tarfile
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import install_local_default as installer
import run_pes_startup_data_test as runner


def capture_result():
    return {'errors': [], 'frames_capture_passed': True, 'screenshots_complete': True,
            'settings_unchanged': True, 'launcher_unchanged': True,
            'vulkan_preflight_passed': True, 'vulkan_layer_loaded': True,
            'gpu_frame_captured': True, 'process_cleanup': {'complete': True}, 'debugger_cleanup': {'complete': True},
            'vulkan_validation': {'synchronization_enabled_in_log': True, 'message_counts': {}},
            'graphics': {'event_count_lower_bounds': {'image-upload': 8, 'present-image': 4}},
            'console_capture': {'status': 'captured', 'truncated': False,
                                'size_at_open': 1, 'parts': [{'bytes': 1}]}}


class SessionTests(unittest.TestCase):
    def test_failed_build_is_packaged_and_never_launches_game(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            log = home / '.cache/shadps4-ngs2-local/ca67919d-docker/default-main-build.log'
            log.parent.mkdir(parents=True)
            log.write_text('previous build output\n')
            def build(*args, **kwargs):
                with log.open('a') as output:
                    output.write('THIS BUILD FAILED\n')
                raise RuntimeError('compiler failed')
            baseline = SimpleNamespace(prepare=lambda h, r, b, m, builder: builder(h, r, b),
                                       verify_installed=Mock())
            startup = SimpleNamespace(run=Mock())
            modules = {'install_local_default': SimpleNamespace(build=build),
                       'pes_current_baseline': baseline, 'run_pes_startup_test': startup}
            with patch.object(runner, 'prepare_helpers', return_value={}), \
                    patch.object(runner.importlib, 'import_module', side_effect=modules.__getitem__), \
                    patch.object(runner, 'validate_wait', return_value=Mock()), \
                    patch.object(runner, 'cleanup_containers', return_value={'complete': True}) as cleanup, \
                    contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(runner.execute_session(home), 1)
            startup.run.assert_not_called()
            cleanup.assert_called_once()
            with tarfile.open(next(home.glob('*.tar.gz'))) as archive:
                session = json.load(archive.extractfile('session.json'))
                self.assertFalse(session['completed'])
                self.assertTrue(session['container_cleanup']['complete'])
                self.assertEqual(archive.extractfile('build.0.log').read(), b'THIS BUILD FAILED\n')

    def test_success_packages_final_image_and_cleanup_without_visual_question(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            calls = []
            def capture(*args, **kwargs):
                calls.append(kwargs)
                work = kwargs['work']
                (work / 'screenshots').mkdir(parents=True)
                (work / 'screenshots/final.png').write_bytes(b'fixture image bytes')
                result = capture_result()
                (work / 'startup.json').write_text(json.dumps(result))
                (work / 'console.0.log').write_text(
                    'STARTUP_DIAG ms=0 thread=[fixture] event=api-enter api=sceKernelRead call=1 args=3\n'
                    'STARTUP_DIAG ms=1 thread=[fixture] event=file-read count=1 path=[/app0/dt00_ps4.cpk] offset=0 requested=8 returned=8\n'
                    'STARTUP_DIAG ms=1 thread=[fixture] event=api-return api=sceKernelRead call=1 result=8\n')
                (work / 'frames').mkdir()
                (work / 'frames/frames-trace.json').write_text('{}')
                gpu = work.parent / 'gpu-frame'
                (gpu / 'gpu-runtime').mkdir(parents=True)
                (gpu / 'gpu-build').mkdir()
                (gpu / 'gpu-runtime/huge.so').write_bytes(b'not report evidence')
                (gpu / 'gpu-build/source.tar.gz').write_bytes(b'not report evidence')
                (gpu / 'capture').mkdir()
                with (gpu / 'capture/game.rdc').open('wb') as raw:
                    raw.truncate(25 * 1024 * 1024)
                (gpu / 'capture.json').write_text('{}')
                return result
            baseline = SimpleNamespace(prepare=Mock(), verify_installed=Mock())
            modules = {'install_local_default': SimpleNamespace(build=Mock()),
                       'pes_current_baseline': baseline, 'run_pes_startup_test': SimpleNamespace(run=capture),
                       'pes_gpu_frame': SimpleNamespace(prepare=Mock(return_value=Mock(captured=Path('/fixture/frame.rdc'))))}
            terminal = io.StringIO()
            with patch.object(runner, 'prepare_helpers', return_value={}), \
                    patch.object(runner.importlib, 'import_module', side_effect=modules.__getitem__), \
                    patch.object(runner, 'validate_wait', return_value=Mock()), \
                    contextlib.redirect_stdout(terminal):
                self.assertEqual(runner.execute_session(home), 0)
            self.assertTrue(calls[0]['close_after'])
            self.assertEqual(calls[0]['trace_delay_seconds'], 40)
            self.assertFalse(calls[0]['archive'])
            self.assertEqual(terminal.getvalue().count('PES_TEST_ARCHIVE='), 1)
            self.assertNotIn('say whether', terminal.getvalue())
            with tarfile.open(next(home.glob('*.tar.gz'))) as archive:
                self.assertEqual(archive.extractfile('startup/screenshots/final.png').read(), b'fixture image bytes')
                self.assertTrue(json.load(archive.extractfile('startup/startup.json'))['process_cleanup']['complete'])
                self.assertIn('gpu-frame/capture.json', archive.getnames())
                self.assertTrue(json.load(archive.extractfile('startup-trace.json'))['trace_observed'])
                self.assertFalse(any('gpu-runtime' in name or 'gpu-build' in name or name.endswith('.rdc') for name in archive.getnames()))

    def test_cleanup_failure_cannot_pass(self):
        for key in ('process_cleanup', 'debugger_cleanup'):
            result = capture_result()
            result[key]['complete'] = False
            with self.assertRaisesRegex(RuntimeError, 'cleanup'):
                runner.check_capture(result)

    def test_stale_baseline_blocks_capture(self):
        startup = SimpleNamespace(run=Mock())
        baseline = SimpleNamespace(prepare=Mock(side_effect=RuntimeError('stale')))
        with self.assertRaisesRegex(RuntimeError, 'stale'):
            runner.run(Path('/fixture'), Path('/fixture/work'), {'ownership_token': 'a' * 32}, {},
                       SimpleNamespace(build=Mock()), baseline, startup)
        startup.run.assert_not_called()

    def test_failed_disposable_gdb_check_prevents_game_launch(self):
        startup = SimpleNamespace(run=Mock())
        baseline = SimpleNamespace(prepare=Mock(), verify_installed=Mock())
        with patch.object(runner, 'validate_wait', side_effect=RuntimeError('GDB snapshot failed')):
            with self.assertRaisesRegex(RuntimeError, 'GDB snapshot'):
                runner.run(Path('/fixture'), Path('/fixture/work'),
                           {'ownership_token': 'a' * 32}, {},
                           SimpleNamespace(build=Mock()), baseline, startup)
        baseline.prepare.assert_called_once()
        startup.run.assert_not_called()

    def test_failed_gpu_preflight_prevents_game_launch(self):
        startup = SimpleNamespace(run=Mock())
        baseline = SimpleNamespace(prepare=Mock(), verify_installed=Mock())
        gpu = SimpleNamespace(prepare=Mock(side_effect=RuntimeError('triangle replay failed')))
        with patch.object(runner, 'validate_wait', return_value=Mock()), \
                patch.object(runner.importlib, 'import_module', return_value=gpu):
            with self.assertRaisesRegex(RuntimeError, 'triangle replay failed'):
                runner.run(Path('/fixture'), Path('/fixture/work'),
                           {'ownership_token': 'a' * 32}, {},
                           SimpleNamespace(build=Mock()), baseline, startup)
        startup.run.assert_not_called()

    def test_replay_waits_for_confirmed_game_cleanup(self):
        result = capture_result()
        result['process_cleanup']['complete'] = False
        collector = Mock(captured=Path('/fixture/frame.rdc'))
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        with patch.object(runner, 'validate_wait', return_value=Mock()), \
                patch.object(runner.importlib, 'import_module', return_value=SimpleNamespace(
                    prepare=Mock(return_value=collector))):
            with self.assertRaisesRegex(RuntimeError, 'closed before GPU replay'):
                runner.run(Path('/fixture'), Path(temporary.name),
                           {'ownership_token': 'a' * 32}, {}, SimpleNamespace(build=Mock()),
                           SimpleNamespace(prepare=Mock(), verify_installed=Mock()),
                           SimpleNamespace(run=Mock(return_value=result)))
        collector.replay.assert_not_called()

    def test_docker_cleanup_verifies_label_before_removing_exact_id(self):
        token, container = 'a' * 32, 'b' * 64
        values = [container + '\n', json.dumps([{'Id': container, 'Config': {'Labels': {'org.shadps4.pes-test': token}}}]), '', '']
        with patch.object(runner.subprocess, 'run', side_effect=[subprocess.CompletedProcess([], 0, value) for value in values]) as run:
            result = runner.cleanup_containers(token)
        self.assertTrue(result['complete'])
        self.assertEqual(result['removed'], [container])
        self.assertIn('label=org.shadps4.pes-test=' + token, run.call_args_list[0].args[0])
        self.assertEqual(run.call_args_list[2].args[0], ['docker', 'rm', '--force', container])

    def test_startup_trace_absence_is_explicit_and_partial_logs_survive(self):
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            self.assertFalse(runner.collect_startup_trace(work)['trace_observed'])
            (work / 'startup').mkdir()
            (work / 'startup/console.0.log').write_text(
                'noise\nSTARTUP_DIAG ms=4 thread=[Main_Thread] event=api-enter api=sceKernelStat call=32 args=abc\n'
                'STARTUP_DIAG ms=5 thread=[Main_Thread] event=api-return api=sceKernelStat call=32 result=ffffffff80020002\n'
                'STARTUP_DIAG ms=7 thread=[Main_Thread] event=api-enter api=sceKernelStat call=64 args=def\n')
            result = runner.collect_startup_trace(work)
            self.assertFalse(result['trace_observed'])
            self.assertEqual(result['api_call_lower_bounds']['sceKernelStat'], 64)
            self.assertEqual(result['api_return_samples']['sceKernelStat'], {'ffffffff80020002': 1})
            self.assertEqual(result['first_ms'], 4)
            self.assertEqual(result['last_ms'], 7)
            self.assertEqual(len((work / 'startup-trace.log').read_text().splitlines()), 3)

    def test_docker_decoy_is_not_removed(self):
        container = 'b' * 64
        values = [container + '\n', json.dumps([{'Id': container, 'Config': {'Labels': {'org.shadps4.pes-test': 'another'}}}])]
        with patch.object(runner.subprocess, 'run', side_effect=[subprocess.CompletedProcess([], 0, value) for value in values]) as run:
            with self.assertRaisesRegex(RuntimeError, 'ownership changed'):
                runner.cleanup_containers('a' * 32)
        self.assertFalse(any('rm' in call.args[0] for call in run.call_args_list))

    def test_build_containers_receive_the_cleanup_label(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            with patch.object(installer, 'fetch_revision'), patch.object(installer, 'smoke'), \
                    patch.object(installer.subprocess, 'check_output', side_effect=[installer.REPO, '']), \
                    patch.object(installer.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0)) as run, \
                    contextlib.redirect_stdout(io.StringIO()):
                installer.build(home, 'a' * 40, 'fixture', cleanup_token='b' * 32)
            containers = [call.args[0] for call in run.call_args_list if call.args[0][:2] == ['docker', 'run']]
            self.assertTrue(containers)
            for args in containers:
                self.assertIn('org.shadps4.pes-test=' + 'b' * 32, args)
                self.assertIn('--rm', args)


if __name__ == '__main__':
    unittest.main()
