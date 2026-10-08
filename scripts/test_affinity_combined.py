#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 shadPS4 Emulator Project
# SPDX-License-Identifier: GPL-2.0-or-later

"""Build and validate the combined affinity/CPU-ID candidate, then capture guarded game tests."""

import fcntl
import json
import os
from pathlib import Path
import re
import shutil
import signal
import sys
import tarfile
import tempfile

import affinity_game_guard as games
import affinity_game_profile as profile
import test_affinity_hotplug_cpu_id as cpu

COMMIT = "ad8e42e098e7a529227f2a5b2a921070c82b029f"
cpu.CPU_ID_COMMIT = COMMIT
base = cpu.base


def suites(work):
    affinity_archive = work / "affinity.tar.gz"
    cpu.download(f"https://raw.githubusercontent.com/Chreece/shadPS4/{base.SUITE_COMMIT}/hardware/affinity_revision/suite.tar.gz",
                 base.SUITE_SHA256, affinity_archive)
    affinity = work / "affinity-suite"
    cpu.extract(affinity_archive, affinity)
    generated_archive = work / "generated.tar.gz"
    cpu.download(f"https://raw.githubusercontent.com/Chreece/shadPS4/{cpu.TEST_COMMIT}/hardware/generated_cpu_id/translation-suite.tar.gz",
                 cpu.TEST_SHA, generated_archive)
    generated = work / "generated-suite"
    cpu.extract(generated_archive, generated)
    faulting = work / "without_cpuid_faulting.py"
    cpu.download(f"https://raw.githubusercontent.com/Chreece/shadPS4/{cpu.TEST_COMMIT}/hardware/generated_cpu_id/without_cpuid_faulting.py",
                 cpu.FAULTING_SHA, faulting)
    for folder in [affinity, generated]:
        for case in json.loads((folder / "cases.json").read_text()):
            boot = folder / case.get("directory", case.get("name", "")) / "eboot.bin"
            if "sha256" in case and cpu.digest(boot) != case["sha256"]:
                raise RuntimeError("Homebrew checksum mismatch: " + str(boot))
    return affinity, generated, faulting


def homebrew(binary, translated, work, evidence, env, allowed, results):
    affinity, generated, faulting = suites(work)
    affinity_cases = json.loads((affinity / "cases.json").read_text())
    generated_cases = json.loads((generated / "cases.json").read_text())
    profiles = [("all", allowed), ("four", allowed[-4:]), ("two", allowed[-2:]),
                ("one", allowed[-1:]), ("sparse", allowed[1::2][:4] or allowed[:1])]
    unique = []
    for label, cpus in profiles:
        if tuple(cpus) not in {tuple(mask) for _, mask in unique}:
            unique.append((label, cpus))
    expected_affinity = sum(sum(not c.get("cpu_id") and
        (label in {"all", "two", "one"} or c.get("extended", False)) for c in affinity_cases)
        for label, _ in unique)
    expected = expected_affinity + 4 * len(unique) + 9

    def record(result):
        results.append(result)
        (evidence / "homebrew-progress.json").write_text(json.dumps(results, indent=2) + "\n")
        if not result["ok"]:
            raise RuntimeError("Combined candidate failed " + result["name"] + "; game test was not armed")

    for label, cpus in unique:
        for case in affinity_cases:
            if case.get("cpu_id") or label not in {"all", "two", "one"} and not case.get("extended"):
                continue
            record(base.run_case(binary, case, affinity, cpus, label, work, evidence, env))
        for case in generated_cases:
            if "samples" in case:
                record(cpu.run_generated(translated, case, generated, cpus,
                    "translated-" + label + "-" + case["name"], work, evidence, env, translated=True))
    for case in generated_cases:
        if case["name"] in {"cpuid", "rdtscp"}:
            record(cpu.run_generated([sys.executable, str(faulting), *translated], case, generated,
                allowed, "no-faulting-" + case["name"], work, evidence, env,
                translated=True, no_faulting=True))
        if "expected" in case:
            for label, prefix in [("native", [str(binary)]), ("translated", translated)]:
                record(cpu.run_generated(prefix, case, generated, allowed,
                    label + "-" + case["name"], work, evidence, env, translated=label == "translated"))
    static = next(case for case in affinity_cases if case.get("cpu_id"))
    needs_translation = any("CPUID faulting unavailable" in path.read_text(errors="replace")
                            for path in evidence.glob("all-CPUM00000*.log"))
    state_binary = binary
    state_mode = "native"
    if needs_translation:
        state_binary = work / "run-cpu-id-state.py"
        state_binary.write_text("#!/usr/bin/env python3\nimport os, sys\nCOMMAND = " + repr(translated) +
                                "\nos.execv(COMMAND[0], COMMAND + sys.argv[1:])\n")
        state_binary.chmod(0o700)
        state_mode = "translated"
        base.say("CPU_ID_STATE=Using translation because this host cannot fault generated CPUID")
    for label, cpus in [("all", allowed), ("two", allowed[-2:]), ("one", allowed[-1:])]:
        result = base.run_case(state_binary, static, affinity, cpus,
                               state_mode + "-state-" + label, work, evidence, env)
        result["execution_mode"] = state_mode
        log = evidence / (result["name"] + ".log")
        active = "CPU identity translation active" in log.read_text(errors="replace")
        result["translation_active"] = active
        result["ok"] = result["ok"] and active == needs_translation
        record(result)
    if len(results) != expected:
        raise RuntimeError(f"Incomplete homebrew matrix: {len(results)}/{expected}")
    base.say(f"COMBINED_HOMEBREW={len(results)}/{expected} passed")


def installed_games():
    home = Path.home()
    source = home / "user"
    if not (source / "config.json").is_file():
        source = Path(os.environ.get("XDG_DATA_HOME", home / ".local/share")) / "shadPS4"
    config = json.loads((source / "config.json").read_text())
    roots = [profile.absolute(entry["path"], home) for entry in config["General"]["install_dirs"] if entry["enabled"]]
    found = {}
    visited = set()

    def scan(folder, depth):
        folder = folder.resolve()
        if depth < 0 or folder in visited or not folder.is_dir():
            return
        visited.add(folder)
        sfo = folder / "sce_sys/param.sfo"
        if sfo.is_file() and (folder / "eboot.bin").is_file():
            metadata = profile.read_sfo(sfo)
            title = metadata.get("TITLE", "")
            kind = ("pes" if re.search(r"\bPES\b|efootball|pro evolution soccer", title, re.I)
                    else "strikers" if re.search(r"persona\s*5\s*strikers", title, re.I) else None)
            serial = metadata.get("TITLE_ID") or metadata.get("CONTENT_ID", "")[7:16]
            if kind and re.fullmatch(r"CUSA\d{5}", serial) and not folder.name.endswith(("-UPDATE", "-patch", "-mods")):
                found[(kind, serial)] = {"kind": kind, "serial": serial, "title": title, "folder": str(folder)}
            return
        for child in folder.iterdir():
            if child.is_dir():
                scan(child, depth - 1)

    for root in roots:
        scan(root, 5)
    targets = []
    for kind in ["pes", "strikers"]:
        options = [game for game in found.values() if game["kind"] == kind]
        if len(options) > 1:
            preferred = next((game for game in options if game["serial"] == "CUSA18676"), None)
            if preferred is None:
                raise RuntimeError("Multiple installed editions found; no launcher modified: " + json.dumps(options))
            options = [preferred]
        targets.extend(options)
    return targets


def main():
    if len(sys.argv) != 1:
        raise RuntimeError("This pinned runner takes no arguments")
    cache = Path.home() / ".cache/shadps4-affinity-20261007"
    cache.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix="combined-test-", dir=cache))
    evidence = Path(tempfile.mkdtemp(prefix="shadps4-combined-evidence-", dir=Path.home()))
    summary = {"source_commit": COMMIT, "runner_sha256": cpu.digest(Path(__file__)),
               "installed_binary_replaced": False, "homebrew": [], "games": []}
    lock = (cache / "homebrew.lock").open("a")
    handlers = {sig: signal.signal(sig, cpu.cancel) for sig in (signal.SIGTERM, signal.SIGHUP)}
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        base.require_idle()
        if profile.emulators():
            raise RuntimeError("An emulator is still running; close it through the normal session")
        wrapper = Path.home() / ".local/bin/shadps4-esde"
        if wrapper.is_symlink():
            raise RuntimeError("ES-DE wrapper is a symlink; left untouched")
        original_wrapper = wrapper.read_text()
        (evidence / "launcher-before.sh").write_text(original_wrapper)
        games.hook(original_wrapper, wrapper, work / "preflight.json", "CUSA18676")
        targets = installed_games()
        summary["installed_test_games"] = targets
        summary["missing_games"] = [kind for kind in ["pes", "strikers"] if not any(game["kind"] == kind for game in targets)]
        allowed = sorted(os.sched_getaffinity(0))
        base.say("BUILD=" + COMMIT + "; keep games closed until READY appears")
        binary, translated, metadata = cpu.build_cpu_id(cache, work, evidence, allowed)
        summary["build"] = metadata
        homebrew(binary, translated, work, evidence, base.display_environment(), allowed, summary["homebrew"])
        summary["homebrew_passed"] = True
        for game in targets:
            task = "play a controllable match" if game["kind"] == "pes" else "select New Game and reach gameplay"
            base.say("GAME_TEST=" + game["title"] + ": " + task)
            for mode, prefix in [("native", [str(binary)]), ("translated", translated)]:
                result = games.run_stage(game["serial"], game["title"], mode, binary, prefix, work, evidence)
                summary["games"].append(result)
                if not result["capture_complete"]:
                    raise RuntimeError("Game capture incomplete: " + game["title"] + " [" + mode + "]; evidence collected")
        summary["capture_complete"] = bool(summary["games"]) and not summary["missing_games"]
        summary["playability_and_fps"] = "Awaiting user observation; clean process exit does not prove gameplay"
    except KeyboardInterrupt:
        summary["error"] = "Interrupted; guarded launcher restoration and owned-process cleanup requested"
        base.say("INTERRUPTED=Collecting evidence")
    except Exception as error:
        summary["error"] = str(error)
        base.say("TEST_ERROR=" + str(error))
    finally:
        for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
            signal.signal(sig, signal.SIG_IGN)
        try:
            active = False
            for path in work.glob("*/job.json"):
                job = json.loads(path.read_text())
                try:
                    games.restore(job)
                    games.stop_owned(job)
                    status_file = path.parent / "status.json"
                    status = json.loads(status_file.read_text()) if status_file.exists() else {}
                    active |= bool(status.get("bridge") and games.alive(status["bridge"]))
                except Exception as error:
                    summary["cleanup_error"] = str(error)
                    active = True
            (evidence / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
            archive_path = evidence.with_suffix(".tar.gz")
            with tarfile.open(archive_path, "w:gz") as archive:
                archive.add(evidence, arcname=evidence.name)
            if not active:
                shutil.rmtree(work)
            base.say("UPLOAD_ONLY=" + str(archive_path))
            if summary.get("missing_games"):
                base.say("NOT_INSTALLED=" + ", ".join(summary["missing_games"]))
            base.say("SSH remains open. Installed emulator and original saves were not replaced.")
        finally:
            lock.close()
            for sig, handler in handlers.items():
                signal.signal(sig, handler)
            signal.signal(signal.SIGINT, signal.default_int_handler)
    return 1 if "error" in summary or "cleanup_error" in summary else 0


if __name__ == "__main__":
    raise SystemExit(main())
