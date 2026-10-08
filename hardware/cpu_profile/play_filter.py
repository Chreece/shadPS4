#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 shadPS4 Emulator Project
# SPDX-License-Identifier: GPL-2.0-or-later

"""Playtest the CPU feature-filter binary that passed the uploaded homebrew run."""

import fcntl
import hashlib
import importlib
import json
from pathlib import Path
import shutil
import signal
import sys
import tarfile
import tempfile
import urllib.request


HERE = Path(__file__).resolve().parent
HELPER_REVISION = "251278d933106cb139b9b9df61230e274e60e1d9"
HELPERS = {
    "affinity_game_profile.py": "211ec6d217af6d552f3b2409e65e1579ac2a4aaa930f568c9701ab8ae0414b9c",
    "affinity_game_guard.py": "f56530b095fc4cbfc8424a23225e6d955f984129cf2bf81f2642b926a9759c88",
}
BINARY = "profile-build-c7814c49f926/shadps4"
BINARY_SHA = "63144d096215a6163fd84c43d4622c253a44880806d7a692babb7b739c8edf7a"
SOURCE_TREE = "db804b1ffcc3098ed7c6a0725a4631abc373ec64"


def dependencies():
    for name, expected in HELPERS.items():
        path = HERE / name
        if path.is_file():
            data = path.read_bytes()
        else:
            url = f"https://raw.githubusercontent.com/Chreece/shadPS4/{HELPER_REVISION}/scripts/{name}"
            with urllib.request.urlopen(url, timeout=30) as response:
                data = response.read(256 * 1024 + 1)
        if hashlib.sha256(data).hexdigest() != expected:
            raise RuntimeError("Helper checksum mismatch: " + name)
        if not path.exists():
            path.write_bytes(data)
    sys.path.insert(0, str(HERE))
    return importlib.import_module("affinity_game_profile"), importlib.import_module("affinity_game_guard")


def cancel(signum, frame):
    raise KeyboardInterrupt


def main():
    if len(sys.argv) != 1:
        raise RuntimeError("This playtest takes no arguments")
    profile, games = dependencies()
    home = Path.home()
    cache = home / ".cache/shadps4-affinity-20261007"
    if not cache.is_dir():
        raise RuntimeError("The tested build cache is missing; nothing changed")
    with (cache / "homebrew.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        evidence = Path(tempfile.mkdtemp(prefix="shadps4-cpu-filter-pes-", dir=home))
        work = Path(tempfile.mkdtemp(prefix="cpu-filter-pes-", dir=cache))
        installed = home / "Applications/shadps4/shadps4"
        before = profile.digest(installed)
        summary = {"source_tree": SOURCE_TREE, "binary_sha256": BINARY_SHA,
                   "runner_sha256": profile.digest(__file__), "built": False,
                   "visual_result": "requires user observation", "capture_complete": False}
        handlers = {sig: signal.signal(sig, cancel) for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP)}
        try:
            if profile.emulators():
                raise RuntimeError("Close the current emulator normally before starting this test")
            binary = cache / BINARY
            if profile.digest(binary) != BINARY_SHA:
                raise RuntimeError("The binary differs from the passing homebrew run; nothing changed")
            wrapper = home / ".local/bin/shadps4-esde"
            if wrapper.is_symlink():
                raise RuntimeError("ES-DE wrapper is a symlink; left untouched")
            original = wrapper.read_text()
            (evidence / "launcher-before.sh").write_text(original)
            games.hook(original, wrapper, work / "preflight.json", "CUSA18676")
            profile.say("CANDIDATE=Reusing the exact CPU feature-filter binary; no build needed")
            profile.say("PLAY=Start a day match, play briefly, leave the match, then start a night match.")
            profile.say("Keep Moonlight connected until the game has exited and UPLOAD_ONLY appears.")
            result = games.run_stage("CUSA18676", "PES CPU feature filter", "native", binary,
                                     [str(binary)], work, evidence)
            summary["game"] = result
            summary["capture_complete"] = result["capture_complete"]
            summary["clean_exit_verified"] = result["clean_exit_verified"]
        except KeyboardInterrupt:
            summary["error"] = "Interrupted; collecting evidence and stopping only this test"
            profile.say("INTERRUPTED=Collecting evidence")
        except Exception as error:
            summary["error"] = str(error)
            profile.say("TEST_ERROR=" + str(error))
        finally:
            for sig in handlers:
                signal.signal(sig, signal.SIG_IGN)
            try:
                active = False
                errors = []
                for path in work.glob("*/job.json"):
                    job = json.loads(path.read_text())
                    for action in (games.restore, games.stop_owned):
                        try:
                            action(job)
                        except Exception as error:
                            errors.append(str(error))
                    status_path = path.parent / "status.json"
                    status = json.loads(status_path.read_text()) if status_path.exists() else {}
                    active |= bool(status.get("bridge") and games.alive(status["bridge"])) or bool(games.owned(job))
                summary["cleanup_errors"] = errors
                summary["installed_binary_unchanged"] = profile.digest(installed) == before
                summary["capture_complete"] &= not errors and not active and summary["installed_binary_unchanged"]
                games.write_json(evidence / "summary.json", summary)
                archive_path = evidence.with_suffix(".tar.gz")
                with tarfile.open(archive_path, "w:gz") as archive:
                    archive.add(evidence, arcname=evidence.name)
                if not active and not errors:
                    shutil.rmtree(work)
                profile.say("UPLOAD_ONLY=" + str(archive_path))
                profile.say("SSH stays open. The installed emulator and original saves were not replaced.")
            finally:
                for sig, handler in handlers.items():
                    signal.signal(sig, handler)
        return 0 if summary["capture_complete"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
