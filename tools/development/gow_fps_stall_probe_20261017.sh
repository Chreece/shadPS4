#!/usr/bin/env bash
# Read-only GoW CPU/GPU stall sampling. Never builds or terminates processes.
set -Eeuo pipefail
ROOT="$HOME/projects/shadps4"
LOGS="$ROOT/logs"
BASE="$ROOT/GodOfWarRagnarok/tests/native-rendered-gfx-20261016-152535-3014679"
BIN="$BASE/build/shadps4.native-userfaultfd-deferred-gow-uffd-deferred-build-20261016-235543"
EXPECTED=e413ab1d953793f50c5500d528b43aae73fc3ab7ceb966d8d3ab9da275310ffa
mkdir -p "$LOGS"
TAG="gow-fps-stall-$(date +%Y%m%d-%H%M%S)"
OUT="$LOGS/$TAG"
mkdir -p "$OUT"
finish() {
  rc=$?
  trap - EXIT
  printf 'RESULT=%s\n' "$rc" > "$OUT/result.txt"
  tar -czf "$LOGS/$TAG.tar.gz" -C "$LOGS" "$TAG" || true
  echo "ARCHIVE=$LOGS/$TAG.tar.gz"
  echo "BUILD=UNCHANGED SSH=UNCHANGED SUNSHINE=UNCHANGED GAME=NOT_TERMINATED"
  exit "$rc"
}
trap finish EXIT
echo "$EXPECTED  $BIN" | sha256sum -c - | tee "$OUT/binary-check.txt"
found=""
for p in /proc/[0-9]*; do
  id="${p##*/}"
  [[ "$(stat -c %u "$p" 2>/dev/null || :)" == "$(id -u)" ]] || continue
  exe="$(readlink -f "$p/exe" 2>/dev/null || :)"
  case "$exe" in
    *shadps4*|*shadPS4*)
      if [[ "$exe" != "$BIN" ]]; then
        echo "DIFFERENT_EMULATOR_RUNNING=$id; REFUSING_TO_ATTACH_OR_LAUNCH"
        exit 2
      fi
      found="$id"
      ;;
  esac
done
if [[ -z "$found" ]]; then
  echo "AUTO_LAUNCH=PROVEN_GOW_BINARY"
  export DISPLAY="${DISPLAY:-:0}"
  export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"
  export DBUS_SESSION_BUS_ADDRESS="${DBUS_SESSION_BUS_ADDRESS:-unix:path=$XDG_RUNTIME_DIR/bus}"
  for key in ${!SHADPS4_GOW_@} ${!SHADPS4_CPU_ID_@}; do unset "$key"; done
  (cd "$BASE" && exec "$BIN" --cpu-id-mode builtin --userfaultfd --game CUSA34384 --fullscreen true --show-fps >"$OUT/game-console.log" 2>&1 </dev/null) &
  found=$!
  echo "AUTO_LAUNCHED_PID=$found"
  sleep 4
  if [[ "$(readlink -f "/proc/$found/exe" 2>/dev/null || :)" != "$BIN" ]]; then
    echo "GAME_PID_VALIDATION_FAILED; NOT_PROFILING_WRAPPER"
    exit 2
  fi
  if ! kill -0 "$found" 2>/dev/null; then
    echo "GAME_EXITED_EARLY; SEE_GAME_CONSOLE_LOG"
    exit 2
  fi
fi
[[ "$(readlink -f "/proc/$found/exe" 2>/dev/null || :)" == "$BIN" ]] || { echo "NOT_A_GOW_PID"; exit 2; }
echo "PROFILE_PID=$found" | tee "$OUT/pid.txt"
ps -p "$found" -o pid,ppid,etime,pcpu,pmem,rss,nlwp,args > "$OUT/process.txt"
if command -v perf >/dev/null 2>&1; then
  timeout 35s perf record -F 49 -g --call-graph fp -p "$found" -o "$OUT/perf.data" -- sleep 30 >"$OUT/perf-record.log" 2>&1 || true
  if [[ -s "$OUT/perf.data" ]]; then
    perf report --stdio --no-children -i "$OUT/perf.data" --percent-limit 0.5 >"$OUT/perf-report.txt" 2>&1 || true
    perf script -i "$OUT/perf.data" >"$OUT/perf-script.txt" 2>&1 || true
    rm -f "$OUT/perf.data"
  fi
fi
for i in $(seq 1 20); do
  [[ -d "/proc/$found" ]] || break
  printf 'SAMPLE=%s TIME=%s\n' "$i" "$(date -Is)" >> "$OUT/threads.txt"
  ps -L -p "$found" -o tid,comm,stat,pcpu,time,wchan:32 --sort=-pcpu >> "$OUT/threads.txt" 2>&1 || true
  cat "/proc/$found/status" > "$OUT/status-$i.txt" 2>/dev/null || true
  for f in /sys/class/drm/card[0-9]*/device/gpu_busy_percent; do
    [[ -r "$f" ]] && printf '%s %s\n' "$f" "$(cat "$f")" >> "$OUT/gpu-busy.txt"
  done
  sleep 2
done
if [[ -d "/proc/$found" ]]; then
  echo "PROFILING_COMPLETE=YES; GAME_LEFT_RUNNING=YES"
else
  echo "GAME_EXITED_DURING_PROFILING=YES; ARCHIVE_CAPTURED"
  exit 2
fi
