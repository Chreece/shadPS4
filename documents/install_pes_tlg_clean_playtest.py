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
BRANCH = "playtest/tlg-ui-diagnostics-20261005"
REVISION = "160cc34baa619263afb1a1dbe6e02770e57419df"
IMAGE = "shadps4-render-playtest-builder:trixie-clang19-v1"
UNSET_LINE = (
    "unset SHADPS4_NGS2_DIAGNOSTICS SHADPS4_GRAPHICS_DIAGNOSTICS "
    "SHADPS4_NGS2_DIAGNOSTICS_TRIGGER\n"
)
GRAPHICS_EXPORT = "export SHADPS4_GRAPHICS_DIAGNOSTICS=1\n"
UI_DIAG_EXPORT = "export SHADPS4_TLG_UI_DIAGNOSTICS=1\n"
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

    # This is a disposable, dedicated build checkout. A fresh --no-checkout clone
    # intentionally looks "dirty" because every tracked file is absent from the
    # worktree, so checking status before the first checkout is incorrect.
    # Clean only this verified cache checkout, then force the exact pinned commit.
    run(["git", "-C", source, "clean", "-ffd"])
    run(["git", "-C", source, "checkout", "--detach", "--force", REVISION])

    dirty = subprocess.check_output(
        ["git", "-C", source, "status", "--porcelain", "--untracked-files=normal"], text=True
    )
    if dirty:
        raise RuntimeError("Build source is unexpectedly dirty after pinned checkout: " + dirty[:1000])
    translate = (source / "src/shader_recompiler/frontend/translate/translate.cpp").read_text()
    if "ASSERT(base_instance_sgpr == -1);" in translate:
        raise RuntimeError("TLG base-instance/step-rate assertion is still present")
    if "fetch_data.Empty() || fetch_data.instance_offset_sgpr == -1" not in translate:
        raise RuntimeError("Upstream base-instance fetch semantics are missing")
    liverpool = (source / "src/video_core/amdgpu/liverpool.cpp").read_text()
    if "EvaluateZpass(" not in liverpool or "predication_execute" not in liverpool:
        raise RuntimeError("PES WAIT/ZPASS predication implementation is missing")
    emulator = (source / "src/emulator.cpp").read_text()
    if 'SetReadbacksMode(static_cast<u32>(GpuReadbacksMode::Precise), true)' not in emulator:
        raise RuntimeError("TLG game-specific Precise-readback test override is missing")
    startup = (source / "src/core/startup_progress.h").read_text()
    presenter = (source / "src/video_core/renderer_vulkan/vk_presenter.cpp").read_text()
    diagnostics = (source / "src/video_core/graphics_diagnostics.h").read_text()
    if "HasVisibleRgb8Content" not in startup or "DrawStartupLoading" not in presenter:
        raise RuntimeError("Centered startup loading screen implementation is missing")
    if "PrepareStartupReadback" not in presenter or "STARTUP_UI event=black_game_frame" not in presenter:
        raise RuntimeError("Startup loading black-frame retention is missing")
    if '"predicated-skip"' not in diagnostics or '"zpass-evaluation"' not in diagnostics:
        raise RuntimeError("PES predication diagnostics are missing")
    rasterizer = (source / "src/video_core/renderer_vulkan/vk_rasterizer.cpp").read_text()
    if "TLG_UI_PIPE" not in rasterizer or "TLG_UI_FRAME" not in rasterizer:
        raise RuntimeError("TLG UI renderer diagnostics are missing")
    process = (source / "src/core/libraries/kernel/process.cpp").read_text()
    pthread_cpp = (source / "src/core/libraries/kernel/threads/pthread.cpp").read_text()
    pthread_h = (source / "src/core/libraries/kernel/threads/pthread.h").read_text()
    if "g_curthread->guest_cpu.load" not in process:
        raise RuntimeError("PES guest CPU identity query fix is missing")
    if "UpdateGuestCpu(new_thread->attr.cpuset);" not in pthread_cpp:
        raise RuntimeError("PES initial guest CPU affinity tracking is missing")
    if "thread->UpdateGuestCpu(thread->attr.cpuset);" not in pthread_cpp:
        raise RuntimeError("PES guest CPU affinity update tracking is missing")
    if "std::atomic<s32> guest_cpu{0};" not in pthread_h:
        raise RuntimeError("PES guest CPU identity storage is missing")
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
    replacement = UNSET_LINE + GRAPHICS_EXPORT + UI_DIAG_EXPORT
    old_replacement = UNSET_LINE + GRAPHICS_EXPORT
    if replacement not in text:
        if old_replacement in text:
            text = text.replace(old_replacement, replacement, 1)
        elif text.count(UNSET_LINE) == 1:
            text = text.replace(UNSET_LINE, replacement, 1)
        else:
            raise RuntimeError("Launcher diagnostics reset is not recognized; wrapper preserved")
        atomic_write(wrapper, text.encode(), stat.S_IMODE(wrapper.stat().st_mode))
    check = wrapper.read_text()
    if check.count(replacement) != 1:
        raise RuntimeError("Graphics diagnostics launcher verification failed")
    say("GRAPHICS_DIAGNOSTICS=ENABLED")
    say("TLG_UI_DIAGNOSTICS=ENABLED")


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
            "startup_loading_screen": True,
            "pes_guest_cpu_identity": True,
            "pes_predication_diagnostics": True,
            "pes_wait_zpass_predication": True,
            "tlg_base_instance_step_rate": True,
            "tlg_precise_readbacks_test_override": True,
            "graphics_diagnostics": True,
            "tlg_ui_diagnostics": True,
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
        say("STARTUP_LOADING_SCREEN=ENABLED")
        say("PES_GUEST_CPU_IDENTITY=ENABLED")
        say("PES_PREDICATION_DIAGNOSTICS=ENABLED")
        say("PES_WAIT_ZPASS_PREDICATION=ENABLED")
        say("TLG_BASE_INSTANCE_STEP_RATE=ENABLED")
        say("TLG_PRECISE_READBACKS_TEST_OVERRIDE=ENABLED")
        say("TLG_UI_DIAGNOSTICS=ENABLED")
        say("GAME_LAUNCHED=NO")
        say("RESULT=PASS")
        say("")
        say("Launch The Last Guardian and reproduce the missing subtitle/menu-focus scene.")
        say("Move the menu selection several times, close the menu, wait a few seconds, then exit.")
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
