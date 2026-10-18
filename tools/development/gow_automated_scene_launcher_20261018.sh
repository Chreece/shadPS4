#!/usr/bin/env bash
# GoW Ragnarök: automate Continue Cross and hold forward+right while navigating.
# Same pre-existing binary, same save snapshot, dense/optimized log-only A/B.
# No rebuild; no game-file modifications; preserved graphics/SSH/Sunshine.
set -Eeuo pipefail
BASE="$HOME/projects/shadps4"
SCRIPTS="$BASE/scripts"
LOGS="$BASE/logs"
PROJECT="$BASE/GodOfWarRagnarok/tests/native-rendered-gfx-20261016-152535-3014679"
BUILD="$BASE/GodOfWarRagnarok/tests/gow-renderer-log-hotpath-20261017/build"
BINARY="$BUILD/shadps4.fullgfx-sparse-cpu-upload-gow-sparse-upload-20261018-201012"
SHA="7c2104500fe19dab6afe4b0967a0f0e4b568a3273501d2b3ea16970d9744fcfc"
REF="3e2f4ac47fd2251963f0d52e5afdf580e5c5bb23"
URL="https://raw.githubusercontent.com/Chreece/shadPS4/$REF"
mkdir -p "$SCRIPTS" "$LOGS"
[[ -e "$PROJECT/source/.git" ]] || {
  echo "PROTECTED_GOW_SOURCE_ABSENT_NO_CHANGES"; exit 2;
}
[[ -f "$BINARY" && ! -L "$BINARY" &&
   "$(sha256sum "$BINARY" | cut -d' ' -f1)" == "$SHA" ]] || {
  echo "PINNED_AUTOMATED_AB_GAME_BINARY_MISSING_OR_CHANGED"; exit 2;
}
echo "SAME_ALREADY_COMPILED_GPUWRITE_TEST_BINARY=SHA_VERIFIED"
echo "NO_REBUILD_NO_PRODUCTION_CHANGES=YES"
while read -r name blob remote_path; do
  target="$SCRIPTS/$name"
  curl -4 -fLsS --retry 3 --retry-delay 2 "$URL/$remote_path" -o "$target"
  actual="$(git -C "$PROJECT/source" hash-object "$target")"
  [[ "$actual" == "$blob" ]] || {
    echo "SCRIPT_CHECKSUM_MISMATCH=$name expected=$blob actual=$actual"
    exit 2
  }
done <<'SOURCES'
gow_auto_scene_input_20261018.py edab90ac0f179e89e7285ac73d4f8698c8ecf953 tools/development/gow_auto_scene_input_20261018.py
gow_automated_scene_ab_20261018.py 62ef63d6a0f27bdbf2f21f4e87a537e50188e6f5 tools/development/gow_automated_scene_ab_20261018.py
gow_ollama_vram_gameplay_20261018.py 35b9806f7eeae74e95e4738a25d88868b51fc325 tools/development/gow_ollama_vram_gameplay_20261018.py
SOURCES
python3 - "$SCRIPTS" <<'PY'
import pathlib,sys
for path in sorted(pathlib.Path(sys.argv[1]).glob("gow_*20261018.py")):
    if path.name not in {
       'gow_auto_scene_input_20261018.py',
       'gow_automated_scene_ab_20261018.py',
       'gow_ollama_vram_gameplay_20261018.py',
    }:
        continue
    compile(path.read_bytes(),str(path),"exec")
    print("PYTHON_SOURCE_COMPILES=PASS",path.name)
PY
AUTO="$SCRIPTS/gow_auto_scene_input_20261018.py"
RUNNER="$SCRIPTS/gow_automated_scene_ab_20261018.py"
OLLAMA="$SCRIPTS/gow_ollama_vram_gameplay_20261018.py"
python3 -B "$AUTO" --self-test
# A/B runner checks the X11/XTest DISPLAY before any emulator or Ollama action.
TAG="gow-auto-scene-$(date +%Y%m%d-%H%M%S)"
LOG="$LOGS/$TAG.log"
echo "GAMEPLAY_INPUT_MODE=AUTOMATED_CROSS_AND_FORWARD_PLUS_RIGHT"
echo "RUNS=DENSE_THEN_GPUWRITE_SUMMARY_SAME_BINARY"
echo "SCENE_PROOF=NOT_CLAIMED; LOG_COUNTERS_ONLY"
echo "AUTOMATED_INPUT_FORWARD_PLUS_RIGHT_NO_HUMAN_TIMING=YES"
echo "NO_IMAGE_COMPARISON_OR_PERIODIC_CAPTURE=YES"
echo "IDENTICAL_TEMPORARY_PER_RUN_SAVES=YES; ORIGINAL_SAVES_STAY_UNTOUCHED"
echo "CAN_ABORT=Ctrl-C; owned emulator stops and Ollama restoration runs"
set +e
set -o pipefail
python3 -B -u "$RUNNER" \
  --ollama-helper "$OLLAMA" \
  --auto-input "$AUTO" \
  --binary "$BINARY" \
  --sha256 "$SHA" \
  --out "$LOGS/$TAG" \
  --cwd "$PROJECT" \
  --seconds 360 2>&1 | tee "$LOG"
ret=$?
set -e
echo "AUTOMATED_GAMEPLAY_EXIT_CODE=$ret"
echo "UPLOAD_MAIN_LOG=$LOG"
grep -E 'ARCHIVE=|AUTO_STAGE=|CUTSCENE_VISUAL|GAME_EXITED_BEFORE_OLLAMA|OLLAMA_MODELS_RELOADED|GOW_SPARSE_CPU_UPLOAD_AB_RESULT' "$LOG" | tail -20 || true
echo "SSH_SESSION_NOT_CLOSED=YES"
exit "$ret"
