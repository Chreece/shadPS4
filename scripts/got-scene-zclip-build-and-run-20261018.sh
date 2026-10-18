#!/usr/bin/env bash
# GoT only-five-pairs Z clipping A/B: verified host tests -> build -> guarded real menu ->
# screenshots and shader proofs -> preserve active binary and original profile.
set -Eeuo pipefail
ROOT="$HOME/projects/shadps4"
GOT="$ROOT/GhostOfTsushima"
SRC="$GOT/tests/got-upstream-menu-main-20261017-cached/source"
WORK="$GOT/tests/got-upstream-menu-main-20261017-cached"
LOGS="$ROOT/logs"
SCRIPTS="$ROOT/scripts"
BRANCH="experiment/got-scene-depth-clip-20261018"
PIN="8352e60c033cf9370d9c582915b50337331071bc"
CANDIDATE_COMMIT="93b83c3d57c4c6f9b2c183796b19a92ccc6b54ee"
CANDIDATE="$WORK/bin-backups/shadps4-got-scene-zclip-$CANDIDATE_COMMIT"
BUILD_SCRIPT="$SCRIPTS/got-scene-zclip-host-20261018.sh"
RUNTIME_SCRIPT="$SCRIPTS/got-scene-zclip-runtime-20261018.sh"
mkdir -p "$LOGS" "$SCRIPTS"

# No checkout, clean, reset, submodule changes or stopping SSH/Sunshine.
if ! git -C "$SRC" cat-file -e "$PIN^{commit}" 2>/dev/null; then
    git -C "$SRC" -c gc.auto=0 -c http.version=HTTP/1.1 \
        -c fetch.recurseSubmodules=false \
        fetch --depth=256 --no-tags --no-recurse-submodules \
        https://github.com/Chreece/shadPS4.git "refs/heads/$BRANCH"
fi
git -C "$SRC" cat-file -e "$PIN^{commit}"
# Verify BOTH scripts are present at the exact pin before any build mutates
# the isolated source overlay; never repeat the missing-script failure.
for required in \
    scripts/got-scene-zclip-host-20261018.sh \
    scripts/got-scene-zclip-runtime-20261018.sh; do
    if ! git -C "$SRC" cat-file -e "$PIN:$required" 2>/dev/null; then
        echo "MISSING_PINNED_SCRIPT=$PIN:$required"
        exit 2
    fi
done
echo "PINNED_BUILD_AND_RUNTIME_SCRIPTS=PASS"
git -C "$SRC" show "$PIN:scripts/got-scene-zclip-host-20261018.sh" > "$BUILD_SCRIPT"
git -C "$SRC" show "$PIN:scripts/got-scene-zclip-runtime-20261018.sh" > "$RUNTIME_SCRIPT"
bash -n "$BUILD_SCRIPT"
bash -n "$RUNTIME_SCRIPT"

verify_host_archive() {
    local file="$1" status expected actual
    [ -f "$file" ] && [ -x "$CANDIDATE" ] || return 1
    status="$(tar -xOzf "$file" info/final.txt 2>/dev/null)" || return 1
    for expected_line in STATUS=HOST_ONLY_BUILD_PASS EXIT=0 \
       CANDIDATE_BUILT=1 BASELINE_EXECUTABLE_RESTORED=1; do
        grep -qx "$expected_line" <<<"$status" || return 1
    done
    expected="$(tar -xOzf "$file" info/candidate-preserved.sha256 2>/dev/null |
        awk 'NR==1 {print $1}')" || return 1
    [[ "$expected" =~ ^[a-f0-9]{64}$ ]] || return 1
    actual="$(sha256sum "$CANDIDATE" | awk '{print $1}')"
    [ "$expected" = "$actual" ] || return 1
    echo "VERIFIED_CANDIDATE_SHA256=$actual"
    echo "VERIFIED_REPORT=$file"
}

# Build once. A known-good candidate can be reused without compilation.
HOST_REPORT="$(find "$LOGS" -maxdepth 1 -type f \
    -name 'got-scene-zclip-host-*.tar.gz' -printf '%T@ %p\n' |
    sort -nr | head -1 | cut -d' ' -f2-)"
if [ -n "$HOST_REPORT" ] && verify_host_archive "$HOST_REPORT"; then
    echo "CANDIDATE_BUILD_ALREADY_VERIFIED=YES"
else
    echo "SCENE_ZCLIP_BUILD=START"
    bash "$BUILD_SCRIPT"
    HOST_REPORT="$(find "$LOGS" -maxdepth 1 -type f \
        -name 'got-scene-zclip-host-*.tar.gz' -printf '%T@ %p\n' |
        sort -nr | head -1 | cut -d' ' -f2-)"
    if ! verify_host_archive "$HOST_REPORT"; then
        echo "SCENE_ZCLIP_BUILD_NOT_VERIFIED_NO_GAME_LAUNCHED"
        exit 2
    fi
fi

# Execute ONLY after all source regressions and complete shadPS4 link pass.
# The runtime is bounded, has GPU fault/VRAM guards, conditional Ollama
# unload and restore, and never replaces the existing executable.
echo "SCENE_ZCLIP_RUNTIME=AUTOMATIC_LAUNCH"
GOT_SCENE_ZCLIP_HOST_REPORT="$HOST_REPORT" bash "$RUNTIME_SCRIPT"
