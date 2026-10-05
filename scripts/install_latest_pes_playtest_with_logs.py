#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
# SPDX-License-Identifier: GPL-2.0-or-later
"""Refresh retained fixes onto current upstream, build/select, and enable ES-DE playtest logs."""

import fcntl
import hashlib
import importlib
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile
import urllib.request

SEED_REVISION = "ab46655439abc75d491e403fe2447ec3d58c4176"
CPU_FIX = "94834eb610f1e92c5a6ae7385f48de417ad984e6"
RAW = "https://raw.githubusercontent.com/Chreece/shadPS4/"
HELPERS = {
    "pes_refresh_baseline.py": (
        "19153e3113228c22b4f233487043d768469532f3",
        "2d0ba312366bbcbd1ea22b60528e0110de69a857218f1735afb87f894998b49f",
    ),
    "install_local_default.py": (
        "19153e3113228c22b4f233487043d768469532f3",
        "8c7a85d2819775ad853d692ba754f905ae2d0306864de31bef7dd91e81a2288c",
    ),
}
MANIFEST_SHA256 = "0b6c90960c80b220105e52f6558e0e597812f6752381184352520529a098721e"
AUTO_CROSS_MARKER = "SHADPS4_DIAG_AUTO_CROSS_MS"


def digest(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def atomic_write(path, data, mode):
    path = Path(path)
    fd, name = tempfile.mkstemp(prefix=path.name + ".new-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as output:
            output.write(data)
            output.flush()
            os.fsync(output.fileno())
            os.fchmod(output.fileno(), mode)
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)


def atomic_link(path, target):
    path = Path(path)
    fd, name = tempfile.mkstemp(prefix=path.name + ".new-", dir=path.parent)
    os.close(fd)
    os.unlink(name)
    temporary = Path(name)
    try:
        temporary.symlink_to(target)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


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


def download(url, expected, target):
    with urllib.request.urlopen(url, timeout=45) as response:
        data = response.read(2 * 1024 * 1024 + 1)
    if len(data) > 2 * 1024 * 1024:
        raise RuntimeError("Download unexpectedly large: " + target.name)
    actual = hashlib.sha256(data).hexdigest()
    if actual != expected:
        raise RuntimeError(
            "Checksum mismatch for " + target.name + "\nexpected=" + expected + "\nactual=" + actual
        )
    target.write_bytes(data)


def prepare_helpers(work):
    helper_dir = work / "helpers"
    helper_dir.mkdir()
    for name, (revision, expected) in HELPERS.items():
        download(RAW + revision + "/scripts/" + name, expected, helper_dir / name)
    manifest_path = helper_dir / "LOCAL_TEST_BASELINE.json"
    download(
        RAW + SEED_REVISION + "/documents/LOCAL_TEST_BASELINE.json",
        MANIFEST_SHA256,
        manifest_path,
    )
    sys.path.insert(0, str(helper_dir))
    return json.loads(manifest_path.read_text())


def verify_refreshed_source(source):
    source_dir = Path(source["source_directory"])
    if source_dir.is_symlink() or not source_dir.is_dir():
        raise RuntimeError("Invalid refreshed source directory")
    head = subprocess.check_output(
        ["git", "-C", source_dir, "rev-parse", "HEAD"], text=True
    ).strip()
    if head != source["revision"]:
        raise RuntimeError("Refreshed source HEAD mismatch")
    pad = source_dir / "src/core/libraries/pad/pad.cpp"
    if AUTO_CROSS_MARKER in pad.read_text(errors="replace"):
        raise RuntimeError("Automatic Cross diagnostic code is present; refusing manual playtest build")
    print("REFRESHED_SOURCE=PASS:" + head, flush=True)
    print("AUTO_CROSS=ABSENT", flush=True)


def select_binary(home, revision, built):
    root = home / "Applications/shadps4"
    releases = root / "releases"
    core = root / "shadps4"
    if root.is_symlink() or releases.is_symlink() or not releases.is_dir():
        raise RuntimeError("Unexpected shadPS4 installation layout")
    if not core.is_symlink():
        raise RuntimeError("Expected symlink-selected shadPS4 core")

    previous = core.resolve(strict=True)
    previous_sha = digest(previous)
    built_sha = digest(built)

    release = releases / ("latest-pes-playtest-" + revision[:12])
    if release.is_symlink() or (release.exists() and not release.is_dir()):
        raise RuntimeError("Unexpected target release path")
    release.mkdir(parents=True, exist_ok=True)
    binary = release / "shadps4"
    if binary.is_symlink():
        raise RuntimeError("Unexpected target binary symlink")
    if binary.exists() and digest(binary) != built_sha:
        raise RuntimeError("Existing target binary differs from the verified build")
    if not binary.exists():
        shutil.copy2(built, binary)
        binary.chmod(0o755)
    if digest(binary) != built_sha:
        raise RuntimeError("Selected binary copy checksum mismatch")

    require_idle()
    atomic_link(core, binary)
    if core.resolve(strict=True) != binary or digest(core) != built_sha:
        atomic_link(core, previous)
        raise RuntimeError("Core switch verification failed; previous core restored")

    return {
        "binary": str(binary),
        "binary_sha256": built_sha,
        "previous_binary": str(previous),
        "previous_sha256": previous_sha,
    }


def install_logging_wrapper(home, revision, binary_sha):
    wrapper = home / ".local/bin/shadps4-esde"
    if not wrapper.is_file() or wrapper.is_symlink():
        raise RuntimeError("Expected regular shadps4-esde wrapper")

    state = home / ".local/lib/shadps4-playtest-logging"
    state.mkdir(parents=True, exist_ok=True)
    inner = state / "shadps4-esde-inner"
    original_backup = state / "shadps4-esde.before-logging"
    marker = "# SHADPS4_PLAYTEST_LOG_WRAPPER_V1"

    current = wrapper.read_text(errors="strict")
    if marker not in current:
        data = wrapper.read_bytes()
        atomic_write(inner, data, stat.S_IMODE(wrapper.stat().st_mode))
        if not original_backup.exists():
            atomic_write(original_backup, data, stat.S_IMODE(wrapper.stat().st_mode))
    elif not inner.is_file() or inner.is_symlink():
        raise RuntimeError("Logging wrapper active but preserved inner launcher is missing")

    log_root = home / ".local/state/shadps4-playtest-logs"
    log_root.mkdir(parents=True, exist_ok=True)

    outer = f"""#!/usr/bin/env bash
{marker}
set -uo pipefail
INNER={str(inner)!r}
ROOT={str(log_root)!r}
REVISION={revision!r}
BINARY_SHA256={binary_sha!r}
mkdir -p "$ROOT"

stamp="$(date +%Y%m%d-%H%M%S-%N)"
arg="${{1:-unknown}}"
base="$(basename -- "$arg")"
safe="$(printf '%s' "$base" | tr -c 'A-Za-z0-9._-' '_')"
session="$ROOT/$stamp-$safe"
mkdir -p "$session"
ln -sfn "$session" "$ROOT/latest"
start_epoch="$(date +%s)"
start_iso="$(date --iso-8601=seconds)"

{{
  printf 'schema=1\n'
  printf 'started=%s\n' "$start_iso"
  printf 'revision=%s\n' "$REVISION"
  printf 'binary_sha256=%s\n' "$BINARY_SHA256"
  printf 'entry=%q\n' "$arg"
  printf 'display=%s\n' "${{DISPLAY:-}}"
  printf 'wayland_display=%s\n' "${{WAYLAND_DISPLAY:-}}"
  printf 'xdg_session_type=%s\n' "${{XDG_SESSION_TYPE:-}}"
  printf 'sdl_videodriver=%s\n' "${{SDL_VIDEODRIVER:-}}"
  printf 'graphics_diagnostics=1\n'
  printf 'startup_diagnostics=1\n'
  uname -a 2>/dev/null || true
}} > "$session/session.meta"

export SHADPS4_GRAPHICS_DIAGNOSTICS=1
export SHADPS4_STARTUP_DIAGNOSTICS=1

"$INNER" "$@" >>"$session/runtime.log" 2>&1
rc=$?
end_epoch="$(date +%s)"
end_iso="$(date --iso-8601=seconds)"
printf 'ended=%s\nexit_code=%s\nduration_seconds=%s\n' \
  "$end_iso" "$rc" "$((end_epoch-start_epoch))" >>"$session/session.meta"

if command -v journalctl >/dev/null 2>&1; then
  timeout 8s journalctl -k --since "@$start_epoch" --until "@$end_epoch" --no-pager 2>/dev/null \
    | grep -Eai 'amdgpu|gpu|vulkan|segfault|general protection|page fault|oom|out of memory|shadps4' \
    | tail -n 2000 >"$session/kernel.log" || true
  timeout 8s journalctl --user --since "@$start_epoch" --until "@$end_epoch" --no-pager 2>/dev/null \
    | grep -Eai 'shadps4|es-de|sunshine|segfault|crash|vulkan' \
    | tail -n 2000 >"$session/user-journal.log" || true
fi

if command -v coredumpctl >/dev/null 2>&1; then
  timeout 8s coredumpctl --no-pager list --since "@$start_epoch" --until "@$end_epoch" 2>/dev/null \
    | grep -Eai 'shadps4|COMM' >"$session/coredumps.list" || true
fi

find "$ROOT" -mindepth 1 -maxdepth 1 -type d -printf '%T@ %p\n' 2>/dev/null \
  | sort -nr | awk 'NR>20 {{print $2}}' \
  | while IFS= read -r old; do rm -rf -- "$old"; done

exit "$rc"
"""
    atomic_write(wrapper, outer.encode(), stat.S_IMODE(wrapper.stat().st_mode))

    packer = home / ".local/bin/shadps4-pack-playtest-logs"
    packer_text = f"""#!/usr/bin/env bash
set -euo pipefail
ROOT={str(log_root)!r}
out="$HOME/shadps4-playtest-logs-$(date +%Y%m%d-%H%M%S).tar.gz"
[[ -d "$ROOT" ]] || {{ echo "No playtest logs found" >&2; exit 1; }}
tar -C "$(dirname "$ROOT")" -czf "$out" "$(basename "$ROOT")"
printf 'PLAYTEST_LOG_ARCHIVE=%s\n' "$out"
"""
    atomic_write(packer, packer_text.encode(), 0o755)

    restore = home / ".local/bin/shadps4-restore-unlogged-wrapper"
    restore_text = f"""#!/usr/bin/env bash
set -euo pipefail
src={str(original_backup)!r}
dst={str(wrapper)!r}
[[ -f "$src" && ! -L "$src" ]] || {{ echo "Original launcher backup missing" >&2; exit 1; }}
install -m "$(stat -c '%a' "$dst")" "$src" "$dst"
echo "RESTORED_UNLOGGED_WRAPPER=$dst"
"""
    atomic_write(restore, restore_text.encode(), 0o755)

    if marker not in wrapper.read_text(errors="strict"):
        raise RuntimeError("Logging wrapper verification failed")

    return {
        "wrapper": str(wrapper),
        "inner": str(inner),
        "log_root": str(log_root),
        "packer": str(packer),
        "restore": str(restore),
    }


def main():
    if os.geteuid() == 0 or sys.argv[1:]:
        raise RuntimeError("Run as chreece without sudo or arguments")

    require_idle()
    home = Path.home()
    lock_root = home / ".local/state/shadps4-ngs2"
    lock_root.mkdir(parents=True, exist_ok=True)

    with (lock_root / "deploy.lock").open("a") as deploy_lock:
        fcntl.flock(deploy_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)

        work = Path(tempfile.mkdtemp(prefix="shadps4-latest-playtest-", dir=home))
        print("WORK=" + str(work), flush=True)
        manifest = prepare_helpers(work)

        refresh = importlib.import_module("pes_refresh_baseline")
        installer = importlib.import_module("install_local_default")

        source = refresh.prepare(work / "source-refresh", SEED_REVISION, manifest)
        verify_refreshed_source(source)

        print("CURRENT_UPSTREAM=" + source["upstream"], flush=True)
        print("REFRESHED_REVISION=" + source["revision"], flush=True)
        print("PENDING_PRS=5228,5229,5230,5232,5234,5235", flush=True)
        print("PES_CPU_FIX=" + CPU_FIX, flush=True)

        built = installer.build(
            home,
            source["revision"],
            source["source_branch"],
            local_source=source["source_directory"],
        )
        require_idle()

        selection = select_binary(home, source["revision"], built)
        logging = install_logging_wrapper(
            home, source["revision"], selection["binary_sha256"]
        )

        state = {
            "schema": 1,
            "seed_revision": SEED_REVISION,
            "upstream_revision": source["upstream"],
            "revision": source["revision"],
            "source_branch": source["source_branch"],
            "binary": selection["binary"],
            "binary_sha256": selection["binary_sha256"],
            "previous_binary": selection["previous_binary"],
            "previous_sha256": selection["previous_sha256"],
            "pending_fixes": source["manifest"]["pending_fixes"],
            "already_upstream": source["manifest"].get("already_upstream", []),
            "cpu_fix": CPU_FIX,
            "auto_cross_absent": True,
            "logging": logging,
        }
        state_path = home / ".local/state/shadps4-playtest-logs/build.json"
        state_path.parent.mkdir(parents=True, exist_ok=True)
        state_path.write_text(json.dumps(state, indent=2) + "\n")

        print("", flush=True)
        print("============================================================", flush=True)
        print(" LATEST shadPS4 + RETAINED FIXES + PES CPU FIX READY", flush=True)
        print("============================================================", flush=True)
        print("UPSTREAM=" + source["upstream"], flush=True)
        print("PLAYTEST_REVISION=" + source["revision"], flush=True)
        print("BINARY_SHA256=" + selection["binary_sha256"], flush=True)
        print("PENDING_PRS=5228,5229,5230,5232,5234,5235", flush=True)
        print("PES_CPU_FIX=" + CPU_FIX, flush=True)
        print("AUTO_CROSS=ABSENT", flush=True)
        print("GAME_LAUNCHED=NO", flush=True)
        print("ES_DE_LOGGING=ENABLED", flush=True)
        print("LOG_DIRECTORY=" + logging["log_root"], flush=True)
        print("PACK_LOGS=" + logging["packer"], flush=True)
        print("RESULT=PASS", flush=True)
        print("", flush=True)
        print("Launch normally with Moonlight/Sunshine -> ES-DE.", flush=True)
        print("After PES and The Last Guardian tests run: shadps4-pack-playtest-logs", flush=True)


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
    finally:
        print("Returning to your existing SSH prompt.", flush=True)
