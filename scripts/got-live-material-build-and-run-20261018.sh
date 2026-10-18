#!/usr/bin/env bash
# No more manual build->runtime pause. Reuse a VERIFIED existing candidate or
# build it, then automatically launch the isolated guarded GoT runtime runner.
set -Eeuo pipefail
ROOT="$HOME/projects/shadps4"
GOT="$ROOT/GhostOfTsushima"
SOURCE="$GOT/tests/got-upstream-menu-main-20261017-cached/source"
WORK="$GOT/tests/got-upstream-menu-main-20261017-cached"
LOGS="$ROOT/logs"
SCRIPTS="$ROOT/scripts"
BRANCH="fix/got-live-material-permutations-20261018"
PIN="de0943c54479372c3a159c22f44203e54c4b6ba4"
CANDIDATE_COMMIT="d27ec61bbad7d5b8836dd23926fcff72a6ae51af"
CANDIDATE="$WORK/bin-backups/shadps4-got-live-material-$CANDIDATE_COMMIT"
BUILD_SCRIPT="$SCRIPTS/got-live-material-host-build-20261018.sh"
RUNTIME_SCRIPT="$SCRIPTS/got-live-material-runtime-20261018.sh"
mkdir -p "$LOGS" "$SCRIPTS"

# Fetch objects only; never checkout/reset the active GoT worktree.
if ! git -C "$SOURCE" cat-file -e "$PIN^{commit}" 2>/dev/null; then
    git -C "$SOURCE" -c gc.auto=0 -c http.version=HTTP/1.1 \
        -c fetch.recurseSubmodules=false \
        fetch --depth=256 --no-tags --no-recurse-submodules \
        https://github.com/Chreece/shadPS4.git "refs/heads/$BRANCH"
fi
git -C "$SOURCE" cat-file -e "$PIN^{commit}"
git -C "$SOURCE" show "$PIN:scripts/got-live-material-host-build-20261018.sh" > "$BUILD_SCRIPT"
git -C "$SOURCE" show "$PIN:scripts/got-live-material-runtime-20261018.sh" > "$RUNTIME_SCRIPT"
bash -n "$BUILD_SCRIPT"
bash -n "$RUNTIME_SCRIPT"

verify() {
    local archive="$1" status expected actual
    [ -f "$archive" ] && [ -x "$CANDIDATE" ] || return 1
    status="$(tar -xOzf "$archive" info/final.txt 2>/dev/null)" || return 1
    grep -qx 'STATUS=HOST_ONLY_BUILD_PASS' <<< "$status" || return 1
    grep -qx 'EXIT=0' <<< "$status" || return 1
    grep -qx 'CANDIDATE_BUILT=1' <<< "$status" || return 1
    grep -qx 'BASELINE_EXECUTABLE_RESTORED=1' <<< "$status" || return 1
    expected="$(tar -xOzf "$archive" info/candidate-preserved.sha256 2>/dev/null |
        awk 'NR==1 {print $1}')" || return 1
    [[ "$expected" =~ ^[a-f0-9]{64}$ ]] || return 1
    actual="$(sha256sum "$CANDIDATE" | awk '{print $1}')" || return 1
    [ "$expected" = "$actual" ] || return 1
    printf 'VERIFIED_BUILD_ARCHIVE=%s\n' "$archive"
    printf 'VERIFIED_CANDIDATE_SHA256=%s\n' "$actual"
}

# First check the already successful compilation. Never waste time rebuilding.
HOST_REPORT="$LOGS/got-live-material-host-20261018-164638.tar.gz"
if verify "$HOST_REPORT"; then
    echo "COMPILE_ALREADY_PASS=YES; AUTO_LAUNCH_NOW"
else
    echo "EXISTING_COMPILE_NOT_VERIFIED; BUILDING_WITH_TWO_JOBS"
    bash "$BUILD_SCRIPT"
    # Only one runner is allowed per checkpoint; take the latest completed
    # report and validate its exact candidate SHA before ever launching.
    HOST_REPORT="$(find "$LOGS" -maxdepth 1 -type f \
        -name 'got-live-material-host-*.tar.gz' -printf '%T@ %p\n' |
        sort -nr | head -1 | cut -d' ' -f2-)"
    verify "$HOST_REPORT" || {
        echo "BUILD_NOT_VERIFIED_NO_GAME_LAUNCHED"
        exit 2
    }
    echo "BUILD_PASS=YES; AUTO_LAUNCH_NOW"
fi

# Directly run the newly built binary from bin-backups, not by overwriting
# the original. Runtime owns the 8 GiB VRAM gate, conditional Ollama unload
# and reload, X11 screenshots, GPU-fault watchdog, and tar report.
GOT_LIVE_MATERIAL_HOST_REPORT="$HOST_REPORT" bash "$RUNTIME_SCRIPT"
