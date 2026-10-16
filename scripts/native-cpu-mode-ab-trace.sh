#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
# SPDX-License-Identifier: GPL-2.0-or-later
# One isolated CPU mode A/B: same executable, no source or system changes.
set -Eeo pipefail
STAGE="$HOME/projects/shadps4/GodOfWarRagnarok/tests/native-cpu-identity-merged-20261016"
BIN="$STAGE/build/shadps4"
LOGROOT="$HOME/projects/shadps4/logs"
ID="$(date +%Y%m%d-%H%M%S)-$$"
OUT="$STAGE/diagnostics/native-vs-builtin-$ID"
ARCHIVE="$LOGROOT/gow-cpu-mode-ab-$ID.tar.gz"
EXPECTED="d428d1f71383b7cf9d4334d4e55b3506e11f5f99a69a818df6577d41e3976602"
mkdir -p "$OUT" "$LOGROOT"
STATUS="PRECHECK_FAILED"
NATIVE_EXIT="NOT_RUN"
GDB_EXIT="NOT_RUN"
on_exit() {
  local code=$?
  trap - EXIT
  printf 'STATUS=%s\nNATIVE_EXIT=%s\nGDB_EXIT=%s\n' "$STATUS" "$NATIVE_EXIT" "$GDB_EXIT" > "$OUT/summary.txt"
  printf 'SOURCE=UNCHANGED\nINSTALLED_EMULATOR=UNCHANGED\nGPU_DRIVERS=UNCHANGED\nSSH=UNCHANGED\n' >> "$OUT/summary.txt"
  tar -C "$OUT" -czf "$ARCHIVE" . || true
  printf '\nSTATUS=%s\nNATIVE_EXIT=%s\nGDB_EXIT=%s\nARCHIVE=%s\n' \
    "$STATUS" "$NATIVE_EXIT" "$GDB_EXIT" "$ARCHIVE"
  return "$code"
}
trap on_exit EXIT
if [[ ! -x "$BIN" ]]; then echo "Candidate missing: $BIN"; exit 2; fi
ACTUAL="$(sha256sum "$BIN" | awk '{print $1}')"
if [[ "$ACTUAL" != "$EXPECTED" ]]; then
  printf 'Candidate hash mismatch expected=%s actual=%s\n' "$EXPECTED" "$ACTUAL"
  echo "Refusing comparison of two different executables."
  exit 2
fi
if ! command -v gdb >/dev/null 2>&1; then echo "gdb not installed"; exit 2; fi
if pgrep -x -u "$(id -u)" shadps4 >/dev/null 2>&1; then
  echo "Another shadps4 already running; no processes stopped."
  exit 2
fi
if [[ -z "$DISPLAY" ]]; then export DISPLAY=":0"; fi
if [[ -z "$XAUTHORITY" && -f "$HOME/.Xauthority" ]]; then
  export XAUTHORITY="$HOME/.Xauthority"
fi
if [[ -z "$XDG_RUNTIME_DIR" && -d "/run/user/$(id -u)" ]]; then
  export XDG_RUNTIME_DIR="/run/user/$(id -u)"
fi
if [[ -z "$DBUS_SESSION_BUS_ADDRESS" && -n "$XDG_RUNTIME_DIR" &&
      -S "$XDG_RUNTIME_DIR/bus" ]]; then
  export DBUS_SESSION_BUS_ADDRESS="unix:path=$XDG_RUNTIME_DIR/bus"
fi
if command -v xdpyinfo >/dev/null 2>&1 && ! xdpyinfo -display "$DISPLAY" >/dev/null 2>&1; then
  echo "Cannot access existing X11 display. No changes attempted."
  exit 2
fi

echo "CANDIDATE_SHA=$ACTUAL" | tee "$OUT/candidate.txt"
echo "A/B_MODE=native,builtin GAME=CUSA34384"
echo "NO_PERMANENT_CPU_MODE_CHANGE=YES"
echo "NO_INPUT_AUTOMATION=YES"
cd "$OUT"
ulimit -c 0 || true

# Native is a diagnostic comparator ONLY. Do not promote it as the default.
# 124 means it survived the bounded native run, not successful gameplay.
STATUS="NATIVE_TRIAL"
set +e
timeout --signal=TERM --kill-after=6s 45s \
  env -u SHADPS4_CPU_ID_RESTART "$BIN" --cpu-id-mode native \
  --game CUSA34384 --fullscreen true --show-fps > "$OUT/native-console.log" 2>&1
NATIVE_EXIT=$?
set -e
echo "NATIVE_EXIT=$NATIVE_EXIT (124=survived time limit, not FPS validated)"
if pgrep -x -u "$(id -u)" shadps4 >/dev/null 2>&1; then
  STATUS="NATIVE_PROCESS_REMAINED"
  echo "Native test did not shut down. Refusing second launch; no force kill."
  exit 0
fi

# Inspect both the HLE boundary and the CPU context at the subsequent fatal
# exception. Recording raw register values avoids speculative filename fixes.
cat > "$OUT/capture.gdb" <<'GDB'
set pagination off
set confirm off
set debuginfod enabled off
set disable-randomization off
set print thread-events off
handle SIGSEGV nostop noprint pass
handle SIGBUS nostop noprint pass
handle SIGILL nostop noprint pass
handle SIGUSR1 nostop noprint pass
handle SIGUSR2 nostop noprint pass
handle SIGPIPE nostop noprint pass
break Libraries::Kernel::sceKernelLoadStartModule
commands
silent
printf "HLE_ENTRY filename=%p args=%llu argp=%p flags=%llu pRes=%p\n", $rdi, $rsi, $rdx, $rcx, $r9
continue
end
break unreachable_impl
run
echo \n==== FATAL STACK ====\n
bt 20
echo \n==== ORIGINAL FAULT FRAME (4) ====\n
frame 4
info registers
x/20i $rip-32
echo \n==== HOST HLE CALLER FRAME (7) ====\n
frame 7
info registers
quit
GDB

STATUS="BUILTIN_GDB_TRIAL"
set +e
timeout --signal=SIGINT --kill-after=6s 85s \
  gdb -nx -q -batch -x "$OUT/capture.gdb" --args "$BIN" \
  --cpu-id-mode builtin --game CUSA34384 --fullscreen true \
  > "$OUT/builtin-gdb.log" 2>&1
GDB_EXIT=$?
set -e

if grep -Eq 'hit Breakpoint|Breakpoint [0-9]+,.*unreachable_impl' "$OUT/builtin-gdb.log"; then
  STATUS="BUILTIN_FATAL_CAPTURED"
elif [[ "$GDB_EXIT" == 124 || "$GDB_EXIT" == 137 ]]; then
  STATUS="BUILTIN_TIMEOUT"
else
  STATUS="BUILTIN_FINISHED"
fi
echo "RESULT=$STATUS"
grep -E 'HLE_ENTRY|Unhandled access|hit Breakpoint|FATAL STACK|ORIGINAL FAULT|HOST HLE|^#(0|1|2|3|4|5|6|7) |rip |rdi |rsi ' \
  "$OUT/builtin-gdb.log" | tail -60 || true
