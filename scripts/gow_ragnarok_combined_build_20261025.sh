#!/usr/bin/env bash
# Isolated Oct 25 upstream + protected GoW + proven GoT engine acceptance build.
# Does NOT install, change ES-DE, change game data, start the game or touch SSH.
set -Eeuo pipefail
export GIT_TERMINAL_PROMPT=0
ROOT="$HOME/projects/shadps4"
SCRIPTS="$ROOT/scripts"
LOGS="$ROOT/logs"
REV="99ad960f99dac2fe24a8791f9f759a9971220f7a"
UPSTREAM="30f4170896ce974353a6f6486d0ecec179aec54b"
PROTECTED_SOURCE="3965a497532edb7b18027692ab04eb97fa4c145b"
PROTECTED_BIN="$ROOT/GodOfWarRagnarok/build/shadps4"
EXPECTED_PROTECTED_SHA="8de2106a3e0d410904d9c8d6a2903ba6f1f65986af3c848b0bf274a4f4b45c8e"
SEED="$ROOT/GodOfWarRagnarok/gow-stage25-restored-20261011-7bpufvsl/source"
[[ -z "$(printenv SHADPS4_SEED_SOURCE || true)" ]] || SEED="$(printenv SHADPS4_SEED_SOURCE)"
mkdir -p "$SCRIPTS" "$LOGS"
if [[ $# -eq 0 ]]; then
    command -v nohup >/dev/null && command -v setsid >/dev/null || {
        echo "FAIL: detached launcher requires nohup and setsid" >&2; exit 2;
    }
    TAG="gow-upstream-got-$(date +%Y%m%d-%H%M%S)-$$"
    LOG="$LOGS/$TAG.log"
    (set -C; : > "$LOG") || { echo "FAIL: log exists" >&2; exit 2; }
    nohup setsid -w bash "$0" --worker "$TAG" </dev/null >"$LOG" 2>&1 &
    PID=$!
    disown "$PID" 2>/dev/null || true
    echo "ISOLATED_GOW_BUILD_STARTED=$TAG"
    echo "PINNED_SOURCE_COMMIT=$REV"
    echo "LIVE_LOG=$LOG"
    echo "MONITOR=tail -f $LOG"
    echo "BUILD_PID=$PID"
    echo "CURRENT_SSH_SHELL_REMAINS_OPEN=YES"
    exit 0
fi

[[ $# -eq 2 && "$1" == "--worker" ]] || { echo "Usage: bash $0" >&2; exit 2; }
TAG="$2"
[[ "$TAG" =~ ^gow-upstream-got-[0-9]{8}-[0-9]{6}-[0-9]+$ ]] || {
    echo "Invalid run tag" >&2; exit 2;
}
STAGE="$ROOT/GodOfWarRagnarok/tests/$TAG"
SRC="$STAGE/source"
BUILD="$STAGE/build"
CAPTURE="$LOGS/$TAG-capture"
ARCHIVE="$LOGS/$TAG.tar.gz"
LOG="$LOGS/$TAG.log"
mkdir -p "$CAPTURE"
ORIGINAL_SHA=absent
if [[ -f "$PROTECTED_BIN" ]]; then
    ORIGINAL_SHA="$(sha256sum "$PROTECTED_BIN" | cut -d' ' -f1)"
fi
finish() {
    rc=$?
    trap - EXIT
    printf '%s\n' "$rc" > "$LOGS/$TAG.exit"
    actual=absent
    if [[ -f "$PROTECTED_BIN" ]]; then
        actual="$(sha256sum "$PROTECTED_BIN" | cut -d' ' -f1)"
    fi
    if [[ "$actual" != "$ORIGINAL_SHA" ]]; then
        echo "FAIL: protected GoW executable fingerprint unexpectedly changed" >&2
        rc=85
    fi
    {
        printf 'RESULT_EXIT_CODE=%s\n' "$rc"
        printf 'SOURCE_PIN=%s\n' "$REV"
        printf 'PROTECTED_EXECUTABLE_SHA_BEFORE=%s\n' "$ORIGINAL_SHA"
        printf 'PROTECTED_EXECUTABLE_SHA_AFTER=%s\n' "$actual"
        printf 'CANDIDATE_BINARY=%s\n' "$BUILD/shadps4"
    } > "$CAPTURE/summary.txt"
    if tar -czf "$ARCHIVE" -C "$LOGS" "$TAG.log" "$TAG.exit" -C "$CAPTURE" .; then
        echo "UPLOAD_THIS=$ARCHIVE"
    fi
    echo "BUILD_RESULT_EXIT=$rc"
    echo "PRODUCTION_ESDE_AND_SAVES_UNCHANGED=YES"
    exit "$rc"
}
trap finish EXIT

command -v git >/dev/null
command -v cmake >/dev/null
command -v ninja >/dev/null
command -v flock >/dev/null
command -v sha256sum >/dev/null
exec 9>"$LOGS/gow-upstream-got-build.lock"
flock -n 9 || { echo "FAIL: another combined build is running" >&2; exit 8; }
[[ -d "$SEED" ]] && git -C "$SEED" rev-parse --git-dir >/dev/null || {
    echo "FAIL: protected seed Git source missing at $SEED" >&2; exit 3;
}
git -C "$SEED" cat-file -e "$PROTECTED_SOURCE^{commit}" || {
    echo "FAIL: seed does not include protected gameplay source" >&2; exit 3;
}
if [[ "$ORIGINAL_SHA" != "$EXPECTED_PROTECTED_SHA" ]]; then
    echo "PROTECTED_BINARY_HASH_DIFFERENT_FROM_HISTORIC_BASE=$ORIGINAL_SHA"
    echo "NOTE: original executable remains untouched, but visual A/B source provenance must be verified"
else
    echo "HISTORIC_PROTECTED_EXECUTABLE_HASH_MATCH=YES"
fi

echo "BUILD_STAGE=ISOLATED_LOCAL_SOURCE_CLONE"
mkdir -p "$STAGE"
git clone --quiet --no-hardlinks "$SEED" "$SRC"
echo "BUILD_STAGE=FETCH_PINNED_PUBLIC_INTEGRATION"
git -C "$SRC" -c http.version=HTTP/1.1 fetch --no-tags --no-recurse-submodules \
    https://github.com/Chreece/shadPS4.git \
    refs/heads/integration/gow-ragnarok-upstream-got-20261025
git -C "$SRC" cat-file -e "$REV^{commit}"
git -C "$SRC" merge-base --is-ancestor "$REV" FETCH_HEAD || {
    echo "FAIL: integration branch no longer descends from pinned candidate"; exit 4;
}
git -C "$SRC" checkout --quiet --detach "$REV"
[[ "$(git -C "$SRC" rev-parse HEAD)" == "$REV" ]]
git -C "$SRC" merge-base --is-ancestor "$UPSTREAM" HEAD
git -C "$SRC" merge-base --is-ancestor "$PROTECTED_SOURCE" HEAD
# Check first-party integration diffs, not the historical DynamoRIO source
# patch. Its five literal '+ ' context/addition lines predate this integration
# and are deliberately retained byte-for-byte for the CPU-identity fix.
git -C "$SRC" diff --check "$UPSTREAM" HEAD -- . \
    ':(exclude)src/core/cpu_id_translation/dynamorio.patch'
# Ensure the skipped historical patch is still pristine in this checkout.
git -C "$SRC" diff --exit-code HEAD -- \
    src/core/cpu_id_translation/dynamorio.patch
echo "PINNED_UPSTREAM_AND_PROTECTED_GOW_HISTORY=PASS"
echo "WHITESPACE_GATE=PASS (historical DynamoRIO patch exempted)"

echo "BUILD_STAGE=PINNED_SUBMODULES"
git -C "$SRC" submodule sync --recursive
git -C "$SRC" -c http.version=HTTP/1.1 submodule update --init --recursive --jobs 3
git -C "$SRC" submodule status --recursive > "$CAPTURE/pinned-submodules.txt"
git -C "$SRC" status --porcelain > "$CAPTURE/source-status.txt"

echo "BUILD_STAGE=GENERIC_ENGINE_SOURCE_CHECKS"
test -f "$SRC/src/shader_recompiler/ir/passes/indexed_image_selector.h"
test -f "$SRC/src/shader_recompiler/guest_tsharp_array.h"
test -f "$SRC/src/core/startup_progress.h"
test -f "$SRC/src/imgui/startup_loading.cpp"
grep -q 'SDL_GAMEPAD_BUTTON_GUIDE' "$SRC/src/sdl_window.cpp"
grep -q 'DrawStartupLoading' "$SRC/src/video_core/renderer_vulkan/vk_presenter.cpp"
grep -Fq 'return is_guest_indexed ? indexed_guest_entries + 1u : 1u;' \
  "$SRC/src/shader_recompiler/resource.h"
grep -q 'FeedPort(' "$SRC/src/core/libraries/audio3d/audio3d.cpp"
grep -q 'DrainAssociatedPorts(port)' "$SRC/src/core/libraries/audio3d/audio3d.cpp"
echo "ENGINE_SOURCE_FEATURES=PASS"

echo "BUILD_STAGE=SHADER_PROFILE_CAPABILITY_REGRESSION"
python3 - "$SRC" <<'PYPROFILE'
from pathlib import Path
import re, sys
root=Path(sys.argv[1])
profile=(root/"src/shader_recompiler/profile.h").read_text()
pipeline=(root/"src/video_core/renderer_vulkan/vk_pipeline_cache.cpp").read_text()
instance=(root/"src/video_core/renderer_vulkan/vk_instance.h").read_text()
fields=[m.group(1) for m in re.finditer(r"(?m)^\s*(?:u32|bool)\s+(\w+)\s*(?:\{|;)",profile)]
initializer=pipeline.split("profile = Shader::Profile{",1)[1].split("    WarmUp();",1)[0]
given=[m.group(1) for m in re.finditer(r"(?m)^\s*\.(\w+)\s*=",initializer)]
assert len(given)==49 and all(k in fields for k in given), "Missing shader profile fields"
assert [fields.index(k) for k in given]==sorted(fields.index(k) for k in given), "Profile field order differs"
assert "bool(features.shaderImageGatherExtended)" in instance, "Gather extension not device-derived"
print("SHADER_PROFILE_49_FIELDS_VULKAN_DEVICE_GATE=PASS",flush=True)
PYPROFILE

echo "BUILD_STAGE=STARTUP_UI_REGRESSION_TESTS"
cmake -S "$SRC/tests/startup_loading" -B "$STAGE/startup-tests" -G Ninja \
    -DCMAKE_BUILD_TYPE=Release
cmake --build "$STAGE/startup-tests" --parallel 2
ctest --test-dir "$STAGE/startup-tests" --output-on-failure --no-tests=error
echo "STARTUP_UI_REGRESSION_TESTS=PASS"

echo "BUILD_STAGE=FULL_NATIVE_CONFIGURE"
cmake -S "$SRC" -B "$BUILD" -G Ninja -DCMAKE_BUILD_TYPE=Release \
    -DENABLE_TESTS=OFF
echo "BUILD_STAGE=FULL_NATIVE_LINK"
nice -n 10 cmake --build "$BUILD" --target shadps4 --parallel 2
BUILT="$BUILD/shadps4"
[[ -f "$BUILT" && -x "$BUILT" ]] || {
    echo "FAIL: complete emulator binary was not created" >&2; exit 6;
}
sha256sum "$BUILT" > "$CAPTURE/candidate-binary.sha256"
echo "CANDIDATE_BINARY=$BUILT"
echo "BUILD_STAGE=CLI_AND_DEPENDENCY_SMOKE"
"$BUILT" --help > "$CAPTURE/cli-help.txt" 2>&1
ldd "$BUILT" > "$CAPTURE/linked-libraries.txt"
if grep -q 'not found' "$CAPTURE/linked-libraries.txt"; then
    echo "FAIL: runtime library missing" >&2; exit 7;
fi
echo "BUILD_RESULT=PASS_COMPILED_ONLY"
echo "NATIVE_GOW_GAMEPLAY_VERIFIED=NO"
echo "NATIVE_GOT_GAMEPLAY_VERIFIED=NO"
echo "PRESERVED_GOW_BIN=$PROTECTED_BIN"
echo "NEXT_GATE=Run isolated Ragnarök visual/audio/input test; do not switch ES-DE yet"
