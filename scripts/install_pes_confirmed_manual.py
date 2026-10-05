#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
# SPDX-License-Identifier: GPL-2.0-or-later
"""Build/select the known-good v0.19 graphics core plus the proven PES guest-CPU fix."""

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.request

REVISION = "ac691d782afa92019a1be5f493d7fc522c878270"
SOURCE_BRANCH = "playtest/pes-v019-goodgfx-cpu-20261005"
HELPER_REVISION = "19153e3113228c22b4f233487043d768469532f3"
HELPER_SHA256 = "8c7a85d2819775ad853d692ba754f905ae2d0306864de31bef7dd91e81a2288c"
HELPER_URL = (
    "https://raw.githubusercontent.com/Chreece/shadPS4/"
    + HELPER_REVISION
    + "/scripts/install_local_default.py"
)
AUTO_CROSS_MARKER = "SHADPS4_DIAG_AUTO_CROSS_MS"


def digest(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def snapshot(paths):
    result = {}
    for path in paths:
        path = Path(path)
        if path.exists():
            if not path.is_file() or path.is_symlink():
                raise RuntimeError("Expected regular preserved file: " + str(path))
            result[str(path)] = digest(path)
        else:
            result[str(path)] = None
    return result


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
        raise RuntimeError(
            "Close shadPS4/ES-DE before switching the core, then rerun: " + ", ".join(busy)
        )


def download_helper(work):
    target = work / "install_local_default.py"
    print("Downloading checksum-verified build helper...", flush=True)
    with urllib.request.urlopen(HELPER_URL, timeout=45) as response:
        data = response.read(2 * 1024 * 1024 + 1)
    if len(data) > 2 * 1024 * 1024:
        raise RuntimeError("Build helper unexpectedly large")
    actual = hashlib.sha256(data).hexdigest()
    if actual != HELPER_SHA256:
        raise RuntimeError(
            "Build helper checksum mismatch\nexpected=" + HELPER_SHA256 + "\nactual=" + actual
        )
    target.write_bytes(data)
    spec = importlib.util.spec_from_file_location("pes_confirmed_installer", target)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def atomic_link(path, target):
    fd, name = tempfile.mkstemp(prefix=path.name + ".pes-confirmed-", dir=path.parent)
    os.close(fd)
    os.unlink(name)
    temporary = Path(name)
    try:
        temporary.symlink_to(target)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def verify_source_cache(home):
    source = home / ".cache/shadps4-ngs2-local/ca67919d-docker/source"
    head = subprocess.check_output(
        ["git", "-C", source, "rev-parse", "HEAD"], text=True
    ).strip()
    if head != REVISION:
        raise RuntimeError("Build source HEAD differs from pinned revision: " + head)
    pad = source / "src/core/libraries/pad/pad.cpp"
    if AUTO_CROSS_MARKER in pad.read_text(errors="replace"):
        raise RuntimeError("Refusing playtest build: automatic Cross injection is present")
    print("PES_PLAYTEST_SOURCE=PASS:" + head, flush=True)
    print("PES_AUTO_CROSS=ABSENT", flush=True)


def main():
    if os.geteuid() == 0 or sys.argv[1:]:
        raise RuntimeError("Run as chreece without sudo or arguments")
    if sys.platform != "linux" or os.uname().machine != "x86_64":
        raise RuntimeError("This installer is for the existing Linux x86_64 host")

    home = Path.home()
    require_idle()

    core = home / "Applications/shadps4/shadps4"
    releases = home / "Applications/shadps4/releases"
    if not core.is_symlink() or not releases.is_dir() or releases.is_symlink():
        raise RuntimeError("Expected the existing symlink-selected shadPS4 installation")

    preserved = [
        home / ".local/bin/shadps4-esde",
        home / "ES-DE/custom_systems/es_systems.xml",
        home / ".local/share/shadPS4/config.json",
        home / ".local/share/shadPS4/custom_configs/CUSA18676.json",
    ]
    before = snapshot(preserved)
    previous_target = core.resolve(strict=True)
    previous_sha = digest(previous_target)

    work = Path(tempfile.mkdtemp(prefix="pes-confirmed-install-", dir=home))
    installer = download_helper(work)

    print("============================================================", flush=True)
    print(" BUILDING KNOWN-GOOD GRAPHICS + PES CPU FIX CORE", flush=True)
    print(" revision      : " + REVISION, flush=True)
    print(" source branch : " + SOURCE_BRANCH, flush=True)
    print(" auto Cross    : disabled / absent", flush=True)
    print(" game launch   : none", flush=True)
    print(" ES-DE changes : none", flush=True)
    print("============================================================", flush=True)

    built = installer.build(home, REVISION, SOURCE_BRANCH)
    verify_source_cache(home)
    require_idle()

    if snapshot(preserved) != before:
        raise RuntimeError("A preserved ES-DE/launcher/settings file changed during build")
    if digest(previous_target) != previous_sha:
        raise RuntimeError("Previously selected core changed during build")

    built_sha = digest(built)
    release = releases / ("pes-v019-goodgfx-cpu-" + REVISION[:12])
    if release.is_symlink() or (release.exists() and not release.is_dir()):
        raise RuntimeError("Unexpected playtest release path")
    release.mkdir(parents=True, exist_ok=True)
    binary = release / "shadps4"
    if binary.is_symlink():
        raise RuntimeError("Unexpected playtest binary symlink")
    if binary.exists() and digest(binary) != built_sha:
        raise RuntimeError("Existing playtest binary differs from this build")
    if not binary.exists():
        shutil.copy2(built, binary)
        binary.chmod(0o755)
    if digest(binary) != built_sha:
        raise RuntimeError("Copied playtest binary checksum mismatch")

    state_root = home / ".local/state/shadps4-pes-v019-goodgfx-cpu"
    state_root.mkdir(parents=True, exist_ok=True)
    state = {
        "schema": 1,
        "revision": REVISION,
        "source_branch": SOURCE_BRANCH,
        "binary": str(binary),
        "binary_sha256": built_sha,
        "previous_target": str(previous_target),
        "previous_sha256": previous_sha,
        "preserved_files": before,
        "auto_cross_absent": True,
    }
    state_file = state_root / "installed.json"
    temporary_state = state_root / ".installed.json.tmp"
    temporary_state.write_text(json.dumps(state, indent=2) + "\n")
    os.replace(temporary_state, state_file)

    atomic_link(core, binary)

    if core.resolve(strict=True) != binary or digest(core) != built_sha:
        atomic_link(core, previous_target)
        raise RuntimeError("Core selection verification failed; previous core restored")
    if snapshot(preserved) != before:
        atomic_link(core, previous_target)
        raise RuntimeError("Preserved files changed during selection; previous core restored")

    print("", flush=True)
    print("============================================================", flush=True)
    print(" PES / LAST GUARDIAN A-B PLAYTEST BUILD READY", flush=True)
    print("============================================================", flush=True)
    print("COMMIT=" + REVISION, flush=True)
    print("BINARY=" + str(binary), flush=True)
    print("BINARY_SHA256=" + built_sha, flush=True)
    print("PREVIOUS_CORE=" + str(previous_target), flush=True)
    print("ES_DE_WRAPPER=UNCHANGED", flush=True)
    print("ES_DE_CONFIG=UNCHANGED", flush=True)
    print("GAME_SETTINGS=UNCHANGED", flush=True)
    print("AUTO_CROSS=ABSENT", flush=True)
    print("GAME_LAUNCHED=NO", flush=True)
    print("RESULT=PASS", flush=True)
    print("", flush=True)
    print("Now connect with Moonlight/Sunshine, open ES-DE and launch PES normally.", flush=True)


if __name__ == "__main__":
    try:
        main()
    except (Exception, KeyboardInterrupt) as error:
        print("RESULT=FAIL: " + type(error).__name__ + ": " + (str(error) or "Interrupted"),
              file=sys.stderr, flush=True)
        sys.exit(1)
    finally:
        print("Returning to your existing SSH prompt.", flush=True)
