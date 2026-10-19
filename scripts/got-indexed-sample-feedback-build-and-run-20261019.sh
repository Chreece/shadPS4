#!/usr/bin/env bash
# Bounded GPU-indexed sample feedback: host regressions -> build -> native GPU capture ->
# screenshots and shader proofs -> preserve active binary and original profile.
set -Eeuo pipefail
ROOT="$HOME/projects/shadps4"
GOT="$ROOT/GhostOfTsushima"
SRC="$GOT/tests/got-upstream-menu-main-20261017-cached/source"
WORK="$GOT/tests/got-upstream-menu-main-20261017-cached"
LOGS="$ROOT/logs"
SCRIPTS="$ROOT/scripts"
BRANCH="diagnostic/got-indexed-sample-feedback-20261019"
PIN="5228e7c2eafb057216b69498c0f065e9327e2353"
CANDIDATE_COMMIT="c8121ad971408e5fb28b2da28de09625fa53efe0"
CANDIDATE="$WORK/bin-backups/shadps4-got-indexed-sample-feedback-$CANDIDATE_COMMIT"
BUILD_SCRIPT="$SCRIPTS/got-indexed-sample-feedback-host-20261019.sh"
RUNTIME_SCRIPT="$SCRIPTS/got-indexed-sample-feedback-runtime-20261019.sh"
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
    scripts/got-indexed-sample-feedback-host-20261019.sh \
    scripts/got-indexed-sample-feedback-runtime-20261019.sh; do
    if ! git -C "$SRC" cat-file -e "$PIN:$required" 2>/dev/null; then
        echo "MISSING_PINNED_SCRIPT=$PIN:$required"
        exit 2
    fi
done
echo "PINNED_BUILD_AND_RUNTIME_SCRIPTS=PASS"
git -C "$SRC" show "$PIN:scripts/got-indexed-sample-feedback-host-20261019.sh" > "$BUILD_SCRIPT"
git -C "$SRC" show "$PIN:scripts/got-indexed-sample-feedback-runtime-20261019.sh" > "$RUNTIME_SCRIPT"
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
# Preserve the existing source, active executable, and rendering behavior.
HOST_REPORT="$(find "$LOGS" -maxdepth 1 -type f \
    -name 'got-indexed-sample-feedback-host-*.tar.gz' -printf '%T@ %p\n' |
    sort -nr | head -1 | cut -d' ' -f2-)"
if [ -n "$HOST_REPORT" ] && verify_host_archive "$HOST_REPORT"; then
    echo "CANDIDATE_BUILD_ALREADY_VERIFIED=YES"
else
    echo "GOT_INDEXED_SAMPLE_FEEDBACK_BUILD=START"
    bash "$BUILD_SCRIPT"
    HOST_REPORT="$(find "$LOGS" -maxdepth 1 -type f \
        -name 'got-indexed-sample-feedback-host-*.tar.gz' -printf '%T@ %p\n' |
        sort -nr | head -1 | cut -d' ' -f2-)"
    if ! verify_host_archive "$HOST_REPORT"; then
        echo "GOT_INDEXED_SAMPLE_FEEDBACK_BUILD_NOT_VERIFIED_NO_GAME_LAUNCHED"
        exit 2
    fi
fi

# Execute ONLY after all source regressions and complete shadPS4 link pass.
# The runtime is bounded, has GPU fault/VRAM guards, conditional Ollama
# unload and restore, and never replaces the existing executable.
echo "GOT_INDEXED_SAMPLE_FEEDBACK_RUNTIME=AUTOMATIC_LAUNCH"
CAPTURE_START="$(date +%s)"
RUNTIME_STATUS=0
GOT_INDEXED_SAMPLE_FEEDBACK_HOST_REPORT="$HOST_REPORT" bash "$RUNTIME_SCRIPT" || RUNTIME_STATUS=$?
RUNTIME_REPORT="$(find "$LOGS" -maxdepth 1 -type f \
    -name 'got-indexed-sample-feedback-runtime-*.tar.gz' -newermt "@$CAPTURE_START" \
    -printf '%T@ %p\n' | sort -nr | head -1 | cut -d' ' -f2-)"
if [ -n "$RUNTIME_REPORT" ] &&
   tar -xOzf "$RUNTIME_REPORT" info/final.txt 2>/dev/null |
       grep -qx "CANDIDATE_SOURCE=$CANDIDATE_COMMIT"; then
    BUNDLE="$LOGS/got-indexed-table-resize-evidence-$(date +%Y%m%d-%H%M%S).tar.gz"
    tar -czf "$BUNDLE" -C "$LOGS" "$(basename "$HOST_REPORT")" "$(basename "$RUNTIME_REPORT")"
    echo "UPLOAD_THIS=$BUNDLE"
else
    echo "RUNTIME_ARCHIVE_NOT_FOUND; HOST_REPORT=$HOST_REPORT"
    [ "$RUNTIME_STATUS" -ne 0 ] || RUNTIME_STATUS=2
fi
exit "$RUNTIME_STATUS"
