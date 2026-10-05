#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
# SPDX-License-Identifier: GPL-2.0-or-later
"""Repair playtest logging without changing the guarded ES-DE launcher path."""

import os
from pathlib import Path
import stat
import tempfile
import sys

GUARD_END = "# END SHADPS4_SESSION_GUARD_V1\n"
OLD_MARKER = "# SHADPS4_PLAYTEST_LOG_WRAPPER_V1"
NEW_MARKER = "# SHADPS4_PLAYTEST_LOG_INLINE_V2"


def atomic_write(path, data, mode):
    path = Path(path)
    fd, name = tempfile.mkstemp(prefix=path.name + ".repair-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as output:
            output.write(data)
            output.flush()
            os.fsync(output.fileno())
            os.fchmod(output.fileno(), mode)
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)


def main():
    if os.geteuid() == 0 or sys.argv[1:]:
        raise RuntimeError("Run as chreece without sudo or arguments")

    home = Path.home()
    wrapper = home / ".local/bin/shadps4-esde"
    state = home / ".local/lib/shadps4-playtest-logging"
    original = state / "shadps4-esde.before-logging"
    log_root = home / ".local/state/shadps4-playtest-logs"

    if not wrapper.is_file() or wrapper.is_symlink():
        raise RuntimeError("Expected regular shadps4-esde launcher")
    if not original.is_file() or original.is_symlink():
        raise RuntimeError("Original guarded launcher backup is missing")

    original_text = original.read_text(errors="strict")
    if original_text.count(GUARD_END) != 1:
        raise RuntimeError("Original launcher guard marker is not recognized")
    if OLD_MARKER in original_text or NEW_MARKER in original_text:
        raise RuntimeError("Original launcher backup unexpectedly contains playtest logging")

    current_text = wrapper.read_text(errors="strict")
    if OLD_MARKER not in current_text and NEW_MARKER not in current_text:
        raise RuntimeError("Current launcher is not the logging wrapper installed by this playtest")

    log_root.mkdir(parents=True, exist_ok=True)
    injection = f"""{NEW_MARKER}
_PLAYTEST_LOG_ROOT={str(log_root)!r}
mkdir -p "$_PLAYTEST_LOG_ROOT"
_playtest_stamp="$(date +%Y%m%d-%H%M%S-%N)"
_playtest_arg="${{1:-unknown}}"
_playtest_base="$(basename -- "$_playtest_arg")"
_playtest_safe="$(printf '%s' "$_playtest_base" | tr -c 'A-Za-z0-9._-' '_')"
_PLAYTEST_SESSION="$_PLAYTEST_LOG_ROOT/$_playtest_stamp-$_playtest_safe"
mkdir -p "$_PLAYTEST_SESSION"
ln -sfn "$_PLAYTEST_SESSION" "$_PLAYTEST_LOG_ROOT/latest"
{{
  printf 'schema=2\\n'
  printf 'started=%s\\n' "$(date --iso-8601=seconds)"
  printf 'launcher=%s\\n' "$0"
  printf 'launcher_pid=%s\\n' "$$"
  printf 'entry=%q\\n' "$_playtest_arg"
  printf 'display=%s\\n' "${{DISPLAY:-}}"
  printf 'wayland_display=%s\\n' "${{WAYLAND_DISPLAY:-}}"
  printf 'xdg_session_type=%s\\n' "${{XDG_SESSION_TYPE:-}}"
  printf 'graphics_diagnostics=1\\n'
  printf 'startup_diagnostics=1\\n'
  uname -a 2>/dev/null || true
}} > "$_PLAYTEST_SESSION/session.meta"
export SHADPS4_GRAPHICS_DIAGNOSTICS=1
export SHADPS4_STARTUP_DIAGNOSTICS=1
exec >>"$_PLAYTEST_SESSION/runtime.log" 2>&1
# END SHADPS4_PLAYTEST_LOG_INLINE_V2
"""
    repaired = original_text.replace(GUARD_END, GUARD_END + injection, 1)
    mode = stat.S_IMODE(wrapper.stat().st_mode)
    atomic_write(wrapper, repaired.encode(), mode)

    check = wrapper.read_text(errors="strict")
    if check.count(GUARD_END) != 1 or check.count(NEW_MARKER) != 1 or OLD_MARKER in check:
        raise RuntimeError("Repaired launcher verification failed")

    packer = home / ".local/bin/shadps4-pack-playtest-logs"
    packer_text = f"""#!/usr/bin/env bash
set -euo pipefail
ROOT={str(log_root)!r}
[[ -d "$ROOT" ]] || {{ echo "No playtest logs found" >&2; exit 1; }}

since="$(find "$ROOT" -mindepth 2 -maxdepth 2 -name session.meta -type f -print0 2>/dev/null \
  | xargs -0 -r grep -h '^started=' \
  | cut -d= -f2- | sort | head -n1 || true)"
if [[ -z "$since" ]]; then
  since="$(date --iso-8601=seconds -d '2 hours ago')"
fi

journalctl -k --since "$since" --no-pager 2>/dev/null \
  | grep -Eai 'amdgpu|gpu|vulkan|segfault|general protection|page fault|oom|out of memory|shadps4' \
  | tail -n 5000 >"$ROOT/kernel-since-first-session.log" || true

journalctl --user --since "$since" --no-pager 2>/dev/null \
  | grep -Eai 'shadps4|es-de|sunshine|segfault|crash|vulkan' \
  | tail -n 5000 >"$ROOT/user-journal-since-first-session.log" || true

if command -v coredumpctl >/dev/null 2>&1; then
  coredumpctl --no-pager list --since "$since" 2>/dev/null \
    | grep -Eai 'shadps4|COMM' >"$ROOT/coredumps-since-first-session.list" || true
fi

out="$HOME/shadps4-playtest-logs-$(date +%Y%m%d-%H%M%S).tar.gz"
tar -C "$(dirname "$ROOT")" -czf "$out" "$(basename "$ROOT")"
printf 'PLAYTEST_LOG_ARCHIVE=%s\\n' "$out"
"""
    atomic_write(packer, packer_text.encode(), 0o755)

    # Keep only the failed zero/one-second sessions from the broken outer wrapper out of the
    # next evidence set. build.json is retained.
    for child in log_root.iterdir():
        if child.name in {"build.json", "latest"} or not child.is_dir() or child.is_symlink():
            continue
        meta = child / "session.meta"
        runtime = child / "runtime.log"
        try:
            if runtime.is_file() and "Unexpected ES-DE launcher path" in runtime.read_text(errors="replace"):
                for item in child.iterdir():
                    if item.is_file() or item.is_symlink():
                        item.unlink()
                child.rmdir()
        except OSError:
            pass
    latest = log_root / "latest"
    if latest.is_symlink() and not latest.exists():
        latest.unlink(missing_ok=True)

    print("PLAYTEST_LOGGING_REPAIR=PASS")
    print("LAUNCHER_PATH=" + str(wrapper))
    print("GUARD_PATH_PRESERVED=YES")
    print("BUILD_CHANGED=NO")
    print("PACK_LOGS=" + str(packer))
    print("Now launch through Moonlight/Sunshine -> ES-DE normally.")


if __name__ == "__main__":
    try:
        main()
    except (Exception, KeyboardInterrupt) as error:
        print("PLAYTEST_LOGGING_REPAIR=FAIL: " + type(error).__name__ + ": " + str(error),
              file=sys.stderr)
        sys.exit(1)
