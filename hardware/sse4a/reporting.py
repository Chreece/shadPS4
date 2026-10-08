#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 shadPS4 Emulator Project
# SPDX-License-Identifier: GPL-2.0-or-later

"""Verify SSE4a reporting and execution on the validated fallback build without installing it."""

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
SUITE_SHA = "da547e583454c878c554f33e640579c953d969cb678009c7d5c9f76482ced4c5"
SOURCE_REVISION = "d7ff0dffa041d243d9d4161b78126a80086eeb5c"
SOURCE_SHA = "e4935fcb8e954950688f56a5f8ab1f99c34c32c0f0e728fb00f1c752ff87b286"
SUITE_REVISION = "04f489dfd87fbcd254ff03beee8956a6b7a34780"
PROFILE_SHA = "e69e669064b9347310b567f1952ee1a5270a7857b7d182d61ebe4181d487812b"
BASE_TREE = "60b0a33ab40e239e51e523f40d055ed323a9cc99"
BASE_BINARY = "9b280145daa260005c4d33dd67c99022b90eab2afbc3193a81af5c867de64f20"
BASE_CLIENT = "0533b91e25c24c20f162edf3b9bb0974dd23294252dd9665be5eb37e3aa564e5"
CASES = ("extrq_reg", "extrq_imm", "insertq_reg", "insertq_imm", "extrq_high", "extrq_imm_high",
         "insertq_high", "insertq_imm_high", "movntss", "movntsd", "movntss_high", "movntsd_high")


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


def download_routes(routes, relative, destination, expected, maximum):
    curl = shutil.which("curl")
    if curl is None:
        raise RuntimeError("curl is required for the bounded IPv4 download fallback")
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


def download(revision, relative, destination, expected, maximum=2 * 1024 * 1024):
    routes = (
        ("GitHub API", f"https://api.github.com/repos/Chreece/shadPS4/contents/{relative}?ref={revision}"),
        ("GitHub raw", f"https://raw.githubusercontent.com/Chreece/shadPS4/{revision}/{relative}"),
    )
    download_routes(routes, relative, destination, expected, maximum)


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


def build_candidate(builder, cache, evidence, work, revision):
    git, original_command = builder.git, builder.command
    def command(arguments, log, cwd=None):
        stage("Build " + " ".join(map(str, arguments)))
        return original_command(arguments, log, cwd)
    original = cache / "sse4a-source-0c8f18444ca7"
    baseline_build = cache / "sse4a-build-0c8f18444ca7"
    source = cache / ("sse4a-report-source-" + SOURCE_REVISION[:12])
    build = cache / ("sse4a-report-build-" + SOURCE_REVISION[:12])
    if git(original, "rev-parse", "HEAD^{tree}") != BASE_TREE:
        raise RuntimeError("The verified SSE4a source changed; preserved")
    if git(original, "status", "--porcelain", "--untracked-files=no", "--ignore-submodules=all"):
        raise RuntimeError("The original source has local changes; preserved")
    relative = "src/core/cpu_id.cpp"
    candidate = work / "cpu_id.cpp"
    builder.download(SOURCE_REVISION, relative, candidate, SOURCE_SHA)
    expected_source = candidate.read_text()
    log = evidence / "build.log"
    base_revision = git(original, "rev-parse", "HEAD")
    if not source.exists():
        command(["git", "clone", "--shared", "--no-checkout", original, source], log)
        command(["git", "checkout", "--detach", base_revision], log, source)
    if git(source, "status", "--porcelain", "--untracked-files=no", "--ignore-submodules=all"):
        raise RuntimeError("Isolated source has local changes; preserved")
    if git(source, "rev-parse", "HEAD^{tree}") == BASE_TREE:
        (source / relative).write_text(expected_source)
        command(["git", "add", relative], log, source)
        command(["git", "-c", "user.name=shadPS4 playtest", "-c", "user.email=playtest@localhost",
                 "-c", "commit.gpgsign=false", "-c", "core.hooksPath=/dev/null", "commit", "-m",
                 "playtest: advertise validated SSE4a emulation"], log, source)
    if git(source, "diff", "--name-only", base_revision, "HEAD").splitlines() != [relative] or (source / relative).read_text() != expected_source:
        raise RuntimeError("Unexpected changes in the isolated candidate")
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
    stage("Prepare cached FFmpeg dependency")
    ffmpeg_cache = baseline_build
    if not any(path.stat().st_size == builder.FFMPEG_SIZE and digest(path) == builder.FFMPEG_SHA
               for path in (baseline_build / "externals").glob("ffmpeg-94dde08*.zip")):
        ffmpeg_cache = work / "ffmpeg-cache"
        dependency = ffmpeg_cache / "externals/ffmpeg-94dde08.zip"
        dependency.parent.mkdir(parents=True)
        url = "https://github.com/shadps4-emu/ext-ffmpeg-core/releases/download/94dde08/ffmpeg-linux-x64.zip"
        download_routes((("GitHub release", url),), "FFmpeg", dependency,
                        builder.FFMPEG_SHA, builder.FFMPEG_SIZE)
    builder.prepare_ffmpeg(source, build, ffmpeg_cache)
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


def parse_result(text, case, generated):
    begin = re.findall(r"SSE4A_BEGIN case=(\w+) generated=(\d+) advertised=(\d+)", text)
    end = re.findall(r"SSE4A_END checks=(\d+) failures=(\d+)", text)
    rows = re.findall(r"^SSE4A_CHECK (.+)$", text, re.M)
    expected = 20 if case in ("extrq_reg", "insertq_reg", "extrq_high", "insertq_high") else 1
    valid = (len(begin) == 1 and begin[0][:2] == (case, str(generated)) and len(end) == 1
             and len(rows) == expected and int(end[0][0]) == expected)
    failures = [row for row in rows if not row.endswith("ok=1")]
    ok = valid and not failures and int(end[0][1]) == 0
    return {"ok": bool(ok), "complete": bool(valid), "failed_checks": failures,
            "advertised": int(begin[0][2]) if len(begin) == 1 else None, "checks": len(rows)}


def compare_reporting(builder, before, after):
    old, new = builder.records(before), builder.records(after)
    errors = []
    feature_rows = 0
    for key in sorted(old.keys() | new.keys()):
        if key not in old or key not in new:
            errors.append({"key": key, "error": "CPU profile coverage changed"})
            continue
        expected = list(old[key])
        if key[2] == 0x80000001:
            expected[2] |= 1 << 6
            feature_rows += 1
        if tuple(expected) != new[key]:
            errors.append({"key": key, "expected": expected, "actual": new[key]})
    states = lambda text: sorted(line for line in text.splitlines() if line.startswith("CPU_PROFILE_XCR0 "))
    if states(before) != states(after):
        errors.append({"error": "XCR0 changed"})
    if not feature_rows:
        errors.append({"error": "Missing SSE4a feature queries"})
    return {"ok": not errors, "errors": errors, "compared_rows": len(new), "feature_rows": feature_rows}


def read_profile(collector, prefix, work, evidence, env, name):
    runtime = work / ("profile-" + name)
    collector.prepare_runtime(runtime)
    output = evidence / ("profile-" + name + ".log")
    profile = runtime / "user/data/cpu-profile.txt"
    try:
        collector.run(prefix + ["--ignore-game-patch", work / "profile-suite/CPUP00001/eboot.bin"],
                      runtime, env, output, emulator=True)
    finally:
        if profile.is_file():
            shutil.copy2(profile, evidence / ("profile-" + name + ".txt"))
    text = profile.read_text()
    result = collector.parse_profile(text)
    return text, result


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
        print("START=SSE4a reporting test; checking the test lock", flush=True)
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print("BUSY=Another homebrew test is running; nothing started", flush=True)
            return 1
        evidence = Path(tempfile.mkdtemp(prefix="shadps4-sse4a-reporting-", dir=Path.home()))
        EVIDENCE = evidence
        print("EVIDENCE=" + str(evidence), flush=True)
        summary = {"source_change": SOURCE_REVISION, "tests": {}, "ok": False,
                   "feature_reporting_changed": True, "profiles": {}}
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
            with tempfile.TemporaryDirectory(prefix="sse4a-reporting-", dir=cache) as folder:
                work = Path(folder)
                collector = load_helper(work, "collect.py", COLLECT_SHA)
                builder = load_helper(work, "test_filter.py", BUILD_SHA)
                builder.download = download
                builder.git = git
                stage("Check running emulators")
                collector.require_idle()
                protected = {cache / "sse4a-build-0c8f18444ca7/shadps4": BASE_BINARY,
                    cache / "sse4a-build-0c8f18444ca7/src/core/cpu_id_translation/libshadps4_cpu_id.so": BASE_CLIENT}
                protected.update({cache / name: sha for name, sha in collector.ARTIFACTS.items()})
                for path, expected in protected.items():
                    stage("Verify " + str(path.relative_to(cache)))
                    if digest(path) != expected:
                        raise RuntimeError("The verified artifact changed: " + str(path))
                stage("Find display session")
                env = collector.display_environment()
                stage("Prepare candidate build")
                binary, client, info = build_candidate(builder, cache, evidence, work, args.revision)
                summary["build"] = info
                suite = work / "suite.tar.gz"
                builder.download(SUITE_REVISION, "hardware/sse4a/suite.tar.gz", suite, SUITE_SHA)
                with tarfile.open(suite) as archive:
                    archive.extractall(work / "suite", filter="data")
                drrun = cache / "runtime-build-a522a5055820/bin64/drrun"
                modes = {"after-native": [binary], "after-translated": [drrun, "-disable_rseq",
                         "-vm_base", "0x710020000000", "-no_vm_base_near_app", "-c", client, "--", binary]}
                profile_suite = work / "profile.tar.gz"
                builder.download(HELPER_REVISION, "hardware/cpu_profile/suite.tar.gz", profile_suite, PROFILE_SHA)
                collector.unpack_suite(profile_suite, work / "profile-suite")
                profiles = {}
                profile_modes = {"before-native": [cache / "sse4a-build-0c8f18444ca7/shadps4"], **modes}
                for name, prefix in profile_modes.items():
                    stage("Read CPU profile " + name)
                    text, result = read_profile(collector, prefix, work, evidence, env, name)
                    profiles[name] = text
                    summary["profiles"][name] = result
                    if name != "before-native":
                        comparison = compare_reporting(builder, profiles["before-native"], text)
                        result["comparison"] = comparison
                        result["ok"] &= comparison["ok"]
                    if not result["ok"]:
                        raise RuntimeError("CPU profile validation failed: " + name)
                summary["native_translated_equal"] = (
                    builder.records(profiles["after-native"]) == builder.records(profiles["after-translated"]))
                if not summary["native_translated_equal"]:
                    raise RuntimeError("Native and translated CPU profiles disagree")
                for mode, prefix in modes.items():
                    for generated in (0, 1):
                        for index, case in enumerate(CASES):
                            name = f"{mode}-{'generated' if generated else 'static'}-{case}"
                            stage(f"Run {len(summary['tests']) + 1}/48 " + name)
                            runtime = work / name
                            collector.prepare_runtime(runtime)
                            (runtime / "user/data").mkdir(exist_ok=True)
                            (runtime / "user/data/sse4a-case.txt").write_text(f"{index} {generated}")
                            output = evidence / (name + ".log")
                            result = {"ok": False}
                            try:
                                collector.run(prefix + ["--ignore-game-patch", work / "suite/SSE400001/eboot.bin"],
                                              runtime, env, output, emulator=True)
                            except RuntimeError as error:
                                result["error"] = str(error)
                                collector.require_idle()
                            finally:
                                profile = runtime / "user/data/sse4a-result.txt"
                                if profile.is_file():
                                    text = profile.read_text()
                                    (evidence / (name + ".result.txt")).write_text(text)
                                    result.update(parse_result(text, case, generated))
                                process_file = output.with_suffix(".process.json")
                                if process_file.is_file():
                                    result["process"] = json.loads(process_file.read_text())
                                    result["ok"] &= result["process"]["returncode"] == 0 and not result["process"]["forced_cleanup"]
                                else:
                                    result["ok"] = False
                                result["ok"] &= "error" not in result and result.get("advertised") == 1
                                summary["tests"][name] = result
                                (evidence / "progress.json").write_text(json.dumps(summary["tests"], indent=2) + "\n")
                            print("RESULT=" + name + (" PASS" if result["ok"] else " FAIL"), flush=True)
                summary["ok"] = len(summary["tests"]) == 48 and all(result["ok"] for result in summary["tests"].values())
                print("SSE4A_REPORTING=" + ("PASS" if summary["ok"] else "FAIL"), flush=True)
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
