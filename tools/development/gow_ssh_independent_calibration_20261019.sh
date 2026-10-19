#!/usr/bin/env bash
# Detached single GoW Continue/navigation calibration, independent from SSH.
# NO B/A-B trial. No source/build/game/save mutation. Always use verified binary.
set -Eeuo pipefail

ROOT="$HOME/projects/shadps4"
SCRIPTS="$ROOT/scripts"
LOGS="$ROOT/logs"
PROTECTED="$ROOT/GodOfWarRagnarok/tests/native-rendered-gfx-20261016-152535-3014679"
BUILD="$ROOT/GodOfWarRagnarok/tests/gow-renderer-log-hotpath-20261017/build"
BINARY="$BUILD/shadps4.fullgfx-sparse-cpu-upload-gow-sparse-upload-20261018-201012"
BINARY_SHA="7c2104500fe19dab6afe4b0967a0f0e4b568a3273501d2b3ea16970d9744fcfc"
REF="c289847ff96c22611d482814f41a8aeac0609ff8"
URL="https://raw.githubusercontent.com/Chreece/shadPS4/$REF"
SELF="$SCRIPTS/gow_ssh_independent_calibration_20261019.sh"

mkdir -p "$SCRIPTS" "$LOGS"
if (( $# == 0 )); then
  command -v setsid >/dev/null || { echo "SET_SID_NOT_INSTALLED_NO_GAME"; exit 2; }
  command -v nohup >/dev/null || { echo "NOHUP_NOT_INSTALLED_NO_GAME"; exit 2; }
  [[ "$SELF" == "$(readlink -f -- "$0")" ]] || {
    echo "LAUNCH_FROM_EXPECTED_PROJECT_SCRIPTS_PATH_ONLY=$SELF"; exit 2;
  }
  TAG="gow-ssh-safe-$(date +%Y%m%d-%H%M%S)-$$"
  LOG="$LOGS/$TAG.log"
  ( set -C; : >"$LOG" ) || { echo "LOG_ALREADY_EXISTS_NO_GAME=$LOG"; exit 2; }

  # The -w supervisor waits for child; nohup ignores HUP, setsid detaches.
  # All input/output redirected BEFORE emulator starts.
  nohup setsid -w bash "$SELF" --worker "$TAG" </dev/null >"$LOG" 2>&1 &
  supervisor=$!
  printf '%s\n' "$supervisor" >"$LOGS/$TAG.pid"
  disown "$supervisor" 2>/dev/null || true
  sleep 2
  echo "DETACHED_GOW_TEST=$TAG"
  echo "SUPERVISOR_PID=$supervisor"
  echo "LIVE_LOG=$LOG"
  echo "RESULT_FILE=$LOGS/$TAG.exit"
  echo "NO_B_RUN=YES STARTS_A_ONLY=YES"
  echo "YOU_CAN_MONITOR_WITH=tail -f $LOG"
  if [[ -s "$LOGS/$TAG.exit" ]]; then
    echo "EARLY_EXIT_RESULT=$(cat "$LOGS/$TAG.exit")"
    tail -25 "$LOG" || true
  else
    echo "BACKGROUND_TEST_STARTED_OR_PREFLIGHT_RUNNING=YES"
  fi
  echo "CURRENT_SSH_SHELL_STILL_OPEN=YES"
  exit 0
fi

[[ $# == 2 && "$1" == "--worker" ]] || { echo "WORKER_INVOCATION_INCORRECT"; exit 2; }
TAG="$2"
[[ "$TAG" =~ ^gow-ssh-safe-[0-9]{8}-[0-9]{6}-[0-9]+$ ]] || {
  echo "INVALID_WORKER_TAG"; exit 2;
}
LOG="$LOGS/$TAG.log"
STAGE="$SCRIPTS/$TAG"
trap 'rc=$?; printf "WORKER_EXIT_CODE=%s\n" "$rc" >"$LOGS/$TAG.exit" || true' EXIT

command -v flock >/dev/null || { echo "FLOCK_NOT_AVAILABLE_NO_GAME"; exit 2; }
exec 9>"$LOGS/gow-ssh-safe-calibration.lock"
flock -n 9 || { echo "ANOTHER_DETACHED_GOW_CALIBRATION_RUNNING_NO_GAME"; exit 2; }

[[ -e "$PROTECTED/source/.git" ]] || {
  echo "ORIGINAL_GOW_WORKTREE_MISSING_NO_GAME"; exit 2;
}
[[ -f "$BINARY" && ! -L "$BINARY" &&
   "$(sha256sum "$BINARY" | cut -d' ' -f1)" == "$BINARY_SHA" ]] || {
  echo "EXPERIMENTAL_BINARY_CHANGED_OR_MISSING_NO_GAME"; exit 2;
}
mkdir -m 700 "$STAGE"
echo "RUN_ID=$TAG"
echo "CHECKPOINT_BINARY_SHA_PINNED=PASS"
echo "MODE=ONE_DENSE_NAVIGATION_CALIBRATION_NO_B_GAME"
echo "DETACHED_PROCESS_NO_SSH_SIGHUP=YES"

while read -r name blob path; do
  destination="$STAGE/$name"
  curl -4 -fLsS --retry 3 --retry-delay 2 "$URL/$path" -o "$destination"
  actual="$(git hash-object "$destination")"
  if [[ "$actual" != "$blob" ]]; then
    echo "PINNED_SOURCE_HASH_MISMATCH=$name got=$actual expected=$blob"
    exit 2
  fi
done <<'PINNED'
gow_route_steering_20261018.py acec83a7655002554994bfdf9c57c7b13ad3510e tools/development/gow_route_steering_20261018.py
gow_auto_scene_input_20261018.py 01bc98787caa6ee69fcbfca1c97da1ed4e90ea01 tools/development/gow_auto_scene_input_20261018.py
gow_automated_scene_ab_20261018.py 4bae8205e0cf5a7eb013cb0289d2a98ef7ea7866 tools/development/gow_automated_scene_ab_20261018.py
gow_ollama_vram_gameplay_20261018.py 4c8385a5332e1e9c03431793fbf1d0a9f801f212 tools/development/gow_ollama_vram_gameplay_20261018.py
PINNED

python3 - "$STAGE" <<'PY'
from pathlib import Path
import sys
for file in sorted(Path(sys.argv[1]).glob("*.py")):
    compile(file.read_bytes(),str(file),"exec")
    print("PYTHON_COMPILE_PASS="+file.name,flush=True)
PY
python3 -B "$STAGE/gow_route_steering_20261018.py" --self-test
python3 -B "$STAGE/gow_auto_scene_input_20261018.py" --self-test
echo "SELF_TESTS_PASSED_BEFORE_GAME=YES"

# Runner verifies no emulator already exists before any Ollama action and
# restores Ollama if VRAM <8 GiB required unloading. Saves are isolated.
python3 -B -u "$STAGE/gow_automated_scene_ab_20261018.py" \
  --ollama-helper "$STAGE/gow_ollama_vram_gameplay_20261018.py" \
  --auto-input "$STAGE/gow_auto_scene_input_20261018.py" \
  --binary "$BINARY" --sha256 "$BINARY_SHA" \
  --out "$LOGS/$TAG" --cwd "$PROTECTED" \
  --navigation-only --seconds 230

echo "DETACHED_GOW_CALIBRATION_COMPLETE=YES"
echo "UPLOAD_ARCHIVE=$LOGS/$TAG.tar.gz"
echo "SSH_SUNSHINE_NOT_RESTARTED=YES"
