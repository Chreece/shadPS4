#!/usr/bin/env python3
"""Offline tests: no AppImage, GPU, Sunshine or Ollama daemon is modified."""
from __future__ import annotations

import importlib.util
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import threading
import shutil
import subprocess
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

GUARD = Path(__file__).with_name('esde-ollama-vram-guard.py')
GIB = 1024 ** 3


class GuardTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.env = mock.patch.dict(os.environ, {
            'ESDE_GUARD_PROJECT': str(self.root / 'project'),
            'ESDE_GUARD_APPIMAGE': str(self.root / 'apps/es/ES-DE_x64.AppImage'),
            'ESDE_GUARD_DRM_ROOT': str(self.root / 'drm'),
            'ESDE_GUARD_MIN_FREE_GIB': '8',
        })
        self.env.start()
        self.addCleanup(self.env.stop)
        spec = importlib.util.spec_from_file_location('guard_test_unique', GUARD)
        self.guard = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.guard)
        card = self.guard.SYSFS / 'card0/device'
        card.mkdir(parents=True)
        self.total = card / 'mem_info_vram_total'
        self.used = card / 'mem_info_vram_used'
        self.total.write_text(str(24 * GIB))
        self.used.write_text(str(18 * GIB))
        self.guard.LOGS.mkdir(parents=True)
        self.log = self.guard.LOGS / 'test.jsonl'

    def test_historical_ollama_host_endpoint(self):
        self.assertEqual(self.guard.API, os.environ.get(
            'ESDE_GUARD_OLLAMA_API', 'http://127.0.0.1:7869').rstrip('/'))

    def test_read_only_status_probes_backend(self):
        called = []
        def fake_status():
            called.append('api/ps')
            return [{'name': 'qwen3.8-greek:latest', 'size_vram': 19 * GIB}]
        with mock.patch.object(self.guard, 'loaded_models', side_effect=fake_status):
            with mock.patch.object(self.guard, 'api_model', side_effect=AssertionError('never unload or load')):
                with mock.patch.object(sys, 'argv', ['guard', 'status']):
                    self.assertEqual(self.guard.main(), 0)
        self.assertEqual(called, ['api/ps'])

    def test_no_models_touched_when_enough_free(self):
        self.used.write_text(str(15 * GIB))
        unloaded = []
        with mock.patch.object(self.guard, 'loaded_models', side_effect=AssertionError('must not query')):
            self.guard.unload_for_vram(self.log, unloaded)
        self.assertEqual(unloaded, [])

    def test_unload_just_enough_and_restore_original_models(self):
        self.used.write_text(str(20 * GIB))
        models = [
            {'name': 'a:latest', 'size_vram': 9 * GIB},
            {'name': 'b:latest', 'size_vram': 3 * GIB},
            {'name': 'cpu', 'size_vram': 0},
        ]
        commands = []
        def api(name, ttl):
            commands.append((name, ttl))
            if ttl == 0:
                self.used.write_text(str(11 * GIB))
        unloaded = []
        with mock.patch.object(self.guard, 'loaded_models', return_value=models), \
             mock.patch.object(self.guard, 'api_model', side_effect=api):
            self.guard.unload_for_vram(self.log, unloaded)
        self.assertEqual([name for name, _ in unloaded], ['a:latest'])
        self.assertEqual(commands[0], ('a:latest', 0))
        with mock.patch.object(self.guard, 'loaded_models', return_value=[]), \
             mock.patch.object(self.guard, 'api_model', side_effect=api):
            self.guard.restore(self.log, unloaded)
        self.assertEqual(commands[1][0], 'a:latest')
        self.assertGreaterEqual(commands[1][1], 300)

    def test_unknown_gpu_skips_ollama(self):
        self.total.unlink()
        unloaded = []
        with mock.patch.object(self.guard, 'loaded_models', side_effect=AssertionError('no API')):
            self.guard.unload_for_vram(self.log, unloaded)
        self.assertEqual(unloaded, [])

    def test_install_wraps_without_mutating_native_and_is_reversible(self):
        self.guard.APPIMAGE.parent.mkdir(parents=True)
        self.guard.APPIMAGE.write_bytes(b'\x7fELF-appimage-original')
        self.guard.APPIMAGE.chmod(0o755)
        self.guard.SCRIPT.parent.mkdir(parents=True)
        self.guard.SCRIPT.write_text('guard placeholder')
        self.guard.install()
        self.assertTrue(self.guard.is_wrapper())
        self.assertEqual(self.guard.REAL.read_bytes(), b'\x7fELF-appimage-original')
        self.guard.install()  # idempotent
        self.guard.uninstall()
        self.assertEqual(self.guard.APPIMAGE.read_bytes(), b'\x7fELF-appimage-original')
        self.assertFalse(self.guard.REAL.exists())

    def test_launch_restores_on_normal_nonzero_exit(self):
        self.used.write_text(str(15 * GIB))  # 9 GiB free, no Ollama action required
        app = self.root / 'mock-esde.sh'
        app.write_text('#!/bin/sh\nexit 7\n')
        app.chmod(0o755)
        seen = []
        def before(log, state):
            state.append(('loaded:before', 300))
        def after(log, state):
            seen.extend(state)
        with mock.patch.object(self.guard, 'unload_for_vram', side_effect=before), \
             mock.patch.object(self.guard, 'restore', side_effect=after):
            result = self.guard.run(app, ['--help'])
        self.assertEqual(result, 7)
        self.assertEqual(seen, [('loaded:before', 300)])

    def test_unexpected_preflight_error_still_restores_incremental_state(self):
        self.used.write_text(str(15 * GIB))
        app = self.root / 'mock-esde.sh'
        app.write_text('#!/bin/sh\nexit 0\n')
        app.chmod(0o755)
        seen = []
        def before(log, state):
            state.append(('unloaded-before-exception', 300))
            raise RuntimeError('simulated failure')
        with mock.patch.object(self.guard, 'unload_for_vram', side_effect=before), \
             mock.patch.object(self.guard, 'restore', side_effect=lambda log, state: seen.extend(state)):
            self.assertEqual(self.guard.run(app, []), 0)
        self.assertEqual(seen, [('unloaded-before-exception', 300)])

    def test_installed_wrapper_executes_real_binary_with_original_arguments(self):
        self.used.write_text(str(10 * GIB))  # 14 GiB free; Ollama unused
        self.guard.APPIMAGE.parent.mkdir(parents=True)
        shutil.copy2(sys.executable, self.guard.APPIMAGE)
        self.guard.APPIMAGE.chmod(0o755)
        self.guard.SCRIPT.parent.mkdir(parents=True)
        shutil.copy2(GUARD, self.guard.SCRIPT)
        self.guard.install()
        result = subprocess.run(
            [str(self.guard.APPIMAGE), '-c', 'import sys; print("CUSTOM_ARG="+sys.argv[1])', 'hello'],
            capture_output=True, text=True, timeout=8,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('CUSTOM_ARG=hello', result.stdout)
        self.assertIn('ENOUGH_VRAM', result.stdout)
        self.assertEqual(self.guard.REAL.read_bytes()[:4], b'\x7fELF')

    def test_insufficient_vram_blocks_launch_without_ollama(self):
        self.used.write_text(str(20 * GIB))  # 4 GiB free; protect Sunshine
        app = self.root / 'must-not-execute.sh'
        marker = self.root / 'unexpected-launch'
        app.write_text('#!/bin/sh\ntouch ' + str(marker) + '\n')
        app.chmod(0o755)
        with mock.patch.object(self.guard, 'loaded_models', side_effect=RuntimeError('Ollama API down')):
            self.assertEqual(self.guard.run(app, []), 75)
        self.assertFalse(marker.exists())
        self.assertIn('BLOCKED_ESDE_TO_PROTECT_SUNSHINE', self.log_text())

    def test_missing_vram_blocks_launch(self):
        self.total.unlink()
        app = self.root / 'must-not-execute.sh'
        marker = self.root / 'unexpected-launch'
        app.write_text('#!/bin/sh\ntouch ' + str(marker) + '\n')
        app.chmod(0o755)
        self.assertEqual(self.guard.run(app, []), 75)
        self.assertFalse(marker.exists())

    def log_text(self):
        return '\n'.join(p.read_text() for p in self.guard.LOGS.glob('esde-vram-*.jsonl'))

    def test_multiple_gpu_cards_prefers_largest_vram(self):
        intel = self.guard.SYSFS / 'card1/device'
        intel.mkdir(parents=True)
        (intel / 'mem_info_vram_total').write_text(str(2 * GIB))
        (intel / 'mem_info_vram_used').write_text(str(1 * GIB))
        self.assertEqual(self.guard.vram()[2], 'card0')

    def test_http_api_full_unload_launch_restore(self):
        self.used.write_text(str(20 * GIB))  # only 4 GiB initially free
        actions = []
        device_used = self.used
        class FakeOllama(BaseHTTPRequestHandler):
            loaded = True
            def log_message(self, *args):
                pass
            def reply(self, response):
                payload = json.dumps(response).encode()
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)
            def do_GET(self):
                if self.path != '/api/ps':
                    self.send_error(404)
                    return
                models = [{'name': 'test:latest', 'size_vram': 9 * GIB}] if self.loaded else []
                self.reply({'models': models})
            def do_POST(self):
                self.assert_post_path()
                size = int(self.headers['Content-Length'])
                args = json.loads(self.rfile.read(size))
                assert args['prompt'] == '' and args['stream'] is False
                actions.append((args['model'], args['keep_alive']))
                type(self).loaded = args['keep_alive'] != 0
                device_used.write_text(str((20 if self.loaded else 10) * GIB))
                self.reply({'done': True})
            def assert_post_path(self):
                assert self.path == '/api/generate'
        server = ThreadingHTTPServer(('127.0.0.1', 0), FakeOllama)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.guard.API = 'http://127.0.0.1:' + str(server.server_address[1])
        app = self.root / 'quick-app.sh'
        app.write_text('#!/bin/sh\nexit 0\n')
        app.chmod(0o755)
        try:
            self.assertEqual(self.guard.run(app, []), 0)
            self.assertEqual(actions[0], ('test:latest', 0))
            self.assertEqual(actions[1][0], 'test:latest')
            self.assertGreaterEqual(actions[1][1], 300)
            self.assertEqual(device_used.read_text(), str(20 * GIB))
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=3)


if __name__ == '__main__':
    unittest.main(verbosity=2)
