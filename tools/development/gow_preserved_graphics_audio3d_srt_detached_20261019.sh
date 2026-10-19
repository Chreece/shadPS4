#!/usr/bin/env bash
# Combined verified graphics/UFFD + Audio3D queue mutex + SRT shift source fixes.
# Detached from SSH. Exact pinned sources, reversible disposable build,
# preserved immutable graphics binaries and isolated original game saves.
set -Eeuo pipefail
ROOT="$HOME/projects/shadps4"
SCRIPTS="$ROOT/scripts"
LOGS="$ROOT/logs"
SELF="$SCRIPTS/gow_preserved_graphics_audio3d_srt_detached_20261019.sh"
BUILD_SCRIPT="$SCRIPTS/gow_preserved_graphics_audio3d_srt_builder_20261019.sh"
REF="5502a97dd18bc60e0d1331d8f0912749951e619f"
URL="https://raw.githubusercontent.com/Chreece/shadPS4/$REF"
mkdir -p "$SCRIPTS" "$LOGS"

if (( $# == 0 )); then
  [[ "$(readlink -f "$0")" == "$SELF" ]] || {
    echo "EXPECTED_PROJECT_SCRIPT_PATH=$SELF; NO_GAME_STARTED"; exit 2;
  }
  command -v nohup >/dev/null && command -v setsid >/dev/null || {
    echo "DETACH_COMMANDS_MISSING_NO_BUILD"; exit 2;
  }
  curl -4 -fLsS --retry 3 --retry-delay 2 \
     "$URL/tools/development/gow_audio3d_queue_isolated_launcher_20261019.sh" \
     -o "$BUILD_SCRIPT"
  [[ -f "$BUILD_SCRIPT" && ! -L "$BUILD_SCRIPT" &&
     "$(git hash-object "$BUILD_SCRIPT")" == "ca5d1719693ecadf8c5c26082ccf3ee51fd883ea" ]] || {
    echo "BUILD_LAUNCHER_BLOB_HASH_MISMATCH_NO_BUILD"; exit 2;
  }
  bash -n "$BUILD_SCRIPT" || {
    echo "BUILD_LAUNCHER_SHELL_SYNTAX_INVALID_NO_BUILD"; exit 2;
  }
  # Catch errors in the full ~900-line build runner BEFORE detaching or
  # touching any test source. The previous 10:20 trial failed here because
  # an Audio3D object-list entry had no trailing shell continuation.
  RUNNER_PREFLIGHT="$SCRIPTS/gow_preserved_graphics_audio3d_srt_full_runner_20261019.sh"
  curl -4 -fLsS --retry 3 --retry-delay 2 \
    "$URL/tools/development/gow_audio3d_queue_rebuild_and_one_run_20261019.sh" \
    -o "$RUNNER_PREFLIGHT"
  [[ -f "$RUNNER_PREFLIGHT" && ! -L "$RUNNER_PREFLIGHT" &&
     "$(git hash-object "$RUNNER_PREFLIGHT")" == "ba824a9fbfc7610d01af47c4c73683279240e5ed" ]] || {
    echo "FULL_RUNNER_PINNED_BLOB_MISMATCH_NO_BUILD"; exit 2;
  }
  bash -n "$RUNNER_PREFLIGHT" || {
    echo "FULL_RUNNER_SHELL_SYNTAX_INVALID_NO_BUILD"; exit 2;
  }
  echo "FULL_RUNNER_SYNTAX_CHECK=PASS_BEFORE_DETACH"
  # Before any detached worker/build exists, validate SRT on the protected
  # source that runner.sh later copies into the disposable test worktree.
  SRT_SOURCE="$ROOT/GodOfWarRagnarok/tests/native-rendered-gfx-20261016-152535-3014679/source/src/shader_recompiler/ir/passes/flatten_extended_userdata_pass.cpp"
  SRT_DIAGNOSTIC="$SCRIPTS/gow_srt_shift_surgical_overlay_20261019.py"
  curl -4 -fLsS --retry 3 --retry-delay 2 \
    "$URL/tools/development/gow_srt_shift_surgical_overlay_20261019.py" \
    -o "$SRT_DIAGNOSTIC"
  [[ -f "$SRT_DIAGNOSTIC" && ! -L "$SRT_DIAGNOSTIC" &&
     "$(git hash-object "$SRT_DIAGNOSTIC")" == "6ce589a3346a34326f1a46de5d44ec05f75802e8" &&
     -f "$SRT_SOURCE" && ! -L "$SRT_SOURCE" ]] || {
    echo "SRT_PROTECTED_SOURCE_OR_PINNED_OVERLAY_UNSAFE_NO_BUILD"; exit 2;
  }
  python3 -B "$SRT_DIAGNOSTIC" --self-test || {
    echo "SRT_OVERLAY_SELFTEST_FAILED_NO_BUILD"; exit 2;
  }
  SRT_PREFLIGHT_DIFF="$LOGS/gow-srt-replay-preflight-$(date +%Y%m%d-%H%M%S)-$.diff"
  python3 -B "$SRT_DIAGNOSTIC" "$SRT_SOURCE" "$SRT_PREFLIGHT_DIFF" || {
    echo "SRT_PROTECTED_REPLAY_SOURCE_NOT_RECOGNIZED_NO_BUILD"; exit 2;
  }
  echo "SRT_PROTECTED_REPLAY_PREFLIGHT=PASS_BEFORE_DETACH"
  echo "SRT_PREFLIGHT_EVIDENCE=$SRT_PREFLIGHT_DIFF"
  TAG="gow-preserved-audio3d-srt-worker-$(date +%Y%m%d-%H%M%S)-$"
  LOG="$LOGS/$TAG.log"
  [[ ! -e "$LOG" ]] || { echo "LOG_COLLISION_NO_BUILD"; exit 2; }
  nohup setsid -w bash "$SELF" --worker "$TAG" </dev/null >"$LOG" 2>&1 &
  job=$!
  printf '%s\n' "$job" >"$LOGS/$TAG.pid"
  disown "$job" 2>/dev/null || true
  sleep 2
  echo "DETACHED_PROVEN_GRAPHICS_AUDIO3D_SRT_BUILD=STARTED"
  echo "WORKER_PID=$job"
  echo "LIVE_LOG=$LOG"
  echo "EXIT_FILE=$LOGS/$TAG.exit"
  echo "RUNS=ONE_CANDIDATE_AFTER_SUCCESSFUL_ISOLATED_BUILD"
  echo "PROVEN_BINARY_SOURCE_SAVES_OLLAMA_SUNSHINE_SSH_PROTECTED=YES"
  echo "FOLLOW_LOG=tail -f $LOG"
  [[ ! -s "$LOGS/$TAG.exit" ]] || {
    echo "WORKER_ALREADY_EXITED=$(cat "$LOGS/$TAG.exit")"
    tail -60 "$LOG" || true
  }
  echo "SSH_SESSION_STILL_OPEN=YES"
  exit 0
fi

[[ $# == 2 && "$1" == "--worker" ]] || { echo "INVALID_WORKER_ARGS"; exit 2; }
TAG="$2"
[[ "$TAG" =~ ^gow-preserved-audio3d-srt-worker-[0-9]{8}-[0-9]{6}-[0-9]+$ ]] || {
  echo "INVALID_WORKER_TAG"; exit 2;
}
trap 'rc=$?; printf "%s\n" "$rc" >"$LOGS/$TAG.exit" || true' EXIT
echo "WORKER_LAUNCHED=$TAG"
echo "DETACHED_FROM_SSH=YES"
echo "CONTROLLED_BUILD=EXACT_PROVEN_GRAPHICS_PLUS_AUDIO3D_MUTEX_PLUS_SRT_SHIFT"
echo "GAME_RUNNER=ONE_A_DENSE_AUDIO_RUN_NO_B"
bash "$BUILD_SCRIPT"
echo "PROVEN_GRAPHICS_AUDIO3D_SRT_BUILD_AND_ONE_RUN_RETURNED=YES"
echo "SSH_SESSION_STILL_OPEN=YES"
