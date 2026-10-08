#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 shadPS4 Emulator Project
# SPDX-License-Identifier: GPL-2.0-or-later

"""Build and test the guest instruction filter on the verified working graphics source."""

import argparse
import fcntl
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import tarfile
import tempfile
import time
import urllib.request
import zipfile


SOURCE_REVISION = "c7814c49f926dbc26e4a777fc3bcdf64c715523a"
SOURCE_SHA = "843dff9608df3051c1391ff63ed56ecf155b9c552339c68a5d650afa1d4679db"
BEFORE_SHA = "3bdbea9e6391ea9a147f286e9c20bfd0b7fd0395c207960cb8903a683d6023f1"
BASE_TREE = "95b74819840db085b987fa8e8709306a8ba4acd7"
COLLECT_SHA = "058c4767f20f2a54b980c1e9681e30c78d58c9d5d0c01de4e10d277efbcec632"
SUITE_SHA = "e69e669064b9347310b567f1952ee1a5270a7857b7d182d61ebe4181d487812b"
FFMPEG_SHA = "aacbbfb8e622b684bc5d3b4cd6c9f9f77f5def64ae8d83c0c5b3ebe657aa33dd"
FFMPEG_SIZE = 15543319
HERE = Path(__file__).resolve().parent


def say(message):
    print(message, flush=True)


def digest(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def download(revision, relative, destination, expected, maximum=2 * 1024 * 1024):
    url = f"https://raw.githubusercontent.com/Chreece/shadPS4/{revision}/{relative}"
    with urllib.request.urlopen(url, timeout=30) as response:
        data = response.read(maximum + 1)
    if len(data) > maximum or hashlib.sha256(data).hexdigest() != expected:
        raise RuntimeError("Download checksum mismatch: " + relative)
    destination.write_bytes(data)


def git(source, *args):
    return subprocess.check_output(["git", "-C", str(source), *args],
                                   stderr=subprocess.STDOUT).decode().strip()


def command(arguments, log, cwd=None):
    say("BUILD=" + " ".join(map(str, arguments)))
    with log.open("a") as output:
        process = subprocess.Popen(list(map(str, arguments)), cwd=cwd, stdout=output,
                                   stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                   start_new_session=True)
        start = time.monotonic()
        try:
            while True:
                try:
                    result = process.wait(timeout=15)
                    break
                except subprocess.TimeoutExpired:
                    say(f"BUILD_RUNNING={int(time.monotonic() - start)}s")
                    if time.monotonic() - start > 3600:
                        raise RuntimeError("Build command exceeded one hour; see build.log")
            if result:
                raise RuntimeError("Build command failed; see build.log")
        finally:
            if process.poll() is None:
                try:
                    os.killpg(process.pid, signal.SIGTERM)
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait()
                except ProcessLookupError:
                    process.wait()


def prepare_ffmpeg(source, build, baseline_build):
    dependency = source / "externals/ffmpeg-core"
    if git(dependency, "rev-parse", "HEAD") != "94dde08c8a9e4271a93a2a7e4159e9fb05d30c0a":
        raise RuntimeError("Unexpected FFmpeg source revision")
    short = git(dependency, "rev-parse", "--short", "HEAD")
    candidates = list((baseline_build / "externals").glob("ffmpeg-94dde08*.zip"))
    data = None
    for path in candidates:
        if path.stat().st_size == FFMPEG_SIZE and digest(path) == FFMPEG_SHA:
            data = path.read_bytes()
            break
    if data is None:
        url = "https://github.com/shadps4-emu/ext-ffmpeg-core/releases/download/94dde08/ffmpeg-linux-x64.zip"
        with urllib.request.urlopen(url, timeout=30) as response:
            data = response.read(FFMPEG_SIZE + 1)
        if len(data) != FFMPEG_SIZE or hashlib.sha256(data).hexdigest() != FFMPEG_SHA:
            raise RuntimeError("FFmpeg checksum mismatch")
    destination = build / "externals" / ("ffmpeg-" + short + ".zip")
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(data)
    libraries = destination.with_suffix("") / "lib"
    libraries.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(destination) as archive:
        for name in ("avformat", "avcodec", "swscale", "avutil", "avfilter", "swresample"):
            filename = "lib" + name + ".a"
            content = archive.read(filename)
            if not content.startswith(b"!<arch>\n"):
                raise RuntimeError("Invalid FFmpeg library")
            (libraries / filename).write_bytes(content)


def build_candidate(cache, work, evidence):
    original = cache / "working-source-95b74819840d"
    baseline_build = cache / "working-build-95b74819840d"
    source = cache / ("profile-source-" + SOURCE_REVISION[:12])
    build = cache / ("profile-build-" + SOURCE_REVISION[:12])
    if git(original, "rev-parse", "HEAD^{tree}") != BASE_TREE:
        raise RuntimeError("The verified working source changed; preserved")
    if git(original, "status", "--porcelain", "--untracked-files=no", "--ignore-submodules=all"):
        raise RuntimeError("The original source has local changes; preserved")
    if digest(original / "src/core/cpu_id.cpp") != BEFORE_SHA:
        raise RuntimeError("The CPU source differs from the tested baseline")
    candidate = work / "cpu_id.cpp"
    download(SOURCE_REVISION, "src/core/cpu_id.cpp", candidate, SOURCE_SHA)
    log = evidence / "build.log"
    base_revision = git(original, "rev-parse", "HEAD")
    if not source.exists():
        command(["git", "clone", "--shared", "--no-checkout", original, source], log)
        command(["git", "checkout", "--detach", base_revision], log, source)
    if git(source, "status", "--porcelain", "--untracked-files=no", "--ignore-submodules=all"):
        raise RuntimeError("Isolated source has local changes; preserved")
    if git(source, "rev-parse", "HEAD^{tree}") == BASE_TREE:
        shutil.copyfile(candidate, source / "src/core/cpu_id.cpp")
        command(["git", "add", "src/core/cpu_id.cpp"], log, source)
        command(["git", "-c", "user.name=shadPS4 playtest", "-c", "user.email=playtest@localhost",
                 "-c", "commit.gpgsign=false", "-c", "core.hooksPath=/dev/null", "commit", "-m",
                 "playtest: filter guest instruction features on verified graphics source"], log, source)
    changed = git(source, "diff", "--name-only", base_revision, "HEAD").splitlines()
    if changed != ["src/core/cpu_id.cpp"] or digest(source / changed[0]) != SOURCE_SHA:
        raise RuntimeError("Unexpected changes in the isolated candidate")
    status = git(source, "submodule", "status", "--recursive")
    if any(line[:1] in {"+", "-", "U"} for line in status.splitlines()):
        command(["git", "-c", "submodule.alternateErrorStrategy=info", "submodule", "update",
                 "--init", "--recursive", "--jobs", "4", "--reference", original], log, source)
    status = git(source, "submodule", "status", "--recursive")
    if any(line[:1] in {"+", "-", "U"} for line in status.splitlines()):
        raise RuntimeError("A dependency differs from the verified source")
    settings = {}
    for line in (baseline_build / "CMakeCache.txt").read_text().splitlines():
        match = re.fullmatch(r"(CMAKE_C_COMPILER|CMAKE_CXX_COMPILER):[^=]+=(.+)", line)
        if match:
            settings[match[1]] = match[2]
    if len(settings) != 2 or not all(Path(path).is_file() for path in settings.values()):
        raise RuntimeError("The verified build compilers are unavailable")
    prepare_ffmpeg(source, build, baseline_build)
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


def records(text):
    result = {}
    for line in text.splitlines():
        if not line.startswith("CPU_PROFILE_CPUID "):
            continue
        row = dict(item.split("=", 1) for item in line.split()[1:])
        key = row["mode"], int(row["cpu"]), int(row["leaf"], 16), int(row["subleaf"], 16)
        result[key] = tuple(int(row[reg], 16) for reg in ("eax", "ebx", "ecx", "edx"))
    return result


def validate_filter(before, after):
    old, new = records(before), records(after)
    errors = []
    for key in sorted(old.keys() | new.keys()):
        leaf, subleaf = key[2:]
        if leaf == 7 and subleaf in (1, 2, 0xffffffff) and key not in old:
            expected = (0, 0, 0, 0)
        elif key not in old:
            errors.append({"key": key, "error": "Unexpected additional CPUID result"})
            continue
        else:
            expected = list(old[key])
            if leaf == 1:
                expected[2] &= ~0x40001000
            elif leaf == 7:
                expected = [0, expected[1] & 8 if subleaf == 0 else 0, 0, 0]
            elif leaf == 0x80000001:
                expected[2] &= ~0x20218800
            expected = tuple(expected)
        if new.get(key) != expected:
            errors.append({"key": key, "expected": expected, "actual": new.get(key)})
    states = lambda text: sorted(line for line in text.splitlines() if line.startswith("CPU_PROFILE_XCR0 "))
    if states(before) != states(after):
        errors.append({"error": "XCR0 changed"})
    for mode, cpu in {(key[0], key[1]) for key in old}:
        for subleaf in (1, 2, 0xffffffff):
            if new.get((mode, cpu, 7, subleaf)) != (0, 0, 0, 0):
                errors.append({"error": "Missing zero subleaf", "mode": mode, "cpu": cpu, "subleaf": subleaf})
    return {"ok": not errors, "errors": errors, "compared_rows": len(new)}


def read_baseline(path, collector):
    with tarfile.open(path) as archive:
        members = {Path(m.name).name: m for m in archive.getmembers() if m.isfile()}
        def read(name):
            member = members[name]
            if member.size > 4 * 1024 * 1024:
                raise RuntimeError("Unexpected baseline file size")
            return archive.extractfile(member).read().decode()
        summary = json.loads(read("summary.json"))
        if not summary.get("ok") or summary.get("artifacts") != collector.ARTIFACTS:
            raise RuntimeError("The baseline archive is not the verified passing candidate")
        logs = {name: read(name + ".profile.txt") for name in ("guest-native", "guest-translated")}
        for text in logs.values():
            if not collector.parse_profile(text)["ok"]:
                raise RuntimeError("The baseline profile is incomplete")
        return summary, logs


def cancel(signum, frame):
    raise KeyboardInterrupt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--revision", required=True)
    args = parser.parse_args()
    if not re.fullmatch(r"[0-9a-f]{40}", args.revision):
        raise RuntimeError("An immutable revision is required")
    cache = Path.home() / ".cache/shadps4-affinity-20261007"
    cache.mkdir(parents=True, exist_ok=True)
    with (cache / "homebrew.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        evidence = Path(tempfile.mkdtemp(prefix="shadps4-cpu-filter-", dir=Path.home()))
        summary = {"source_change": SOURCE_REVISION, "reference_ps4_validated": False, "tests": {}, "ok": False}
        installed = Path.home() / "Applications/shadps4/shadps4"
        before = digest(installed) if installed.is_file() else None
        handlers = {sig: signal.signal(sig, cancel) for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP)}
        try:
            with tempfile.TemporaryDirectory(prefix="cpu-filter-", dir=cache) as folder:
                work = Path(folder)
                helper = work / "collect.py"
                download(args.revision, "hardware/cpu_profile/collect.py", helper, COLLECT_SHA)
                spec = importlib.util.spec_from_file_location("cpu_profile_collect", helper)
                collector = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(collector)
                collector.require_idle()
                baseline_path = Path.home() / "shadps4-cpu-profile-gn9jb_8u.tar.gz"
                baseline_summary, baseline_logs = read_baseline(baseline_path, collector)
                summary["baseline_archive"] = str(baseline_path)
                for name, text in baseline_logs.items():
                    (evidence / ("baseline-" + name + ".profile.txt")).write_text(text)
                for relative, expected in collector.ARTIFACTS.items():
                    if digest(cache / relative) != expected:
                        raise RuntimeError("The verified runtime changed: " + relative)
                env = collector.display_environment()
                binary, client, info = build_candidate(cache, work, evidence)
                summary["build"] = info
                suite = work / "suite.tar.gz"
                download(args.revision, "hardware/cpu_profile/suite.tar.gz", suite, SUITE_SHA)
                collector.unpack_suite(suite, work / "suite")
                host_log = evidence / "host.log"
                collector.run([work / "suite/native-cpu-profile"], work, os.environ.copy(), host_log)
                host = collector.parse_profile(host_log.read_text())
                host["features_unchanged"] = (host["features_by_cpu"] == baseline_summary["tests"]["host"]["features_by_cpu"])
                host["ok"] &= host["features_unchanged"]
                summary["tests"]["host"] = host
                drrun = cache / "runtime-build-a522a5055820/bin64/drrun"
                modes = {"guest-native": [binary], "guest-translated": [drrun, "-disable_rseq",
                         "-vm_base", "0x710020000000", "-no_vm_base_near_app", "-c", client, "--", binary]}
                profiles = {}
                for name, prefix in modes.items():
                    say("TEST=" + name + "; automatic, keep games closed")
                    runtime = work / name
                    collector.prepare_runtime(runtime)
                    output = evidence / (name + ".log")
                    try:
                        collector.run(prefix + ["--ignore-game-patch", work / "suite/CPUP00001/eboot.bin"],
                                      runtime, env, output, emulator=True)
                    finally:
                        for log in (runtime / "user/log").glob("*"):
                            if log.is_file():
                                shutil.copy2(log, evidence / (name + "-" + log.name))
                        profile = runtime / "user/data/cpu-profile.txt"
                        if profile.is_file():
                            shutil.copy2(profile, evidence / (name + ".profile.txt"))
                    text = (evidence / (name + ".profile.txt")).read_text()
                    result = collector.parse_profile(text)
                    result["filter_comparison"] = validate_filter(baseline_logs[name], text)
                    result["ok"] &= result["filter_comparison"]["ok"]
                    summary["tests"][name] = result
                    profiles[name] = records(text)
                    say("RESULT=" + name + (" PASS" if result["ok"] else " FAIL"))
                summary["native_translated_equal"] = profiles["guest-native"] == profiles["guest-translated"]
                summary["baseline_artifacts_preserved"] = all(digest(cache / name) == expected for name, expected in collector.ARTIFACTS.items())
                summary["ok"] = (all(test["ok"] for test in summary["tests"].values())
                                 and summary["native_translated_equal"] and summary["baseline_artifacts_preserved"])
        except (Exception, KeyboardInterrupt) as error:
            summary["error"] = type(error).__name__ + ": " + str(error)
            say("TEST_ERROR=" + summary["error"])
            if (evidence / "build.log").exists() and "build" not in summary:
                say("\n".join((evidence / "build.log").read_text(errors="replace").splitlines()[-25:]))
        finally:
            for sig in handlers:
                signal.signal(sig, signal.SIG_IGN)
            try:
                after = digest(installed) if installed.is_file() else None
                summary["installed_binary_changed"] = before != after
                summary["ok"] &= before == after
                (evidence / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
                archive_path = evidence.with_suffix(".tar.gz")
                with tarfile.open(archive_path, "w:gz") as archive:
                    archive.add(evidence, arcname=evidence.name)
                say("UPLOAD_ONLY=" + str(archive_path))
                say("SSH stays open. Installed emulator, saves and session guard were not edited.")
            finally:
                for sig, handler in handlers.items():
                    signal.signal(sig, handler)
        return 0 if summary["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
