#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright 2026 shadPS4 Contributors
# SPDX-License-Identifier: GPL-2.0-or-later
"""Fast self-contained smoke checks for the automatic GoW game observer."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from types import SimpleNamespace

GAMEPLAY = Path(__file__).with_name("gow_upstream_gameplay.py")
SPEC = importlib.util.spec_from_file_location("gow_upstream_gameplay", GAMEPLAY)
assert SPEC is not None and SPEC.loader is not None
gameplay = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(gameplay)


class GameplayObserverTests(unittest.TestCase):
    def test_stream_sha256(self):
        with tempfile.TemporaryDirectory() as dirname:
            path = Path(dirname) / "test.bin"
            path.write_bytes(b"abc" * (1024 * 1024))
            self.assertEqual(gameplay.file_sha256(path),
                             hashlib.sha256(path.read_bytes()).hexdigest())

    def test_log_summary_extracts_shader_and_pm4_warnings(self):
        with tempfile.TemporaryDirectory() as dirname:
            root = Path(dirname)
            log = root / "game.log"
            log.write_text(
                "[Render] <Error> Clamped size from 4294967295 to 123 for stage 0xabcdef12\n"
                "[Render] <Warning> Unhandled compute SetQueueReg: vqid=4, reg_offset=0xb\n"
                "[Shader] <Warning> Shader 0xabcdef12: sharp source was not flattened\n",
                encoding="utf-8")
            result = gameplay.summarize_log(log, root)
            self.assertEqual(result["lines"], 3)
            self.assertEqual(result["counts"]["buffer_clamps"], 1)
            self.assertEqual(result["counts"]["queue_register"], 1)
            self.assertEqual(result["counts"]["shader_unflattened"], 1)
            self.assertEqual(result["top_shader_hashes"][0][0], "abcdef12")
            self.assertTrue((root / "console-excerpt.log").is_file())

    def test_absent_console_reports_failure_without_crashing(self):
        with tempfile.TemporaryDirectory() as dirname:
            root = Path(dirname)
            self.assertEqual(gameplay.summarize_log(root / "missing.log", root),
                             {"console_missing": True})

    def test_core_metadata_never_requires_core_dump_file(self):
        with tempfile.TemporaryDirectory() as dirname:
            root = Path(dirname)
            with patch.object(gameplay.shutil, "which", return_value=None):
                with patch.object(gameplay.subprocess, "run") as execute:
                    gameplay.capture_crash_metadata(1234567, root)
                    execute.assert_not_called()
            saved = json.loads((root / "crash-metadata.json").read_text())
            self.assertEqual(saved["pid"], 1234567)
            self.assertFalse(saved["coredumpctl_available"])

    def test_core_capture_uses_exact_pid_without_extraction(self):
        with tempfile.TemporaryDirectory() as dirname:
            root = Path(dirname)
            record = []
            def fake_run(argv, **kwargs):
                record.append(argv)
                return SimpleNamespace(returncode=0, stdout="Signal: 11 (SEGV)",
                                       stderr="")
            def which(program):
                return f"/usr/bin/{program}" if program in ("coredumpctl", "gdb") else None
            with patch.object(gameplay.shutil, "which", side_effect=which):
                with patch.object(gameplay.subprocess, "run", side_effect=fake_run):
                    gameplay.capture_crash_metadata(888888, root)
            self.assertEqual(len(record), 2)
            self.assertIn("info", record[0])
            self.assertIn("debug", record[1])
            self.assertEqual(record[0][-1], "888888")
            self.assertEqual(record[1][-1], "888888")
            self.assertFalse(any("dump" in args for args in record
                                 if args and args[0] == "coredumpctl"))
            self.assertTrue((root / "systemd-core-gdb-stacks.txt").exists())

    def test_audio_route_snapshot_has_no_mutating_actions(self):
        with tempfile.TemporaryDirectory() as dirname:
            root = Path(dirname)
            commands = []
            def fake_run(argv, **options):
                commands.append(argv)
                return SimpleNamespace(returncode=0, stdout="Default Sink: test.sink\n",
                                       stderr="")
            with patch.object(gameplay.shutil, "which",
                              side_effect=lambda p: "/usr/bin/" + p):
                with patch.object(gameplay.subprocess, "run", side_effect=fake_run):
                    result = gameplay.capture_audio_route(root, "test", {})
            self.assertTrue((root / "host-audio-route-test.json").exists())
            self.assertEqual(result["pactl_info"], 0)
            self.assertTrue(commands)
            self.assertTrue(all(args[0] in ("pactl", "wpctl") for args in commands))
            self.assertFalse(any("set-" in part or part in ("set", "mute", "move")
                                 for argv in commands for part in argv))

    def test_audio_log_counters_preserve_pipeline_stages(self):
        with tempfile.TemporaryDirectory() as dirname:
            root = Path(dirname)
            log = root / "audio.log"
            log.write_text(
                "sceAudio3dInitialize: called\n"
                "sceAudioOutInit: Audio system initialized\n"
                "sceAudioOutOpen: opened port_type=Main\n"
                "Opened audio device: Default Device\n"
                "SDL audio: first non-silent PCM on port 0\n"
                "SDL audio route: port=0, queued_bytes=4096\n", encoding="utf-8")
            counted = gameplay.summarize_log(log, root)["counts"]
            for marker in ("audio3d_initialized", "guest_audio_initialized",
                           "audio_main_port", "audio_sdl_device_open",
                           "audio_pcm_detected", "audio_pcm_route"):
                self.assertEqual(counted[marker], 1, marker)

    def test_runtime_proc_queries_are_read_only(self):
        if not Path("/proc").is_dir():
            self.skipTest("Linux procfs not present")
        self.assertIsInstance(gameplay.active_emulators(), list)
        self.assertIsInstance(gameplay.sample_threads(os.getpid()), dict)


if __name__ == "__main__":
    result = unittest.TextTestRunner(verbosity=1).run(
        unittest.defaultTestLoader.loadTestsFromTestCase(GameplayObserverTests))
    if not result.wasSuccessful():
        raise SystemExit(1)
    print(f"GAME_OBSERVER_UNIT_TEST=PASS cases={result.testsRun}")
