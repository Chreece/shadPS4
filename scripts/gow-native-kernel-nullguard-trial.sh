#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
# SPDX-License-Identifier: GPL-2.0-or-later
# Isolated native CPU + general kernel HLE regression and GoW gameplay trial.
set -Eeo pipefail
STAGE="$HOME/projects/shadps4/GodOfWarRagnarok/tests/native-cpu-identity-merged-20261016"
SOURCE="$STAGE/source"
BUILD="$STAGE/build"
BIN="$BUILD/shadps4"
LOGROOT="$HOME/projects/shadps4/logs"
ID="$(date +%Y%m%d-%H%M%S)-$$"
OUT="$STAGE/tests/stack-align-$ID"
ARCHIVE="$LOGROOT/gow-native-stack-align-$ID.tar.gz"
PIN="1fe3e8b626f34f092bb2be37de8d14fe26d45bc2"
REPO="https://github.com/Chreece/shadPS4.git"
BRANCH="fix/native-cpu-identity-merged-20261016"
mkdir -p "$OUT/screenshots" "$LOGROOT"
exec > >(tee "$OUT/progress.log") 2>&1
STATUS="PRECHECK"
GAME_EXIT="NOT_STARTED"
SHOTS=0
DURATION=150
if [[ -n "$GOW_NATIVE_RUN_SECONDS" ]]; then DURATION="$GOW_NATIVE_RUN_SECONDS"; fi
finish() {
  local code=$?
  trap - EXIT
  printf 'STATUS=%s\nGAME_EXIT=%s\nSCREENSHOTS=%s\nSOURCE=%s\n' \
      "$STATUS" "$GAME_EXIT" "$SHOTS" "$PIN" >"$OUT/summary.txt"
  printf 'DRIVERS=UNCHANGED\nINSTALLED_EMULATOR=UNCHANGED\nSSH=UNCHANGED\nOTHER_PROGRAMS=UNCHANGED\n' >> "$OUT/summary.txt"
  tar -C "$OUT" -czf "$ARCHIVE" . || true
  printf '\nRESULT=%s\nARCHIVE=%s\nCANDIDATE=%s\n' "$STATUS" "$ARCHIVE" "$BIN"
  return "$code"
}
trap finish EXIT
if [[ ! "$DURATION" =~ ^[0-9]+$ ]] || (( DURATION < 30 || DURATION > 900 )); then
  echo "GOW_NATIVE_RUN_SECONDS must be 30..900"; exit 2
fi
if ! git -C "$SOURCE" rev-parse --git-dir >/dev/null 2>&1 ||
   [[ ! -f "$BUILD/build.ninja" ]]; then
  echo "Previous isolated native source/build not found under $STAGE"; exit 2
fi
if pgrep -x -u "$(id -u)" shadps4 >/dev/null 2>&1; then
  echo "A shadps4 instance is active; will not interrupt it."; exit 2
fi
if [[ -n "$(git -C "$SOURCE" status --porcelain)" ]]; then
  echo "Isolated source worktree is modified; refusing overwrite."; exit 3
fi
echo "SOURCE_BEFORE=$(git -C "$SOURCE" rev-parse HEAD)"
git -C "$SOURCE" -c http.version=HTTP/1.1 fetch --no-tags "$REPO" "refs/heads/$BRANCH"
if ! git -C "$SOURCE" merge-base --is-ancestor "$PIN" FETCH_HEAD; then
  echo "Branch does not contain the reviewed fix; refusing checkout."; exit 3
fi
git -C "$SOURCE" checkout --detach "$PIN"
echo "SOURCE_NOW=$(git -C "$SOURCE" rev-parse HEAD)"
mkdir -p "$OUT/tests"
for spec in guest_cpuid rdpid_trap_table; do
  if [[ "$spec" == guest_cpuid ]]; then
    file="$SOURCE/tests/cpu_id/test_guest_cpuid.cpp"
  else
    file="$SOURCE/tests/cpu_id/test_rdpid_trap_table.cpp"
  fi
  g++ -std=c++23 -O2 -Wall -Wextra -pthread -I"$SOURCE/src" "$file" -o "$OUT/tests/$spec"
  "$OUT/tests/$spec"
done
g++ -std=c++23 -O2 -Wall -Wextra -I"$SOURCE/src" \
  "$SOURCE/tests/kernel/test_module_filename.cpp" -o "$OUT/tests/kernel_module_filename"
"$OUT/tests/kernel_module_filename"
STATUS="BUILDING"
cmake -S "$SOURCE" -B "$BUILD" -G Ninja -DCMAKE_BUILD_TYPE=Release \
  -DENABLE_CPU_ID_TRANSLATION=OFF -DENABLE_TESTS=OFF
JOBS="$(nproc)"
if (( JOBS > 8 )); then JOBS=8; fi
cmake --build "$BUILD" --target shadps4 --parallel "$JOBS"
test -x "$BIN"
sha256sum "$BIN" | tee "$OUT/candidate-sha256.txt"
# Verify the *built* HLE entry realigns the stack, not merely source comments.
# On Linux x86-64, guest calls may have an 8-byte-displaced native stack.
if command -v nm >/dev/null 2>&1 && command -v objdump >/dev/null 2>&1; then
  SYMBOL="$(nm -C "$BIN" | awk 'index($0, " T Libraries::Kernel::sceKernelLoadStartModule(") || index($0, " t Libraries::Kernel::sceKernelLoadStartModule(") {if (!address) address=$1} END {print address}')"
  if [[ -z "$SYMBOL" ]]; then
    STATUS="HLE_STACK_PROLOGUE_NOT_FOUND"
    echo "Cannot locate host HLE entry in candidate binary; refusing gameplay trial."
    exit 4
  fi
  STOP="$(printf '%x' "$((16#$SYMBOL + 160))")"
  objdump -dC --start-address="0x$SYMBOL" --stop-address="0x$STOP" "$BIN" > "$OUT/hle-stack-prologue.txt"
  if ! grep -Eq '[[:space:]]and[[:space:]].*%rsp' "$OUT/hle-stack-prologue.txt"; then
    STATUS="HLE_STACK_PROLOGUE_UNALIGNED"
    echo "Compiled HLE function did not realign RSP: refusing gameplay trial."
    exit 4
  fi
  echo "COMPILED_HLE_STACK_REALIGN=PASS"
else
  STATUS="HLE_DISASSEMBLER_MISSING"
  echo "nm and objdump required to verify HLE stack alignment."
  exit 4
fi
STATUS="BUILD_OK"
echo "CPU_TESTS=PASS KERNEL_HLE_TESTS=PASS DYNAMORIO=DISABLED"
export DISPLAY
if [[ -z "$DISPLAY" ]]; then DISPLAY=":0"; fi
if [[ -z "$XAUTHORITY" && -f "$HOME/.Xauthority" ]]; then export XAUTHORITY="$HOME/.Xauthority"; fi
if [[ -z "$XDG_RUNTIME_DIR" && -d "/run/user/$(id -u)" ]]; then
  export XDG_RUNTIME_DIR="/run/user/$(id -u)"
fi
if [[ -z "$DBUS_SESSION_BUS_ADDRESS" && -S "$XDG_RUNTIME_DIR/bus" ]]; then
  export DBUS_SESSION_BUS_ADDRESS="unix:path=$XDG_RUNTIME_DIR/bus"
fi
if command -v xdpyinfo >/dev/null 2>&1 &&
   ! xdpyinfo -display "$DISPLAY" >/dev/null 2>&1; then
  STATUS="DISPLAY_UNAVAILABLE"
  echo "Existing X11 display inaccessible; leaving session untouched."; exit 0
fi
if pgrep -x -u "$(id -u)" shadps4 >/dev/null 2>&1; then
  STATUS="OTHER_GAME_ACTIVE"
  echo "Another game started; not launching over it."; exit 0
fi
capture() {
  local sec="$1" dst="$OUT/screenshots/at-$1.png"
  if command -v import >/dev/null 2>&1 &&
     timeout 8s nice -n 15 import -display "$DISPLAY" -window root "$dst" >/dev/null 2>&1 &&
     [[ -s "$dst" ]]; then
    SHOTS=$((SHOTS+1)); echo "SCREENSHOT=$dst"; return
  fi
  if command -v ffmpeg >/dev/null 2>&1 && command -v xdpyinfo >/dev/null 2>&1; then
    local dim
    dim="$(xdpyinfo -display "$DISPLAY" | awk '/dimensions:/ {print $2; exit}')"
    if [[ "$dim" =~ ^[0-9]+x[0-9]+$ ]] &&
       timeout 8s nice -n 15 ffmpeg -nostdin -hide_banner -loglevel error -y \
         -f x11grab -framerate 1 -video_size "$dim" -i "$DISPLAY.0" \
         -frames:v 1 -update 1 "$dst" >/dev/null 2>&1 &&
       [[ -s "$dst" ]]; then
      SHOTS=$((SHOTS+1)); echo "SCREENSHOT=$dst"; return
    fi
  fi
  rm -f "$dst"
  echo "SCREENSHOT_UNAVAILABLE=$sec"
}
ulimit -c 0 || true
cd "$OUT"
STATUS="PLAYING"
echo "GAME=CUSA34384 CPU_BACKEND=builtin PLAY_WINDOW=$DURATION seconds"
echo "Use your controller normally; no automatic input."
printf 'elapsed_s\tcpu_pct\tmem_pct\trss_kb\tstate\n' > "$OUT/samples.tsv"
env -u SHADPS4_CPU_ID_RESTART "$BIN" --cpu-id-mode builtin \
  --game CUSA34384 --fullscreen true --show-fps > "$OUT/game-console.log" 2>&1 &
PID=$!
echo "GAME_PID=$PID"
EARLY=0
for ((sec=0;sec<DURATION;sec+=5)); do
  STATE="$(ps -p "$PID" -o stat= 2>/dev/null | tr -d '[:space:]' || true)"
  if [[ -z "$STATE" || "$STATE" == Z* ]]; then EARLY=1; echo "EXIT_AT=$sec seconds"; break; fi
  printf '%s\t%s\n' "$sec" "$(LC_ALL=C ps -p "$PID" -o %cpu=,%mem=,rss=,stat= 2>/dev/null || true)" >> "$OUT/samples.tsv"
  if (( sec==5 || sec==15 || sec==45 || sec==90 || sec==135 )); then capture "$sec"; fi
  if (( sec%30==0 )); then echo "PLAY_PROGRESS=$sec seconds"; fi
  sleep 5
done
if (( EARLY==0 )); then
  echo "Trial complete. Requesting graceful exit of candidate PID=$PID only."
  kill -TERM "$PID" 2>/dev/null || true
  for ((n=0;n<12;n++)); do
    STATE="$(ps -p "$PID" -o stat= 2>/dev/null | tr -d '[:space:]' || true)"
    if [[ -z "$STATE" || "$STATE" == Z* ]]; then break; fi
    sleep 1
  done
  STATE="$(ps -p "$PID" -o stat= 2>/dev/null | tr -d '[:space:]' || true)"
  if [[ -n "$STATE" && "$STATE" != Z* ]]; then
    GAME_EXIT="RUNNING"
    STATUS="CANDIDATE_STILL_RUNNING"
    echo "Candidate did not exit gracefully; leaving it alone."
    exit 0
  fi
fi
set +e
wait "$PID"
GAME_EXIT=$?
set -e
if (( EARLY==0 )); then STATUS="PLAY_WINDOW_COMPLETED"
elif (( GAME_EXIT==0 )); then STATUS="GAME_EXITED_NORMALLY"
else STATUS="GAME_CRASHED"; fi
echo "GAME_RESULT=$STATUS EXIT=$GAME_EXIT"
if [[ "$STATUS" == GAME_CRASHED ]] && command -v gdb >/dev/null 2>&1 &&
   ! pgrep -x -u "$(id -u)" shadps4 >/dev/null 2>&1; then
  echo "CRASH_CAPTURE=START"
  set +e
  timeout --signal=SIGINT --kill-after=5s 90s gdb -nx -q -batch \
    -ex 'set pagination off' -ex 'set confirm off' \
    -ex 'set debuginfod enabled off' -ex 'set disable-randomization off' \
    -ex 'handle SIGSEGV nostop noprint pass' \
    -ex 'handle SIGILL nostop noprint pass' \
    -ex 'handle SIGUSR1 nostop noprint pass' \
    -ex 'handle SIGUSR2 nostop noprint pass' \
    -ex 'break unreachable_impl' -ex run -ex 'bt 35' \
    -ex 'thread apply all bt 8' -ex 'info registers' -ex quit \
    --args "$BIN" --cpu-id-mode builtin --game CUSA34384 --fullscreen true \
    > "$OUT/gdb-on-crash.log" 2>&1
  echo "GDB_EXIT=$?"
  set -e
fi
