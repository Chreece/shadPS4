# SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
# SPDX-License-Identifier: GPL-2.0-or-later
import importlib.util
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
        previous = '038bb3d83e751e50328abb98f04fcb2c3ee7897e'
        binary = self.home / 'Applications/shadps4/releases/ngs2-038bb3d8/shadps4'
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
                         '038bb3d83e751e50328abb98f04fcb2c3ee7897e'):
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


if __name__ == '__main__':
    unittest.main()
