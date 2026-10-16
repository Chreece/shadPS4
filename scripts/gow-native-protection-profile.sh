#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
# SPDX-License-Identifier: GPL-2.0-or-later
#
# Profile the checksum-verified general GPU page-protection sparse-range
# coalescing candidate. Avoid per-call renderer probes; capture real moving
# gameplay, kernel mprotect stacks, low-overhead CPU/GPU and Vulkan presents.
set -Eeuo pipefail
BUILD="$HOME/projects/shadps4/GodOfWarRagnarok/tests/native-rendered-gfx-20261016-152535-3014679/build"
BIN="$BUILD/shadps4.native-sparse-protection-v1"
MANIFEST="$BUILD/shadps4.native-sparse-protection-v1.sha256"
EXPECTED="UNVERIFIED"
BASE="$BUILD/shadps4"
BASE_SHA="264fccf27f48f1df06f1e9d54d5138eb476b71b72a29fb4cb7c6f058439aa239"
PREVIOUS="$BUILD/shadps4.native-dma-dirty-gate-v1"
PREVIOUS_SHA="d34c35848997ecac41f5a7c03da55e00dc1503ed3aa1794a6bd28f4e22a2e486"
LOGROOT="$HOME/projects/shadps4/logs"
STAMP="$(date +%Y%m%d-%H%M%S)-$$"
OUT="$LOGROOT/gow-native-protect-perf-$STAMP"
ARCHIVE="$LOGROOT/gow-native-protect-perf-$STAMP.tar.gz"
GAME_PID=""
PHASE="PRECHECK"
PROFILE_STATUS="NOT_STARTED"
GPU_BUSY=""
DISPLAY="$(printenv DISPLAY || true)"
XAUTHORITY="$(printenv XAUTHORITY || true)"
mkdir -p "$OUT/screenshots"

finish() {
  local code=$?
  trap - EXIT
  {
    printf 'PHASE=%s\nSCRIPT_EXIT=%s\nPROFILE=%s\n' "$PHASE" "$code" "$PROFILE_STATUS"
    printf 'EXECUTABLE=%s\nEXPECTED_SHA256=%s\n' "$BIN" "$EXPECTED"
    printf 'EMULATOR_PID=%s\nCPU_MODE=builtin\nDYNAMORIO=DISABLED\n' "$GAME_PID"
    printf 'GAME_AUTOKILL=DISABLED\nSUNSHINE=UNCHANGED\nX11=UNCHANGED\n'
    printf 'GPU_DRIVER=UNCHANGED\nINSTALLED_EMULATOR=UNCHANGED\nSSH=UNCHANGED\n'
  } >"$OUT/summary.txt"
  if [[ -f "$OUT/game-console.log" ]]; then
    grep -Ei 'CPU deferred static|Static CPU patching|GPU|buffer.*clamp|unreachable|error' \
      "$OUT/game-console.log" | tail -200 >"$OUT/console-important-tail.txt" || true
    for pattern in 'Clamping buffer' 'Clamped' 'SetQueueReg' \
       'Failed to compute offset for SRT walker' 'Unhandled access violation'; do
      printf '%s\t%s\n' "$pattern" \
        "$(grep -Fc "$pattern" "$OUT/game-console.log" || true)" \
        >> "$OUT/console-counts.tsv"
    done
  fi
  # perf.data itself is retained locally; text reports are compact enough
  # for sharing without uploading raw executable memory samples.
  tar -C "$OUT" -czf "$ARCHIVE" --exclude=perf.data . || true
  printf '\nRESULT=%s\nPROFILE=%s\nARCHIVE=%s\n' "$PHASE" "$PROFILE_STATUS" "$ARCHIVE"
  return "$code"
}
trap finish EXIT
exec > >(tee "$OUT/progress.log") 2>&1

if [[ ! -f "$MANIFEST" || -L "$MANIFEST" ]] ||
   [[ ! -f "$BASE" || -L "$BASE" || ! -f "$PREVIOUS" || -L "$PREVIOUS" ]]; then
  PHASE="NATIVE_BUILDER_NOT_FINISHED"
  echo "Run the reviewed native HLE clock build first."
  exit 2
fi
if [[ "$(sha256sum "$BASE" | awk '{print $1}')" != "$BASE_SHA" ||
      "$(sha256sum "$PREVIOUS" | awk '{print $1}')" != "$PREVIOUS_SHA" ]]; then
  PHASE="RENDERED_GRAPHICS_OR_NATIVE_CPU_BASELINE_CHANGED"
  echo "An exact baseline checksum failed. No game launched."
  exit 3
fi
EXPECTED="$(awk 'NR==1 {print $1}' "$MANIFEST")"
if [[ ! "$EXPECTED" =~ ^[a-f0-9]{64}$ || ! -x "$BIN" || -L "$BIN" ]]; then
  PHASE="CANDIDATE_MANIFEST_MISSING_OR_INVALID"
  exit 3
fi
ACTUAL="$(sha256sum "$BIN" | awk '{print $1}')"
if [[ "$ACTUAL" != "$EXPECTED" ]]; then
  PHASE="CLOCK_BINARY_CHECKSUM_MISMATCH"
  echo "The compiled CPU-native clock candidate differs from its SHA256 manifest."
  exit 3
fi
echo "NATIVE_SPARSE_PROTECTION_CANDIDATE=VERIFIED sha256=$EXPECTED"

if ps -u "$(id -un)" -o comm= | grep -Eq '^shadps4'; then
  PHASE="ANOTHER_EMULATOR_ACTIVE"
  echo "Another shadPS4 process is active. No programs stopped."
  exit 0
fi
if [[ -f "$HOME/Applications/shadps4/shadps4" ]] &&
   [[ "$BIN" -ef "$HOME/Applications/shadps4/shadps4" ]]; then
  PHASE="ALIASES_INSTALLED_EMULATOR"
  echo "Refusing to operate on an installed emulator alias."
  exit 3
fi
if ! command -v perf >/dev/null 2>&1 ||
   ! command -v xdpyinfo >/dev/null 2>&1 ||
   ! command -v sudo >/dev/null 2>&1; then
  PHASE="MISSING_PROFILING_PREREQUISITES"
  echo "Require perf, xdpyinfo and sudo (no packages installed by this script)."
  exit 2
fi
if [[ ! -r /dev/tty ]]; then
  PHASE="INTERACTIVE_SSH_REQUIRED"
  echo "Run from an interactive SSH terminal; gameplay profiling starts on Enter."
  exit 2
fi

# Discover only an already-existing Moonlight/Sunshine X11 session.
sunpid="$(pgrep -u "$(id -u)" -x sunshine | head -1 || true)"
sunenv=""
if [[ -n "$sunpid" && -r "/proc/$sunpid/environ" ]]; then
  sunenv="$(tr '\0' '\n' < "/proc/$sunpid/environ" 2>/dev/null || true)"
fi
sun_display="$(printf '%s\n' "$sunenv" | sed -n 's/^DISPLAY=//p' | head -1)"
sun_auth="$(printf '%s\n' "$sunenv" | sed -n 's/^XAUTHORITY=//p' | head -1)"
xpid="$(pgrep -x Xorg | head -1 || true)"
x_auth=""
if [[ -n "$xpid" && -r "/proc/$xpid/cmdline" ]]; then
  x_auth="$(tr '\0' '\n' < "/proc/$xpid/cmdline" 2>/dev/null |
    awk 'previous=="-auth" {print;exit} {previous=$0}' || true)"
fi
selected_display=""
selected_auth=""
for d in "$sun_display" "$DISPLAY" :0 :1 :2; do
  [[ -n "$d" ]] || continue
  for auth in "$sun_auth" "$x_auth" "$XAUTHORITY" \
              "$HOME/.Xauthority" "/run/user/$(id -u)/Xauthority" ""; do
    if [[ -n "$auth" ]]; then
      [[ -f "$auth" && -r "$auth" ]] || continue
      if env DISPLAY="$d" XAUTHORITY="$auth" timeout 3s xdpyinfo -display "$d" \
        >/dev/null 2>&1; then
        selected_display="$d"; selected_auth="$auth"; break 2
      fi
    elif env -u XAUTHORITY DISPLAY="$d" timeout 3s xdpyinfo -display "$d" \
      >/dev/null 2>&1; then
      selected_display="$d"; selected_auth=""; break 2
    fi
  done
done
if [[ -z "$selected_display" ]]; then
  PHASE="NO_ACCESSIBLE_EXISTING_X11"
  echo "Moonlight's X11 display is not accessible in SSH."
  echo "No game launched and no display service changed."
  exit 0
fi
export DISPLAY="$selected_display"
if [[ -n "$selected_auth" ]]; then
  export XAUTHORITY="$selected_auth"
else
  unset XAUTHORITY
fi
if [[ -d "/run/user/$(id -u)" ]]; then
  export XDG_RUNTIME_DIR="/run/user/$(id -u)"
  if [[ -S "$XDG_RUNTIME_DIR/bus" ]]; then
    export DBUS_SESSION_BUS_ADDRESS="unix:path=$XDG_RUNTIME_DIR/bus"
  fi
fi
echo "EXISTING_MOONLIGHT_X11=PASS display=$DISPLAY"

for file in /sys/class/drm/card*/device/gpu_busy_percent; do
  [[ -r "$file" ]] || continue
  vendor="$(dirname "$file")/vendor"
  if [[ -r "$vendor" && "$(cat "$vendor" 2>/dev/null || true)" == "0x1002" ]]; then
    GPU_BUSY="$file"
    break
  fi
done

cd "$OUT"
ulimit -c 0 || true
PHASE="LAUNCHING"
echo "LAUNCHING_GOW_NOW using native builtin CPU (without sudo)"
echo "No debugger, emulator replacement, DynamoRIO or test timer."
echo "CPU_HLE_CLOCK=real CLOCK_MONOTONIC via Linux syscall"
echo "HRTIMER_WAIT=condition_variable precise deadline (no busy sched_yield)"
echo "DMA_DIRTY_GATE=atomic notification before resident-range scan (page protection intact)"
echo "SPARSE_PAGE_PROTECTION=limit no-op gaps within mprotect range (all rights unchanged)"
echo "RENDERER_SCOPE_PROBES=OFF; FPS is measured without heavy profiling hooks"
echo "The last rendered native CPU executable remains unchanged."
env -u SHADPS4_CPU_ID_RESTART -u SHADPS4_CPU_TIMING_CSV \
  SHADPS4_FRAME_METRICS_PREFIX="$OUT/frame" \
  "$BIN" --cpu-id-mode builtin --game CUSA34384 --fullscreen true --show-fps \
  > "$OUT/game-console.log" 2>&1 &
GAME_PID=$!
sleep 3
if [[ ! -e "/proc/$GAME_PID/exe" ]]; then
  PHASE="GAME_EXITED_ON_LAUNCH"
  echo "Emulator exited on startup; game console retained for diagnosis."
  set +e
  wait "$GAME_PID"
  printf 'GAME_STARTUP_EXIT=%s\n' "$?" > "$OUT/game-exit.txt"
  set -e
  exit 4
fi
actual_exe="$(readlink -f "/proc/$GAME_PID/exe" || true)"
if [[ "$actual_exe" != "$BIN" ]]; then
  PHASE="WRONG_PROCESS_ID"
  printf 'Expected exe: %s\nActual exe: %s\n' "$BIN" "$actual_exe"
  echo "No profiling performed. Emulator is not killed."
  exit 4
fi
echo "GAME_LAUNCHED=PASS PID=$GAME_PID EXE=$actual_exe"
echo "Now play GoW in Moonlight until you reach active, MOVING gameplay."
echo "Do not profile a menu, loading screen, interaction prompt, or static QTE."
echo "Only once you're actually playing, return to SSH and press ENTER."
if ! IFS= read -r _unused < /dev/tty; then
  PHASE="TTY_NOT_AVAILABLE"
  echo "Cannot receive interactive confirmation. Emulator left running."
  exit 4
fi
if [[ ! -e "/proc/$GAME_PID/exe" ||
      "$(readlink -f "/proc/$GAME_PID/exe" || true)" != "$BIN" ]]; then
  PHASE="GAME_EXITED_BEFORE_PROFILE"
  echo "Emulator has already exited; no false perf results will be reported."
  exit 4
fi
PHASE="PROFILING"
echo "PROFILING_REAL_GOW_PID=$GAME_PID"
echo "sudo authentication may be requested; this never runs the GAME as root."
if ! sudo -v < /dev/tty; then
  PROFILE_STATUS="SUDO_AUTH_FAILED"
  PHASE="PROFILER_PERMISSION_BLOCKED"
  echo "Cannot obtain profiling permission; emulator remains running."
  exit 4
fi

# Real screenshots surrounding the profile, for visual-regression checks.
capture() {
  local name="$1" output="$OUT/screenshots/$1.png"
  if command -v import >/dev/null 2>&1 &&
     timeout 7s nice -n 15 import -display "$DISPLAY" -window root "$output"        >/dev/null 2>&1 && [[ -s "$output" ]]; then
    echo "SCREENSHOT_CAPTURED=$name"
  else
    rm -f "$output"
    echo "SCREENSHOT_UNAVAILABLE=$name (profiling continues)"
  fi
  return 0
}
capture before-perf
ps -L -p "$GAME_PID" -o tid,pcpu,psr,stat,comm   > "$OUT/threads-before.txt" 2>&1 || true
# Read-only CPU and AMD GPU activity samples, one normalized row per second.
(
  printf 'unix_second\tprocess_cpu_pct_rss_kb_threads_state\tamd_gpu_busy_pct\n'
  for ((i=0;i<45;i++)); do
    [[ -d "/proc/$GAME_PID" ]] || break
    now="$(date +%s)"
    process_stats="$(LC_ALL=C ps -p "$GAME_PID" -o %cpu=,rss=,nlwp=,stat=       2>/dev/null | tr -d '\n' || true)"
    busy="NA"
    if [[ -n "$GPU_BUSY" ]]; then busy="$(cat "$GPU_BUSY" 2>/dev/null || echo NA)"; fi
    printf '%s\t%s\t%s\n' "$now" "$process_stats" "$busy"
    sleep 1
  done
) > "$OUT/process-samples.tsv" 2>&1 &
sampler_pid=$!
sudo -n perf record -e cpu-clock -F 29 -g --call-graph dwarf,2048 \
  -p "$GAME_PID" -o "$OUT/perf.data" -- sleep 45 \
  > "$OUT/perf-record.stdout" 2>"$OUT/perf-record.stderr" && perf_rc=0 || perf_rc=$?
# The sampler is our own child, NOT an emulator process.
wait "$sampler_pid" || true
ps -L -p "$GAME_PID" -o tid,pcpu,psr,stat,comm   > "$OUT/threads-after.txt" 2>&1 || true
capture after-perf
printf 'PERF_EXIT_CODE=%s\n' "$perf_rc" | tee "$OUT/perf-status.txt"
if (( perf_rc==0 )); then
  sudo -n perf report --stdio --no-children --percent-limit 0.2 \
    -i "$OUT/perf.data" > "$OUT/report.txt" 2> "$OUT/perf-report.stderr" || true
  sudo -n perf script -i "$OUT/perf.data" \
    > "$OUT/stacks.txt" 2> "$OUT/perf-script.stderr" || true
  sample_count="$(grep -c 'cpu-clock:' "$OUT/stacks.txt" || true)"
  printf 'STACK_SAMPLE_LINES=%s\n' "$sample_count" >> "$OUT/perf-status.txt"
  if [[ -s "$OUT/stacks.txt" ]]; then
    PROFILE_STATUS="SAMPLES_CAPTURED"
    echo "CPU_PROFILING=PASS"
  else
    PROFILE_STATUS="EMPTY_STACKS"
    echo "CPU_PROFILING=NO_SAMPLES; see perf stderr"
  fi
else
  PROFILE_STATUS="PERF_RECORD_FAILED"
  echo "perf could not capture samples; its stderr was saved."
fi
if [[ -f "$OUT/screenshots/before-perf.png" && -f "$OUT/screenshots/after-perf.png" ]]; then
  if cmp -s "$OUT/screenshots/before-perf.png" "$OUT/screenshots/after-perf.png"; then
    echo "STATIC_SCENE_DURING_PROFILING=YES; Vulkan presents do not prove gameplay FPS"
  else
    echo "STATIC_SCENE_DURING_PROFILING=NO; screenshots changed"
  fi
fi
# Optional kernel syscall event counter. Failure to expose a tracepoint
# is NOT considered a gameplay or emulator failure. It is evidence only,
# and does not change any memory protection, kernel setting or guest process.
if sudo -n perf list --raw-dump 2>/dev/null | grep -Fq 'syscalls:sys_enter_mprotect'; then
  if sudo -n perf stat -x, -e syscalls:sys_enter_mprotect -p "$GAME_PID" \
    -o "$OUT/mprotect-call-count.csv" -- sleep 12 \
    > "$OUT/mprotect-stat.stdout" 2> "$OUT/mprotect-stat.stderr"; then
    echo "MPROTECT_SYSCALL_COUNTER=CAPTURED"
  else
    echo "MPROTECT_SYSCALL_COUNTER=UNAVAILABLE"
  fi
else
  echo "MPROTECT_SYSCALL_COUNTER=NO_TRACEPOINT"
fi
# The fully symbolized report and perf script are the portable evidence.
# Raw DWARF perf.data can exceed 800 MB in 45 seconds. Discard ONLY this
# run's ephemeral raw recording once its report and stacks are verified,
# preserving screenshots, call stacks, counters, and game logs in the archive.
if [[ "$PROFILE_STATUS" == "SAMPLES_CAPTURED" &&
      -s "$OUT/stacks.txt" && -s "$OUT/report.txt" &&
      -f "$OUT/perf.data" ]]; then
  raw_bytes="$(stat -c %s "$OUT/perf.data" || echo 0)"
  rm -f -- "$OUT/perf.data"
  echo "RAW_PERF_TEMPORARY_REMOVED_AFTER_REPORT=YES bytes=$raw_bytes"
fi
PHASE="PROFILE_FINISHED_GAME_STILL_RUNNING"
echo "CPU profiling finished. GoW has NOT been stopped."
echo "Exit shadPS4 normally when you are done inspecting gameplay."
echo "This SSH command will package logs after the game exits."
set +e
wait "$GAME_PID"
game_rc=$?
set -e
printf 'GAME_EXIT_CODE=%s\n' "$game_rc" > "$OUT/game-exit.txt"
PHASE="GAME_EXITED_AND_PROFILE_SAVED"
echo "GAME_EXIT_CODE=$game_rc"
