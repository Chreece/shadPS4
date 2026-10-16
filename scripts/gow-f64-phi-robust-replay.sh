#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
# SPDX-License-Identifier: GPL-2.0-or-later
# Recheck the exact previously built F64 Phi candidate without rebuilding.
# Moonlight must already be connected; it is never started, stopped or modified.
# Screenshot failures do NOT interrupt game monitoring.
set -Eeo pipefail

ROOT="$HOME/projects/shadps4/GodOfWarRagnarok/tests/native-cpu-identity-merged-20261016"
BIN="$ROOT/build/shadps4"
EXPECTED="9b7c667658e0129d97528a116603ff2b369e17eac1a7ef5c5c6c95bc6222e34c"
LOGROOT="$HOME/projects/shadps4/logs"
ID="$(date +%Y%m%d-%H%M%S)-$$"
OUT="$ROOT/gameplay/f64-phi-recheck-$ID"
ARCHIVE="$LOGROOT/gow-f64-phi-recheck-$ID.tar.gz"
WINDOW=180
mkdir -p "$OUT/screenshots" "$LOGROOT"
STATUS="PRECHECK"
GAME_EXIT="NOT_STARTED"
SHOTS=0
CAPTURE_FAILURES=0

finish() {
  local script_exit=$?
  trap - EXIT
  {
    printf 'RESULT=%s\nGAME_EXIT=%s\nMONITOR_EXIT=%s\n' "$STATUS" "$GAME_EXIT" "$script_exit"
    printf 'CANDIDATE_SHA256=%s\nSCREENSHOTS=%s\nCAPTURE_FAILURES=%s\n' "$EXPECTED" "$SHOTS" "$CAPTURE_FAILURES"
    printf 'INSTALLED_EMULATOR=UNCHANGED\nSUNSHINE=UNCHANGED\nX11=UNCHANGED\nGPU_DRIVERS=UNCHANGED\nSSH=UNCHANGED\n'
  } > "$OUT/summary.txt"
  if [[ -f "$OUT/game-console.log" ]]; then
    for pattern in 'Phi node type F64' 'Float64 metadata' \
      'Unexpected instruction for offset computation, Phi' \
      'Unexpected instruction for offset computation, ReadFirstLane' \
      'Unexpected instruction for offset computation, FindILsb32' \
      'Failed to compute offset for SRT walker' \
      'Sharp source was not flatenned' \
      'Unhandled access violation' 'ErrorSurfaceLostKHR' 'X connection to'; do
      printf '%s: %s\n' "$pattern" \
        "$(grep -Fc "$pattern" "$OUT/game-console.log" || true)" \
        >> "$OUT/error-counts.txt"
    done
    grep -Ei 'critical|unreachable|F64|SRT walker|unexpected instruction for offset|Unhandled access|ErrorSurfaceLost|X connection' \
      "$OUT/game-console.log" | tail -120 > "$OUT/error-tail.txt" || true
  fi
  tar -C "$OUT" -czf "$ARCHIVE" . || true
  printf '\nSTATUS=%s\nGAME_EXIT=%s\nSCREENSHOTS=%s\nARCHIVE=%s\n' \
    "$STATUS" "$GAME_EXIT" "$SHOTS" "$ARCHIVE"
  return "$script_exit"
}
trap finish EXIT
exec > >(tee "$OUT/progress.log") 2>&1

if [[ ! -x "$BIN" ]]; then
  echo "Native F64 Phi candidate missing: $BIN"
  exit 2
fi
ACTUAL="$(sha256sum "$BIN" | awk '{print $1}')"
if [[ "$ACTUAL" != "$EXPECTED" ]]; then
  printf 'Binary differs from validated F64 Phi candidate.\nEXPECTED=%s\nACTUAL=%s\n' "$EXPECTED" "$ACTUAL"
  exit 3
fi
if pgrep -x -u "$(id -u)" shadps4 >/dev/null 2>&1; then
  STATUS="ANOTHER_GAME_RUNNING"
  echo "A shadps4 process is already running. Nothing was stopped; close it normally before this test."
  exit 0
fi

if [[ -z "$DISPLAY" ]]; then DISPLAY=":0"; fi
export DISPLAY
if [[ -z "$XAUTHORITY" && -f "$HOME/.Xauthority" ]]; then export XAUTHORITY="$HOME/.Xauthority"; fi
if [[ -z "$XDG_RUNTIME_DIR" && -d "/run/user/$(id -u)" ]]; then export XDG_RUNTIME_DIR="/run/user/$(id -u)"; fi
if [[ -z "$DBUS_SESSION_BUS_ADDRESS" && -n "$XDG_RUNTIME_DIR" && -S "$XDG_RUNTIME_DIR/bus" ]]; then
  export DBUS_SESSION_BUS_ADDRESS="unix:path=$XDG_RUNTIME_DIR/bus"
fi
if command -v xdpyinfo >/dev/null 2>&1 &&
   ! timeout 5s xdpyinfo -display "$DISPLAY" >/dev/null 2>&1; then
  STATUS="DISPLAY_NOT_READY"
  echo "Connect Moonlight and allow the host display to become ready, then rerun."
  exit 0
fi

GPU_BUSY=""
for file in /sys/class/drm/card*/device/gpu_busy_percent; do
  [[ -r "$file" ]] || continue
  vendor="$(dirname "$file")/vendor"
  if [[ -r "$vendor" && "$(cat "$vendor" 2>/dev/null || true)" == "0x1002" ]]; then
    GPU_BUSY="$file"
    break
  fi
done

capture() {
  local sec="$1" image="$OUT/screenshots/at-$1.png"
  if command -v import >/dev/null 2>&1 &&
     timeout 7s nice -n 15 import -display "$DISPLAY" -window root "$image" >/dev/null 2>&1 &&
     [[ -s "$image" ]]; then
    SHOTS=$((SHOTS+1))
    echo "SCREENSHOT=$image"
    return 0
  fi
  if command -v ffmpeg >/dev/null 2>&1 && command -v xdpyinfo >/dev/null 2>&1; then
    local dimensions
    dimensions="$(timeout 4s xdpyinfo -display "$DISPLAY" 2>/dev/null |
      awk '/dimensions:/ {print $2; exit}' || true)"
    if [[ "$dimensions" =~ ^[0-9]+x[0-9]+$ ]] &&
       timeout 7s nice -n 15 ffmpeg -nostdin -hide_banner -loglevel error -y \
         -f x11grab -framerate 1 -video_size "$dimensions" -i "$DISPLAY.0" \
         -frames:v 1 -update 1 "$image" >/dev/null 2>&1 && [[ -s "$image" ]]; then
      SHOTS=$((SHOTS+1))
      echo "SCREENSHOT=$image"
      return 0
    fi
  fi
  CAPTURE_FAILURES=$((CAPTURE_FAILURES+1))
  rm -f "$image"
  echo "SCREENSHOT_SKIPPED_AT=$sec (monitoring continues)"
  return 0
}

ulimit -c 0 || true
cd "$OUT"
echo "GAME=CUSA34384 CPU_BACKEND=builtin WINDOW=180s"
echo "No rebuild, no DynamoRIO, no controller automation."
echo "No Moonlight/X11/driver modifications."
printf 'elapsed_s\tcpu_pct\tmem_pct\trss_kb\tstate\tamd_gpu_busy_percent\n' > "$OUT/performance.tsv"
env -u SHADPS4_CPU_ID_RESTART "$BIN" --cpu-id-mode builtin \
  --game CUSA34384 --fullscreen true --show-fps >"$OUT/game-console.log" 2>&1 &
PID=$!
GAME_EXIT="RUNNING"
STATUS="PLAYING"
echo "CANDIDATE_PID=$PID"
EARLY=0

for ((sec=0;sec<WINDOW;sec+=5)); do
  state="$(ps -p "$PID" -o stat= 2>/dev/null | tr -d '[:space:]' || true)"
  if [[ -z "$state" || "$state" == Z* ]]; then
    EARLY=1
    echo "GAME_EXITED_AT=$sec"
    break
  fi
  busy="NA"
  if [[ -n "$GPU_BUSY" ]]; then busy="$(cat "$GPU_BUSY" 2>/dev/null || echo NA)"; fi
  printf '%s\t%s\t%s\n' "$sec" \
    "$(LC_ALL=C ps -p "$PID" -o %cpu=,%mem=,rss=,stat= 2>/dev/null || true)" \
    "$busy" >> "$OUT/performance.tsv"
  if (( sec==10 || sec==30 || sec==60 || sec==95 || sec==140 || sec==175 )); then
    capture "$sec" || true
  fi
  if (( sec%30==0 )); then echo "PLAY_PROGRESS=$sec seconds"; fi
  sleep 5
done

if (( EARLY==0 )); then
  STATUS="PLAY_WINDOW_COMPLETED"
  echo "Play window ended. Requesting graceful termination of candidate PID=$PID only."
  kill -TERM "$PID" 2>/dev/null || true
  for ((n=0;n<10;n++)); do
    state="$(ps -p "$PID" -o stat= 2>/dev/null | tr -d '[:space:]' || true)"
    if [[ -z "$state" || "$state" == Z* ]]; then break; fi
    sleep 1
  done
  state="$(ps -p "$PID" -o stat= 2>/dev/null | tr -d '[:space:]' || true)"
  if [[ -n "$state" && "$state" != Z* ]]; then
    GAME_EXIT="STILL_RUNNING"
    STATUS="WINDOW_COMPLETED_CANDIDATE_STILL_RUNNING"
    echo "Candidate didn't exit on TERM, no force kill attempted."
    exit 0
  fi
fi
set +e
wait "$PID"
GAME_EXIT=$?
set -e
if (( EARLY==1 )); then
  if (( GAME_EXIT==0 )); then STATUS="GAME_EXITED_NORMALLY"; else STATUS="GAME_CRASHED"; fi
fi
echo "GAME_RESULT=$STATUS EXIT=$GAME_EXIT"
