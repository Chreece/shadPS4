#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 shadPS4 Emulator Project
# SPDX-License-Identifier: GPL-2.0-or-later

"""Build automatic CPU identity startup on the tested source and play PES through ES-DE."""

import fcntl
import hashlib
import importlib
import json
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tarfile
import tempfile


HERE = Path(__file__).resolve().parent
SOURCE_TREE = "cf5f4396c91519925e42ee7ae9c9be7514bc76f1"


def say(message):
    print(message, flush=True)


def digest(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def dependencies():
    manifest = json.loads((HERE / "manifest.json").read_text())
    for name, expected in manifest.items():
        if digest(HERE / name) != expected:
            raise RuntimeError("Package checksum mismatch: " + name)
    sys.path.insert(0, str(HERE))
    return importlib.import_module("affinity_game_profile"), importlib.import_module("affinity_game_guard")


def cancel(signum, frame):
    raise KeyboardInterrupt


def main():
    home = Path.home()
    evidence = Path(tempfile.mkdtemp(prefix="shadps4-auto-cpu-pes-", dir=home))
    cache = home / ".cache/shadps4-affinity-20261007"
    installed = home / "Applications/shadps4/shadps4"
    summary = {"source_tree": SOURCE_TREE, "runner_sha256": digest(__file__), "built": False,
               "visual_result": "requires user observation", "capture_complete": False}
    profile = games = work = lock = before = None
    handlers = {sig: signal.signal(sig, cancel) for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP)}
    try:
        if len(sys.argv) != 1:
            raise RuntimeError("This playtest takes no arguments")
        if not cache.is_dir():
            raise RuntimeError("The tested build cache is missing; nothing changed")
        lock = (cache / "homebrew.lock").open("a")
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError("Another test is running; finish it before starting this playtest") from None
        before = digest(installed)
        profile, games = dependencies()
        if profile.emulators():
            raise RuntimeError("Close the current emulator normally before starting this test")
        work = Path(tempfile.mkdtemp(prefix="auto-cpu-pes-", dir=cache))
        wrapper = home / ".local/bin/shadps4-esde"
        if wrapper.is_symlink():
            raise RuntimeError("ES-DE wrapper is a symlink; left untouched")
        original = wrapper.read_text()
        (evidence / "launcher-before.sh").write_text(original)
        games.hook(original, wrapper, work / "preflight.json", "CUSA18676")
        from build_candidate import build
        say("BUILD=Preparing automatic CPU identity startup on your tested graphics source. Keep games closed.")
        binary, info = build(cache, evidence)
        summary.update(info)
        say("CANDIDATE=Built with the bundled runtime. Start PES only after READY below.")
        say("PLAY=Start a day match, play briefly, leave the match, then start a night match.")
        say("Then exit PES normally. Keep Moonlight connected until UPLOAD_ONLY appears.")
        result = games.run_stage("CUSA18676", "PES automatic CPU identity", "auto", binary,
                                 [str(binary)], work, evidence)
        summary["game"] = result
        summary["capture_complete"] = result["capture_complete"]
        summary["clean_exit_verified"] = result["clean_exit_verified"]
    except KeyboardInterrupt:
        summary["error"] = "Interrupted; collecting evidence and stopping only this test"
        say("INTERRUPTED=Collecting evidence")
    except Exception as error:
        summary["error"] = str(error)
        say("TEST_ERROR=" + str(error))
    finally:
        for sig in handlers:
            signal.signal(sig, signal.SIG_IGN)
        try:
            active = False
            errors = []
            if games is not None and work is not None:
                for path in work.glob("*/job.json"):
                    try:
                        job = json.loads(path.read_text())
                        for action in (games.restore, games.stop_owned):
                            try:
                                action(job)
                            except Exception as error:
                                errors.append(str(error))
                        status_path = path.parent / "status.json"
                        status = json.loads(status_path.read_text()) if status_path.exists() else {}
                        active |= bool(status.get("bridge") and games.alive(status["bridge"])) or bool(games.owned(job))
                    except Exception as error:
                        errors.append(str(error))
            try:
                summary["installed_binary_unchanged"] = before is not None and digest(installed) == before
            except OSError as error:
                summary["installed_binary_unchanged"] = False
                errors.append(str(error))
            summary["cleanup_errors"] = errors
            summary["capture_complete"] &= not errors and not active and summary["installed_binary_unchanged"]
            (evidence / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
            archive_path = evidence.with_suffix(".tar.gz")
            with tarfile.open(archive_path, "w:gz") as archive:
                archive.add(evidence, arcname=evidence.name)
            if work is not None and not active and not errors:
                shutil.rmtree(work)
            say("UPLOAD_ONLY=" + str(archive_path))
            say("SSH stays open. The installed emulator and original saves were not replaced.")
        finally:
            if lock is not None:
                lock.close()
            for sig, handler in handlers.items():
                signal.signal(sig, handler)
    return 0 if summary["capture_complete"] and summary.get("clean_exit_verified") else 1


if __name__ == "__main__":
    raise SystemExit(main())
