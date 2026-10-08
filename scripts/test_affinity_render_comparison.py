#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 shadPS4 Emulator Project
# SPDX-License-Identifier: GPL-2.0-or-later

"""Compare two existing binaries through the normal guarded PES launcher."""

import fcntl
import json
from pathlib import Path
import shutil
import signal
import sys
import tarfile
import tempfile

import affinity_game_guard as games
import affinity_game_profile as profile

AFFINITY_COMMIT = "53d152a07bf08438c5fba84e09deec014d9a891a"
AFFINITY_SHA = "e924d095eebeb078d04b5182fc0406fa3624e0c68bec30e877692fa169962456"
PREVIOUS = {
    "commit": "ad8e42e098e7a529227f2a5b2a921070c82b029f",
    "binary_sha256": "d748d94c98d6c26324723beac69931d8f6c9f3665227c8fba6ab3134d19988e5",
    "installed_sha256": "6bd789967f77e3e8eb315e5d7fde920928c6f6cc86f598d973f733e64a31bfd2",
    "config_sha256": "233fb59cb471ea28e4633b50901386387927ac974f2f77115e826c5e418f1d8c",
    "users_sha256": "01cec94697af5e58e4a18831e7a458c95297186b5a50fa898bcf34069d299de1",
    "evidence": "shadps4-combined-evidence-n63pccyq.tar.gz",
    "mode": "native",
    "observation": "User screenshots show white/missing rendering; clean exit was not recorded",
}


def cancel(signum, frame):
    raise KeyboardInterrupt


def main():
    if len(sys.argv) != 1:
        raise RuntimeError("This pinned runner takes no arguments")
    home = Path.home()
    cache = home / ".cache/shadps4-affinity-20261007"
    cache.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix="render-comparison-", dir=cache))
    evidence = Path(tempfile.mkdtemp(prefix="shadps4-render-comparison-", dir=home))
    summary = {"purpose": "Compare installed and affinity-only rendering with the recorded native CPU-ID run",
               "previous_cpu_id_run": PREVIOUS, "games": [], "installed_binary_replaced": False,
               "homebrew_repeated": False, "build_performed": False,
               "runner_sha256": profile.digest(__file__)}
    lock = (cache / "homebrew.lock").open("a")
    handlers = {sig: signal.signal(sig, cancel) for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP)}
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if profile.emulators():
            raise RuntimeError("An emulator is running; close it normally before this comparison")
        installed = (home / "Applications/shadps4/shadps4").resolve(strict=True)
        affinity = cache / "build-53d152a07bf0/shadps4"
        if profile.digest(affinity) != AFFINITY_SHA:
            raise RuntimeError("The cached affinity-only binary is missing or changed; no build or launch attempted")
        installed_sha = profile.digest(installed)
        summary["binaries"] = {
            "installed-baseline": {"path": str(installed), "sha256": installed_sha,
                                   "matches_previous_installed": installed_sha == PREVIOUS["installed_sha256"]},
            "affinity-only": {"path": str(affinity), "sha256": AFFINITY_SHA, "commit": AFFINITY_COMMIT}}
        wrapper = home / ".local/bin/shadps4-esde"
        if wrapper.is_symlink():
            raise RuntimeError("ES-DE wrapper is a symlink; left untouched")
        original = wrapper.read_text()
        (evidence / "launcher-before.sh").write_text(original)
        games.hook(original, wrapper, work / "preflight.json", "CUSA18676")
        profile.say("COMPARISON=Two PES launches: installed-baseline, then affinity-only. No build or homebrew rerun.")
        profile.say("Use the same match/stadium/camera. Take one screenshot per mode, then exit the game normally.")
        profile.say("Wait for each READY before launching. Ctrl+C collects evidence and stops this test only.")
        preservation = None
        for mode, binary in [("installed-baseline", installed), ("affinity-only", affinity)]:
            profile.say("BINARY=" + mode + " " + summary["binaries"][mode]["sha256"])
            result = games.run_stage("CUSA18676", "PES", mode, binary, [str(binary)], work, evidence,
                                     expected_preservation=preservation)
            summary["games"].append(result)
            games.write_json(evidence / "summary.json", summary)
            if not result["capture_complete"]:
                raise RuntimeError("Capture or preservation check incomplete for " + mode + "; evidence collected")
            current = json.loads((evidence / ("CUSA18676-" + mode) / "preservation.json").read_text())
            if preservation is None:
                preservation = current
                summary["profile_matches_previous_cpu_id_run"] = all(
                    current[key] == PREVIOUS[key] for key in ("config_sha256", "users_sha256"))
            profile.say("CAPTURED=" + mode + "; clean process exit " +
                        ("verified" if result["clean_exit_verified"] else "not verified; termination details recorded"))
        settings = [json.loads((evidence / ("CUSA18676-" + mode) / "graphics-settings.json").read_text())
                    for mode in ("installed-baseline", "affinity-only")]
        summary["same_graphics_settings"] = settings[0] == settings[1]
        summary["captures_complete"] = True
        summary["visual_result"] = "Requires the user's two labelled screenshots; logs do not prove correct rendering"
    except KeyboardInterrupt:
        summary["error"] = "Interrupted; collecting completed and partial stages"
        profile.say("INTERRUPTED=Collecting evidence")
    except Exception as error:
        summary["error"] = str(error)
        profile.say("TEST_ERROR=" + str(error))
    finally:
        for sig in handlers:
            signal.signal(sig, signal.SIG_IGN)
        try:
            active = False
            cleanup_errors = []
            for path in work.glob("*/job.json"):
                job = json.loads(path.read_text())
                for action in (games.restore, games.stop_owned):
                    try:
                        action(job)
                    except Exception as error:
                        cleanup_errors.append(str(error))
                status_file = path.parent / "status.json"
                status = json.loads(status_file.read_text()) if status_file.exists() else {}
                active |= bool(status.get("bridge") and games.alive(status["bridge"])) or bool(games.owned(job))
            if cleanup_errors:
                summary["cleanup_error"] = cleanup_errors
            summary["stage_results"] = [str(path.relative_to(evidence)) for path in evidence.glob("*/result.json")]
            games.write_json(evidence / "summary.json", summary)
            archive_path = evidence.with_suffix(".tar.gz")
            with tarfile.open(archive_path, "w:gz") as archive:
                archive.add(evidence, arcname=evidence.name)
            if not active and not cleanup_errors:
                shutil.rmtree(work)
            profile.say("UPLOAD_ONLY=" + str(archive_path))
            profile.say("SSH remains open. The installed binary was not replaced; profile checks are in the archive.")
        finally:
            lock.close()
            for sig, handler in handlers.items():
                signal.signal(sig, handler)
    return 1 if "error" in summary or "cleanup_error" in summary else 0


if __name__ == "__main__":
    raise SystemExit(main())
