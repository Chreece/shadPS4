#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
# SPDX-License-Identifier: GPL-2.0-or-later
# Generic guest CPU native-fault investigation. Never patches or installs anything.
# Use as a diagnostic runner only: debugger is NOT a runtime dependency.
set -Eeuo pipefail
STAGE="$HOME/projects/shadps4/GodOfWarRagnarok/tests/native-cpu-identity-merged-20261016"
BIN="$STAGE/build/shadps4"
LOGROOT="$HOME/projects/shadps4/logs"
ID="$(date +%Y%m%d-%H%M%S)-$$"
OUT="$STAGE/diagnostics/native-fault-$ID"
ARCHIVE="$LOGROOT/gow-native-cpu-fault-$ID.tar.gz"
mkdir -p "$OUT" "$LOGROOT"
STATUS="PRECHECK_FAILED"
GDB_EXIT="NOT_STARTED"
on_exit() {
    local rc=$?
    trap - EXIT
    printf 'STATUS=%s\nGDB_EXIT=%s\nGDB_LOG=%s\n' "$STATUS" "$GDB_EXIT" "$OUT/gdb.log" >"$OUT/summary.txt"
    printf 'INSTALLED_EMULATOR=UNCHANGED\nEXISTING_X11=UNCHANGED\nSSH_SESSION=UNCHANGED\nOTHER_EMULATORS=UNCHANGED\n' >> "$OUT/summary.txt"
    tar -C "$OUT" -czf "$ARCHIVE" . 2>/dev/null || true
    printf '\nSTATUS=%s\nGDB_EXIT=%s\nARCHIVE=%s\n' "$STATUS" "$GDB_EXIT" "$ARCHIVE"
    return "$rc"
}
trap on_exit EXIT

if ! command -v gdb >/dev/null 2>&1; then
    echo "GDB is missing; no package installation or system changes attempted."
    exit 2
fi
if [[ ! -x "$BIN" ]]; then
    echo "Native candidate not found: $BIN"
    exit 2
fi
EXPECTED="75fced13d1d4ef10998369160ee001871115127720fb08f52214fef4a5df7024"
ACTUAL="$(sha256sum "$BIN" | awk '{print $1}')"
if [[ "$ACTUAL" != "$EXPECTED" ]]; then
    printf 'Candidate changed; refusing mismatched binary. Expected=%s Actual=%s\n' "$EXPECTED" "$ACTUAL"
    exit 2
fi
if pgrep -x -u "$(id -u)" shadps4 >/dev/null 2>&1; then
    echo "Another shadps4 is running. Nothing will be stopped or launched."
    exit 2
fi

export DISPLAY="${DISPLAY:-:0}"
if [[ -z "${XAUTHORITY:-}" && -f "$HOME/.Xauthority" ]]; then
    export XAUTHORITY="$HOME/.Xauthority"
fi
if [[ -z "${XDG_RUNTIME_DIR:-}" && -d "/run/user/$(id -u)" ]]; then
    export XDG_RUNTIME_DIR="/run/user/$(id -u)"
fi
if [[ -z "${DBUS_SESSION_BUS_ADDRESS:-}" && -S "${XDG_RUNTIME_DIR:-/nonexistent}/bus" ]]; then
    export DBUS_SESSION_BUS_ADDRESS="unix:path=$XDG_RUNTIME_DIR/bus"
fi
if command -v xdpyinfo >/dev/null 2>&1 && ! xdpyinfo -display "$DISPLAY" >/dev/null 2>&1; then
    echo "X11 display unavailable. Not changing existing X11 or Sunshine setup."
    exit 2
fi

GAME="${NATIVE_CPU_TEST_GAME:-CUSA34384}"
LIMIT="${NATIVE_CPU_GDB_SECONDS:-90}"
if ! [[ "$LIMIT" =~ ^[0-9]+$ ]] || (( LIMIT < 15 || LIMIT > 300 )); then
    echo "Diagnostic time limit must be from 15 to 300 seconds"
    exit 2
fi
if ! nm -an "$BIN" 2>/dev/null | grep -q '[[:space:]]unreachable_impl$'; then
    echo "unreachable_impl symbol not found. Proceeding with signal-stop fallback."
    BP="break abort"
else
    BP="break unreachable_impl"
fi
echo "TEST_GAME=$GAME"
echo "CPU_MODE=builtin"
echo "CRASH_TARGET=$BP"
echo "SOURCE_LOG=$OUT/gdb.log"
echo "DEBUGGER_TIME_LIMIT=$LIMIT seconds"
echo "No controls or application windows will be closed except this test candidate."

# No large process core dump; capture a live native backtrace instead.
ulimit -c 0 || true
cd "$OUT"
set +e
timeout --foreground --signal=SIGINT --kill-after=6s "$LIMIT" \
    gdb -nx -q -batch \
      -ex 'set pagination off' \
      -ex 'set confirm off' \
      -ex 'set debuginfod enabled off' \
      -ex 'set disable-randomization off' \
      -ex 'set print thread-events off' \
      -ex 'handle SIGSEGV nostop noprint pass' \
      -ex 'handle SIGBUS nostop noprint pass' \
      -ex 'handle SIGILL nostop noprint pass' \
      -ex 'handle SIGUSR1 nostop noprint pass' \
      -ex 'handle SIGUSR2 nostop noprint pass' \
      -ex 'handle SIGPIPE nostop noprint pass' \
      -ex "$BP" \
      -ex 'run' \
      -ex 'bt 35' \
      -ex 'thread apply all bt 7' \
      -ex 'info registers' \
      -ex 'info proc mappings' \
      -ex 'frame 3' \
      -ex 'info registers' \
      -ex 'x/20i $pc-24' \
      -ex 'quit' \
      --args "$BIN" --cpu-id-mode builtin --game "$GAME" --fullscreen true \
  >"$OUT/gdb.log" 2>&1
GDB_EXIT=$?
set -e
if grep -Eq 'Breakpoint [0-9]+, (unreachable_impl|abort)' "$OUT/gdb.log"; then
    STATUS="FATAL_BREAKPOINT_CAPTURED"
elif grep -Eq 'Program received signal|Program terminated with signal' "$OUT/gdb.log"; then
    STATUS="SIGNAL_CAPTURED"
elif [[ "$GDB_EXIT" == "124" || "$GDB_EXIT" == "137" ]]; then
    STATUS="TIME_LIMIT_NO_FATAL"
else
    STATUS="DEBUGGER_FINISHED"
fi
echo "RESULT=$STATUS (debugger exit=$GDB_EXIT)"
echo "Fault context excerpt:"
grep -E 'Breakpoint |Program received signal|Program terminated|unreachable_impl|SignalHandler|Thread [0-9]|^#[0-9]+ |Unhandled access|warning:|Error' \
    "$OUT/gdb.log" | tail -75 || true
