#!/usr/bin/env bash
# Fully pinned, source-preserving launcher for GoW Audio3D output experiments.
set -Eeuo pipefail
REV="$1"
[[ "$REV" =~ ^[0-9a-f]{40}$ ]] || { echo "UNPINNED_GITHUB_REVISION"; exit 2; }

ROOT="$HOME/projects/shadps4"
SOURCE="$ROOT/GodOfWarRagnarok/tests/native-rendered-gfx-20261016-152535-3014679/source"
LOGS="$ROOT/logs"
REL="src/core/libraries/audio3d/audio3d.cpp"
HEADER="src/core/libraries/audio3d/audio3d.h"
REFERENCE="f909c3abb1ac1e7c8d87de833d3659f7d0b8fc87"

[[ -e "$SOURCE/.git" && -f "$SOURCE/$REL" && ! -L "$SOURCE/$REL" &&
   -f "$SOURCE/$HEADER" && ! -L "$SOURCE/$HEADER" ]] || {
    echo "PROVEN_LOCAL_AUDIO3D_PATH_OR_HEADER_UNSAFE"; exit 1;
}
mkdir -p "$LOGS"
WORK="$(mktemp -d "$LOGS/.gow-audio3d-source-preflight.XXXXXXXX")"
IN_RUNNER=0
cleanup() {
    local code="$?"
    trap - EXIT
    set +e
    if [[ "$code" != 0 && "$IN_RUNNER" == 0 ]]; then
        cp -p "$SOURCE/$REL" "$WORK/current-server-audio3d.cpp" 2>/dev/null
        cp -p "$SOURCE/$HEADER" "$WORK/current-server-audio3d.h" 2>/dev/null
        local archive="$LOGS/gow-audio3d-local-merge-preflight-$(date +%Y%m%d-%H%M%S).tar.gz"
        if tar -czf "$archive" -C "$LOGS" "$(basename "$WORK")"; then
            echo "READ_ONLY_AUDIO3D_PREFLIGHT_ARCHIVE=$archive"
        fi
    fi
    rm -rf -- "$WORK"
    echo "PROVEN_AUDIO3D_SOURCE=UNCHANGED"
    echo "SSH_SESSION=OPEN"
    exit "$code"
}
trap cleanup EXIT

[[ -n "$(command -v git)" && -n "$(command -v python3)" ]] || {
    echo "GIT_OR_PYTHON3_MISSING"; exit 1;
}
git -C "$SOURCE" cat-file -e "$REV^{commit}"
git -C "$SOURCE" cat-file -e "$REFERENCE^{commit}"

while read -r name gitblob path; do
    [[ -n "$name" ]] || continue
    git -C "$SOURCE" show "$REV:$path" > "$WORK/$name"
    actual="$(git -C "$SOURCE" hash-object "$WORK/$name")"
    [[ "$actual" == "$gitblob" ]] || {
        echo "PINNED_AUDIO3D_LAUNCH_BLOB_MISMATCH=$name"
        exit 1
    }
done <<'PINNED_AUDIO3D_TOOLS'
runner.sh 133aa1cb8816590b463ced91a3e21517a598627d tools/development/gow_audio3d_direct_drain_test_20261017.sh
observer.py fb06c095431b91c932d1890a3ce4bd967faefa6a tools/development/gow_upstream_gameplay.py
restorer.py 0bb5acf3149c53e58fb4739845fc33538ed32ff8 tools/development/gow_restore_graphics_renderer_20261017.py
support.py 5e8d17492c592421af95413ba8e00610b66aa255 tools/development/gow_preserve_uffd_support_20261017.py
contract.py 081e72f9f7510cdea07bd40e6afe4df74313e027 tools/development/gow_audio3d_direct_drain_contract_20261017.py
merge.py 7a4562fbbe449135e1766127601ad0fb90b8c6af tools/development/gow_audio3d_preserve_local_merge_20261017.py
merge-test.py d241748c6c81b4fd57e72151dd7528ec9e7da352 tools/development/gow_audio3d_preserve_local_merge_test_20261017.py
trial.cpp 3b6d5bf02ba954a4e30ce9cd063897a0046bbde0 src/core/libraries/audio3d/audio3d.cpp
PINNED_AUDIO3D_TOOLS

git -C "$SOURCE" show "$REFERENCE:$REL" > "$WORK/git-reference.cpp"
[[ "$(git -C "$SOURCE" hash-object "$WORK/git-reference.cpp")" == \
    "b1b1445eda8823f07b4f66c07afcb175f7ee04b9" ]] || {
    echo "PINNED_AUDIO3D_REFERENCE_GIT_BLOB_MISMATCH"; exit 1;
}

bash -n "$WORK/runner.sh"
python3 -m py_compile "$WORK/observer.py" "$WORK/restorer.py" \
    "$WORK/support.py" "$WORK/contract.py" "$WORK/merge.py" "$WORK/merge-test.py"

echo "=== SEVEN REAL-SOURCE AUDIO3D MERGE REGRESSION TESTS ==="
python3 -B "$WORK/merge-test.py" "$WORK/merge.py" \
    "$WORK/git-reference.cpp" "$WORK/trial.cpp" "$SOURCE/$HEADER"

echo "=== PREPARE NON-MUTATING MERGE OF REAL LOCAL SOURCE ==="
sha_before="$(sha256sum "$SOURCE/$REL" | cut -d' ' -f1)"
printf 'LOCAL_ORIGINAL_SHA256=%s\n' "$sha_before" |
    tee "$WORK/local-original-fingerprint.txt"

python3 -B "$WORK/merge.py" \
    "$SOURCE/$REL" "$WORK/git-reference.cpp" "$WORK/trial.cpp" \
    "$SOURCE/$HEADER" "$WORK/safe-merged-audio3d.cpp" \
    "$WORK/merge-preflight-result.json"

[[ "$(sha256sum "$SOURCE/$REL" | cut -d' ' -f1)" == "$sha_before" ]] || {
    echo "LOCAL_AUDIO3D_SOURCE_CHANGED_DURING_PREFLIGHT"; exit 1;
}

echo "=== REAL LOCAL CODE AND PCM SAFETY CONTRACT ==="
python3 -B "$WORK/contract.py" \
    "$SOURCE/$REL" "$WORK/safe-merged-audio3d.cpp" \
    "$WORK/runner.sh" "$WORK/observer.py" "$SOURCE/$HEADER"

echo "AUDIO3D_LOCAL_SOURCE_MERGE_PREFLIGHT=PASS"
echo "SOURCE_DIVERGENCE=HANDLED_WITHOUT_REPLACING_LOCAL_CHANGES"
echo "GRAPHICS_AND_UFFD_BATCH_REFERENCE=PROTECTED"
echo "OPTIONAL_DIRECT_AUDIO3D_DRAIN_ONLY=YES"
echo "AUTO_GOW_GAMEPLAY_TEST=START"

IN_RUNNER=1
bash "$WORK/runner.sh" \
    "$WORK/observer.py" "$WORK/restorer.py" "$WORK/support.py" "$WORK/merge.py"
