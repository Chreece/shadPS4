#!/usr/bin/env bash
# Source-verified one-paste launcher for opt-in UFFD GPU callback batching.
set -Eeuo pipefail
PINNED_COMMIT="\${1:?Pass the exact pinned GitHub commit SHA}"
[[ "$PINNED_COMMIT" =~ ^[0-9a-f]{40}$ ]] || { echo "REVISION_MUST_BE_FULL_SHA"; exit 2; }
ROOT="$HOME/projects/shadps4"
SOURCE="$ROOT/GodOfWarRagnarok/tests/native-rendered-gfx-20261016-152535-3014679/source"
LOGS="$ROOT/logs"
BASE="f909c3abb1ac1e7c8d87de833d3659f7d0b8fc87"
[[ -e "$SOURCE/.git" ]] || { echo "GOW_PROVEN_SOURCE_GIT_MISSING"; exit 1; }
mkdir -p "$LOGS"
WORK="$(mktemp -d "$LOGS/.gow-callback-preflight.XXXXXXXX")"
trap 'rm -rf -- "$WORK"' EXIT

git -C "$SOURCE" cat-file -e "$PINNED_COMMIT^{commit}"
git -C "$SOURCE" cat-file -e "$BASE^{commit}"
while read -r name blob path; do
    git -C "$SOURCE" show "$PINNED_COMMIT:$path" > "$WORK/$name"
    actual="$(git -C "$SOURCE" hash-object "$WORK/$name")"
    [[ "$actual" == "$blob" ]] || {
        echo "PINNED_GPU_CALLBACK_TOOL_BLOB_MISMATCH=$name"
        exit 1
    }
done <<'PINNED_FILES'
runner.sh c7b58d7577e46dad92bc2ae42ac7ead74e396030 tools/development/gow_uffd_callback_batch_trial_20261018.sh
observer.py e3163bd7525472da484a4bb707425558255250e9 tools/development/gow_upstream_gameplay.py
helper.py 0bb5acf3149c53e58fb4739845fc33538ed32ff8 tools/development/gow_restore_graphics_renderer_20261017.py
support.py 5e8d17492c592421af95413ba8e00610b66aa255 tools/development/gow_preserve_uffd_support_20261017.py
contract.py 0c578142884407456729cc0d4bce7d074958190a tools/development/gow_uffd_callback_batch_contract_20261018.py
trial.cpp 8574e55d2500f662ce0cdfc93df6ca47a60e29c5 src/video_core/page_manager.cpp
host-test.cpp 25b8871f7037d2e38a2594c3c3386cac9a2de8a3 tests/uffd/test_gpu_callback_batch_20261018.cpp
PINNED_FILES

git -C "$SOURCE" show "$BASE:src/video_core/page_manager.cpp" \
    > "$WORK/proven-page-manager.cpp"
[[ "$(git -C "$SOURCE" hash-object "$WORK/proven-page-manager.cpp")" == \
    "75ede506fa3a07b01263542af320a3e90535adc4" ]] || {
    echo "PROVEN_UFFD_BASELINE_BLOB_CHANGED"; exit 1;
}

bash -n "$WORK/runner.sh"
python3 -m py_compile \
    "$WORK/observer.py" "$WORK/helper.py" \
    "$WORK/support.py" "$WORK/contract.py"
echo "=== EXACT FULL-GRAPHICS MEMORY SEMANTICS PREFLIGHT ==="
python3 -B "$WORK/contract.py" \
    "$WORK/proven-page-manager.cpp" "$WORK/trial.cpp" \
    "$WORK/runner.sh" "$WORK/observer.py" "$WORK/host-test.cpp"
echo "=== TARGETED HOST GPU CALLBACK TESTS, ISOLATED BUILD, AND GAMEPLAY ==="
bash "$WORK/runner.sh" "$WORK/observer.py" "$WORK/helper.py" "$WORK/support.py"
