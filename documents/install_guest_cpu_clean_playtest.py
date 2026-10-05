#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
# SPDX-License-Identifier: GPL-2.0-or-later
"""Build, test and select the exact clean guest-CPU candidate."""

import fcntl
import hashlib
import json
import os
from pathlib import Path
import shlex
import shutil
import stat
import subprocess
import sys
import tempfile

REPO = "https://github.com/Chreece/shadPS4.git"
BRANCH = "kernel/current-cpu-affinity-upstream-20261005"
REVISION = "6e9091173b6d548f6f1e947ac65998b57439a192"
IMAGE = "shadps4-guest-cpu-builder:trixie-clang19-v1"

DOCKERFILE = """FROM debian:trixie-slim
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates git cmake ninja-build build-essential clang-19 clang-tools-19 llvm-19-dev \
    llvm-19-tools lld-19 ccache pkg-config python3 nasm \
    libasound2-dev libpulse-dev libopenal-dev libssl-dev zlib1g-dev libedit-dev \
    libudev-dev libevdev-dev libjack-jackd2-dev libsndio-dev libvulkan-dev \
    libpng-dev libx11-dev libxext-dev libwayland-dev libdecor-0-dev \
    libxkbcommon-dev libxcursor-dev libxi-dev libxss-dev libxtst-dev \
    libxrandr-dev libxfixes-dev libxinerama-dev libegl1-mesa-dev \
    libgl1-mesa-dev libgles2-mesa-dev uuid-dev libdbus-1-dev \
    && test -x /usr/bin/clang-scan-deps-19 \
    && rm -rf /var/lib/apt/lists/*
"""


def say(value):
    print(value, flush=True)


def digest(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def atomic_link(path, target):
    path = Path(path)
    fd, name = tempfile.mkstemp(prefix=path.name + ".new-", dir=path.parent)
    os.close(fd)
    os.unlink(name)
    temp = Path(name)
    try:
        temp.symlink_to(target)
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def require_idle():
    busy = []
    for proc in Path("/proc").iterdir():
        if not proc.name.isdigit():
            continue
        try:
            if proc.stat().st_uid != os.getuid():
                continue
            exe = (proc / "exe").readlink().name.removesuffix(" (deleted)").lower()
            comm = (proc / "comm").read_text().strip().lower()
            if exe in ("shadps4", "shadps4.exe", "es-de") or comm == "es-de":
                busy.append(proc.name + ":" + exe)
        except (OSError, ValueError):
            continue
    if busy:
        raise RuntimeError("Close shadPS4/ES-DE before install: " + ", ".join(busy))


def smoke(binary):
    with binary.open("rb") as stream:
        if stream.read(5) != b"\x7fELF\x02":
            raise RuntimeError("Built core is not a 64-bit ELF executable")
    result = subprocess.run(["ldd", str(binary)], capture_output=True, text=True, timeout=30)
    if result.returncode or "not found" in result.stdout:
        raise RuntimeError("Missing runtime libraries: " + result.stdout + result.stderr)
    with tempfile.TemporaryDirectory(prefix="shadps4-smoke-") as tmp:
        result = subprocess.run(
            [str(binary), "--help"], cwd=tmp, capture_output=True, text=True, timeout=30
        )
        if result.returncode or "shadPS4 Emulator CLI" not in result.stdout:
            raise RuntimeError("Core startup check failed: " + (result.stdout + result.stderr)[-2000:])


def run(command, *, cwd=None, log=None):
    say("STEP=" + shlex.join(map(str, command)))
    result = subprocess.run(
        list(map(str, command)),
        cwd=cwd,
        stdout=log if log else None,
        stderr=subprocess.STDOUT if log else None,
        text=True,
    )
    if result.returncode:
        if log:
            log.flush()
            try:
                path = Path(log.name)
                with path.open("rb") as stream:
                    stream.seek(max(0, path.stat().st_size - 12000))
                    say(stream.read().decode(errors="replace"))
            except (OSError, AttributeError):
                pass
        raise RuntimeError("Command failed: " + shlex.join(map(str, command)))
    return result


def build(home):
    work = home / ".cache/shadps4-guest-cpu-playtest"
    source = work / "source"
    tests_folder = work / "build-tests"
    app_folder = work / "build-app"
    context = work / "docker-context"
    log_path = work / "build.log"
    for path in (work, context, work / "container-home", work / "ccache"):
        path.mkdir(parents=True, exist_ok=True)

    run(["docker", "info", "--format", "{{.ServerVersion}}"])
    if not source.exists():
        run(["git", "clone", "--no-checkout", REPO, source])
    if source.is_symlink():
        raise RuntimeError("Unexpected build-source symlink")

    remote = subprocess.check_output(
        ["git", "-C", source, "remote", "get-url", "origin"], text=True
    ).strip()
    if remote != REPO:
        raise RuntimeError("Build cache belongs to another repository")

    run([
        "git", "-C", source, "fetch", "--no-tags", "--no-recurse-submodules",
        "origin", BRANCH,
    ])
    fetched = subprocess.check_output(["git", "-C", source, "rev-parse", "FETCH_HEAD"], text=True).strip()
    if fetched != REVISION:
        raise RuntimeError(
            "Guest-CPU branch moved; refusing an unreviewed build. expected="
            + REVISION + " fetched=" + fetched
        )

    run(["git", "-C", source, "clean", "-ffd"])
    run(["git", "-C", source, "checkout", "--detach", "--force", REVISION])

    dirty = subprocess.check_output(
        ["git", "-C", source, "status", "--porcelain", "--untracked-files=normal"], text=True
    )
    if dirty:
        raise RuntimeError("Build source is unexpectedly dirty after pinned checkout: " + dirty[:1000])

    helper = (source / "src/core/libraries/kernel/threads/cpu_affinity.h").read_text()
    process = (source / "src/core/libraries/kernel/process.cpp").read_text()
    pthread_cpp = (source / "src/core/libraries/kernel/threads/pthread.cpp").read_text()
    pthread_h = (source / "src/core/libraries/kernel/threads/pthread.h").read_text()
    test = (source / "tests/test_kernel_cpu_affinity.cpp").read_text()

    required = {
        "guest CPU selector": "SelectGuestCpu" in helper,
        "current CPU query": "g_curthread->guest_cpu.load" in process,
        "thread creation update": "new_thread->UpdateGuestCpu((*attr)->cpuset);" in pthread_cpp,
        "affinity change update": "thread->UpdateGuestCpu(thread->attr.cpuset);" in pthread_cpp,
        "guest CPU storage": "std::atomic<s32> guest_cpu{0};" in pthread_h,
        "unit tests": "KernelCpuAffinity" in test,
    }
    missing = [name for name, present in required.items() if not present]
    if missing:
        raise RuntimeError("Candidate validation failed: " + ", ".join(missing))

    jobs = str(min(8, os.cpu_count() or 2))
    run(["git", "-C", source, "submodule", "update", "--init", "--recursive", "--jobs", jobs])

    (context / "Dockerfile").write_text(DOCKERFILE)
    run(["docker", "build", "--tag", IMAGE, context])

    def docker(args):
        return [
            "docker", "run", "--rm", "--user", f"{os.getuid()}:{os.getgid()}",
            "--mount", f"type=bind,src={work},dst={work}",
            "--workdir", str(work),
            "--env", "HOME=" + str(work / "container-home"),
            "--env", "CCACHE_DIR=" + str(work / "ccache"),
            IMAGE, *map(str, args),
        ]

    compiler = [
        "-DCMAKE_C_COMPILER=clang-19",
        "-DCMAKE_CXX_COMPILER=clang++-19",
        "-DCMAKE_CXX_COMPILER_CLANG_SCAN_DEPS=/usr/bin/clang-scan-deps-19",
    ]

    with log_path.open("w") as log:
        run(
            docker([
                "cmake", "-S", source, "-B", tests_folder, "-G", "Ninja",
                *compiler,
                "-DCMAKE_BUILD_TYPE=Release",
                "-DENABLE_TESTS=ON",
                "-DCMAKE_INTERPROCEDURAL_OPTIMIZATION_RELEASE=OFF",
                "-DCMAKE_C_COMPILER_LAUNCHER=ccache",
                "-DCMAKE_CXX_COMPILER_LAUNCHER=ccache",
            ]),
            log=log,
        )
        run(
            docker([
                "cmake", "--build", tests_folder, "--target",
                "shadps4_kernel_cpu_test", "--parallel", jobs,
            ]),
            log=log,
        )
        run(
            docker([
                tests_folder / "tests/shadps4_kernel_cpu_test",
                "--gtest_filter=KernelCpuAffinity.*",
            ]),
            log=log,
        )

        # The root CMakeLists deliberately omits the emulator executable when
        # ENABLE_TESTS=ON, so build the tested source revision again with tests
        # disabled instead of requesting a target that cannot exist.
        run(
            docker([
                "cmake", "-S", source, "-B", app_folder, "-G", "Ninja",
                *compiler,
                "-DCMAKE_BUILD_TYPE=Release",
                "-DENABLE_TESTS=OFF",
                "-DCMAKE_INTERPROCEDURAL_OPTIMIZATION_RELEASE=OFF",
                "-DCMAKE_C_COMPILER_LAUNCHER=ccache",
                "-DCMAKE_CXX_COMPILER_LAUNCHER=ccache",
            ]),
            log=log,
        )
        run(
            docker([
                "cmake", "--build", app_folder, "--target", "shadps4",
                "--parallel", jobs,
            ]),
            log=log,
        )

    binary = app_folder / "shadps4"
    smoke(binary)
    say("BUILD_LOG=" + str(log_path))
    say("UNIT_TESTS=PASS")
    return binary


def select(home, built):
    root = home / "Applications/shadps4"
    releases = root / "releases"
    core = root / "shadps4"
    if root.is_symlink() or releases.is_symlink() or not releases.is_dir():
        raise RuntimeError("Unexpected shadPS4 installation layout")
    if not core.exists():
        raise RuntimeError("Existing shadPS4 core is missing")

    previous = core.resolve(strict=True)
    previous_sha = digest(previous)
    built_sha = digest(built)
    release = releases / ("guest-cpu-playtest-" + REVISION[:12])
    if release.is_symlink() or (release.exists() and not release.is_dir()):
        raise RuntimeError("Unexpected target release path")
    release.mkdir(parents=True, exist_ok=True)
    binary = release / "shadps4"
    if binary.is_symlink():
        raise RuntimeError("Unexpected target binary symlink")
    if binary.exists() and digest(binary) != built_sha:
        raise RuntimeError("Existing target binary differs from verified build")
    if not binary.exists():
        shutil.copy2(built, binary)
        binary.chmod(0o755)
    if digest(binary) != built_sha:
        raise RuntimeError("Copied binary checksum mismatch")

    require_idle()
    atomic_link(core, binary)
    if core.resolve(strict=True) != binary or digest(core) != built_sha:
        atomic_link(core, previous)
        raise RuntimeError("Core switch verification failed; previous core restored")
    return previous, previous_sha, binary, built_sha


def main():
    if os.geteuid() == 0 or sys.argv[1:]:
        raise RuntimeError("Run as chreece without sudo or arguments")
    if sys.platform != "linux" or os.uname().machine != "x86_64":
        raise RuntimeError("This installer is only for the existing Linux x86-64 playtest host")
    for tool in ("git", "docker", "ldd"):
        if not shutil.which(tool):
            raise RuntimeError("Missing required tool: " + tool)

    home = Path.home()
    lock_root = home / ".local/state/shadps4-ngs2"
    lock_root.mkdir(parents=True, exist_ok=True)
    with (lock_root / "deploy.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        require_idle()
        built = build(home)
        require_idle()
        previous, previous_sha, binary, built_sha = select(home, built)

        state_path = home / ".local/state/shadps4-playtest-logs/build.json"
        state_path.parent.mkdir(parents=True, exist_ok=True)
        state = {
            "schema": 3,
            "revision": REVISION,
            "branch": BRANCH,
            "binary": str(binary),
            "binary_sha256": built_sha,
            "previous_binary": str(previous),
            "previous_sha256": previous_sha,
            "guest_cpu_candidate": True,
            "unit_tests": True,
            "scope": "upstream-main-plus-guest-cpu-only",
        }
        state_path.write_text(json.dumps(state, indent=2) + "\n")

        say("")
        say("============================================================")
        say(" CLEAN GUEST CPU PLAYTEST READY")
        say("============================================================")
        say("REVISION=" + REVISION)
        say("BINARY=" + str(binary))
        say("BINARY_SHA256=" + built_sha)
        say("PREVIOUS_BINARY=" + str(previous))
        say("UNIT_TESTS=PASS")
        say("SCOPE=UPSTREAM_MAIN_PLUS_GUEST_CPU_ONLY")
        say("GAME_LAUNCHED=NO")
        say("RESULT=PASS")
        say("")
        say("Launch PES from the normal ES-DE entry.")
        say("For this isolated test, startup/progress is the important result; rendering may differ.")
        say("After the test: shadps4-pack-playtest-logs")
        say("Returning to your existing SSH prompt.")


if __name__ == "__main__":
    try:
        main()
    except (Exception, KeyboardInterrupt) as error:
        print(
            "RESULT=FAIL: " + type(error).__name__ + ": " + (str(error) or "Interrupted"),
            file=sys.stderr,
            flush=True,
        )
        sys.exit(1)
