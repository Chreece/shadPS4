# SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
# SPDX-License-Identifier: GPL-2.0-or-later
"""Installer regression tests: only temporary homes and synthetic artifacts."""
import contextlib
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock
import zipfile

spec = importlib.util.spec_from_file_location("deploy", Path(__file__).with_name("deploy_test.py"))
deploy = importlib.util.module_from_spec(spec)
spec.loader.exec_module(deploy)


class DeploymentTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.home = Path(self.directory.name)
        self.wrapper = self.home / ".local/bin/shadps4-esde"
        self.wrapper.parent.mkdir(parents=True)
        self.original = (
            '#!/bin/bash\nCORE=/working/shadps4\nSPARSEQUEUE=/working/sparse/shadps4\n'
            'GAME_ARG="$1"\ncase "$GAME_ARG" in CUSA36843) CORE="$SPARSEQUEUE";; esac\n'
            '"$CORE" --game "$GAME_ARG" --fullscreen true\n'
        ).encode()
        self.wrapper.write_bytes(self.original)
        self.wrapper.chmod(0o751)
        self.archive = self.home / "fixture.zip"
        with zipfile.ZipFile(self.archive, "w") as zipped:
            zipped.write("/bin/true", "shadps4")
        self.archive_bytes = self.archive.read_bytes()
        self.artifact = {
            "id": 123, "digest": "sha256:" + deploy.digest(self.archive_bytes),
            "size_in_bytes": len(self.archive_bytes),
        }
        self.patches = contextlib.ExitStack()
        self.addCleanup(self.patches.close)
        self.patches.enter_context(mock.patch.object(deploy, "no_running_core"))
        self.patches.enter_context(mock.patch.object(deploy, "say"))

    def mock_install_inputs(self, broken=False):
        self.patches.enter_context(mock.patch.object(deploy, "artifact_metadata", return_value=self.artifact))
        def download(_artifact, destination):
            destination.write_bytes(b"corrupted" if broken else self.archive_bytes)
        self.patches.enter_context(mock.patch.object(deploy, "download", side_effect=download))
        self.patches.enter_context(mock.patch.object(deploy, "smoke"))

    def test_real_shell_uses_new_core_after_game_specific_override(self):
        core = self.home / "release with spaces/shadps4"
        core.parent.mkdir()
        core.write_text('#!/bin/bash\nprintf "%s\\n" "$@"\n')
        core.chmod(0o755)
        patched = deploy.selected_wrapper(self.original, core)
        self.wrapper.write_bytes(patched)
        result = subprocess.run(["bash", str(self.wrapper), "CUSA36843"],
                                capture_output=True, text=True, check=True)
        self.assertEqual(result.stdout.splitlines(), ["--game", "CUSA36843", "--fullscreen", "true"])
        self.assertIn('SPARSEQUEUE=/working/sparse/shadps4', patched.decode())

    def test_install_and_restore_preserve_old_binary_settings_and_wrapper_mode(self):
        self.mock_install_inputs()
        old = self.home / "Applications/shadps4/shadps4"
        old.parent.mkdir(parents=True)
        old.write_bytes(b"old binary")
        config = self.home / ".local/share/shadPS4/config.json"
        config.parent.mkdir(parents=True)
        config.write_text('{"audioChannels":8}')
        deploy.install(self.home, 0)
        self.assertIn(deploy.MARKER.encode(), self.wrapper.read_bytes())
        self.assertEqual(self.wrapper.stat().st_mode & 0o777, 0o751)
        states = list((self.home / ".local/state/shadps4-ngs2").glob("*/deployment.json"))
        self.assertEqual(len(states), 1)
        state = json.loads(states[0].read_text())
        self.assertEqual(Path(state["binary"]).read_bytes(), Path("/bin/true").read_bytes())
        self.assertTrue((states[0].parent / "restore.sh").exists())
        deploy.restore(states[0])
        self.assertEqual(self.wrapper.read_bytes(), self.original)
        self.assertEqual(old.read_bytes(), b"old binary")
        self.assertEqual(config.read_text(), '{"audioChannels":8}')

    def test_probe_dispatch_routes_only_test_entry_and_restores(self):
        self.mock_install_inputs()
        fallback = self.wrapper.with_name("shadps4-esde.before-ngs2-probe.20260930-223701")
        fallback.write_text('#!/bin/bash\nprintf "original\\n"\nprintf "%s\\n" "$@"\n')
        original = deploy.probe_wrapper(self.home, fallback).encode()
        self.wrapper.write_bytes(original)
        deploy.install(self.home, 0)
        binary = self.home / "Applications/shadps4/releases/ngs2-f00bef80/shadps4"
        binary.write_text('#!/bin/bash\nprintf "test-core\\n"\nprintf "%s\\n" "$@"\n')
        entry = self.home / "probe entry.txt"
        entry.write_bytes(b"CUSA36843|ngs2probe\r\n")
        result = subprocess.run(["bash", str(self.wrapper), str(entry)],
                                capture_output=True, text=True, check=True)
        self.assertEqual(result.stdout.splitlines(),
                         ["test-core", "--game", "CUSA36843", "--fullscreen", "true"])
        for token in ("CUSA36843", "OTHER|ngs2probe"):
            entry.write_text(token + "\n")
            result = subprocess.run(["bash", str(self.wrapper), str(entry), "argument with spaces"],
                                    capture_output=True, text=True, check=True)
            self.assertEqual(result.stdout.splitlines(), ["original", str(entry), "argument with spaces"])
        state = next((self.home / ".local/state/shadps4-ngs2").glob("*/deployment.json"))
        deploy.restore(state)
        self.assertEqual(self.wrapper.read_bytes(), original)

    def test_probe_dispatch_rejects_unknown_logic(self):
        original = deploy.probe_wrapper(self.home, self.wrapper.with_name(
            "shadps4-esde.before-ngs2-probe.20260930-223701"))
        binary = self.home / "Applications/shadps4/releases/ngs2-f00bef80/shadps4"
        for changed in (original.replace("CUSA36843|ngs2probe", "OTHER|ngs2probe"),
                        original.replace("run_probe.py", "different.py"),
                        original + "echo extra-logic\n"):
            with self.subTest(changed=changed):
                with self.assertRaisesRegex(RuntimeError, "differs"):
                    deploy.selected_wrapper(changed.encode(), binary)

    def test_restore_refuses_to_erase_later_user_edits(self):
        self.mock_install_inputs()
        deploy.install(self.home, 0)
        edited = self.wrapper.read_bytes() + b"# user change\n"
        self.wrapper.write_bytes(edited)
        state = next((self.home / ".local/state/shadps4-ngs2").glob("*/deployment.json"))
        with self.assertRaisesRegex(RuntimeError, "changed after deployment"):
            deploy.restore(state)
        self.assertEqual(self.wrapper.read_bytes(), edited)

    def test_bad_archive_and_startup_failure_do_not_switch_wrapper(self):
        self.mock_install_inputs(broken=True)
        with self.assertRaises(zipfile.BadZipFile):
            deploy.install(self.home, 0)
        self.assertEqual(self.wrapper.read_bytes(), self.original)
        with mock.patch.object(deploy, "download", side_effect=lambda a, p: p.write_bytes(self.archive_bytes)):
            with mock.patch.object(deploy, "smoke", side_effect=RuntimeError("missing dependency")):
                with self.assertRaisesRegex(RuntimeError, "missing dependency"):
                    deploy.install(self.home, 0)
        self.assertEqual(self.wrapper.read_bytes(), self.original)

    def test_changed_wrapper_during_download_is_not_overwritten(self):
        self.mock_install_inputs()
        edited = self.original + b"# changed while downloading\n"
        def download(_artifact, destination):
            destination.write_bytes(self.archive_bytes)
            self.wrapper.write_bytes(edited)
        with mock.patch.object(deploy, "download", side_effect=download):
            with self.assertRaisesRegex(RuntimeError, "changed during download"):
                deploy.install(self.home, 0)
        self.assertEqual(self.wrapper.read_bytes(), edited)

    def test_unrecognized_ambiguous_or_duplicate_patch_is_rejected(self):
        variants = [
            b'#!/bin/bash\nother --game "$1"\n',
            self.original + b'"$CORE" --game "$2"\n',
            self.original.replace(b'"$CORE" --game', b'echo \\\n"$CORE" --game'),
            deploy.selected_wrapper(self.original, self.home / "new"),
        ]
        for original in variants:
            with self.subTest(original=original):
                with self.assertRaises(RuntimeError):
                    deploy.selected_wrapper(original, self.home / "new")

    def test_download_verifies_checksum_and_bounds_size(self):
        with mock.patch.object(deploy.urllib.request, "urlopen", return_value=io.BytesIO(self.archive_bytes)):
            deploy.download(self.artifact, self.home / "download.zip")
        wrong = dict(self.artifact, digest="sha256:" + "0" * 64)
        with mock.patch.object(deploy.urllib.request, "urlopen", return_value=io.BytesIO(self.archive_bytes)):
            with self.assertRaisesRegex(RuntimeError, "checksum"):
                deploy.download(wrong, self.home / "download.zip")
        with mock.patch.object(deploy.urllib.request, "urlopen", return_value=io.BytesIO(self.archive_bytes + b"x")):
            with self.assertRaisesRegex(RuntimeError, "size"):
                deploy.download(self.artifact, self.home / "download.zip")

    def test_compile_and_upload_gate_rejects_pending_or_failed_build(self):
        jobs = [
            {"name": name, "status": "completed", "conclusion": "success"}
            for name in ("Run C++ Tests on ubuntu-latest", "Run C++ Tests on windows-latest",
                         "Run C++ Tests on macos-26", "clang-format", "reuse")
        ]
        linux = {"name": "linux-sdl", "status": "in_progress", "conclusion": None,
                 "steps": [{"name": "Build", "conclusion": "success"},
                           {"name": "Run actions/upload-artifact@v7", "conclusion": None}]}
        jobs.append(linux)
        self.assertFalse(deploy.gate_ready(jobs))
        linux["steps"][1]["conclusion"] = "success"
        self.assertTrue(deploy.gate_ready(jobs))
        linux.update(status="completed", conclusion="failure")
        with self.assertRaises(RuntimeError):
            deploy.gate_ready(jobs)
        with self.assertRaises(RuntimeError):
            deploy.require_run({"id": deploy.BUILD, "head_sha": "older"}, deploy.BUILD)


if __name__ == "__main__":
    unittest.main()
