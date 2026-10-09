# SPDX-FileCopyrightText: 2026 shadPS4 Emulator Project
# SPDX-License-Identifier: GPL-2.0-or-later

import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import time
import zipfile

REVISION = "35cc6d34e8e21e4b62cfdad5ebe185eaa2c31854"
TREE = "a2d32b356103b523a4c023778dbec142e6b98930"
PREVIOUS_TREE = "555a0c57a421bca7a172268ee3c492ac151e2cfd"
CACHE_KEY = "b85320a062e7"
FFMPEG_SHA = "aacbbfb8e622b684bc5d3b4cd6c9f9f77f5def64ae8d83c0c5b3ebe657aa33dd"


def digest(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def command(args, log, cwd=None, timeout=3600, env=None):
    print("STEP=" + " ".join(map(str, args)), flush=True)
    environment = dict(os.environ, GIT_TERMINAL_PROMPT="0")
    if env:
        environment.update(env)
    count = int(environment.get("GIT_CONFIG_COUNT", "0"))
    for offset, (key, value) in enumerate((("http.lowSpeedLimit", "1024"),
                                         ("http.lowSpeedTime", "30"))):
        environment[f"GIT_CONFIG_KEY_{count + offset}"] = key
        environment[f"GIT_CONFIG_VALUE_{count + offset}"] = value
    environment["GIT_CONFIG_COUNT"] = str(count + 2)
    with Path(log).open("a") as output:
        process = subprocess.Popen(list(map(str, args)), cwd=cwd, env=environment,
                                   stdout=output, stderr=subprocess.STDOUT,
                                   stdin=subprocess.DEVNULL, start_new_session=True)
        start = time.monotonic()
        try:
            while True:
                try:
                    rc = process.wait(timeout=min(15, timeout))
                    break
                except subprocess.TimeoutExpired:
                    elapsed = time.monotonic() - start
                    print(f"WORKING={elapsed:.0f}s; log={log}", flush=True)
                    if elapsed >= timeout:
                        raise RuntimeError("Step timed out; see " + str(log))
            if rc:
                with Path(log).open("rb") as stream:
                    stream.seek(max(0, stream.seek(0, 2) - 4000))
                    print(stream.read().decode(errors="replace"), flush=True)
                raise RuntimeError(f"Step failed ({rc}); see {log}")
        finally:
            if process.poll() is None:
                try:
                    os.killpg(process.pid, signal.SIGTERM)
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait(timeout=5)
                except ProcessLookupError:
                    process.wait(timeout=5)


def git(source, *args):
    return subprocess.check_output(["git", "-C", str(source), *args],
                                   stderr=subprocess.STDOUT, timeout=60).decode().strip()


def verify_source(source, expected=TREE):
    if git(source, "rev-parse", "HEAD^{tree}") != expected:
        raise RuntimeError("Candidate source differs from the locally validated tree")
    if git(source, "status", "--porcelain", "--untracked-files=no", "--ignore-submodules=all"):
        raise RuntimeError("Candidate source has local edits; left untouched")


def build(cache, evidence):
    source = cache / ("generated-rcp-source-" + CACHE_KEY)
    output = cache / ("generated-rcp-build-" + CACHE_KEY)
    log = evidence / "build.log"
    if not source.exists():
        source.mkdir()
        command(["git", "init", source], log, timeout=30)
        command(["git", "remote", "add", "origin", "https://github.com/Chreece/shadPS4.git"],
                log, source, timeout=30)
    if not (source / "CMakeLists.txt").exists():
        command(["git", "fetch", "--depth=1", "--no-tags", "origin", REVISION],
                log, source, timeout=300)
        command(["git", "checkout", "--detach", REVISION], log, source, timeout=120)
    if git(source, "rev-parse", "HEAD^{tree}") == PREVIOUS_TREE:
        verify_source(source, PREVIOUS_TREE)
        print("BUILD=Adding the validated gamepad quit fix; reusing the previous build cache", flush=True)
        command(["git", "fetch", "--depth=1", "--no-tags", "origin", REVISION],
                log, source, timeout=300)
        command(["git", "checkout", "--detach", REVISION], log, source, timeout=120)
    verify_source(source)
    command(["git", "-c", "submodule.alternateErrorStrategy=info", "submodule", "update",
             "--init", "--recursive", "--jobs", "4"], log, source, timeout=1800)
    status = git(source, "submodule", "status", "--recursive")
    if any(line[:1] in {"+", "-", "U"} for line in status.splitlines()):
        raise RuntimeError("A dependency does not match the pinned source")
    (evidence / "submodules.txt").write_text(status + "\n")
    marker = output / "validated-build.json"
    if marker.is_file():
        saved = json.loads(marker.read_text())
        intact = bool(saved.get("files")) and all(
            Path(p).is_file() and digest(p) == sha for p, sha in saved["files"].items())
        if not intact or saved.get("source_tree") not in {TREE, PREVIOUS_TREE}:
            raise RuntimeError("Cached candidate changed; preserved for inspection")
        if saved["source_tree"] == TREE:
            print("BUILD=Reusing the verified candidate", flush=True)
            return output / "shadps4", saved
        marker.rename(output / "validated-build-before-exit-fix.json")
    compilers = next(((shutil.which(c), shutil.which(cxx)) for c, cxx in (
        ("clang-19", "clang++-19"), ("gcc-14", "g++-14"), ("gcc-15", "g++-15"))
        if shutil.which(c) and shutil.which(cxx)), None)
    if compilers is None:
        raise RuntimeError("Clang 19 or GCC 14/15 is required; no packages were installed")
    short = git(source / "externals/ffmpeg-core", "rev-parse", "--short", "HEAD")
    if not short.startswith("94dde08"):
        raise RuntimeError("Unexpected FFmpeg dependency")
    archive = output / "externals" / ("ffmpeg-" + short + ".zip")
    archive.parent.mkdir(parents=True, exist_ok=True)
    if not archive.exists() or digest(archive) != FFMPEG_SHA:
        candidates = cache.glob("*/externals/ffmpeg-94dde08*.zip")
        cached = next((p for p in candidates if p != archive and digest(p) == FFMPEG_SHA), None)
        if cached:
            shutil.copyfile(cached, archive)
        else:
            command(["curl", "--fail", "--location", "--connect-timeout", "10",
                     "--max-time", "120", "--max-filesize", "20000000", "--output", archive,
                     "https://github.com/shadps4-emu/ext-ffmpeg-core/releases/download/94dde08/ffmpeg-linux-x64.zip"],
                    log, timeout=130)
    if digest(archive) != FFMPEG_SHA:
        raise RuntimeError("FFmpeg download checksum mismatch")
    libraries = archive.with_suffix("") / "lib"
    libraries.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive) as package:
        for name in ("avformat", "avcodec", "swscale", "avutil", "avfilter", "swresample"):
            filename = "lib" + name + ".a"
            data = package.read(filename)
            if not data.startswith(b"!<arch>\n"):
                raise RuntimeError("Invalid FFmpeg archive")
            (libraries / filename).write_bytes(data)
    configure = ["cmake", "-S", source, "-B", output, "-G", "Ninja", "-DCMAKE_BUILD_TYPE=Release",
                 "-DENABLE_TESTS=OFF", "-DENABLE_UPDATER=OFF", "-DENABLE_DISCORD_RPC=OFF",
                 "-DCMAKE_CXX_SCAN_FOR_MODULES=OFF", "-DCMAKE_INTERPROCEDURAL_OPTIMIZATION_RELEASE=OFF",
                 "-DENABLE_CPU_ID_TRANSLATION=ON", "-DCMAKE_C_COMPILER=" + compilers[0],
                 "-DCMAKE_CXX_COMPILER=" + compilers[1]]
    if shutil.which("ccache"):
        configure += ["-DCMAKE_C_COMPILER_LAUNCHER=ccache", "-DCMAKE_CXX_COMPILER_LAUNCHER=ccache"]
    command(configure, log, timeout=900)
    command(["cmake", "--build", output, "--target", "shadps4", "--parallel",
             str(min(4, len(os.sched_getaffinity(0))))], log, timeout=5400)
    verify_source(source)
    binary = output / "shadps4"
    runtime = output / "cpu-id-runtime"
    files = [binary, runtime / "bin64/drrun", runtime / "libshadps4_cpu_id.so",
             runtime / "lib64/release/libdynamorio.so"]
    info = {"source_commit": REVISION, "source_tree": TREE,
            "source": str(source), "binary": str(binary),
            "files": {str(p): digest(p) for p in files}}
    marker.write_text(json.dumps(info, indent=2) + "\n")
    return binary, info
