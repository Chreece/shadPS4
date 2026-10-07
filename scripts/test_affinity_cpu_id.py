#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 shadPS4 Emulator Project
# SPDX-License-Identifier: GPL-2.0-or-later

"""Build/test CPU identity, then collect a guarded PES session with the same binary."""

import fcntl
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import signal
import sys
import tarfile
import tempfile
import time
import urllib.error
import urllib.request


SOURCE = "ab86cf43ce0ade85bbe810d4ab012f3b4b796deb"
HELPER_REVISION = "a1546d48252ba5bca8afcec914b10751a5de1418"
HELPERS = {
    "test_affinity_revision.py": "cdfc210d92bc54758eb820ff23dba0b2d21f9ab70ae5ab02bc3245292201d724",
    "test_affinity_pes.py": "b94022c40c464ed5b1e807295175b93d545289f61dd3d19276276357c67704d7",
    "test_affinity_pes_guarded.py": "a222fa30886bcc02f2043c8654378dc52e4244ff344f6375d6fed4c0f4a5b1a6",
}


def say(message):
    print(message, flush=True)


def digest(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def load_module(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def expected_tests(allowed):
    profiles = [("all", allowed), ("four", allowed[-4:]), ("two", allowed[-2:]),
                ("one", allowed[-1:]), ("sparse", allowed[1::2][:4] or allowed[:1])]
    expected = {}
    seen = set()
    for label, cpus in profiles:
        if tuple(cpus) in seen:
            continue
        seen.add(tuple(cpus))
        cases = range(9) if label in {"all", "two", "one"} else [5, 6, 8]
        for case in cases:
            expected[f"{label}-CPUM{case:05d}"] = cpus
    return expected


def verified_homebrew(path, cache, allowed):
    result = json.loads(path.read_text())
    if (result.get("source_commit") != SOURCE or result.get("suite_commit") != SOURCE
            or result.get("runner_sha256") != HELPERS["test_affinity_revision.py"]
            or result.get("ok") is not True or result.get("startup_check") != "passed"
            or result.get("initial_host_cpus") != allowed):
        raise ValueError("Homebrew evidence is for a different or incomplete test")
    tests = result.get("tests", [])
    expected = expected_tests(allowed)
    if (len(tests) != len(expected) or {test["name"] for test in tests} != set(expected)
            or any(test.get("ok") is not True or test.get("returncode") != 0
                   or test.get("timeout") is not False
                   or test.get("host_cpus") != expected[test["name"]] for test in tests)):
        raise ValueError("The complete CPU-ID homebrew matrix has not passed")
    for test in tests:
        if test["name"].endswith("CPUM00008"):
            if (test.get("cpu_id") != "CPU_ID_STATE_RESULT failures=0 samples=1728"
                    or test.get("cpu_id_signals") != "CPU_ID_SIGNAL_RESULT failures=0 handled=32"
                    or len(test.get("host_mask_changes", [])) != 12):
                raise ValueError("CPU-ID instruction, signal or migration checks are missing")
    binary = cache / ("build-" + SOURCE[:12]) / "shadps4"
    checksum = result.get("binary_sha256", "")
    if (result.get("binary") != str(binary) or not re.fullmatch(r"[0-9a-f]{64}", checksum)
            or digest(binary) != checksum):
        raise ValueError("Candidate differs from the homebrew-tested binary")
    return result


def find_homebrew(home, cache, allowed):
    paths = sorted(home.glob("shadps4-affinity-evidence-*/summary.json"),
                   key=lambda path: path.stat().st_mtime_ns, reverse=True)
    for path in paths:
        try:
            return path, verified_homebrew(path, cache, allowed)
        except (OSError, ValueError, KeyError, TypeError):
            continue
    return None, None


def fetch_helpers(folder):
    for name, expected in HELPERS.items():
        url = f"https://raw.githubusercontent.com/Chreece/shadPS4/{HELPER_REVISION}/scripts/{name}"
        for attempt in range(3):
            try:
                with urllib.request.urlopen(url, timeout=30) as response:
                    data = response.read(1024 * 1024 + 1)
                break
            except (urllib.error.URLError, TimeoutError):
                if attempt == 2:
                    raise
                say("DOWNLOAD_RETRY=" + name)
                time.sleep(attempt + 1)
        if len(data) > 1024 * 1024 or hashlib.sha256(data).hexdigest() != expected:
            raise RuntimeError("Helper checksum mismatch: " + name)
        (folder / name).write_bytes(data)


def bind_candidate(path, checksum):
    if not re.fullmatch(r"[0-9a-f]{64}", checksum):
        raise ValueError("Invalid candidate checksum")
    if digest(path) != HELPERS[path.name]:
        raise RuntimeError("PES helper changed before candidate binding")
    content = path.read_text()
    for name, value in {"SOURCE": SOURCE, "BINARY_SHA256": checksum}.items():
        content, count = re.subn(r'^' + name + r' = "[0-9a-f]+"$',
                                 name + " = " + json.dumps(value), content, flags=re.MULTILINE)
        if count != 1:
            raise RuntimeError("Expected PES candidate declaration changed: " + name)
    compile(content, str(path), "exec")
    path.write_text(content)


def main():
    if len(sys.argv) != 1:
        raise SystemExit("Run this helper without arguments")
    home = Path.home()
    cache = home / ".cache/shadps4-affinity-20261007"
    cache.mkdir(parents=True, exist_ok=True)
    evidence = Path(tempfile.mkdtemp(prefix="shadps4-cpu-id-playtest-", dir=home))
    work = Path(tempfile.mkdtemp(prefix="cpu-id-playtest-", dir=cache))
    archives = {}
    summary = {"source_commit": SOURCE, "helper_revision": HELPER_REVISION,
               "helper_checksums": HELPERS, "homebrew_passed": False,
               "pes_playability": "unconfirmed", "installed_binary_replaced": False}
    owner = test_lock = None

    def capture(stage):
        def output(message):
            if message.startswith("UPLOAD_ONLY="):
                archive = Path(message.partition("=")[2])
                if archive.is_file():
                    archives[stage] = archive
            else:
                say(message)
        return output

    try:
        owner = (cache / "cpu-id-playtest.lock").open("a")
        try:
            fcntl.flock(owner, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError("Another combined CPU-ID playtest is already running") from None
        fetch_helpers(work)
        homebrew = load_module(work / "test_affinity_revision.py", "cpu_id_homebrew")
        if homebrew.existing_emulators():
            raise RuntimeError("Close the running shadPS4 session through ES-DE before testing")
        allowed = sorted(os.sched_getaffinity(0))
        proof, result = find_homebrew(home, cache, allowed)
        if proof is None:
            say("HOMEBREW=Building the isolated CPU-ID candidate and running the full test matrix")
            homebrew.say = capture("homebrew")
            code = homebrew.main()
            if code != 0:
                raise RuntimeError("CPU-ID homebrew failed; PES was not armed. Evidence collected")
            archive = archives.get("homebrew")
            if archive is None:
                raise RuntimeError("Homebrew result archive was not produced")
            proof = Path(str(archive)[:-len(".tar.gz")]) / "summary.json"
            result = verified_homebrew(proof, cache, allowed)
        else:
            say("HOMEBREW=Reusing the complete passing matrix for this unchanged CPU-ID binary")
        summary.update(homebrew_passed=True, homebrew_summary=str(proof),
                       candidate_binary=result["binary"], candidate_sha256=result["binary_sha256"],
                       homebrew_test_count=len(result["tests"]))
        shutil.copy2(proof, evidence / "homebrew-summary.json")
        previous_archive = Path(str(proof.parent) + ".tar.gz")
        if previous_archive.is_file():
            archives["homebrew"] = previous_archive
        else:
            shutil.copytree(proof.parent, evidence / "homebrew")

        test_lock = (cache / "homebrew.lock").open("a")
        try:
            fcntl.flock(test_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError("Another homebrew test started; PES was not armed") from None
        verified_homebrew(proof, cache, allowed)
        bind_candidate(work / "test_affinity_pes.py", result["binary_sha256"])
        guarded = load_module(work / "test_affinity_pes_guarded.py", "cpu_id_guarded_pes")
        if guarded.base.SOURCE != SOURCE or guarded.base.BINARY_SHA256 != result["binary_sha256"]:
            raise RuntimeError("PES runner did not bind to the verified homebrew binary")
        summary["bound_pes_helper_sha256"] = digest(work / "test_affinity_pes.py")
        guarded.base.say = capture("pes")
        guarded.base.ask_result = lambda: "unconfirmed"
        say("CANDIDATE_VERIFIED=" + SOURCE[:12] + " (the exact homebrew-tested binary)")
        code = guarded.run()
        summary["pes_controller_returncode"] = code
        if code != 0:
            raise RuntimeError("Guarded PES capture reported an error; evidence collected")
        if "pes" not in archives:
            raise RuntimeError("Guarded PES capture did not produce its archive")
        summary["capture_completed"] = True
    except KeyboardInterrupt:
        summary["interrupted"] = True
        say("TEST_INTERRUPTED=Evidence collected")
    except Exception as error:
        summary["error"] = str(error)
        say("TEST_ERROR=" + str(error))
    finally:
        previous_handler = signal.signal(signal.SIGINT, signal.SIG_IGN)
        try:
            summary["archives"] = {name: str(path) for name, path in archives.items()}
            (evidence / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
            destination = Path(str(evidence) + ".tar.gz")
            with tarfile.open(destination, "w:gz") as archive:
                archive.add(evidence, arcname=evidence.name)
                for name, path in archives.items():
                    archive.add(path, arcname=evidence.name + "/" + name + ".tar.gz")
            shutil.rmtree(work)
            say("UPLOAD_ONLY=" + str(destination))
            say("SSH stays open. The installed emulator and original saves were not replaced.")
        finally:
            if test_lock is not None:
                test_lock.close()
            if owner is not None:
                owner.close()
            signal.signal(signal.SIGINT, previous_handler)
    return 0 if summary.get("capture_completed") else 1


if __name__ == "__main__":
    sys.exit(main())
