# SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
# SPDX-License-Identifier: GPL-2.0-or-later
import importlib.util
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
test_deploy.deploy = deploy


class LocalDeploymentTests(DeploymentTests):
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
                         '9e95c1727d287514d0e85aef9f863e0b293f6e5b'):
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

    def test_runner_preserves_crash_output_after_diagnostic_and_console_caps(self):
        binary = self.home / 'crashing-core'
        binary.write_text('#!' + sys.executable + '\n' + '''
import os, resource, signal, sys
resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
for n in range(2500):
    print("NGS2_DIAG number=%s" % n, file=sys.stderr)
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
        helper.write_bytes(local.runner(binary, trace))
        result = subprocess.run([sys.executable, str(helper)], timeout=20)
        self.assertEqual(result.returncode, 133)
        data = trace.read_text()
        self.assertEqual(sum(line.startswith('NGS2_DIAG ') for line in data.splitlines()), 2048)
        self.assertIn('[Lib.Ngs2] <Error> earlier cutscene failure', data)
        self.assertIn('EMULATOR_ERROR_TAIL', data)
        self.assertIn('critical startup error on stdout', data)
        self.assertIn('critical startup error on stderr', data)
        self.assertIn('exit_code=-5', data)
        self.assertIn('signal=SIGTRAP', data)
        self.assertLess(trace.stat().st_size, 3 * 1024 * 1024)


if __name__ == '__main__':
    unittest.main()
