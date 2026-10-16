#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
# SPDX-License-Identifier: GPL-2.0-or-later
#
# Test shadPS4's existing Linux userfaultfd tracking backend (not an app).
# No compilation, perf sampling, sysctl, settings write, or game auto-stop.
set -Eeuo pipefail
BUILD="$HOME/projects/shadps4/GodOfWarRagnarok/tests/native-rendered-gfx-20261016-152535-3014679/build"
BIN="$BUILD/shadps4.native-sparse-protection-v1"
BIN_SHA="a5b4822645a3e40840815ebd35b722681379f5da3a9b9d537860ea4ad1dafc7c"
BASE="$BUILD/shadps4"
BASE_SHA="264fccf27f48f1df06f1e9d54d5138eb476b71b72a29fb4cb7c6f058439aa239"
DMA="$BUILD/shadps4.native-dma-dirty-gate-v1"
DMA_SHA="d34c35848997ecac41f5a7c03da55e00dc1503ed3aa1794a6bd28f4e22a2e486"
OUT="$HOME/projects/shadps4/logs/gow-native-userfaultfd-no-build-$(date +%Y%m%d-%H%M%S)-$$"
ARCHIVE="$OUT.tar.gz"
PHASE="PRECHECK"
BACKEND="UNDETERMINED"
GAME_PID=""
GPU_BUSY=""
DISPLAY="$(printenv DISPLAY || true)"
XAUTHORITY="$(printenv XAUTHORITY || true)"
mkdir -p "$OUT/screenshots"
finish() {
  local result=$?
  trap - EXIT
  {
    printf 'PHASE=%s\nSCRIPT_EXIT=%s\nBACKEND=%s\n' "$PHASE" "$result" "$BACKEND"
    printf 'BIN_SHA256=%s\nNO_REBUILD=YES\nPERF_SAMPLING=OFF\n' "$BIN_SHA"
    printf 'SHADPS4_BUILTIN_FLAG=--userfaultfd\nPERSISTENT_CONFIG=UNCHANGED\n'
    printf 'GPU_DRIVER=UNCHANGED\nX11=UNCHANGED\nSUNSHINE=UNCHANGED\n'
    printf 'KERNEL_SYSCTL=UNCHANGED\nINSTALLED_EMULATOR=UNCHANGED\nSSH=UNCHANGED\n'
  } > "$OUT/summary.txt"
  if [[ -f "$OUT/game-console.log" ]]; then
    grep -Ei 'Memory tracking method:|userfaultfd|fallback|unhandled access violation|GPU readbacksMode' \
      "$OUT/game-console.log" | tail -50 > "$OUT/tracking-backend.log" || true
  fi
  if [[ -f "$OUT/measurement-window.txt" && -f "$OUT/frame.swapchain-presents.csv" ]]; then
    python3 - "$OUT/frame.swapchain-presents.csv" "$OUT/measurement-window.txt" \
      >"$OUT/measurement-summary.txt" 2>"$OUT/measurement-errors.txt" <<'PY' || true
import csv,sys,statistics
t=[]
with open(sys.argv[1],newline='') as file:
 for row in csv.reader(file):
  try:t.append(int(row[0]))
  except (ValueError,IndexError):pass
with open(sys.argv[2]) as file:
 win={}
 for line in file:
  if '=' in line:
   k,v=line.strip().split('=',1)
   win[k]=int(v)
start=win.get('START_MONOTONIC_US')
end=win.get('END_MONOTONIC_US')
if start and end and end>start:
  frames=[x for x in t if start<=x<=end]
  secs=(end-start)/1e6
  print(f'SAMPLE_DURATION_SECONDS={secs:.2f}')
  print(f'VULKAN_PRESENT_COUNT={len(frames)}')
  print(f'VULKAN_PRESENT_RATE_FPS={len(frames)/secs:.3f}')
  if len(frames)>2:
    intervals=[(b-a)/1000 for a,b in zip(frames,frames[1:])]
    print(f'PRESENT_INTERVAL_MEDIAN_MS={statistics.median(intervals):.2f}')
  print('CAUTION=Vulkan presentations are not necessarily unique guest frames')
else: print('INCOMPLETE_WINDOW')
PY
  fi
  tar -czf "$ARCHIVE" -C "$OUT" . || true
  printf '\nRESULT=%s\nACTUAL_BACKEND=%s\nARCHIVE=%s\n' "$PHASE" "$BACKEND" "$ARCHIVE"
  return "$result"
}
trap finish EXIT
exec > >(tee "$OUT/progress.log") 2>&1

verify_exact() {
  local file="$1" expected="$2" actual
  if [[ ! -f "$file" || -L "$file" || ! -x "$file" ]]; then
    echo "REFERENCE_MISSING=$file"
    return 1
  fi
  actual="$(sha256sum "$file" | awk '{print $1}')"
  if [[ "$actual" != "$expected" ]]; then
    printf 'REFERENCE_CHANGED=%s\nEXPECTED_SHA=%s\nACTUAL_SHA=%s\n' \
      "$file" "$expected" "$actual"
    return 1
  fi
}
if ! verify_exact "$BIN" "$BIN_SHA" ||
   ! verify_exact "$BASE" "$BASE_SHA" ||
   ! verify_exact "$DMA" "$DMA_SHA"; then
  PHASE="REFERENCE_SHA256_FAILED"
  exit 3
fi
if ps -u "$(id -un)" -o comm= | grep -Eq '^shadps4'; then
  PHASE="ANOTHER_EMULATOR_RUNNING"
  echo "Existing emulator left untouched; exit it before trying this test."
  exit 2
fi
if [[ -f "$HOME/Applications/shadps4/shadps4" &&
      "$BIN" -ef "$HOME/Applications/shadps4/shadps4" ]]; then
  PHASE="WOULD_ALIAS_INSTALLED_EMULATOR"
  exit 3
fi
if ! command -v xdpyinfo >/dev/null 2>&1 ||
   ! command -v python3 >/dev/null 2>&1 ||
   ! [[ -r /dev/tty ]]; then
  PHASE="MISSING_EXISTING_PREREQUISITE"
  exit 2
fi

# Discover but never modify the running Sunshine/X11 display.
sunpid="$(pgrep -u "$(id -u)" -x sunshine | head -1 || true)"
sunenv=""
if [[ -n "$sunpid" && -r "/proc/$sunpid/environ" ]]; then
  sunenv="$(tr '\0' '\n' <"/proc/$sunpid/environ" 2>/dev/null || true)"
fi
sun_display="$(printf '%s\n' "$sunenv" | sed -n 's/^DISPLAY=//p' | head -1)"
sun_auth="$(printf '%s\n' "$sunenv" | sed -n 's/^XAUTHORITY=//p' | head -1)"
xpid="$(pgrep -x Xorg | head -1 || true)"
x_auth=""
if [[ -n "$xpid" && -r "/proc/$xpid/cmdline" ]]; then
  x_auth="$(tr '\0' '\n' <"/proc/$xpid/cmdline" 2>/dev/null |
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
      if env DISPLAY="$d" XAUTHORITY="$auth" timeout 3s xdpyinfo -display "$d" >/dev/null 2>&1; then
        selected_display="$d";selected_auth="$auth";break 2
      fi
    elif env -u XAUTHORITY DISPLAY="$d" timeout 3s xdpyinfo -display "$d" >/dev/null 2>&1; then
      selected_display="$d";selected_auth="";break 2
    fi
  done
done
if [[ -z "$selected_display" ]]; then
  PHASE="EXISTING_MOONLIGHT_X11_UNAVAILABLE"
  echo "No X11 environment was changed."
  exit 2
fi
export DISPLAY="$selected_display"
if [[ -n "$selected_auth" ]]; then export XAUTHORITY="$selected_auth"
else unset XAUTHORITY; fi
if [[ -d "/run/user/$(id -u)" ]]; then
  export XDG_RUNTIME_DIR="/run/user/$(id -u)"
  if [[ -S "$XDG_RUNTIME_DIR/bus" ]]; then
    export DBUS_SESSION_BUS_ADDRESS="unix:path=$XDG_RUNTIME_DIR/bus"
  fi
fi
for path in /sys/class/drm/card*/device/gpu_busy_percent; do
  [[ -r "$path" ]] || continue
  vendor="$(dirname "$path")/vendor"
  if [[ -r "$vendor" && "$(cat "$vendor" 2>/dev/null || true)" == "0x1002" ]]; then
    GPU_BUSY="$path"; break
  fi
done
echo "EXISTING_MOONLIGHT_X11=PASS display=$DISPLAY"
echo "NO_BUILD=YES; SAME_NATIVE_CPU_AND_RENDERER=CHECKSUM_VERIFIED"
echo "Testing built-in Linux userfaultfd memory tracking; no config/sysctl changes."

cd "$OUT"
ulimit -c 0 || true
PHASE="LAUNCHING_USERFAULTFD"
env -u SHADPS4_CPU_ID_RESTART -u SHADPS4_CPU_TIMING_CSV \
  SHADPS4_FRAME_METRICS_PREFIX="$OUT/frame" \
  "$BIN" --cpu-id-mode builtin --userfaultfd --game CUSA34384 \
  --fullscreen true --show-fps >"$OUT/game-console.log" 2>&1 &
GAME_PID=$!
sleep 3
if [[ "$(readlink -f "/proc/$GAME_PID/exe" 2>/dev/null || true)" != "$BIN" ]]; then
  PHASE="EXACT_GAME_FAILED_TO_START"
  wait "$GAME_PID" || true
  exit 4
fi
echo "GAME_LAUNCHED=PASS PID=$GAME_PID"
for ((i=0;i<15;i++)); do
  if grep -Fq 'Memory tracking method: userfaultfd' "$OUT/game-console.log"; then
    BACKEND="USERFAULTFD";break
  fi
  if grep -Fq 'Memory tracking method: signals' "$OUT/game-console.log"; then
    BACKEND="SIGNALS_FALLBACK";break
  fi
  if ! kill -0 "$GAME_PID" 2>/dev/null; then break; fi
  sleep 1
done
echo "ACTUAL_MEMORY_TRACKING=$BACKEND"
if [[ "$BACKEND" != USERFAULTFD ]]; then
  PHASE="BACKEND_NOT_AVAILABLE_NO_PERFORMANCE_CLAIM"
  echo "Userfaultfd did not become active; this identifies the first blocker."
  echo "Exit GoW normally to finalize the diagnostic archive."
  set +e;wait "$GAME_PID";set -e
  exit 0
fi

echo "USERFAULTFD=ACTIVE. Reach MOVING gameplay before pressing Enter in SSH."
if ! IFS= read -r answer </dev/tty; then
  PHASE="TTY_CLOSED_GAME_NOT_AUTOSTOPPED"
  exit 4
fi
if [[ "$(readlink -f "/proc/$GAME_PID/exe" 2>/dev/null || true)" != "$BIN" ]]; then
  PHASE="GAME_EXITED_BEFORE_MEASUREMENT"
  exit 4
fi
PHASE="MEASURING_45_SECONDS_NO_PERF"
if command -v import >/dev/null 2>&1; then
  timeout 7s import -display "$DISPLAY" -window root \
    "$OUT/screenshots/before.png" >/dev/null 2>&1 || true
fi
start_us="$(python3 -c 'import time; print(int(time.clock_gettime(time.CLOCK_MONOTONIC)*1000000))')"
echo "START_MONOTONIC_US=$start_us" >"$OUT/measurement-window.txt"
printf 'unix_second\tprocess_cpu_pct_rss_kb_threads_state\tamd_gpu_busy_pct\n' >"$OUT/process-samples.tsv"
for ((i=0;i<45;i++)); do
  [[ "$(readlink -f "/proc/$GAME_PID/exe" 2>/dev/null || true)" == "$BIN" ]] || break
  stats="$(LC_ALL=C ps -p "$GAME_PID" -o %cpu=,rss=,nlwp=,stat= | tr -d '\n' || true)"
  busy=NA
  if [[ -n "$GPU_BUSY" ]]; then busy="$(cat "$GPU_BUSY" 2>/dev/null || echo NA)"; fi
  printf '%s\t%s\t%s\n' "$(date +%s)" "$stats" "$busy" >>"$OUT/process-samples.tsv"
  sleep 1
done
end_us="$(python3 -c 'import time; print(int(time.clock_gettime(time.CLOCK_MONOTONIC)*1000000))')"
echo "END_MONOTONIC_US=$end_us" >>"$OUT/measurement-window.txt"
if command -v import >/dev/null 2>&1; then
  timeout 7s import -display "$DISPLAY" -window root \
    "$OUT/screenshots/after.png" >/dev/null 2>&1 || true
fi
echo "45_SECONDS_COMPLETE=YES. GoW remains open until you exit it normally."
PHASE="WAITING_FOR_MANUAL_GAME_EXIT"
set +e;wait "$GAME_PID";rc=$?;set -e
printf 'GAME_EXIT_CODE=%s\n' "$rc" >"$OUT/game-exit.txt"
PHASE="USERFAULTFD_GAME_EXITED"
