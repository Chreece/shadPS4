#!/usr/bin/env bash
# One source-modified GoW Audio3D PortAdvance mutex build and ONE game run.
# Detached from SSH. Exact pinned sources, reversible disposable build,
# preserved immutable graphics binaries and isolated original game saves.
set -Eeuo pipefail
ROOT="$HOME/projects/shadps4"
SCRIPTS="$ROOT/scripts"
LOGS="$ROOT/logs"
SELF="$SCRIPTS/gow_audio3d_queue_detached_20261019.sh"
BUILD_SCRIPT="$SCRIPTS/gow_audio3d_queue_isolated_launcher_20261019.sh"
REF="64d593036c78883a7044401ea7325ddfbf7c8987"
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
     "$(git hash-object "$BUILD_SCRIPT")" == "f8a2a5ebe56921308577504de12a3c0012bdf8b0" ]] || {
    echo "BUILD_LAUNCHER_BLOB_HASH_MISMATCH_NO_BUILD"; exit 2;
  }
  bash -n "$BUILD_SCRIPT" || {
    echo "BUILD_LAUNCHER_SHELL_SYNTAX_INVALID_NO_BUILD"; exit 2;
  }
  # Catch errors in the full ~900-line build runner BEFORE detaching or
  # touching any test source. The previous 10:20 trial failed here because
  # an Audio3D object-list entry had no trailing shell continuation.
  RUNNER_PREFLIGHT="$SCRIPTS/gow_audio3d_queue_rebuild_and_one_run_20261019.sh"
  curl -4 -fLsS --retry 3 --retry-delay 2 \
    "$URL/tools/development/gow_audio3d_queue_rebuild_and_one_run_20261019.sh" \
    -o "$RUNNER_PREFLIGHT"
  [[ -f "$RUNNER_PREFLIGHT" && ! -L "$RUNNER_PREFLIGHT" &&
     "$(git hash-object "$RUNNER_PREFLIGHT")" == "bb7642d37222b1b54b85c4037d5df5baf029b1ab" ]] || {
    echo "FULL_RUNNER_PINNED_BLOB_MISMATCH_NO_BUILD"; exit 2;
  }
  bash -n "$RUNNER_PREFLIGHT" || {
    echo "FULL_RUNNER_SHELL_SYNTAX_INVALID_NO_BUILD"; exit 2;
  }
  echo "FULL_RUNNER_SYNTAX_CHECK=PASS_BEFORE_DETACH"
  TAG="gow-audio3d-build-worker-$(date +%Y%m%d-%H%M%S)-$$"
  LOG="$LOGS/$TAG.log"
  [[ ! -e "$LOG" ]] || { echo "LOG_COLLISION_NO_BUILD"; exit 2; }
  nohup setsid -w bash "$SELF" --worker "$TAG" </dev/null >"$LOG" 2>&1 &
  job=$!
  printf '%s\n' "$job" >"$LOGS/$TAG.pid"
  disown "$job" 2>/dev/null || true
  sleep 2
  echo "DETACHED_PORTADVANCE_MUTEX_BUILD=STARTED"
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
[[ "$TAG" =~ ^gow-audio3d-build-worker-[0-9]{8}-[0-9]{6}-[0-9]+$ ]] || {
  echo "INVALID_WORKER_TAG"; exit 2;
}
trap 'rc=$?; printf "%s\n" "$rc" >"$LOGS/$TAG.exit" || true' EXIT
echo "WORKER_LAUNCHED=$TAG"
echo "DETACHED_FROM_SSH=YES"
echo "CONTROLLED_BUILD=EXACT_PROVEN_GRAPHICS_PLUS_ONE_AUDIO3D_MUTEX_ONLY"
echo "GAME_RUNNER=ONE_A_DENSE_AUDIO_RUN_NO_B"
bash "$BUILD_SCRIPT"
echo "AUDIO3D_MUTEX_BUILD_AND_ONE_RUN_RETURNED=YES"
echo "SSH_SESSION_STILL_OPEN=YES"
