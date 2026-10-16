#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
# SPDX-License-Identifier: GPL-2.0-or-later
# Manual unlimited-duration gameplay of the pinned native CPU guest-range candidate.
# There is NO timed shutdown. User exits the emulator normally when finished.
# All earlier validated native graphics and installed configurations remain unchanged.
set -Eeo pipefail
BUILD="$HOME/projects/shadps4/GodOfWarRagnarok/tests/native-rendered-gfx-20261016-152535-3014679/build"
BASE="$BUILD/shadps4"
BASE_SHA="264fccf27f48f1df06f1e9d54d5138eb476b71b72a29fb4cb7c6f058439aa239"
TOPIC="$BUILD/shadps4.native-guest-ranges-20261016-165512-3730519"
TOPIC_SHA="b111a74ce5586517d7a0fc35325e686050ed8597a75c75d19b98d290ac8019c7"
LOGROOT="$HOME/projects/shadps4/logs"
ID="$(date +%Y%m%d-%H%M%S)-$$"
OUT="$LOGROOT/gow-native-cpu-manual-$ID"
ARCHIVE="$LOGROOT/gow-native-cpu-manual-$ID.tar.gz"
# Screenshot capture ends after 30 minutes, but gameplay never has a time limit.
mkdir -p "$OUT/ranges/screenshots"
STATUS="PRECHECK"
BASE_STATUS="NOT_LAUNCHED"
RANGES_STATUS="NOT_STARTED"
finish() {
  local code=$?
  trap - EXIT
  {
    printf 'RESULT=%s\nSCRIPT_EXIT=%s\n' "$STATUS" "$code"
    printf 'BASELINE=%s\nRANGES=%s\n' "$BASE_STATUS" "$RANGES_STATUS"
    printf 'BASE_SHA=%s\nTOPIC_SHA=%s\n' "$BASE_SHA" "$TOPIC_SHA"
    printf 'CPU_MODE=builtin\nDYNAMORIO=OFF\nBUILD=UNCHANGED\nAUTO_TERMINATION=DISABLED\n'
    printf 'INSTALLED_EMULATOR=UNCHANGED\nSUNSHINE=UNCHANGED\nX11=UNCHANGED\nSSH=UNCHANGED\n'
  } > "$OUT/summary.txt"
  for phase in ranges; do
    if [[ -f "$OUT/$phase/game-console.log" ]]; then
      grep -E 'Static CPU patching for|CPU deferred static sites for|In-process guest CPU identity' \
        "$OUT/$phase/game-console.log" >"$OUT/$phase/cpu-sites.txt" || true
      for pattern in 'Unhandled access violation' 'Phi node type F64' \
        'Failed to compute offset for SRT walker' 'ErrorSurfaceLostKHR' \
        'X connection to'; do
        printf '%s\t%s\n' "$pattern" \
          "$(grep -Fc "$pattern" "$OUT/$phase/game-console.log" || true)" \
          >>"$OUT/$phase/events.tsv"
      done
    fi
    for image in "$OUT/$phase"/screenshots/*.png; do
      [[ -f "$image" ]] && sha256sum "$image"
    done > "$OUT/$phase/screenshots.sha256"
    awk '{print $1}' "$OUT/$phase/screenshots.sha256" | sort -u | wc -l \
      > "$OUT/$phase/unique-screenshots.txt"
    for suffix in guest-flips.csv swapchain-presents.csv; do
      if [[ -f "$OUT/$phase/fps.$suffix" ]]; then
        wc -l "$OUT/$phase/fps.$suffix" >> "$OUT/$phase/frame-counts.txt"
      fi
    done
  done
  tar -C "$OUT" -czf "$ARCHIVE" . || true
  printf '\nRESULT=%s\nBASE=%s\nRANGES=%s\nARCHIVE=%s\n' \
    "$STATUS" "$BASE_STATUS" "$RANGES_STATUS" "$ARCHIVE"
  return "$code"
}
trap finish EXIT
exec > >(tee "$OUT/progress.log") 2>&1
if [[ ! -f "$BASE" || ! -f "$TOPIC" || -L "$BASE" || -L "$TOPIC" ]]; then
  STATUS="MISSING_OR_SYMLINKED_BINARY"
  exit 2
fi
if [[ "$(sha256sum "$BASE" | awk '{print $1}')" != "$BASE_SHA" ||
      "$(sha256sum "$TOPIC" | awk '{print $1}')" != "$TOPIC_SHA" ]]; then
  STATUS="BINARY_CHECKSUM_MISMATCH"
  echo "Baseline or topic binary differs from exact validated checkpoint."
  exit 3
fi
if pgrep -x -u "$(id -u)" shadps4 >/dev/null 2>&1; then
  STATUS="OTHER_EMULATOR_RUNNING"
  echo "A game is already running. No processes stopped."
  exit 0
fi
if [[ -f "$HOME/Applications/shadps4/shadps4" ]] &&
   { [[ "$BASE" -ef "$HOME/Applications/shadps4/shadps4" ]] ||
     [[ "$TOPIC" -ef "$HOME/Applications/shadps4/shadps4" ]]; }; then
  STATUS="INSTALLED_BINARY_ALIAS"
  exit 3
fi
if ! command -v xdpyinfo >/dev/null 2>&1; then
  STATUS="NO_X11_PROBE"
  exit 0
fi
sunpid="$(pgrep -x -u "$(id -u)" sunshine | head -1 || true)"
sunenv=""
if [[ -n "$sunpid" && -r "/proc/$sunpid/environ" ]]; then
  sunenv="$(tr '\0' '\n' <"/proc/$sunpid/environ" 2>/dev/null || true)"
fi
sd="$(printf '%s\n' "$sunenv" | sed -n 's/^DISPLAY=//p' | head -1)"
sa="$(printf '%s\n' "$sunenv" | sed -n 's/^XAUTHORITY=//p' | head -1)"
xp="$(pgrep -x Xorg | head -1 || true)"
xa=""
if [[ -n "$xp" && -r "/proc/$xp/cmdline" ]]; then
  xa="$(tr '\0' '\n' <"/proc/$xp/cmdline" 2>/dev/null |
    awk 'last=="-auth" {print;exit} {last=$0}' || true)"
fi
CHOSEN=""
COOKIE=""
for d in "$sd" "$DISPLAY" :0 :1 :2; do
  [[ -n "$d" ]] || continue
  for a in "$sa" "$xa" "$XAUTHORITY" "$HOME/.Xauthority" \
    "/run/user/$(id -u)/Xauthority" ""; do
    if [[ -n "$a" ]]; then
      [[ -f "$a" && -r "$a" ]] || continue
      if env DISPLAY="$d" XAUTHORITY="$a" timeout 3s xdpyinfo -display "$d" \
        >/dev/null 2>&1; then CHOSEN="$d"; COOKIE="$a"; break 2; fi
    elif env -u XAUTHORITY DISPLAY="$d" timeout 3s xdpyinfo -display "$d" \
      >/dev/null 2>&1; then CHOSEN="$d"; COOKIE=""; break 2; fi
  done
done
if [[ -z "$CHOSEN" ]]; then
  STATUS="EXISTING_X11_UNAVAILABLE"
  echo "Connect Moonlight before running this test; no display changes made."
  exit 0
fi
export DISPLAY="$CHOSEN"
if [[ -n "$COOKIE" ]]; then export XAUTHORITY="$COOKIE"; else unset XAUTHORITY; fi
echo "X11_ACCESS=PASS display=$DISPLAY"
capture() {
  local phase="$1" second="$2" image="$OUT/$1/screenshots/at-$2.png"
  if command -v import >/dev/null 2>&1 &&
     timeout 7s nice -n 15 import -display "$DISPLAY" -window root "$image" \
       >/dev/null 2>&1 && [[ -s "$image" ]]; then
    echo "SCREENSHOT=$phase at $second seconds"
  else
    echo "SCREENSHOT_UNAVAILABLE=$phase at $second seconds"
  fi
  return 0
}
trial() {
  local phase="$1" binary="$2" pid sec state exited=0 ret
  cd "$OUT/$phase" || return 2
  printf 'elapsed_s\tcpu_pct\tmem_pct\trss_kb\tstate\n' >performance.tsv
  echo "TEST_PHASE=$phase binary=$binary"
  env -u SHADPS4_CPU_ID_RESTART -u SHADPS4_CPU_TIMING_CSV \
    SHADPS4_FRAME_METRICS_PREFIX="$OUT/$phase/fps" \
    "$binary" --cpu-id-mode builtin --game CUSA34384 --fullscreen true --show-fps \
    >game-console.log 2>&1 &
  pid=$!
  for ((sec=0;;sec+=5)); do
    state="$(ps -p "$pid" -o stat= 2>/dev/null | tr -d '[:space:]' || true)"
    if [[ -z "$state" || "$state" == Z* ]]; then
      exited=1
      echo "GAME_EXITED=$phase at $sec seconds (not stopped by test runner)"
      break
    fi
    printf '%s\t%s\n' "$sec" \
      "$(LC_ALL=C ps -p "$pid" -o %cpu=,%mem=,rss=,stat= 2>/dev/null || true)" \
      >>performance.tsv
    if (( sec==20 || sec==60 || sec==110 || sec==140 || (sec>=180 && sec<=1800 && sec%60==0) )); then
      capture "$phase" "$sec" || true
    fi
    if (( sec%30==0 )); then echo "PROGRESS=$phase $sec (waiting for YOUR manual emulator exit)"; fi
    sleep 5
  done
  # Intentionally no kill, timeout, auto-exit or automated controller input.
  # Wait until the user exits the emulator through its own UI.
  set +e
  wait "$pid"
  ret=$?
  set -e
  echo "TEST_EXIT=$phase code=$ret"
  if (( ret!=0 )); then return 7; fi
  return 0
}
STATUS="MANUAL_NATIVE_CPU_PLAY"
echo "MANUAL_GOW_SESSION=START"
echo "CPU=emulator-owned builtin; executable-range fix; graphics unchanged."
echo "NO_AUTO_EXIT=YES; YOU decide when to exit the game."
echo "Please use Continue, play as long as required, then quit shadPS4 normally."
echo "Keep this SSH terminal open; logs are packaged on game exit."
if trial ranges "$TOPIC"; then
  RANGES_STATUS="EXITED_ZERO"
  STATUS="GAME_EXITED_BY_USER"
else
  status=$?
  RANGES_STATUS="EXIT_NONZERO_$status"
  STATUS="GAME_EXITED_NONZERO_OR_CRASH"
fi
echo "MANUAL_GOW_SESSION_END=$STATUS"
