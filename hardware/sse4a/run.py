#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 shadPS4 Emulator Project
# SPDX-License-Identifier: GPL-2.0-or-later

"""Compare generated SSE4a execution before and after the fallback fix without installing it."""

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
import sys
import tarfile
import tempfile

HELPER_REVISION = "605ee9649addd9788e630e7c1bbcdaffda4c091c"
BUILD_SHA = "2b0d969c5914d65683f2da49e0755a9e00891c5327d356e69da792b058dcdf48"
COLLECT_SHA = "058c4767f20f2a54b980c1e9681e30c78d58c9d5d0c01de4e10d277efbcec632"
SUITE_SHA = "da547e583454c878c554f33e640579c953d969cb678009c7d5c9f76482ced4c5"
SOURCE_REVISION = "0c8f18444ca7805a8bf3fd1e142a12803685114b"
PATCH_SHA = "d8cd39c701b12a7d719976863de63e8a13809f3fa5c6b3c829656c550a457a6d"
BASE_TREE = "177f81ade3f26ad44f61229e97b11c359243333d"
BASE_BINARY = "06382c0af6897519aebb530a03e6a0a8b02e28801c2804d83c2aae46211ad027"
BASE_CLIENT = "51973bd84b6d55f47d596a0a81ef266f34b0015965eebf9ea084a1aebbc16bfa"
CASES = ("extrq_reg", "extrq_imm", "insertq_reg", "insertq_imm", "extrq_high", "extrq_imm_high",
         "insertq_high", "insertq_imm_high", "movntss", "movntsd", "movntss_high", "movntsd_high")


def digest(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def load_helper(work, name, expected):
    import urllib.request
    path = work / name
    with urllib.request.urlopen(f"https://raw.githubusercontent.com/Chreece/shadPS4/{HELPER_REVISION}/hardware/cpu_profile/{name}", timeout=30) as response:
        content = response.read(256 * 1024 + 1)
    if hashlib.sha256(content).hexdigest() != expected:
        raise RuntimeError("Helper checksum mismatch: " + name)
    path.write_bytes(content)
    spec = importlib.util.spec_from_file_location(name.removesuffix(".py"), path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def build_candidate(builder, cache, evidence, work, revision):
    git, command = builder.git, builder.command
    original = cache / "sse4a-source-7be5c17ec530"
    baseline_build = cache / "sse4a-build-7be5c17ec530"
    source = cache / ("sse4a-source-" + SOURCE_REVISION[:12])
    build = cache / ("sse4a-build-" + SOURCE_REVISION[:12])
    if git(original, "rev-parse", "HEAD^{tree}") != BASE_TREE:
        raise RuntimeError("The verified SSE4a source changed; preserved")
    if git(original, "status", "--porcelain", "--untracked-files=no", "--ignore-submodules=all"):
        raise RuntimeError("The original source has local changes; preserved")
    relative = "src/core/cpu_patches.cpp"
    patch = work / "generated.patch"
    builder.download(revision, "hardware/sse4a/generated.patch", patch, PATCH_SHA)
    expected = work / "expected-source"
    (expected / relative).parent.mkdir(parents=True)
    (expected / relative).write_bytes((original / relative).read_bytes())
    log = evidence / "build.log"
    command(["git", "apply", "--no-index", "--check", patch], log, expected)
    command(["git", "apply", "--no-index", patch], log, expected)
    expected_source = (expected / relative).read_text()
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
                 "playtest: interpret generated SSE4a bit-field instructions"], log, source)
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


def cancel(signum, frame):
    raise KeyboardInterrupt("Test interrupted")


def main():
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
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        evidence = Path(tempfile.mkdtemp(prefix="shadps4-sse4a-generated-", dir=Path.home()))
        summary = {"source_change": SOURCE_REVISION, "tests": {}, "ok": False,
                   "feature_reporting_changed": False}
        installed = Path.home() / "Applications/shadps4/shadps4"
        before = digest(installed) if installed.is_file() else None
        protected = {}
        handlers = {sig: signal.signal(sig, cancel) for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP)}
        try:
            with tempfile.TemporaryDirectory(prefix="sse4a-", dir=cache) as folder:
                work = Path(folder)
                collector = load_helper(work, "collect.py", COLLECT_SHA)
                builder = load_helper(work, "test_filter.py", BUILD_SHA)
                collector.require_idle()
                protected = {cache / "sse4a-build-7be5c17ec530/shadps4": BASE_BINARY,
                    cache / "sse4a-build-7be5c17ec530/src/core/cpu_id_translation/libshadps4_cpu_id.so": BASE_CLIENT}
                protected.update({cache / name: sha for name, sha in collector.ARTIFACTS.items()})
                for path, expected in protected.items():
                    if digest(path) != expected:
                        raise RuntimeError("The verified artifact changed: " + str(path))
                env = collector.display_environment()
                binary, client, info = build_candidate(builder, cache, evidence, work, args.revision)
                summary["build"] = info
                suite = work / "suite.tar.gz"
                builder.download(args.revision, "hardware/sse4a/suite.tar.gz", suite, SUITE_SHA)
                with tarfile.open(suite) as archive:
                    archive.extractall(work / "suite", filter="data")
                drrun = cache / "runtime-build-a522a5055820/bin64/drrun"
                modes = {"before-native": [cache / "sse4a-build-7be5c17ec530/shadps4"],
                         "after-native": [binary], "after-translated": [drrun, "-disable_rseq",
                         "-vm_base", "0x710020000000", "-no_vm_base_near_app", "-c", client, "--", binary]}
                for mode, prefix in modes.items():
                    for generated in (0, 1):
                        for index, case in enumerate(CASES):
                            name = f"{mode}-{'generated' if generated else 'static'}-{case}"
                            print("TEST=" + name, flush=True)
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
                                result["ok"] &= "error" not in result
                                summary["tests"][name] = result
                            print("RESULT=" + name + (" PASS" if result["ok"] else " FAIL"), flush=True)
                summary["ok"] = all(result["ok"] for name, result in summary["tests"].items() if name.startswith("after-"))
                summary["fixed_cases"] = [name for name, result in summary["tests"].items() if name.startswith("before-")
                    and not result["ok"] and summary["tests"][name.replace("before-", "after-", 1)]["ok"]]
        except (Exception, KeyboardInterrupt) as error:
            summary["error"] = type(error).__name__ + ": " + str(error)
            print("TEST_ERROR=" + summary["error"], flush=True)
            summary["ok"] = False
        finally:
            for sig in handlers:
                signal.signal(sig, signal.SIG_IGN)
            try:
                summary["installed_binary_unchanged"] = before == (digest(installed) if installed.is_file() else None)
                summary["baseline_artifacts_preserved"] = all(path.is_file() and digest(path) == expected for path, expected in protected.items())
                summary["ok"] &= summary["installed_binary_unchanged"] and summary["baseline_artifacts_preserved"]
                (evidence / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
                archive_path = evidence.with_suffix(".tar.gz")
                with tarfile.open(archive_path, "w:gz") as archive:
                    archive.add(evidence, arcname=evidence.name)
                print("UPLOAD_ONLY=" + str(archive_path), flush=True)
                print("SSH stays open. Installed emulator, saves and session guard were not edited.", flush=True)
            finally:
                for sig, handler in handlers.items():
                    signal.signal(sig, handler)
        return 0 if summary["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
