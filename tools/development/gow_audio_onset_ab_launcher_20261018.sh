#!/usr/bin/env bash
# GoW Ragnarok: event-aligned DENSE vs GPU-write-aware CPU scan A/B.
# Exact same binary, isolated same starting saves and proven automatic route.
# Compare AUDIO3D starvation onset and per-call CPU costs; NEVER images.
set -Eeuo pipefail
ROOT="$HOME/projects/shadps4"
SCRIPTS="$ROOT/scripts"
LOGS="$ROOT/logs"
PROJECT="$ROOT/GodOfWarRagnarok/tests/native-rendered-gfx-20261016-152535-3014679"
BUILD="$ROOT/GodOfWarRagnarok/tests/gow-renderer-log-hotpath-20261017/build"
BINARY="$BUILD/shadps4.fullgfx-sparse-cpu-upload-gow-sparse-upload-20261018-201012"
SHA="7c2104500fe19dab6afe4b0967a0f0e4b568a3273501d2b3ea16970d9744fcfc"
REF="7d852b24231a1ef976cb2c709fffb88b96c02562"
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
echo "GAMEPLAY_MODES_THIS_TRIAL=DENSE_THEN_GPUWRITE_SUMMARY_AUDIO_ANCHORED"
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
gow_audio_transition_ab_report_20261018.py 40dbb9bfa8bacb5a3435344381d5891230bd7d57 tools/development/gow_audio_transition_ab_report_20261018.py
SOURCES
python3 - "$SCRIPTS" <<'PY'
import pathlib,sys
names=('gow_route_steering_20261018.py','gow_auto_scene_input_20261018.py',
       'gow_automated_scene_ab_20261018.py','gow_ollama_vram_gameplay_20261018.py',
       'gow_audio_transition_ab_report_20261018.py')
for name in names:
    f=pathlib.Path(sys.argv[1])/name
    compile(f.read_bytes(),str(f),"exec")
    print("GOW_PYTHON_SYNTAX=PASS",name)
PY
python3 -B "$SCRIPTS/gow_route_steering_20261018.py" --self-test
python3 -B "$SCRIPTS/gow_auto_scene_input_20261018.py" --self-test
TAG="gow-audio-onset-ab-$(date +%Y%m%d-%H%M%S)"
LOG="$LOGS/$TAG.log"
echo "FIRST_VERIFY_ACTIVE_GAMEPLAY_THEN_STEER=YES"
echo "STEERING_ROUTE=W+D_PULSES_W_ONLY_AND_W+A_RECOVERY"
echo "STEERING_TIMEOUT=115s; TEST_TIMEOUT_PER_RUN=275s"
echo "COMPARE_BY_FIRST_SUSTAINED_AUDIO3D_STARVATION_NOT_STARTUP_TIME=YES"
echo "CURRENT_SSH_AND_SUNSHINE_PRESERVED=YES"
echo "CAN_ABORT=Ctrl-C (restore existing Ollama policy)"
set +e
set -o pipefail
python3 -B -u "$SCRIPTS/gow_automated_scene_ab_20261018.py" \
 --ollama-helper "$SCRIPTS/gow_ollama_vram_gameplay_20261018.py" \
 --auto-input "$SCRIPTS/gow_auto_scene_input_20261018.py" \
 --binary "$BINARY" --sha256 "$SHA" \
 --out "$LOGS/$TAG" --cwd "$PROJECT" \
 --seconds 275 2>&1 | tee "$LOG"
code=$?
set -e
echo "AUDIO_ANCHORED_AB_PROCESS_RESULT=$code"
if [[ -f "$LOGS/$TAG/report.json" ]]; then
  python3 -B "$SCRIPTS/gow_audio_transition_ab_report_20261018.py" \
    "$LOGS/$TAG" \
    --output "$LOGS/$TAG/audio-onset-comparison.json" \
    2>&1 | tee -a "$LOG" || echo "AUDIO_ANCHOR_ANALYZER_INCOMPLETE=SEE_FULL_LOG"
fi
if [[ -f "$LOGS/$TAG/audio-onset-comparison.json" ]]; then
  # The original emulator archive is retained as a fallback. Make a second
  # small archive that also contains the completed audio-onset analysis.
  tar -czf "$LOGS/$TAG-analyzed.tar.gz" -C "$LOGS" "$TAG" &&
    echo "UPLOAD_ANALYZED_ARCHIVE=$LOGS/$TAG-analyzed.tar.gz" ||
    echo "ANALYZED_ARCHIVE_FAILED_ORIGINAL_PRESERVED=$LOGS/$TAG.tar.gz"
fi
echo "UPLOAD_MAIN_LOG=$LOG"
echo "UPLOAD_ARCHIVE=$LOGS/$TAG.tar.gz"
grep -E 'ARCHIVE=|AUDIO_ANCHOR_IN_BOTH|AFTER_ONSET_|AUDIO_ANCHORED|GOW_LOG_TELEMETRY_AB_RESULT|GOW_INPUT_AUTOMATION_RESULT|OLLAMA' "$LOG" | tail -40 || true
echo "SSH_SESSION_PRESERVED=YES"
exit "$code"
