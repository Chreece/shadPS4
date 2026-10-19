#!/usr/bin/env bash
# GoW: gate an actual Audio3D queue correctness fix on real eboot NID imports.
# NO emulator launch, GPU build, SSH exit, service changes, or save modifications.
set -Eeuo pipefail
ROOT="$HOME/projects/shadps4"
SCRIPTS="$ROOT/scripts"
LOGS="$ROOT/logs"
GAME="/mnt/roms-all/ps4-games/CUSA34384"
REF="33d05a4a4405a55fb405f9308be499075fc7df86"
URL="https://raw.githubusercontent.com/Chreece/shadPS4/$REF"
GATE="$SCRIPTS/gow_audio3d_queue_import_and_source_gate_20261019.py"
SRC="$SCRIPTS/gow_audio3d_queue_candidate_20261019.cpp"
TAG="gow-audio3d-queue-$(date +%Y%m%d-%H%M%S)"
LOG="$LOGS/$TAG.log"
REPORT="$LOGS/$TAG.json"
mkdir -p "$SCRIPTS" "$LOGS"
[[ -d "$GAME" ]] || { echo "GOW_INSTALLATION_NOT_FOUND_NO_GAME_STARTED"; exit 2; }

curl -4 -fLsS --retry 3 --retry-delay 2 \
  "$URL/tools/development/gow_audio3d_queue_import_and_source_gate_20261019.py" -o "$GATE"
curl -4 -fLsS --retry 3 --retry-delay 2 \
  "$URL/src/core/libraries/audio3d/audio3d.cpp" -o "$SRC"
[[ ! -L "$GATE" && ! -L "$SRC" ]] || { echo "SYMLINK_SOURCE_REJECTED"; exit 2; }
[[ "$(git hash-object "$GATE")" == "aa2d37025d16e25b45aa07b2d3459fbdfe788ec2" &&
   "$(git hash-object "$SRC")" == "45320f60d1565ace053b794335bd48c2e1a10edf" ]] || {
  echo "GITHUB_SOURCE_BLOB_VERIFICATION_FAILED_NO_BUILD"; exit 2;
}
python3 -m py_compile "$GATE"
python3 -B "$GATE" --self-test

echo "PROVEN_GRAPHICS_CHECKPOINT=UNTOUCHED"
echo "SOURCE_FIX_ALREADY_COMMITTED=PortAdvance_serializes_shared_PCM_and_mix_queues"
echo "ONLY_RELEVANT_GAME_EXECUTABLE_IMPORTS_WILL_JUSTIFY_BUILD"
set +e
set -o pipefail
python3 -B "$GATE" --game-root "$GAME" --source "$SRC" --out "$REPORT" \
  2>&1 | tee "$LOG"
code=$?
set -e
echo "IMPORT_GATE_EXIT=$code"
echo "UPLOAD_LOG=$LOG"
echo "UPLOAD_JSON=$REPORT"
echo "NO_BUILD_NO_GAME_RUN_NO_OLLAMA_SHUTDOWN=YES"
echo "SSH_SESSION_REMAINS_OPEN=YES"
exit "$code"
