#!/usr/bin/env bash
# GoW Ragnarok: SINGLE navigation-calibration test after 21:47 right-wall failure.
# Never performs a second A/B run or modifies production binaries/source/saves.
set -Eeuo pipefail
ROOT="$HOME/projects/shadps4"
SCRIPTS="$ROOT/scripts"
LOGS="$ROOT/logs"
PROJECT="$ROOT/GodOfWarRagnarok/tests/native-rendered-gfx-20261016-152535-3014679"
BUILD="$ROOT/GodOfWarRagnarok/tests/gow-renderer-log-hotpath-20261017/build"
BINARY="$BUILD/shadps4.fullgfx-sparse-cpu-upload-gow-sparse-upload-20261018-201012"
SHA="7c2104500fe19dab6afe4b0967a0f0e4b568a3273501d2b3ea16970d9744fcfc"
REF="be303777eda596f3aeb391b2a4cc80fc951d6bfb"
URL="https://raw.githubusercontent.com/Chreece/shadPS4/$REF"
mkdir -p "$SCRIPTS" "$LOGS"
[[ -e "$PROJECT/source/.git" ]] || {
  echo "GOW_PROTECTED_SOURCE_MISSING_FAIL_CLOSED"; exit 2;
}
[[ -f "$BINARY" && ! -L "$BINARY" &&
   "$(sha256sum "$BINARY" | cut -d' ' -f1)" == "$SHA" ]] || {
  echo "GOW_PINNED_ALREADY_COMPILED_GAME_BINARY_MISSING_OR_CHANGED"; exit 2;
}
echo "EXISTING_GOW_BINARY_PINNED=PASS"
echo "GAMEPLAY_MODES_THIS_TRIAL=ONE_NAVIGATION_CALIBRATION_ONLY"
echo "NO_BUILD_NO_PRODUCTION_FILES_NO_GAME_SAVE_MODIFICATIONS=YES"
while read -r name blob remote; do
  dest="$SCRIPTS/$name"
  curl -4 -fLsS --retry 3 --retry-delay 2 "$URL/$remote" -o "$dest"
  got="$(git -C "$PROJECT/source" hash-object "$dest")"
  [[ "$got" == "$blob" ]] || {
    echo "SCRIPT_BLOB_VERIFICATION_FAILED=$name actual=$got expected=$blob"; exit 2;
  }
done <<'SOURCES'
gow_route_steering_20261018.py acec83a7655002554994bfdf9c57c7b13ad3510e tools/development/gow_route_steering_20261018.py
gow_auto_scene_input_20261018.py 350a65a9954ee16e912371a36da8415965e3bce9 tools/development/gow_auto_scene_input_20261018.py
gow_automated_scene_ab_20261018.py 2a0a68946dd6ff0f052ae18e15911fc1b2a5f748 tools/development/gow_automated_scene_ab_20261018.py
gow_ollama_vram_gameplay_20261018.py 35b9806f7eeae74e95e4738a25d88868b51fc325 tools/development/gow_ollama_vram_gameplay_20261018.py
SOURCES
python3 - "$SCRIPTS" <<'PY'
import pathlib,sys
names=('gow_route_steering_20261018.py','gow_auto_scene_input_20261018.py',
       'gow_automated_scene_ab_20261018.py','gow_ollama_vram_gameplay_20261018.py')
for name in names:
    f=pathlib.Path(sys.argv[1])/name
    compile(f.read_bytes(),str(f),"exec")
    print("GOW_PYTHON_SYNTAX=PASS",name)
PY
python3 -B "$SCRIPTS/gow_route_steering_20261018.py" --self-test
python3 -B "$SCRIPTS/gow_auto_scene_input_20261018.py" --self-test
TAG="gow-nav-calibration-$(date +%Y%m%d-%H%M%S)"
LOG="$LOGS/$TAG.log"
echo "FIRST_VERIFY_ACTIVE_GAMEPLAY_THEN_STEER=YES"
echo "STEERING_ROUTE=W+D_PULSES_W_ONLY_AND_W+A_RECOVERY"
echo "STEERING_TIMEOUT=115s; TEST_TIMEOUT=230s"
echo "INPUT_AND_AUDIOMETRICS_LOGGED_NO_IMAGE_COMPARISON=YES"
echo "CURRENT_SSH_AND_SUNSHINE_PRESERVED=YES"
echo "CAN_ABORT=Ctrl-C (restore existing Ollama policy)"
set +e
set -o pipefail
python3 -B -u "$SCRIPTS/gow_automated_scene_ab_20261018.py" \
 --ollama-helper "$SCRIPTS/gow_ollama_vram_gameplay_20261018.py" \
 --auto-input "$SCRIPTS/gow_auto_scene_input_20261018.py" \
 --binary "$BINARY" --sha256 "$SHA" \
 --out "$LOGS/$TAG" --cwd "$PROJECT" \
 --navigation-only --seconds 230 2>&1 | tee "$LOG"
code=$?
set -e
echo "NAVIGATION_CALIBRATION_PROCESS_RESULT=$code"
echo "UPLOAD_MAIN_LOG=$LOG"
grep -E 'ARCHIVE=|WORLD_READY=|STEERING|GOW_NAVIGATION_CALIBRATION_RESULT=|GOW_INPUT_AUTOMATION_RESULT=|GOW_NAVIGATION_CALIBRATION=|SOURCE_RESTORED|OLLAMA|NO_SCENE' "$LOG" | tail -30 || true
echo "SSH_SESSION_PRESERVED=YES"
exit "$code"
