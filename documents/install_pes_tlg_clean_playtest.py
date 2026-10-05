#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
# SPDX-License-Identifier: GPL-2.0-or-later
"""Build and select the exact clean PES + The Last Guardian renderer playtest."""

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
BRANCH = "playtest/pes-zpass-tlg-precise-clean-20261005"
REVISION = "869dc7e206574fce19813921db77892f0e447ecc"
IMAGE = "shadps4-render-playtest-builder:trixie-clang19-v1"
UNSET_LINE = (
    "unset SHADPS4_NGS2_DIAGNOSTICS SHADPS4_GRAPHICS_DIAGNOSTICS "
    "SHADPS4_NGS2_DIAGNOSTICS_TRIGGER\n"
)
GRAPHICS_EXPORT = "export SHADPS4_GRAPHICS_DIAGNOSTICS=1\n"
DOCKERFILE = """FROM debian:trixie-slim
RUN apt-get update && apt-get install -y --no-install-recommends \\
    ca-certificates git cmake ninja-build build-essential clang-19 clang-tools-19 llvm-19-dev \\
    llvm-19-tools lld-19 ccache pkg-config python3 nasm \\
    libasound2-dev libpulse-dev libopenal-dev libssl-dev zlib1g-dev libedit-dev \\
    libudev-dev libevdev-dev libjack-jackd2-dev libsndio-dev libvulkan-dev \\
    libpng-dev libx11-dev libxext-dev libwayland-dev libdecor-0-dev \\
    libxkbcommon-dev libxcursor-dev libxi-dev libxss-dev libxtst-dev \\
    libxrandr-dev libxfixes-dev libxinerama-dev libegl1-mesa-dev \\
    libgl1-mesa-dev libgles2-mesa-dev uuid-dev libdbus-1-dev \\
    && test -x /usr/bin/clang-scan-deps-19 \\
    && rm -rf /var/lib/apt/lists/*
"""


def say(value):
    print(value, flush=True)


def digest(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def regular(path):
    path = Path(path)
    return path.is_file() and not path.is_symlink()


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


def atomic_write(path, data, mode):
    path = Path(path)
    fd, name = tempfile.mkstemp(prefix=path.name + ".new-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as out:
            out.write(data)
            out.flush()
            os.fsync(out.fileno())
            os.fchmod(out.fileno(), mode)
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)


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
        raise RuntimeError("Command failed: " + shlex.join(map(str, command)))
    return result


def build(home):
    work = home / ".cache/shadps4-render-playtest"
    source = work / "source"
    folder = work / "build"
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

    dirty = subprocess.check_output(
        ["git", "-C", source, "status", "--porcelain", "--untracked-files=normal"], text=True
    )
    if dirty:
        raise RuntimeError("Build source has local edits/untracked files: " + dirty[:1000])

    run([
        "git", "-C", source, "fetch", "--no-tags", "--no-recurse-submodules",
        "origin", BRANCH,
    ])
    fetched = subprocess.check_output(["git", "-C", source, "rev-parse", "FETCH_HEAD"], text=True).strip()
    if fetched != REVISION:
        raise RuntimeError(
            "Playtest branch moved; refusing an unreviewed build. expected="
            + REVISION + " fetched=" + fetched
        )
    run(["git", "-C", source, "checkout", "--detach", REVISION])
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
                "cmake", "-S", source, "-B", folder, "-G", "Ninja",
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
            docker(["cmake", "--build", folder, "--target", "shadps4", "--parallel", jobs]),
            log=log,
        )
    binary = folder / "shadps4"
    smoke(binary)
    say("BUILD_LOG=" + str(log_path))
    return binary


def ensure_graphics_diagnostics(home):
    wrapper = home / ".local/bin/shadps4-esde"
    if not regular(wrapper):
        raise RuntimeError("Expected regular ES-DE shadPS4 wrapper: " + str(wrapper))
    text = wrapper.read_text()
    replacement = UNSET_LINE + GRAPHICS_EXPORT
    if replacement not in text:
        if text.count(UNSET_LINE) != 1:
            raise RuntimeError("Launcher diagnostics reset is not recognized; wrapper preserved")
        text = text.replace(UNSET_LINE, replacement, 1)
        atomic_write(wrapper, text.encode(), stat.S_IMODE(wrapper.stat().st_mode))
    check = wrapper.read_text()
    if check.count(replacement) != 1:
        raise RuntimeError("Graphics diagnostics launcher verification failed")
    say("GRAPHICS_DIAGNOSTICS=ENABLED")


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
    release = releases / ("render-playtest-" + REVISION[:12])
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
        ensure_graphics_diagnostics(home)

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
            "pes_wait_zpass_predication": True,
            "tlg_base_instance_step_rate": True,
            "tlg_precise_readbacks_test_override": True,
            "graphics_diagnostics": True,
        }
        state_path.write_text(json.dumps(state, indent=2) + "\n")

        say("")
        say("============================================================")
        say(" CLEAN PES + THE LAST GUARDIAN PLAYTEST READY")
        say("============================================================")
        say("REVISION=" + REVISION)
        say("BINARY=" + str(binary))
        say("BINARY_SHA256=" + built_sha)
        say("PREVIOUS_BINARY=" + str(previous))
        say("PES_WAIT_ZPASS_PREDICATION=ENABLED")
        say("TLG_BASE_INSTANCE_STEP_RATE=ENABLED")
        say("TLG_PRECISE_READBACKS_TEST_OVERRIDE=ENABLED")
        say("GAME_LAUNCHED=NO")
        say("RESULT=PASS")
        say("")
        say("Launch The Last Guardian Continue save, then one PES match from the normal ES-DE entries.")
        say("Expected TLG log: GPU readbacksMode: 2")
        say("After both tests: shadps4-pack-playtest-logs")
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
