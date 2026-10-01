# SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
# SPDX-License-Identifier: GPL-2.0-or-later
import importlib.util
import base64
import contextlib
import io
import json
import os
import subprocess
import sys
import unittest
from unittest import mock
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
import local_build as local
import deploy_test as deploy
from test_deploy import DeploymentTests
import test_deploy
import session_guard
import main_build
import collect_graphics
test_deploy.deploy = deploy


class LocalDeploymentTests(DeploymentTests):
    def working_audio_dispatcher(self):
        fallback = self.wrapper.with_name('shadps4-esde.before-ngs2-probe.20260930-223701')
        fallback.write_text('#!/bin/bash\nprintf "normal:%s\\n" "$@"\n')
        old = self.home / 'Applications/shadps4/releases/ngs2-f00bef80/shadps4'
        original = deploy.selected_probe_wrapper(deploy.probe_wrapper(self.home, fallback), old)
        working = self.home / 'Applications/shadps4/releases/ngs2-f1c1c790/shadps4'
        with mock.patch.object(deploy, 'MARKER',
                               '# NGS2 isolated core selection: f1c1c79073b811ada98b963d6a87c066b66e2bc8'):
            return local.selection(original, working)

    def test_graphics_switch_preserves_installed_guard_fallback_and_exact_rollback(self):
        self.mock_install_inputs()
        previous = 'f9f2aa508c90a98e800ee398a824acd57b68fdbb'
        binary = self.home / 'Applications/shadps4/releases/ngs2-f9f2aa50/shadps4'
        original_audio = self.working_audio_dispatcher()
        with mock.patch.object(deploy, 'MARKER', '# NGS2 isolated core selection: ' + previous):
            self.wrapper.write_bytes(local.selection(original_audio, binary))
        with contextlib.redirect_stdout(io.StringIO()):
            session_guard.install(self.home)
        original = self.wrapper.read_bytes()
        revision = main_build.REVISION
        with mock.patch.object(deploy, 'COMMIT', revision), mock.patch.object(
                deploy, 'MARKER', '# NGS2 isolated core selection: ' + revision), mock.patch.object(
                deploy, 'selected_wrapper', local.selection):
            deploy.install(self.home, 0)
        installed = self.wrapper.read_text()
        helper = self.home / '.local/lib/shadps4-session-guard/guard.py'
        self.assertTrue(installed.partition('\n')[2].startswith(session_guard.prefix(helper)))
        self.assertIn('ngs2-' + revision[:8] + '/run_diagnostic.py', installed)
        entry = self.home / 'normal entry with spaces.ps4'
        entry.write_text('CUSA36843\n')
        result = subprocess.run(['bash', str(self.wrapper), str(entry)], check=True,
                                capture_output=True, text=True,
                                env=dict(os.environ, HOME=str(self.home)), timeout=10)
        self.assertEqual(result.stdout, 'normal:' + str(entry) + '\n')
        state = next((self.home / '.local/state/shadps4-ngs2').glob('*/deployment.json'))
        deploy.restore(state)
        self.assertEqual(self.wrapper.read_bytes(), original)

    def test_graphics_switch_rejects_modified_guard(self):
        body = self.working_audio_dispatcher().decode()
        helper = self.home / '.local/lib/shadps4-session-guard/guard.py'
        guarded = deploy.with_session_guard(body, session_guard.prefix(helper))
        modified = guarded.replace('exec python3', 'python3', 1).encode()
        new = self.home / 'Applications/shadps4/releases' / ('ngs2-' + main_build.REVISION[:8]) / 'shadps4'
        with self.assertRaisesRegex(RuntimeError, 'single-instance guard'):
            local.selection(modified, new)

    def validation_backup(self, name='one', original=None):
        profile = self.home / '.local/share/shadPS4/custom_configs/CUSA36843.json'
        profile.parent.mkdir(parents=True, exist_ok=True)
        current = b'{"GPU":{"readbacks_mode":2},"Audio":{"channels":8},"Vulkan":{"vkvalidation_enabled":true}}'
        if original is None:
            original = b'{"GPU":{"readbacks_mode":2},"Audio":{"channels":8}}'
        profile.write_bytes(current)
        record = self.home / ('.local/state/shadps4-graphics-readbacks/test-' + name + '/state.json')
        record.parent.mkdir(parents=True)
        record.write_text(json.dumps({'purpose': 'validation', 'profile': str(profile),
                                      'revision': '77c6bd3a1f116c605370e765464423a668f25ba1',
                                      'installed_sha256': deploy.digest(current),
                                      'existed': True, 'original_mode': 0o600,
                                      'original_base64': base64.b64encode(original).decode(),
                                      'original_sha256': deploy.digest(original)}))
        helper = record.parent / 'readbacks_test.py'
        helper.write_bytes(b'validated restore helper')
        self.patches.enter_context(mock.patch.dict(main_build.VALIDATION_HELPERS, {
            '77c6bd3a1f116c605370e765464423a668f25ba1': deploy.digest(helper.read_bytes())}))
        return profile, record, helper, original

    def test_known_validation_session_is_restored_before_visual_test(self):
        profile, record, helper, original = self.validation_backup()
        with mock.patch.object(main_build.subprocess, 'run',
                               side_effect=lambda *a, **kw: profile.write_bytes(original)) as run:
            main_build.restore_validation_session(self.home)
            run.assert_called_once_with([sys.executable, str(helper), '--restore', str(record)],
                                        check=True)
        self.assertEqual(profile.read_bytes(), original)

    def test_repeated_validation_uses_original_without_validation(self):
        profile, _, _, original = self.validation_backup()
        self.validation_backup('duplicate', original)
        self.validation_backup('nested', profile.read_bytes())
        def restore(command, **kwargs):
            state = json.loads(Path(command[-1]).read_text())
            profile.write_bytes(base64.b64decode(state['original_base64']))
        with mock.patch.object(main_build.subprocess, 'run', side_effect=restore) as run:
            main_build.restore_validation_session(self.home)
            run.assert_called_once()
        self.assertEqual(profile.read_bytes(), original)

    def test_ambiguous_validation_backups_do_not_change_settings(self):
        profile, _, _, _ = self.validation_backup()
        self.validation_backup('different', b'{"GPU":{"readbacks_mode":0}}')
        current = profile.read_bytes()
        with mock.patch.object(main_build.subprocess, 'run') as run:
            with self.assertRaisesRegex(RuntimeError, 'matching test backup'):
                main_build.restore_validation_session(self.home)
            run.assert_not_called()
        self.assertEqual(profile.read_bytes(), current)

    def test_modified_validation_helper_does_not_change_settings(self):
        profile, _, helper, _ = self.validation_backup()
        current = profile.read_bytes()
        helper.write_bytes(b'changed restore helper')
        with mock.patch.object(main_build.subprocess, 'run') as run:
            with self.assertRaisesRegex(RuntimeError, 'matching test backup'):
                main_build.restore_validation_session(self.home)
            run.assert_not_called()
        self.assertEqual(profile.read_bytes(), current)

    def test_unrecognized_validation_settings_are_preserved(self):
        profile = self.home / '.local/share/shadPS4/custom_configs/CUSA36843.json'
        profile.parent.mkdir(parents=True)
        original = b'{"Vulkan":{"vkvalidation_enabled":true},"Audio":{"channels":8}}'
        profile.write_bytes(original)
        with self.assertRaisesRegex(RuntimeError, 'matching test backup'):
            main_build.restore_validation_session(self.home)
        self.assertEqual(profile.read_bytes(), original)

    def test_finished_validation_does_not_change_readbacks_or_audio(self):
        profile = self.home / '.local/share/shadPS4/custom_configs/CUSA36843.json'
        profile.parent.mkdir(parents=True)
        original = b'{"GPU":{"readbacks_mode":2},"Audio":{"channels":8}}'
        profile.write_bytes(original)
        main_build.restore_validation_session(self.home)
        self.assertEqual(profile.read_bytes(), original)

    def prepare_failed_main(self):
        self.mock_install_inputs()
        self.wrapper.write_bytes(self.working_audio_dispatcher())
        with contextlib.redirect_stdout(io.StringIO()):
            session_guard.install(self.home)
        original = self.wrapper.read_bytes()
        working = self.home / 'Applications/shadps4/releases/ngs2-f1c1c790/shadps4'
        working.parent.mkdir(parents=True)
        working.write_bytes(b'known working executable')
        working.chmod(0o700)
        record = self.home / '.local/state/shadps4-ngs2/working/deployment.json'
        record.parent.mkdir(parents=True)
        record.write_text(json.dumps({'commit': main_build.WORKING, 'wrapper': str(self.wrapper),
                                     'binary': str(working), 'binary_sha256': deploy.digest(working.read_bytes())}))
        with mock.patch.object(deploy, 'COMMIT', main_build.FAILED), mock.patch.object(
                deploy, 'MARKER', '# NGS2 isolated core selection: ' + main_build.FAILED), mock.patch.object(
                deploy, 'selected_wrapper', local.selection):
            deploy.install(self.home, 0)
        (self.home / 'ngs2-diagnostic-2abd0fb0.log').write_bytes(b'failed launch signal=SIGTRAP\n')
        return original, working

    def test_failed_main_recovery_preserves_trace_and_guard(self):
        original, _ = self.prepare_failed_main()
        main_build.recover_failed_main(self.home)
        self.assertEqual(self.wrapper.read_bytes(), original)
        traces = list(self.home.glob('ngs2-crash-2abd0fb0-*/ngs2-diagnostic-2abd0fb0.log'))
        self.assertEqual(len(traces), 1)
        self.assertEqual(traces[0].read_bytes(), b'failed launch signal=SIGTRAP\n')
        self.assertEqual(traces[0].parent.stat().st_mode & 0o777, 0o700)
        main_build.recover_failed_main(self.home)
        self.assertEqual(self.wrapper.read_bytes(), original)

    def test_failed_main_recovery_rejects_changed_working_binary(self):
        _, working = self.prepare_failed_main()
        current = self.wrapper.read_bytes()
        working.write_bytes(b'changed executable')
        with self.assertRaisesRegex(RuntimeError, 'does not match'):
            main_build.recover_failed_main(self.home)
        self.assertEqual(self.wrapper.read_bytes(), current)

    def test_failed_main_recovery_rejects_later_launcher_edit(self):
        self.prepare_failed_main()
        current = self.wrapper.read_bytes() + b'# later edit\n'
        self.wrapper.write_bytes(current)
        with self.assertRaisesRegex(RuntimeError, 'No unique verified'):
            main_build.recover_failed_main(self.home)
        self.assertEqual(self.wrapper.read_bytes(), current)

    def test_local_upgrade_capture_fallback_and_rollback(self):
        self.mock_install_inputs()
        backup = self.wrapper.with_name('shadps4-esde.before-ngs2-probe.20260930-223701')
        backup.write_text('#!/bin/bash\nprintf "normal:%s\\n" "$@"\n')
        old = self.home / 'Applications/shadps4/releases/ngs2-f00bef80/shadps4'
        original = deploy.selected_probe_wrapper(deploy.probe_wrapper(self.home, backup), old)
        self.wrapper.write_bytes(original)
        new = self.home / 'Applications/shadps4/releases' / ('ngs2-' + local.REVISION[:8]) / 'shadps4'
        with mock.patch.object(deploy, 'COMMIT', local.REVISION), mock.patch.object(
                deploy, 'MARKER', '# NGS2 isolated core selection: ' + local.REVISION), mock.patch.object(
                deploy, 'selected_wrapper', local.selection):
            deploy.install(self.home, 0)
        new.write_text('#!/bin/bash\nprintf "NGS2_DIAG enabled=%s\\n" "$SHADPS4_NGS2_DIAGNOSTICS" >&2\n')
        trace = self.home / 'trace.log'
        helper = new.parent / 'run_diagnostic.py'
        helper.write_bytes(local.runner(new, trace))
        entry = self.home / 'test entry'
        entry.write_text('CUSA36843|ngs2probe\n')
        subprocess.run(['bash', str(self.wrapper), str(entry)], check=True)
        self.assertIn('NGS2_DIAG enabled=1', trace.read_text())
        entry.write_text('CUSA36843\n')
        result = subprocess.run(['bash', str(self.wrapper), str(entry)], check=True,
                                capture_output=True, text=True)
        self.assertEqual(result.stdout, 'normal:' + str(entry) + '\n')
        state = next((self.home / '.local/state/shadps4-ngs2').glob('*/deployment.json'))
        deploy.restore(state)
        self.assertEqual(self.wrapper.read_bytes(), original)

    def test_upgrade_from_diagnostic_dispatch_preserves_fallback(self):
        backup = self.wrapper.with_name('shadps4-esde.before-ngs2-probe.20260930-223701')
        old = self.home / 'Applications/shadps4/releases/ngs2-f00bef80/shadps4'
        original = deploy.selected_probe_wrapper(deploy.probe_wrapper(self.home, backup), old)
        diagnostic = self.home / 'Applications/shadps4/releases/ngs2-ca67919d/shadps4'
        with mock.patch.object(deploy, 'MARKER', '# NGS2 isolated core selection: ca67919dacf2917140fb957142dcd993737d9dd6'):
            installed = local.selection(original, diagnostic)
        new = self.home / 'Applications/shadps4/releases' / ('ngs2-' + local.REVISION[:8]) / 'shadps4'
        with mock.patch.object(deploy, 'MARKER', '# NGS2 isolated core selection: ' + local.REVISION):
            patched = local.selection(installed, new)
        self.assertIn(str(new.parent / 'run_diagnostic.py').encode(), patched)
        self.assertIn(('bash ' + str(backup) + ' "$@"').encode(), patched)

    def test_upgrade_from_recent_diagnostics_preserves_normal_fallback(self):
        backup = self.wrapper.with_name('shadps4-esde.before-ngs2-probe.20260930-223701')
        old = self.home / 'Applications/shadps4/releases/ngs2-f00bef80/shadps4'
        original = deploy.selected_probe_wrapper(deploy.probe_wrapper(self.home, backup), old)
        new = self.home / 'Applications/shadps4/releases' / ('ngs2-' + local.REVISION[:8]) / 'shadps4'
        for revision in ('59566b916c3ff680616081c9bcde642e70f874a7',
                         '66a2ef4d25e2029628dad50f5ec9a308ef072c47',
                         '9e95c1727d287514d0e85aef9f863e0b293f6e5b',
                         'c827aa1d5b052c70f938d6d34a4d704f5d21e088',
                         '87c0112389d82055570ee6e54a66210625010f51',
                         '7a26f2c2b2461d11461bd1f523cbb8a2087b2d0e',
                         '2abd0fb0f807e84713517e6a25e982043897353f',
                         '038bb3d83e751e50328abb98f04fcb2c3ee7897e',
                         'd5c5acc0787ab64939685010cd5878b57479caa7',
                         '2b82d291daa57d23052594051dacc64e6bdf9e9f'):
            with self.subTest(previous=revision):
                previous = self.home / 'Applications/shadps4/releases' / ('ngs2-' + revision[:8]) / 'shadps4'
                with mock.patch.object(deploy, 'MARKER', '# NGS2 isolated core selection: ' + revision):
                    installed = local.selection(original, previous)
                with mock.patch.object(deploy, 'MARKER', '# NGS2 isolated core selection: ' + local.REVISION):
                    patched = local.selection(installed, new)
                self.assertIn(str(new.parent / 'run_diagnostic.py').encode(), patched)
                self.assertIn(('bash ' + str(backup) + ' "$@"').encode(), patched)

    def test_docker_builder_mounts_only_workspace_as_current_user(self):
        args = local.container_command(self.home, 'builder:test', ['cmake', '--version'])
        self.assertEqual(args.count('--mount'), 1)
        self.assertIn('type=bind,src=' + str(self.home) + ',dst=' + str(self.home), args)
        self.assertIn('--user', args)
        self.assertNotIn('--privileged', args)
        self.assertNotIn('/var/run/docker.sock', ' '.join(args))
        self.assertEqual(args[-3:], ['builder:test', 'cmake', '--version'])

    def test_missing_scanner_cache_repair_preserves_compiled_objects(self):
        compiler = self.home / 'build/CMakeFiles/3.31.6/CMakeCXXCompiler.cmake'
        compiler.parent.mkdir(parents=True)
        original = 'set(CMAKE_CXX_COMPILER_CLANG_SCAN_DEPS "CMAKE_CXX_COMPILER_CLANG_SCAN_DEPS-NOTFOUND")\n'
        compiler.write_text(original)
        cache = self.home / 'build/CMakeCache.txt'
        cache.write_text('OTHER:STRING=unchanged\n')
        obj = compiler.parent / 'already-built.o'
        obj.write_bytes(b'compiled object')
        local.repair_scan_deps_cache(self.home / 'build')
        self.assertIn('/usr/bin/clang-scan-deps-19', compiler.read_text())
        self.assertNotIn('NOTFOUND', compiler.read_text())
        self.assertEqual(cache.read_text(), 'OTHER:STRING=unchanged\n')
        self.assertEqual(obj.read_bytes(), b'compiled object')
        local.repair_scan_deps_cache(self.home / 'build')
        self.assertIn('/usr/bin/clang-scan-deps-19', compiler.read_text())

    def test_local_upgrade_rejects_unexpected_wrapper(self):
        with self.assertRaises(RuntimeError):
            local.selection(self.original, self.home / 'Applications/shadps4/releases/ngs2-ca67919d/shadps4')

    def test_second_launch_preserves_complete_or_interrupted_first_trace(self):
        binary = self.home / 'fake-core'
        binary.write_text('#!' + sys.executable + '\nprint("NGS2_DIAG second-launch")\n')
        binary.chmod(0o700)
        trace = self.home / 'diagnostic.log'
        first = b'NGS2 diagnostic first-launch\nNGS2_DIAG loading-stall\n'
        trace.write_bytes(first)  # No exit footer: preserve interrupted captures too.
        helper = self.home / 'runner.py'
        helper.write_bytes(local.runner(binary, trace))
        subprocess.run([sys.executable, str(helper)], check=True, timeout=20)
        archives = list(self.home.glob('diagnostic-previous-*.log'))
        self.assertEqual(len(archives), 1)
        self.assertEqual(archives[0].read_bytes(), first)
        second = trace.read_bytes()
        self.assertIn(('previous_trace=' + str(archives[0])).encode(), second)
        self.assertIn(b'NGS2_DIAG second-launch', second)
        self.assertIn(b'exit_code=0', second)
        subprocess.run([sys.executable, str(helper)], check=True, timeout=20)
        archives = list(self.home.glob('diagnostic-previous-*.log'))
        self.assertEqual(len(archives), 2)
        self.assertEqual({p.read_bytes() for p in archives}, {first, second})

    def test_runner_preserves_crash_output_after_diagnostic_and_console_caps(self):
        binary = self.home / 'crashing-core'
        binary.write_text('#!' + sys.executable + '\n' + '''
import os, resource, signal, sys
resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
for n in range(2500):
    print("NGS2_DIAG number=%s" % n, file=sys.stderr)
for n in range(600):
    print("GRAPHICS_DIAG enabled=%s number=%s" %
          (os.environ.get("SHADPS4_GRAPHICS_DIAGNOSTICS"), n), file=sys.stderr)
print("[Lib.Ngs2] <Error> earlier cutscene failure", flush=True)
for n in range(600):
    print("startup-line-" + str(n) + "x" * 4000)
print("critical startup error on stdout", flush=True)
print("critical startup error on stderr", file=sys.stderr, flush=True)
os.kill(os.getpid(), signal.SIGTRAP)
''')
        binary.chmod(0o700)
        trace = self.home / 'crash.log'
        helper = self.home / 'runner.py'
        with mock.patch.object(local, 'GRAPHICS_TRACE', True):
            helper.write_bytes(local.runner(binary, trace))
        result = subprocess.run([sys.executable, str(helper)], timeout=20)
        self.assertEqual(result.returncode, 133)
        data = trace.read_text()
        self.assertEqual(sum(line.startswith('NGS2_DIAG ') for line in data.splitlines()), 2048)
        self.assertEqual(sum(line.startswith('GRAPHICS_DIAG ') for line in data.splitlines()), 512)
        self.assertIn('GRAPHICS_DIAG enabled=1 number=511', data)
        self.assertIn('[Lib.Ngs2] <Error> earlier cutscene failure', data)
        self.assertIn('EMULATOR_ERROR_TAIL', data)
        self.assertIn('critical startup error on stdout', data)
        self.assertIn('critical startup error on stderr', data)
        self.assertIn('exit_code=-5', data)
        self.assertIn('signal=SIGTRAP', data)
        self.assertLess(trace.stat().st_size, 3 * 1024 * 1024)

    def test_runner_keeps_late_window_and_resets_only_regular_capture_marker(self):
        binary = self.home / 'fake-core'
        binary.write_text('#!' + sys.executable + '\n' + '''
import os
from pathlib import Path
trigger = Path(os.environ['SHADPS4_NGS2_DIAGNOSTICS_TRIGGER'])
assert not trigger.exists()
for i in range(4097):
    print('NGS2_DIAG captured=%s' % i)
print('NGS2_DIAG should-be-bounded')
''')
        binary.chmod(0o700)
        trace = self.home / 'late.log'
        trigger = trace.with_suffix('.capture')
        trigger.write_text('old launch')
        helper = self.home / 'runner.py'
        with mock.patch.object(local, 'LATE_AUDIO_TRACE', True):
            helper.write_bytes(local.runner(binary, trace))
        subprocess.run([sys.executable, str(helper)], check=True, timeout=20)
        output = trace.read_text()
        self.assertIn('NGS2_DIAG captured=4096', output)
        self.assertNotIn('should-be-bounded', output)
        target = self.home / 'preserve.txt'
        target.write_text('preserve')
        trigger.symlink_to(target)
        result = subprocess.run([sys.executable, str(helper)], capture_output=True, timeout=20)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(target.read_text(), 'preserve')

    def test_audio_routing_reports_channel_maps_without_changing_settings(self):
        entry = {'index': 3, 'channel_map': 'front-left,front-right,front-center,lfe,rear-left,rear-right,side-left,side-right',
                 'sample_specification': 's16le 8ch 48000Hz', 'mute': False,
                 'properties': {'application.name': 'shadps4', 'private.extra': 'not needed'}}
        result = subprocess.CompletedProcess([], 0, stdout=json.dumps([entry]))
        with mock.patch.object(collect_graphics.subprocess, 'run', return_value=result) as run:
            report = collect_graphics.audio_routing()
        self.assertEqual(run.call_count, 3)
        for kind, call in zip(('sinks', 'sink-inputs', 'source-outputs'), run.call_args_list):
            self.assertEqual(call.args[0], ['pactl', '-f', 'json', 'list', kind])
            self.assertEqual(report[kind][0]['channel_map'], entry['channel_map'])
            self.assertEqual(report[kind][0]['application'], {'application.name': 'shadps4'})

    def capture_process(self):
        process = self.home / 'proc/123'
        process.mkdir(parents=True)
        binary = self.home / 'Applications/shadps4/releases/ngs2-f9f2aa50/shadps4'
        binary.parent.mkdir(parents=True)
        binary.write_text('synthetic executable')
        (process / 'exe').symlink_to(binary)
        trigger = self.home / 'ngs2-diagnostic-f9f2aa50.capture'
        (process / 'environ').write_bytes(b'SHADPS4_NGS2_DIAGNOSTICS_TRIGGER=' + os.fsencode(trigger) + b'\0')
        return process.parent, trigger

    def test_capture_requests_one_window_and_detects_acknowledgement(self):
        proc, trigger = self.capture_process()
        def acknowledge(_):
            self.assertEqual(trigger.stat().st_mode & 0o777, 0o600)
            trigger.with_suffix('.log').write_text('NGS2_DIAG ms=400000 capture-rearmed window=2\n')
        with mock.patch.object(collect_graphics.time, 'sleep', side_effect=acknowledge) as wait:
            result = collect_graphics.rearm_audio(self.home, proc)
            self.assertTrue(result['acknowledged'])
            self.assertTrue(result['requested'])
            again = collect_graphics.rearm_audio(self.home, proc)
            self.assertFalse(again['requested'])
            wait.assert_called_once_with(12)

    def test_capture_rejects_unadvertised_trigger_and_duplicate_instances(self):
        proc, trigger = self.capture_process()
        (proc / '123/environ').write_bytes(b'OTHER=value\0')
        with self.assertRaisesRegex(RuntimeError, 'does not advertise'):
            collect_graphics.rearm_audio(self.home, proc)
        self.assertFalse(trigger.exists())
        (proc / '124').mkdir()
        (proc / '124/exe').symlink_to((proc / '123/exe').resolve())
        with self.assertRaisesRegex(RuntimeError, 'exactly one'):
            collect_graphics.rearm_audio(self.home, proc)
        self.assertFalse(trigger.exists())


if __name__ == '__main__':
    unittest.main()
