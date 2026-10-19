#!/usr/bin/env bash
# GoW protected-source four-file permutation candidate: one detached, isolated gameplay run.
# Proven automation; no second run, no saves/binary modifications, conditional Ollama restoration.
set -Eeuo pipefail

ROOT="$HOME/projects/shadps4"
SCRIPTS="$ROOT/scripts"
LOGS="$ROOT/logs"
PROTECTED="$ROOT/GodOfWarRagnarok/tests/native-rendered-gfx-20261016-152535-3014679"
BUILD="$ROOT/GodOfWarRagnarok/tests/permutation-protected-20261019-220440/build"
BINARY="$BUILD/shadps4"
BINARY_SHA="2205aeeebc139bab4f2048882799da42447f93b8338583ec26afa87673b34af7"
REF="c289847ff96c22611d482814f41a8aeac0609ff8"
URL="https://raw.githubusercontent.com/Chreece/shadPS4/$REF"
SELF="$SCRIPTS/gow_permutation_protected_scene_20261019.sh"

mkdir -p "$SCRIPTS" "$LOGS"
if (( $# == 0 )); then
  command -v setsid >/dev/null || { echo "SET_SID_NOT_INSTALLED_NO_GAME"; exit 2; }
  command -v nohup >/dev/null || { echo "NOHUP_NOT_INSTALLED_NO_GAME"; exit 2; }
  [[ "$SELF" == "$(readlink -f -- "$0")" ]] || {
    echo "LAUNCH_FROM_EXPECTED_PROJECT_SCRIPTS_PATH_ONLY=$SELF"; exit 2;
  }
  TAG="gow-permutation-protected-scene-$(date +%Y%m%d-%H%M%S)-$$"
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
  echo "ONE_CANDIDATE_RUN_ONLY=YES SHADER_PERMUTATION_TRACE=ON"
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
[[ "$TAG" =~ ^gow-permutation-protected-scene-[0-9]{8}-[0-9]{6}-[0-9]+$ ]] || {
  echo "INVALID_WORKER_TAG"; exit 2;
}
LOG="$LOGS/$TAG.log"
STAGE="$SCRIPTS/$TAG"
trap 'rc=$?; printf "WORKER_EXIT_CODE=%s\n" "$rc" >"$LOGS/$TAG.exit" || true' EXIT

command -v flock >/dev/null || { echo "FLOCK_NOT_AVAILABLE_NO_GAME"; exit 2; }
exec 9>"$LOGS/gow-permutation-scene-calibration.lock"
flock -n 9 || { echo "ANOTHER_DETACHED_GOW_CALIBRATION_RUNNING_NO_GAME"; exit 2; }

[[ -e "$PROTECTED/source/.git" ]] || {
  echo "ORIGINAL_GOW_WORKTREE_MISSING_NO_GAME"; exit 2;
}
[[ -f "$BINARY" && ! -L "$BINARY" &&
   "$(sha256sum "$BINARY" | cut -d' ' -f1)" == "$BINARY_SHA" ]] || {
  echo "EXPERIMENTAL_BINARY_CHANGED_OR_MISSING_NO_GAME"; exit 2;
}
FIX_SOURCE="$ROOT/GodOfWarRagnarok/tests/permutation-protected-20261019-220440/source"
FIX_HEAD="e6ca28e7386faa0624c1421a5dc0585eeb028532"
BASE_SOURCE="$ROOT/GodOfWarRagnarok/gow-stage25-restored-20261011-7bpufvsl/source"
BASE_HEAD="3965a497532edb7b18027692ab04eb97fa4c145b"
[[ "$(git -C "$FIX_SOURCE" rev-parse HEAD 2>/dev/null)" == "$FIX_HEAD" ]] || {
  echo "CANDIDATE_SOURCE_REVISION_CHANGED_NO_GAME"; exit 2;
}
[[ "$(git -C "$BASE_SOURCE" rev-parse HEAD 2>/dev/null)" == "$BASE_HEAD" ]] || {
  echo "PROTECTED_SOURCE_REVISION_CHANGED_NO_GAME"; exit 2;
}
echo "CANDIDATE_FOUR_FILE_SOURCE_AND_PROTECTED_SOURCE_SHA_PINNED=PASS"

PROTECTED_BIN="$ROOT/GodOfWarRagnarok/build/shadps4"
PROTECTED_SHA="8de2106a3e0d410904d9c8d6a2903ba6f1f65986af3c848b0bf274a4f4b45c8e"
[[ -f "$PROTECTED_BIN" && ! -L "$PROTECTED_BIN" &&
   "$(sha256sum "$PROTECTED_BIN" | cut -d' ' -f1)" == "$PROTECTED_SHA" ]] || {
  echo "PROTECTED_BINARY_HASH_MISMATCH_NO_GAME"; exit 2;
}
echo "PROTECTED_BASELINE_SHA_PINNED=PASS"

mkdir -m 700 "$STAGE"
echo "RUN_ID=$TAG"
echo "CHECKPOINT_BINARY_SHA_PINNED=PASS"
echo "MODE=ONE_ISOLATED_PERMUTATION_SCENE_TRACE"
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

python3 -B - "$STAGE/gow_automated_scene_ab_20261018.py" <<'PYTRACE'
import pathlib,sys
p=pathlib.Path(sys.argv[1])
src=p.read_text()
anchor='            "SHADPS4_FRAME_AUDIO_PROBE": "1",\n'
if src.count(anchor)!=1 or "SHADPS4_PERMUTATION_TRACE" in src:
    raise SystemExit("PINNED_AUTOMATION_TRACE_PATCH_ANCHOR_MISMATCH_NO_GAME")
p.write_text(src.replace(anchor,anchor+'            "SHADPS4_PERMUTATION_TRACE": "1",\n'))
print("PERMUTATION_TRACE_IN_ACTUAL_GAME_ENV=PASS")
PYTRACE

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
set +e
python3 -B -u "$STAGE/gow_automated_scene_ab_20261018.py" \
  --ollama-helper "$STAGE/gow_ollama_vram_gameplay_20261018.py" \
  --auto-input "$STAGE/gow_auto_scene_input_20261018.py" \
  --binary "$BINARY" --sha256 "$BINARY_SHA" \
  --out "$LOGS/$TAG" --cwd "$PROTECTED" \
  --navigation-only --seconds 230
RUN_RC=$?
set -e

CONSOLE="$LOGS/$TAG/A_DENSE_CPU_UPLOAD/full-console.log"
SUMMARY="$LOGS/$TAG/permutation-scene-summary.txt"
if [[ -f "$CONSOLE" ]]; then
  {
    echo '=== REAL GOW PERMUTATION TRACE ==='
    echo "PERM_TRACE_ACTIVE=$(grep -c 'PERM_TRACE_ACTIVE' "$CONSOLE" || true)"
    echo "PERM_TRACE_CREATED=$(grep -c 'PERM_TRACE_CREATED' "$CONSOLE" || true)"
    echo "PERM_TRACE_REUSED=$(grep -c 'PERM_TRACE_REUSED' "$CONSOLE" || true)"
    echo "PERM_TRACE_COUNT_DIFFERENCES=$(grep 'PERM_TRACE_CREATED' "$CONSOLE" | grep -Ec 'different_counts=(true|1)' || true)"
    echo "DEVICE_LOSS_EVENTS=$(grep -c 'VK_ERROR_DEVICE_LOST' "$CONSOLE" || true)"
    echo 'SCENE_EQUIVALENCE_PROVEN=NO'
    echo 'GRAPHICS_IMPROVEMENT_PROVEN=NO'
    echo "RUNNER_EXIT_CODE=$RUN_RC"
    echo 'FIRST_PERMUTATION_TRACE_LINES:'
    grep -E 'PERM_TRACE_(ACTIVE|CREATED|REUSED)' "$CONSOLE" | head -60 || true
  } > "$SUMMARY"
  cat "$SUMMARY"
else
  echo "FULL_CONSOLE_MISSING_NO_SCENE_CLAIM"
fi
if [[ -d "$LOGS/$TAG" ]]; then
  tar -czf "$LOGS/$TAG.tar.gz" -C "$LOGS" "$TAG"
fi
[[ "$(sha256sum "$BINARY" | cut -d' ' -f1)" == "$BINARY_SHA" ]] || {
  echo "CANDIDATE_BINARY_CHANGED_DURING_RUN"; exit 3;
}
[[ "$(sha256sum "$PROTECTED_BIN" | cut -d' ' -f1)" == "$PROTECTED_SHA" ]] || {
  echo "PROTECTED_BINARY_CHANGED_DURING_RUN"; exit 3;
}
[[ "$(git -C "$FIX_SOURCE" rev-parse HEAD 2>/dev/null)" == "$FIX_HEAD" &&
   "$(git -C "$BASE_SOURCE" rev-parse HEAD 2>/dev/null)" == "$BASE_HEAD" ]] || {
  echo "PROTECTED_OR_CANDIDATE_SOURCE_HEAD_CHANGED_DURING_RUN"; exit 3;
}
echo "PROTECTED_AND_CANDIDATE_BINARIES_SHA_UNCHANGED=YES"
echo "ORIGINAL_AND_CANDIDATE_SOURCE_REVISIONS_UNCHANGED=YES"
echo "GAME_RUN_EXIT_CODE=$RUN_RC"
echo "UPLOAD_ARCHIVE=$LOGS/$TAG.tar.gz"
echo "SSH_SUNSHINE_NOT_RESTARTED=YES"
exit "$RUN_RC"
