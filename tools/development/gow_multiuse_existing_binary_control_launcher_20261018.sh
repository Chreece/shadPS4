#!/usr/bin/env bash
# GoW: compare known-good 12:47 and already-built 16:52 executable.
# No rebuild, no source edits, no git reset, no Sunshine/SSH changes.
# Stop Ollama only if free VRAM is below 8 GiB, restore its models on exit.
set -Eeuo pipefail

ROOT="$HOME/projects/shadps4"
PROTECTED_SOURCE="$ROOT/GodOfWarRagnarok/tests/native-rendered-gfx-20261016-152535-3014679/source"
BUILD="$ROOT/GodOfWarRagnarok/tests/gow-renderer-log-hotpath-20261017/build"
CWD="$ROOT/GodOfWarRagnarok/tests/native-rendered-gfx-20261016-152535-3014679"
BASELINE="$BUILD/shadps4.fullgfx-gpu-phase-probe-gow-gpu-invalidate-timing-20261018-124729"
BASELINE_SHA="63a00275b2f78ba58ef35577af0bf0b1681ac245a7e1636de1673cb4f65275ce"
CANDIDATE="$BUILD/shadps4.fullgfx-srt-multiuse-gow-srt-multiuse-20261018-165221"
CANDIDATE_SHA="40483b615a8953c031451c785c179f4cb9786b7e933e7b1d7b11339d252c2941"

[[ -e "$PROTECTED_SOURCE/.git" ]] || {
  echo "PROTECTED_GAME_WORKTREE_MISSING; NO_ACTION"; exit 2;
}
mkdir -p "$ROOT/scripts" "$ROOT/logs"
WORK="$(mktemp -d "$ROOT/scripts/.gow-srt-startup-control.XXXXXXXX")"
trap 'rm -rf -- "$WORK"' EXIT

# Both executables must match the uploaded logs BEFORE touching Ollama.
[[ -f "$BASELINE" && ! -L "$BASELINE" &&
   -f "$CANDIDATE" && ! -L "$CANDIDATE" ]] || {
  echo "BASELINE_OR_CANDIDATE_BINARY_MISSING_NO_ACTION"; exit 2;
}
printf '%s  %s\n' "$BASELINE_SHA" "$BASELINE" | sha256sum -c - || {
  echo "BASELINE_SHA256_DIFFERS_NO_ACTION"; exit 2;
}
printf '%s  %s\n' "$CANDIDATE_SHA" "$CANDIDATE" | sha256sum -c - || {
  echo "CANDIDATE_SHA256_DIFFERS_NO_ACTION"; exit 2;
}

REF="920c2a9fc4801415845fbd4cae51093ee12e74b3"
URL="https://raw.githubusercontent.com/Chreece/shadPS4/$REF/tools/development"
curl -4 -fsSL --retry 3 --retry-delay 1 \
  "$URL/gow_multiuse_existing_binary_control_20261018.py" -o "$WORK/control.py"
curl -4 -fsSL --retry 3 --retry-delay 1 \
  "$URL/gow_ollama_vram_gameplay_20261018.py" -o "$WORK/ollama.py"
[[ "$(git -C "$PROTECTED_SOURCE" hash-object "$WORK/control.py")" == \
  "79796682eae0c93b424ce8a941a6708d7db91944" ]] || {
  echo "PINNED_STARTUP_CONTROL_SOURCE_CHANGED"; exit 2;
}
[[ "$(git -C "$PROTECTED_SOURCE" hash-object "$WORK/ollama.py")" == \
  "35b9806f7eeae74e95e4738a25d88868b51fc325" ]] || {
  echo "PINNED_OLLAMA_HELPER_SOURCE_CHANGED"; exit 2;
}
python3 -m py_compile "$WORK/control.py" "$WORK/ollama.py"
TAG="gow-multiuse-startup-control-$(date +%Y%m%d-%H%M%S)"
echo "EMULATOR_COMPILE=NO"
echo "GAME_BINARY=1247_PROVEN_CONTROL_THEN_1652_EXISTING_CANDIDATE"
echo "OLLLAMA_MODEL_UNLOAD_ONLY_IF_FREE_GPU_VRAM_BELOW_8_GIB=YES"
python3 -B -u "$WORK/control.py" \
  --ollama-helper "$WORK/ollama.py" \
  --baseline "$BASELINE" --baseline-sha "$BASELINE_SHA" \
  --candidate "$CANDIDATE" --candidate-sha "$CANDIDATE_SHA" \
  --cwd "$CWD" --out "$ROOT/logs/$TAG" \
  --baseline-seconds 75 --candidate-seconds 135
echo "SSH_SESSION_STILL_OPEN=YES"
