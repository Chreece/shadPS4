#!/usr/bin/env bash
set -Eeuo pipefail

B="$HOME/projects/shadps4/GodOfWarRagnarok/tests/gow91-build-20261014-192744-3709287/shadps4"
EXPECT="42409e853db1428392ee6ea0a35b7dcd28ccac7a5378f28f038a61ae96c1ed80"
CWD="$HOME/projects/shadps4/GodOfWarRagnarok/runtime/gow91"
PROFILE="$HOME/.local/share/shadPS4"
CFG="$PROFILE/custom_configs/CUSA34384.json"
DUMPS="$PROFILE/shader/dumps"
LOGS="$HOME/projects/shadps4/logs"
O="$LOGS/gow101-correct-runtime-$(date +%Y%m%d-%H%M%S)-$$"
TMP="$PROFILE/.gow101-manual-$$"
P=""
RUNPID=""
C=""
HAD_CFG=0
HAD_DUMPS=0
PROFILE_MUTATED=0

fail() { echo "ERROR=$*" >&2; exit 1; }

[ -x "$B" ] || fail "missing binary: $B"
[ "$(sha256sum "$B" | awk '{print $1}')" = "$EXPECT" ] || fail "binary checksum changed"
[ -d "$CWD" ] && [ ! -L "$CWD" ] || fail "working directory missing/unsafe: $CWD"
if pgrep -u "$(id -u)" -x shadps4 >/dev/null 2>&1; then
  fail "shadps4 is already running"
fi

mkdir -p "$LOGS" "$O/screenshots" "$PROFILE/custom_configs" "$PROFILE/shader" "$TMP"
chmod 700 "$TMP"

cleanup() {
  status=$?
  set +e
  trap - EXIT INT TERM HUP
  if [ -n "$C" ]; then
    kill "$C" 2>/dev/null || true
    wait "$C" 2>/dev/null || true
  fi
  if [ -n "$P" ] && kill -0 "$P" 2>/dev/null; then
    kill -TERM -- "-$P" 2>/dev/null || kill -TERM "$P" 2>/dev/null || true
    for _ in 1 2 3 4 5 6 7; do
      kill -0 "$P" 2>/dev/null || break
      sleep 1
    done
    kill -KILL -- "-$P" 2>/dev/null || true
    wait "$P" 2>/dev/null || true
  fi

  if [ "$PROFILE_MUTATED" -eq 1 ]; then
    if [ -d "$DUMPS" ] && [ -f "$DUMPS/.gow101-owner" ] && [ "$(cat "$DUMPS/.gow101-owner" 2>/dev/null)" = "$$" ]; then
      rm -rf -- "$DUMPS"
    fi
    if [ "$HAD_DUMPS" -eq 1 ] && [ -d "$TMP/dumps.before" ]; then
      mv -- "$TMP/dumps.before" "$DUMPS"
    fi

    if [ "$HAD_CFG" -eq 1 ] && [ -f "$TMP/config.before" ]; then
      cp -a -- "$TMP/config.before" "$CFG"
    elif [ "$HAD_CFG" -eq 0 ]; then
      rm -f -- "$CFG"
    fi
  fi
  rm -rf -- "$TMP"
  exit "$status"
}
trap cleanup EXIT INT TERM HUP

if [ -e "$CFG" ]; then
  [ -f "$CFG" ] && [ ! -L "$CFG" ] || fail "unsafe per-game config: $CFG"
  cp -a -- "$CFG" "$TMP/config.before"
  HAD_CFG=1
fi
if [ -e "$DUMPS" ]; then
  [ -d "$DUMPS" ] && [ ! -L "$DUMPS" ] || fail "unsafe shader dump path: $DUMPS"
  HAD_DUMPS=1
fi

PROFILE_MUTATED=1
if [ "$HAD_DUMPS" -eq 1 ]; then
  mv -- "$DUMPS" "$TMP/dumps.before"
fi
mkdir -p "$DUMPS"
printf '%s\n' "$$" > "$DUMPS/.gow101-owner"

python3 - "$CFG" <<'PY'
import json, os, sys, tempfile
from pathlib import Path
p = Path(sys.argv[1])
if p.exists():
    obj = json.loads(p.read_text())
else:
    obj = {}
if not isinstance(obj, dict):
    raise SystemExit("per-game config is not a JSON object")
for name in ("GPU", "Vulkan"):
    if name in obj and not isinstance(obj[name], dict):
        raise SystemExit(f"invalid {name} section")
gpu = obj.setdefault("GPU", {})
gpu["direct_memory_access_enabled"] = True
gpu["dump_shaders"] = True
gpu["readbacks_mode"] = 0
vk = obj.setdefault("Vulkan", {})
vk["pipeline_cache_enabled"] = False
vk["pipeline_cache_archived"] = False
raw = (json.dumps(obj, indent=2, sort_keys=True) + "\n").encode()
fd, name = tempfile.mkstemp(prefix=".gow101-config-", dir=p.parent)
try:
    os.fchmod(fd, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(raw)
        f.flush()
        os.fsync(f.fileno())
    os.replace(name, p)
finally:
    try: os.unlink(name)
    except FileNotFoundError: pass
PY

{
  echo "binary=$B"
  echo "binary_sha256=$EXPECT"
  echo "working_directory=$CWD"
  echo "SHADPS4_GOW_SRT_AUTO_ROOTS=2"
  echo "readbacks_mode=0"
  echo "direct_memory_access_enabled=true"
  echo "pipeline_cache_enabled=false"
  echo "automatic_controller_input=false"
} > "$O/runtime-contract.txt"

cd "$CWD"
export SHADPS4_GOW_SRT_AUTO_ROOTS=2
export RADV_DEBUG=hang
export DISPLAY="${DISPLAY:-:0}"
[ -f "$HOME/.Xauthority" ] && export XAUTHORITY="${XAUTHORITY:-$HOME/.Xauthority}"

setsid "$B" --cpu-id-mode auto --game CUSA34384 --fullscreen true >"$O/console.log" 2>&1 &
P=$!
RUNPID=$P
echo "PID=$P"
echo "MANUAL_CONTROL=YES — enter New Game yourself; close shadPS4 when you are done."

(
  n=0
  while kill -0 "$P" 2>/dev/null; do
    sleep 10
    command -v xdotool >/dev/null 2>&1 || continue
    command -v xwininfo >/dev/null 2>&1 || continue
    command -v ffmpeg >/dev/null 2>&1 || continue
    W="$(xdotool search --pid "$P" 2>/dev/null | tail -n1 || true)"
    [ -n "$W" ] || continue
    I="$(xwininfo -id "$W" 2>/dev/null || true)"
    [ -n "$I" ] || continue
    X="$(printf '%s\n' "$I" | sed -n 's/.*Absolute upper-left X: *//p' | head -n1)"
    Y="$(printf '%s\n' "$I" | sed -n 's/.*Absolute upper-left Y: *//p' | head -n1)"
    WW="$(printf '%s\n' "$I" | sed -n 's/.*Width: *//p' | head -n1)"
    HH="$(printf '%s\n' "$I" | sed -n 's/.*Height: *//p' | head -n1)"
    case "${WW:-}:${HH:-}:${X:-}:${Y:-}" in
      *[!0-9:-]*|:::*) continue ;;
    esac
    [ "${WW:-0}" -gt 128 ] && [ "${HH:-0}" -gt 128 ] || continue
    n=$((n+1))
    ffmpeg -hide_banner -loglevel error -y -f x11grab \
      -video_size "${WW}x${HH}" -i "${DISPLAY}+${X},${Y}" \
      -frames:v 1 -q:v 3 "$O/screenshots/$(printf '%04d' "$n").jpg" </dev/null || true
  done
) &
C=$!

set +e
wait "$P"
RC=$?
set -e
P=""
kill "$C" 2>/dev/null || true
wait "$C" 2>/dev/null || true
C=""

echo "returncode=$RC" >> "$O/runtime-contract.txt"

mkdir -p "$O/radv"
found=0
for d in "$HOME"/radv_dumps_${RUNPID}_*; do
  [ -d "$d" ] || continue
  cp -a -- "$d" "$O/radv/"
  found=1
done
[ "$found" -eq 1 ] || rmdir "$O/radv" 2>/dev/null || true

tar -C "$LOGS" -czf "$O.tar.gz" "$(basename "$O")"
echo "UPLOAD=$O.tar.gz"
exit 0
