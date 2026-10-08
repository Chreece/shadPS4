#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 shadPS4 Emulator Project
# SPDX-License-Identifier: GPL-2.0-or-later

"""Playtest the SSE4a reporting binary verified by the uploaded homebrew results."""

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
HELPER_REVISION = "251278d933106cb139b9b9df61230e274e60e1d9"
HELPERS = {
    "affinity_game_profile.py": "211ec6d217af6d552f3b2409e65e1579ac2a4aaa930f568c9701ab8ae0414b9c",
    "affinity_game_guard.py": "f56530b095fc4cbfc8424a23225e6d955f984129cf2bf81f2642b926a9759c88",
}
BINARY = "sse4a-report-build-d7ff0dffa041/shadps4"
BINARY_SHA = "8e9d11e300a550ed428e29af9951a5c9e8f8a832108a20d4f66d6d2a1d6d72de"
SOURCE_TREE = "cf5f4396c91519925e42ee7ae9c9be7514bc76f1"
SOURCE_CHANGE = "d7ff0dffa041d243d9d4161b78126a80086eeb5c"


def say(message):
    print(message, flush=True)


def digest(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def dependencies():
    for name, expected in HELPERS.items():
        path = HERE / name
        if path.exists():
            if digest(path) != expected:
                raise RuntimeError("Helper checksum mismatch: " + name)
            continue
        relative = "scripts/" + name
        routes = (
            ("GitHub API", f"https://api.github.com/repos/Chreece/shadPS4/contents/{relative}?ref={HELPER_REVISION}"),
            ("GitHub raw", f"https://raw.githubusercontent.com/Chreece/shadPS4/{HELPER_REVISION}/{relative}"),
        )
        errors = []
        for label, url in routes:
            say(f"DOWNLOAD={name} via {label}; IPv4; maximum 35 seconds")
            temporary = path.with_suffix(".download")
            try:
                result = subprocess.run([
                    "curl", "-4", "--fail", "--location", "--silent", "--show-error",
                    "--connect-timeout", "8", "--max-time", "30", "--max-filesize", "262144",
                    "--header", "Accept: application/vnd.github.raw+json",
                    "--header", "User-Agent: shadps4-playtest", "--output", str(temporary), url,
                ], capture_output=True, text=True, timeout=35)
                if result.returncode == 0 and digest(temporary) == expected:
                    temporary.replace(path)
                    break
                error = result.stderr.strip() or "Checksum mismatch"
            except (OSError, subprocess.TimeoutExpired) as exception:
                error = str(exception)
            finally:
                temporary.unlink(missing_ok=True)
            errors.append(f"{label}: {error}")
            say("RETRY=" + error)
        else:
            raise RuntimeError("Could not download " + name + ": " + "; ".join(errors))
    sys.path.insert(0, str(HERE))
    return importlib.import_module("affinity_game_profile"), importlib.import_module("affinity_game_guard")


def cancel(signum, frame):
    raise KeyboardInterrupt


def main():
    home = Path.home()
    evidence = Path(tempfile.mkdtemp(prefix="shadps4-sse4a-pes-", dir=home))
    cache = home / ".cache/shadps4-affinity-20261007"
    installed = home / "Applications/shadps4/shadps4"
    summary = {"source_tree": SOURCE_TREE, "source_change": SOURCE_CHANGE,
               "binary_sha256": BINARY_SHA, "runner_sha256": digest(__file__), "built": False,
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
        binary = cache / BINARY
        say("VERIFY=Checking the exact binary from the passing SSE4a homebrew run")
        if digest(binary) != BINARY_SHA:
            raise RuntimeError("The candidate differs from the passing homebrew run; nothing changed")
        profile, games = dependencies()
        if profile.emulators():
            raise RuntimeError("Close the current emulator normally before starting this test")
        work = Path(tempfile.mkdtemp(prefix="sse4a-pes-", dir=cache))
        wrapper = home / ".local/bin/shadps4-esde"
        if wrapper.is_symlink():
            raise RuntimeError("ES-DE wrapper is a symlink; left untouched")
        original = wrapper.read_text()
        (evidence / "launcher-before.sh").write_text(original)
        games.hook(original, wrapper, work / "preflight.json", "CUSA18676")
        say("CANDIDATE=Verified SSE4a reporting binary; no build needed")
        say("PLAY=Start a day match, play briefly, leave the match, then start a night match.")
        say("Then exit PES normally. Keep Moonlight connected until UPLOAD_ONLY appears.")
        result = games.run_stage("CUSA18676", "PES SSE4a reporting", "native", binary,
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
