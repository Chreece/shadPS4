#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
# SPDX-License-Identifier: GPL-2.0-or-later
# GoW CPU trial: only the compiled candidate is launched, with manual input.
set -Eeuo pipefail
STAGE="$HOME/projects/shadps4/GodOfWarRagnarok/tests/native-cpu-identity-merged-20261016"
BIN="$STAGE/build/shadps4"
LOGROOT="$HOME/projects/shadps4/logs"
RUN_ID="$(date +%Y%m%d-%H%M%S)-$$"
OUT="$STAGE/play/native-$RUN_ID"
ARCHIVE="$LOGROOT/gow-native-cpu-gameplay-$RUN_ID.tar.gz"
mkdir -p "$OUT/screenshots" "$LOGROOT"
GAME_LOG="$OUT/emulator-console.log"
EXPECTED="75fced13d1d4ef10998369160ee001871115127720fb08f52214fef4a5df7024"
STATUS="PRECHECK_FAILED"
EXIT_CODE="NOT_STARTED"
SHOTS=0
trap '{
  printf "STATUS=%s\nGAME_EXIT=%s\nSCREENSHOTS=%s\n" "$STATUS" "$EXIT_CODE" "$SHOTS" > "$OUT/summary.txt"
  printf "INSTALLED_EMULATOR=UNCHANGED\nSSH=UNCHANGED\nOTHER_APPS=UNCHANGED\n" >> "$OUT/summary.txt"
  tar -C "$OUT" -czf "$ARCHIVE" . || true
  printf "\nSTATUS=%s\nGAME_EXIT=%s\nLOG=%s\nARCHIVE=%s\n" "$STATUS" "$EXIT_CODE" "$GAME_LOG" "$ARCHIVE"
}' EXIT
if [[ ! -x "$BIN" ]]; then echo "MISSING_CANDIDATE=$BIN"; exit 2; fi
ACTUAL="$(sha256sum "$BIN" | cut -d" " -f1)"
if [[ "$ACTUAL" != "$EXPECTED" ]]; then
  echo "Binary hash differs from the validated successful build; refusing to launch."
  exit 2
fi
if pgrep -x -u "$(id -u)" shadps4 >/dev/null 2>&1; then
  echo "Another emulator is running. No process stopped."
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
if command -v xdpyinfo >/dev/null && ! xdpyinfo -display "$DISPLAY" >/dev/null 2>&1; then
  echo "Display not accessible; not changing existing X11/Sunshine setup."
  exit 2
fi
DURATION="${GOW_CPU_PLAY_SECONDS:-180}"
if ! [[ "$DURATION" =~ ^[0-9]+$ ]] || (( DURATION<30 || DURATION>1200 )); then
  echo "Invalid duration: must be 30 to 1200 seconds"; exit 2
fi

echo "GAME=CUSA34384 CPU_BACKEND=builtin"
echo "PLAY_WINDOW=$DURATION seconds; no automated controller inputs"
echo "GAME_LOG=$GAME_LOG"
printf 'timestamp\telapsed_s\tpid\tcpu_percent\tmem_percent\trss_kb\tstate\n' >"$OUT/process-samples.tsv"
cd "$OUT"
env -u SHADPS4_CPU_ID_RESTART "$BIN" --cpu-id-mode builtin \
  --game CUSA34384 --fullscreen true --show-fps >"$GAME_LOG" 2>&1 &
PID=$!
STATUS="RUNNING"
echo "CANDIDATE_PID=$PID"

capture() {
  local second="$1" output="$OUT/screenshots/at-$1-s.png"
  if command -v import >/dev/null; then
    if timeout 8s nice -n 15 import -display "$DISPLAY" -window root "$output" >/dev/null 2>&1 && [[ -s "$output" ]]; then
      SHOTS=$((SHOTS+1)); echo "SCREENSHOT=$output"; return
    fi
  fi
  if command -v ffmpeg >/dev/null && command -v xdpyinfo >/dev/null; then
    local dimensions
    dimensions="$(xdpyinfo -display "$DISPLAY" | awk '/dimensions:/ {print $2; exit}')"
    if [[ "$dimensions" =~ ^[0-9]+x[0-9]+$ ]] &&
       timeout 8s nice -n 15 ffmpeg -nostdin -hide_banner -loglevel error -y \
         -f x11grab -framerate 1 -video_size "$dimensions" -i "$DISPLAY.0" \
         -frames:v 1 -update 1 "$output" >/dev/null 2>&1 && [[ -s "$output" ]]; then
      SHOTS=$((SHOTS+1)); echo "SCREENSHOT=$output"; return
    fi
  fi
  rm -f "$output"
  echo "SCREENSHOT_UNAVAILABLE=$second"
}

FINISHED_EARLY=0
for ((sec=0;sec<DURATION;sec+=5)); do
  ps_state="$(ps -p "$PID" -o stat= 2>/dev/null | tr -d '[:space:]' || true)"
  if [[ -z "$ps_state" || "$ps_state" == Z* ]]; then FINISHED_EARLY=1; break; fi
  printf '%s\t%s\t%s\t%s\n' "$(date +%s)" "$sec" "$PID" \
    "$(LC_ALL=C ps -p "$PID" -o %cpu=,%mem=,rss=,stat= 2>/dev/null || true)" \
    >> "$OUT/process-samples.tsv"
  if (( sec==15 || sec==45 || sec==90 || sec==135 )); then capture "$sec"; fi
  if (( sec%30==0 )); then echo "PROGRESS=$sec seconds"; fi
  sleep 5
done
if (( FINISHED_EARLY==0 )); then
  STATUS="TIME_LIMIT_REACHED"
  echo "Stopping only candidate PID=$PID (no other emulator/process)."
  kill -TERM "$PID" 2>/dev/null || true
  for ((n=0;n<8;n++)); do
    ps_state="$(ps -p "$PID" -o stat= 2>/dev/null | tr -d '[:space:]' || true)"
    if [[ -z "$ps_state" || "$ps_state" == Z* ]]; then break; fi
    sleep 1
  done
  ps_state="$(ps -p "$PID" -o stat= 2>/dev/null | tr -d '[:space:]' || true)"
  if [[ -n "$ps_state" && "$ps_state" != Z* ]]; then
    echo "Candidate ignored TERM; leaving it alive rather than force killing."
    EXIT_CODE="STILL_RUNNING"
    exit 0
  fi
fi
set +e
wait "$PID"
EXIT_CODE=$?
set -e
if (( FINISHED_EARLY==1 )); then
  STATUS="GAME_EXITED"
  if [[ "$EXIT_CODE" != 0 ]]; then STATUS="GAME_ERROR"; fi
fi
BASE_LOG="${XDG_DATA_HOME:-$HOME/.local/share}/shadPS4/log/shad_log.txt"
if [[ -f "$BASE_LOG" ]]; then cp "$BASE_LOG" "$OUT/shad_log.txt" 2>/dev/null || true; fi
echo "TEST_FINISHED=$STATUS EXIT=$EXIT_CODE"
