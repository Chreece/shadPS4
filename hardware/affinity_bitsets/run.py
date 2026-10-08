#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 shadPS4 Emulator Project
# SPDX-License-Identifier: GPL-2.0-or-later

"""Build and test the shared affinity bitmask candidate without installing it."""

import argparse
import fcntl
import faulthandler
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import sys
import tarfile
import tempfile
import time
import traceback

HELPER_REVISION = "605ee9649addd9788e630e7c1bbcdaffda4c091c"
BUILD_SHA = "2b0d969c5914d65683f2da49e0755a9e00891c5327d356e69da792b058dcdf48"
COLLECT_SHA = "058c4767f20f2a54b980c1e9681e30c78d58c9d5d0c01de4e10d277efbcec632"
SUITE_SHA = "02d358e53e90e045dcbb9f5cebafc1f3c33769c8d9fd3cd8bf48bf4d7e1c3550"
SUITE_REVISION = "3341bc772037912f11702695d69a0627b182309f"
AFFINITY_RUNNER_SHA = "cdfc210d92bc54758eb820ff23dba0b2d21f9ab70ae5ab02bc3245292201d724"
SIGNALS_SHA = "02ba665dd3a1349a375b3c37aa77de0e653d0b37ada56dd1eb4a36afbe6610a7"
SOURCES = {
    "src/core/cpu_affinity.cpp": ("f12cc20bbcf75459aaf879ef249023d43d87ccff7f1b3b0c9a0af8ad1e08cb6f", "6055dce77514fce991da1d4288ae5802fbdead5335dfce74feeb2b77008b7b62"),
    "src/core/cpu_affinity.h": ("deeb5b501bc2d52152a80a091231532320a48a5e82ddda9b832dcafd583c1d3d", "589d8ae773f078a2ac95d3201c1b0197aecfa85a4359c212467d5cde38135945"),
}
SOURCE_REVISION = "9811afcdcfd14172adc7a13514b91442c5e5ef88"
BASE_TREE = "db804b1ffcc3098ed7c6a0725a4631abc373ec64"
BASE_BINARY = "63144d096215a6163fd84c43d4622c253a44880806d7a692babb7b739c8edf7a"
BASE_CLIENT = "be700042dab3f805280cb1942b37b1fd9730ea902f34d924787901f28e7c45a3"


def digest(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


EVIDENCE = None
LAST_STAGE = "starting"


def stage(message):
    global LAST_STAGE
    LAST_STAGE = message
    print("STEP=" + message, flush=True)
    if EVIDENCE is not None:
        with (EVIDENCE / "startup.log").open("a") as output:
            output.write(time.strftime("%Y-%m-%dT%H:%M:%S%z ") + message + "\n")
        (EVIDENCE / "stage.json").write_text(json.dumps({"stage": message}) + "\n")


def capture(arguments, cwd=None, timeout=60):
    process = subprocess.Popen(list(map(str, arguments)), cwd=cwd, stdin=subprocess.DEVNULL,
                               stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                               start_new_session=True)
    started = time.monotonic()
    try:
        while True:
            remaining = timeout - (time.monotonic() - started)
            if remaining <= 0:
                raise TimeoutError(f"{LAST_STAGE} exceeded {timeout} seconds")
            try:
                output, _ = process.communicate(timeout=min(15, remaining))
                break
            except subprocess.TimeoutExpired:
                print(f"WAITING={LAST_STAGE} ({int(time.monotonic() - started)}s)", flush=True)
        if process.returncode:
            detail = output.decode(errors="replace")[-8000:]
            raise RuntimeError(f"{LAST_STAGE} failed ({process.returncode}):\n{detail}")
        return output.decode().strip()
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
        process.stdout.close()


def download(revision, relative, destination, expected, maximum=2 * 1024 * 1024):
    curl = shutil.which("curl")
    if curl is None:
        raise RuntimeError("curl is required for the bounded IPv4 download fallback")
    routes = (
        ("GitHub API", f"https://api.github.com/repos/Chreece/shadPS4/contents/{relative}?ref={revision}"),
        ("GitHub raw", f"https://raw.githubusercontent.com/Chreece/shadPS4/{revision}/{relative}"),
    )
    failures = []
    with tempfile.TemporaryDirectory(prefix="download-", dir=Path(destination).parent) as directory:
        temporary = Path(directory) / "payload"
        for label, url in routes:
            stage(f"Download {relative} via {label} (IPv4)")
            try:
                capture([curl, "-4", "--fail", "--location", "--silent", "--show-error",
                         "--connect-timeout", "8", "--max-time", "30", "--max-filesize", str(maximum),
                         "--header", "Accept: application/vnd.github.raw+json",
                         "--header", "User-Agent: shadps4-playtest", "--output", temporary, url], timeout=35)
                if temporary.stat().st_size > maximum or digest(temporary) != expected:
                    raise RuntimeError("Download checksum or size mismatch via " + label)
                Path(destination).write_bytes(temporary.read_bytes())
                return
            except (RuntimeError, TimeoutError) as error:
                failures.append(str(error))
                stage("Download attempt failed: " + str(error))
        raise RuntimeError("All download routes failed for " + relative + "\n" + "\n".join(failures))


def git(source, *arguments):
    stage("Check Git " + " ".join(arguments))
    return capture(["git", "-C", source, *arguments], timeout=120)


def load_helper(work, name, expected, revision=HELPER_REVISION, directory="hardware/cpu_profile"):
    path = work / name
    download(revision, directory + "/" + name, path, expected, 256 * 1024)
    stage("Load " + name)
    spec = importlib.util.spec_from_file_location(name.removesuffix(".py"), path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def build_candidate(builder, cache, work, evidence):
    git, original_command = builder.git, builder.command
    def command(arguments, log, cwd=None):
        stage("Build " + " ".join(map(str, arguments)))
        return original_command(arguments, log, cwd)
    original = cache / "profile-source-c7814c49f926"
    baseline_build = cache / "profile-build-c7814c49f926"
    source = cache / ("affinity-bitsets-source-" + SOURCE_REVISION[:12])
    build = cache / ("affinity-bitsets-build-" + SOURCE_REVISION[:12])
    if git(original, "rev-parse", "HEAD^{tree}") != BASE_TREE:
        raise RuntimeError("The verified CPU-filter source changed; preserved")
    if git(original, "status", "--porcelain", "--untracked-files=no", "--ignore-submodules=all"):
        raise RuntimeError("The original source has local changes; preserved")
    for relative, (before, after) in SOURCES.items():
        if digest(original / relative) != before:
            raise RuntimeError("The baseline source changed: " + relative)
        builder.download(SOURCE_REVISION, relative, work / Path(relative).name, after)
    log = evidence / "build.log"
    base_revision = git(original, "rev-parse", "HEAD")
    if not source.exists():
        command(["git", "clone", "--shared", "--no-checkout", original, source], log)
        command(["git", "checkout", "--detach", base_revision], log, source)
    if git(source, "status", "--porcelain", "--untracked-files=no", "--ignore-submodules=all"):
        raise RuntimeError("Isolated source has local changes; preserved")
    if git(source, "rev-parse", "HEAD^{tree}") == BASE_TREE:
        for relative in SOURCES:
            shutil.copyfile(work / Path(relative).name, source / relative)
        command(["git", "add", *SOURCES], log, source)
        command(["git", "-c", "user.name=shadPS4 playtest", "-c", "user.email=playtest@localhost",
                 "-c", "commit.gpgsign=false", "-c", "core.hooksPath=/dev/null", "commit", "-m",
                 "playtest: shared affinity bitmasks"], log, source)
    if git(source, "diff", "--name-only", base_revision, "HEAD").splitlines() != sorted(SOURCES):
        raise RuntimeError("Unexpected changes in the isolated candidate")
    for relative, (before, after) in SOURCES.items():
        if digest(source / relative) != after:
            raise RuntimeError("Unexpected candidate source: " + relative)
    status = git(source, "submodule", "status", "--recursive")
    if any(line[:1] in {"+", "-", "U"} for line in status.splitlines()):
        command(["git", "-c", "submodule.alternateErrorStrategy=info", "submodule", "update", "--init",
                 "--recursive", "--jobs", "4", "--reference", original], log, source)
    if any(line[:1] in {"+", "-", "U"} for line in git(source, "submodule", "status", "--recursive").splitlines()):
        raise RuntimeError("A dependency differs from the verified source")
    settings = {}
    for line in (baseline_build / "CMakeCache.txt").read_text().splitlines():
        match = re.fullmatch(r"(CMAKE_C_COMPILER|CMAKE_CXX_COMPILER):[^=]+=(.+)", line)
        if match:
            settings[match[1]] = match[2]
    if len(settings) != 2 or not all(Path(path).is_file() for path in settings.values()):
        raise RuntimeError("The verified build compilers are unavailable")
    builder.prepare_ffmpeg(source, build, baseline_build)
    runtime = cache / "runtime-build-a522a5055820"
    configure = ["cmake", "-S", source, "-B", build, "-G", "Ninja", "-DCMAKE_BUILD_TYPE=Release",
                 "-DENABLE_TESTS=OFF", "-DENABLE_UPDATER=OFF", "-DCMAKE_CXX_SCAN_FOR_MODULES=OFF",
                 "-DCMAKE_INTERPROCEDURAL_OPTIMIZATION_RELEASE=OFF", "-DENABLE_CPU_ID_TRANSLATION=ON",
                 "-DDynamoRIO_DIR=" + str(runtime / "cmake")]
    configure += ["-D" + key + "=" + value for key, value in settings.items()]
    if shutil.which("ccache"):
        configure += ["-DCMAKE_C_COMPILER_LAUNCHER=ccache", "-DCMAKE_CXX_COMPILER_LAUNCHER=ccache"]
    command(configure, log)
    command(["cmake", "--build", build, "--target", "shadps4", "shadps4_cpu_id", "--parallel",
             str(max(1, min(6, len(os.sched_getaffinity(0)))))], log)
    command(["git", "diff", "--exit-code", "HEAD", "--"], log, source)
    binary = build / "shadps4"
    client = build / "src/core/cpu_id_translation/libshadps4_cpu_id.so"
    return binary, client, {"base_tree": BASE_TREE, "source_change": SOURCE_REVISION,
        "candidate_tree": git(source, "rev-parse", "HEAD^{tree}"), "source": str(source),
        "binary": str(binary), "binary_sha256": digest(binary), "client_sha256": digest(client)}


def cancel(signum, frame):
    raise KeyboardInterrupt("Test interrupted")


def main():
    global EVIDENCE
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--revision", required=True)
    args = parser.parse_args()
    if not re.fullmatch(r"[0-9a-f]{40}", args.revision):
        raise RuntimeError("An immutable revision is required")
    if sys.platform != "linux" or os.uname().machine != "x86_64":
        raise RuntimeError("This test requires Linux x86-64")
    cache = Path.home() / ".cache/shadps4-affinity-20261007"
    cache.mkdir(parents=True, exist_ok=True)
    with (cache / "homebrew.lock").open("a") as lock:
        print("START=Affinity bitset test; checking the test lock", flush=True)
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print("BUSY=Another homebrew test is running; nothing started", flush=True)
            return 1
        evidence = Path(tempfile.mkdtemp(prefix="shadps4-affinity-bitsets-", dir=Path.home()))
        EVIDENCE = evidence
        print("EVIDENCE=" + str(evidence), flush=True)
        summary = {"source_change": SOURCE_REVISION, "tests": [], "ok": False}
        trace = (evidence / "waiting-tracebacks.log").open("w")
        faulthandler.dump_traceback_later(60, repeat=True, file=trace)
        installed = Path.home() / "Applications/shadps4/shadps4"
        before = None
        installed_checked = False
        protected = {}
        handlers = {sig: signal.signal(sig, cancel) for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP)}
        try:
            stage("Verify installed emulator")
            before = digest(installed) if installed.is_file() else None
            installed_checked = True
            with tempfile.TemporaryDirectory(prefix="affinity-bitsets-", dir=cache) as folder:
                work = Path(folder)
                collector = load_helper(work, "collect.py", COLLECT_SHA)
                builder = load_helper(work, "test_filter.py", BUILD_SHA)
                base = load_helper(work, "test_affinity_revision.py", AFFINITY_RUNNER_SHA,
                                   SUITE_REVISION, "scripts")
                builder.download = download
                builder.git = git
                stage("Check running emulators")
                collector.require_idle()
                protected = {cache / "profile-build-c7814c49f926/shadps4": BASE_BINARY,
                    cache / "profile-build-c7814c49f926/src/core/cpu_id_translation/libshadps4_cpu_id.so": BASE_CLIENT}
                protected.update({cache / name: sha for name, sha in collector.ARTIFACTS.items()})
                for path, expected in protected.items():
                    stage("Verify " + str(path.relative_to(cache)))
                    if digest(path) != expected:
                        raise RuntimeError("The verified artifact changed: " + str(path))
                stage("Find display session")
                env = collector.display_environment()
                stage("Prepare candidate build")
                binary, client, info = build_candidate(builder, cache, work, evidence)
                summary["build"] = info
                suite = work / "suite.tar.gz"
                builder.download(SUITE_REVISION, "hardware/affinity_revision/suite.tar.gz", suite, SUITE_SHA)
                with tarfile.open(suite) as archive:
                    archive.extractall(work / "suite", filter="data")
                signals = work / "signals.tar.gz"
                builder.download(args.revision, "hardware/affinity_bitsets/signals.tar.gz", signals, SIGNALS_SHA)
                with tarfile.open(signals) as archive:
                    archive.extractall(work / "suite", filter="data")
                original_threads = base.host_threads
                def monitored_threads(pid):
                    if any(other != pid for other in collector.existing_emulators()):
                        raise RuntimeError("Another emulator started; stopping only this test")
                    return original_threads(pid)
                base.host_threads = monitored_threads
                allowed = sorted(os.sched_getaffinity(0))
                summary["initial_host_cpus"] = allowed
                profiles = [("all", allowed), ("four", allowed[-4:]), ("two", allowed[-2:]),
                            ("one", allowed[-1:]), ("sparse", allowed[1::2][:4] or allowed[:1])]
                cases = [case for case in json.loads((work / "suite/cases.json").read_text())
                         if not case.get("cpu_id")]
                seen = set()
                for label, cpus in profiles:
                    if tuple(cpus) in seen:
                        continue
                    seen.add(tuple(cpus))
                    selected = cases if label in {"all", "two", "one"} else [c for c in cases if c.get("extended")]
                    selected = selected + [{"directory": "CPUM00009", "extended": True,
                                             "expected_mask": 127, "expected_samples": 2048}]
                    for case in selected:
                        collector.require_idle()
                        stage("Run " + label + "-" + case["directory"])
                        result = base.run_case(binary, case, work / "suite", cpus, label,
                                               work, evidence, env)
                        summary["tests"].append(result)
                        (evidence / "progress.json").write_text(json.dumps(summary["tests"], indent=2))
                        if result.get("infrastructure_failure"):
                            raise RuntimeError("Homebrew could not start; evidence collected")
                summary["ok"] = bool(summary["tests"]) and all(r["ok"] for r in summary["tests"])
                print("AFFINITY_SUITE=" + ("PASS" if summary["ok"] else "FAIL"), flush=True)
        except (Exception, KeyboardInterrupt) as error:
            summary["last_stage"] = LAST_STAGE
            (evidence / "error-traceback.log").write_text(traceback.format_exc())
            summary["error"] = type(error).__name__ + ": " + str(error)
            print("TEST_ERROR=" + summary["error"], flush=True)
            summary["ok"] = False
        finally:
            for sig in handlers:
                signal.signal(sig, signal.SIG_IGN)
            try:
                stage("Verify original files and collect archive")
                summary["installed_binary_unchanged"] = installed_checked and before == (digest(installed) if installed.is_file() else None)
                summary["baseline_artifacts_preserved"] = all(path.is_file() and digest(path) == expected for path, expected in protected.items())
                summary["ok"] &= summary["installed_binary_unchanged"] and summary["baseline_artifacts_preserved"]
                faulthandler.cancel_dump_traceback_later()
                trace.close()
                (evidence / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
                archive_path = evidence.with_suffix(".tar.gz")
                with tarfile.open(archive_path, "w:gz") as archive:
                    archive.add(evidence, arcname=evidence.name)
                print("UPLOAD_ONLY=" + str(archive_path), flush=True)
                print("SSH stays open. Installed emulator, saves and session guard were not edited.", flush=True)
            finally:
                faulthandler.cancel_dump_traceback_later()
                trace.close()
                EVIDENCE = None
                for sig, handler in handlers.items():
                    signal.signal(sig, handler)
        return 0 if summary["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
