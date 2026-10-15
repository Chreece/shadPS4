#!/usr/bin/env bash
set -Eeuo pipefail

B="$HOME/projects/shadps4/GodOfWarRagnarok/tests/gow91-build-20261015-053223-1000248/shadps4"
EXPECT="7d95059004bc9db11bead8eb0408eea8dc3c24398e52ffc97b2a93dae1147f24"
CWD="$HOME/projects/shadps4/GodOfWarRagnarok/runtime/gow91"
PROFILE="$HOME/.local/share/shadPS4"
CFG="$PROFILE/custom_configs/CUSA34384.json"
LOGS="$HOME/projects/shadps4/logs"

P=""
C=""
RUNPID=""
STOP_REQUESTED=0
PROFILE_MUTATED=0
HAD_CFG=0
TMP=""

fail() { echo "ERROR=$*" >&2; exit 1; }

patch_config() {
  local cfg="$1"
  python3 - "$cfg" <<'PY'
import json, os, sys, tempfile
from pathlib import Path
p = Path(sys.argv[1])
obj = json.loads(p.read_text()) if p.exists() else {}
if not isinstance(obj, dict):
    raise SystemExit("per-game config is not a JSON object")
for section in ("GPU", "Vulkan"):
    if section in obj and not isinstance(obj[section], dict):
        raise SystemExit(f"invalid {section} section")
gpu = obj.setdefault("GPU", {})
gpu["direct_memory_access_enabled"] = True
gpu["dump_shaders"] = False
gpu["readbacks_mode"] = 0
vk = obj.setdefault("Vulkan", {})
# Force fresh translation for this first GOW102 correctness run. Shader dumping
# is disabled, so this is much lighter than the prior diagnostic launcher.
vk["pipeline_cache_enabled"] = False
vk["pipeline_cache_archived"] = False
raw = (json.dumps(obj, indent=2, sort_keys=True) + "\n").encode()
p.parent.mkdir(parents=True, exist_ok=True)
fd, name = tempfile.mkstemp(prefix=".gow102-config-", dir=p.parent)
try:
    os.fchmod(fd, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(raw)
        f.flush()
        os.fsync(f.fileno())
    os.replace(name, p)
finally:
    try:
        os.unlink(name)
    except FileNotFoundError:
        pass
PY
}

if [[ "${1:-}" == "--self-test" ]]; then
  T="$(mktemp -d)"
  trap 'rm -rf -- "$T"' EXIT
  TESTCFG="$T/custom_configs/CUSA34384.json"
  mkdir -p "$(dirname "$TESTCFG")"
  printf '%s\n' '{"GPU":{"direct_memory_access_enabled":false,"dump_shaders":true,"readbacks_mode":2},"Vulkan":{"pipeline_cache_enabled":true,"pipeline_cache_archived":true}}' >"$TESTCFG"
  patch_config "$TESTCFG"
  python3 - "$TESTCFG" <<'PY'
import json, sys
x=json.load(open(sys.argv[1]))
assert x["GPU"]["direct_memory_access_enabled"] is True
assert x["GPU"]["dump_shaders"] is False
assert x["GPU"]["readbacks_mode"] == 0
assert x["Vulkan"]["pipeline_cache_enabled"] is False
assert x["Vulkan"]["pipeline_cache_archived"] is False
PY
  grep -q 'SHADPS4_GOW_SRT_AUTO_ROOTS=2' "$0"
  grep -q 'automatic_controller_input=false' "$0"
  echo "GOW102_LAUNCHER_SELF_TEST=PASS config mutation, manual-input contract, SRT roots"
  exit 0
fi

[ -x "$B" ] || fail "missing binary: $B"
[ "$(sha256sum "$B" | awk '{print $1}')" = "$EXPECT" ] || fail "binary checksum changed"
[ -d "$CWD" ] && [ ! -L "$CWD" ] || fail "working directory missing/unsafe: $CWD"
if pgrep -u "$(id -u)" -x shadps4 >/dev/null 2>&1; then
  fail "shadps4 is already running"
fi

mkdir -p "$LOGS" "$PROFILE/custom_configs"
O="$LOGS/gow102-manual-$(date +%Y%m%d-%H%M%S)-$$"
TMP="$PROFILE/.gow102-manual-$$"
mkdir -p "$O/screenshots" "$TMP"
chmod 700 "$TMP"

restore_profile() {
  set +e
  if [ "$PROFILE_MUTATED" -eq 1 ]; then
    if [ "$HAD_CFG" -eq 1 ] && [ -f "$TMP/config.before" ]; then
      cp -a -- "$TMP/config.before" "$CFG"
    elif [ "$HAD_CFG" -eq 0 ]; then
      rm -f -- "$CFG"
    fi
  fi
  [ -n "$TMP" ] && rm -rf -- "$TMP"
}

terminate_game() {
  set +e
  if [ -n "$P" ] && kill -0 "$P" 2>/dev/null; then
    kill -TERM -- "-$P" 2>/dev/null || kill -TERM "$P" 2>/dev/null || true
    for _ in 1 2 3 4 5 6 7; do
      kill -0 "$P" 2>/dev/null || break
      sleep 1
    done
    if kill -0 "$P" 2>/dev/null; then
      kill -KILL -- "-$P" 2>/dev/null || kill -KILL "$P" 2>/dev/null || true
    fi
  fi
}

request_stop() {
  STOP_REQUESTED=1
  echo
  echo "STOP_REQUESTED=YES — stopping shadPS4, packaging evidence..."
  terminate_game
}

cleanup() {
  status=$?
  set +e
  trap - EXIT INT TERM HUP
  if [ -n "$C" ]; then
    kill "$C" 2>/dev/null || true
    wait "$C" 2>/dev/null || true
  fi
  terminate_game
  restore_profile
  exit "$status"
}

trap cleanup EXIT
trap request_stop INT TERM HUP

if [ -e "$CFG" ]; then
  [ -f "$CFG" ] && [ ! -L "$CFG" ] || fail "unsafe per-game config: $CFG"
  cp -a -- "$CFG" "$TMP/config.before"
  HAD_CFG=1
fi
PROFILE_MUTATED=1
patch_config "$CFG"

{
  echo "binary=$B"
  echo "binary_sha256=$EXPECT"
  echo "working_directory=$CWD"
  echo "SHADPS4_GOW_SRT_AUTO_ROOTS=2"
  echo "readbacks_mode=0"
  echo "direct_memory_access_enabled=true"
  echo "dump_shaders=false"
  echo "pipeline_cache_enabled=false"
  echo "automatic_controller_input=false"
} >"$O/runtime-contract.txt"

cd "$CWD"
export SHADPS4_GOW_SRT_AUTO_ROOTS=2
export RADV_DEBUG=hang
export DISPLAY="${DISPLAY:-:0}"
[ -f "$HOME/.Xauthority" ] && export XAUTHORITY="${XAUTHORITY:-$HOME/.Xauthority}"

setsid "$B" --cpu-id-mode auto --game CUSA34384 --fullscreen true >"$O/console.log" 2>&1 &
P=$!
RUNPID=$P
echo "PID=$P"
echo "MANUAL_CONTROL=YES"
echo "Use Ctrl+C here when you want to stop; the launcher will package the run."

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

{
  echo "returncode=$RC"
  echo "stop_requested=$STOP_REQUESTED"
  echo "srt_coherency_readbacks=$(grep -c 'SRT_COHERENCY_READBACK' "$O/console.log" 2>/dev/null || true)"
  echo "phi_offset_errors=$(grep -c 'Unexpected instruction for offset computation, Phi' "$O/console.log" 2>/dev/null || true)"
  echo "sharp_unflattened=$(grep -c 'Sharp source was not flatenned' "$O/console.log" 2>/dev/null || true)"
} >>"$O/runtime-contract.txt"

mkdir -p "$O/radv"
found=0
for d in "$HOME"/radv_dumps_${RUNPID}_*; do
  [ -d "$d" ] || continue
  cp -a -- "$d" "$O/radv/"
  found=1
done
[ "$found" -eq 1 ] || rmdir "$O/radv" 2>/dev/null || true

restore_profile
PROFILE_MUTATED=0

tar -C "$LOGS" -czf "$O.tar.gz" "$(basename "$O")"
echo "UPLOAD=$O.tar.gz"

trap - EXIT INT TERM HUP
rm -rf -- "$TMP" 2>/dev/null || true
exit 0
