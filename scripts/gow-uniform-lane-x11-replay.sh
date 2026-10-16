#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
# SPDX-License-Identifier: GPL-2.0-or-later
# Replay the exact wave-uniform SRT native candidate with existing X11 discovery.
# Moonlight must already be connected; it is never started, stopped or modified.
# Screenshot failures do NOT interrupt game monitoring.
set -Eeo pipefail

ROOT="$HOME/projects/shadps4/GodOfWarRagnarok/tests/native-cpu-identity-merged-20261016"
BIN="$ROOT/build/shadps4"
EXPECTED="2705b293baa4632288475875486e1e27907b7de96d29a18e3ffbf9f9460b5fc1"
LOGROOT="$HOME/projects/shadps4/logs"
ID="$(date +%Y%m%d-%H%M%S)-$$"
OUT="$LOGROOT/gow-uniform-lane-replay-$ID"
ARCHIVE="$LOGROOT/gow-uniform-lane-replay-$ID.tar.gz"
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
  echo "Compiled wave-uniform SRT candidate missing: $BIN"
  exit 2
fi
ACTUAL="$(sha256sum "$BIN" | awk '{print $1}')"
if [[ "$ACTUAL" != "$EXPECTED" ]]; then
  printf 'Binary differs from the validated SRT candidate.\nEXPECTED=%s\nACTUAL=%s\n' "$EXPECTED" "$ACTUAL"
  exit 3
fi
if pgrep -x -u "$(id -u)" shadps4 >/dev/null 2>&1; then
  STATUS="ANOTHER_GAME_RUNNING"
  echo "A shadps4 process is already running. Nothing was stopped; close it normally before this test."
  exit 0
fi

# Query existing Sunshine/Xorg session settings without changing any service,
# the X server, authorization cookies, or the SSH session.
if ! command -v xdpyinfo >/dev/null 2>&1; then
  STATUS="DISPLAY_PROBE_UNAVAILABLE"
  echo "xdpyinfo unavailable. Aborting safely rather than guessing a display."
  exit 0
fi
user_id="$(id -u)"
sun_pid="$(pgrep -u "$user_id" -x sunshine | head -1 || true)"
sun_env=""
if [[ -n "$sun_pid" && -r "/proc/$sun_pid/environ" ]]; then
  sun_env="$(tr '\0' '\n' < "/proc/$sun_pid/environ" 2>/dev/null || true)"
fi
sun_display="$(printf '%s\n' "$sun_env" | sed -n 's/^DISPLAY=//p' | head -1)"
sun_auth="$(printf '%s\n' "$sun_env" | sed -n 's/^XAUTHORITY=//p' | head -1)"
x_pid="$(pgrep -x Xorg | head -1 || true)"
x_auth=""
if [[ -n "$x_pid" && -r "/proc/$x_pid/cmdline" ]]; then
  x_auth="$(tr '\0' '\n' < "/proc/$x_pid/cmdline" 2>/dev/null |
    awk 'p=="-auth" {print;exit} {p=$0}' || true)"
fi
socket_displays=""
for sock in /tmp/.X11-unix/X[0-9]*; do
  [[ -S "$sock" ]] || continue
  socket_displays="$socket_displays :$(basename "$sock" | cut -c2-)"
done
selected_display=""
selected_auth=""
for display_try in "$sun_display" "$DISPLAY" $socket_displays :0 :1 :2; do
  [[ -n "$display_try" ]] || continue
  for auth_try in "$sun_auth" "$x_auth" "$XAUTHORITY" \
    "$HOME/.Xauthority" "/run/user/$user_id/Xauthority" \
    "/run/user/$user_id/gdm/Xauthority" ""; do
    if [[ -n "$auth_try" ]]; then
      [[ -f "$auth_try" && -r "$auth_try" ]] || continue
      if env DISPLAY="$display_try" XAUTHORITY="$auth_try" \
        timeout 3s xdpyinfo -display "$display_try" >/dev/null 2>&1; then
        selected_display="$display_try"
        selected_auth="$auth_try"
        break 2
      fi
    elif env -u XAUTHORITY DISPLAY="$display_try" timeout 3s \
      xdpyinfo -display "$display_try" >/dev/null 2>&1; then
      selected_display="$display_try"
      selected_auth=""
      break 2
    fi
  done
done
if [[ -z "$selected_display" ]]; then
  STATUS="NO_ACCESSIBLE_EXISTING_X11"
  {
    echo "Sunshine process PID=$sun_pid, Xorg PID=$x_pid"
    echo "Existing X11 sockets:"
    ls -l /tmp/.X11-unix/X* 2>&1 || true
    echo "Display-related processes:"
    ps -eo pid,comm,args | grep -Ei 'Xorg|Xwayland|sunshine' | grep -v grep || true
  } > "$OUT/display-diagnostics.txt"
  echo "No existing X11 session is accessible with this SSH user's credentials."
  echo "Connect Moonlight to the server first, then rerun this command."
  exit 0
fi
export DISPLAY="$selected_display"
if [[ -n "$selected_auth" ]]; then
  export XAUTHORITY="$selected_auth"
else
  unset XAUTHORITY
fi
printf 'DISPLAY_SELECTED=%s\nXAUTHORITY_FILE=%s\n' "$DISPLAY" "$selected_auth" \
  | tee "$OUT/display-diagnostics.txt"
echo "EXISTING_X11_SESSION=ACCESSIBLE"

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
echo "GAME=CUSA34384 CPU_BACKEND=builtin WINDOW=180s (SRT wave-uniform candidate)"
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
