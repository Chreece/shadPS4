#!/usr/bin/env bash
# Complete the isolated shadPS4 build with its exact pinned submodules.
set -Eeuo pipefail
ROOT="$HOME/projects/shadps4/GhostOfTsushima"
SRC="$ROOT/tests/got-descriptor-fix-20261016/source"
BUILD="$ROOT/tests/got-descriptor-fix-20261016/build"
EXPECTED="215d48f2a61a5470848ed9e5586c398c70a46e99"
LOGDIR="$HOME/projects/shadps4/logs"
mkdir -p "$LOGDIR"

if [[ "$#" -eq 0 ]]; then
    LOG="$LOGDIR/got-descriptor-build-$(date +%Y%m%d-%H%M%S).log"
    nohup bash "$0" --worker "$LOG" >"$LOG" 2>&1 </dev/null &
    echo "BUILD_PID=$!"
    echo "LOG=$LOG"
    echo "FOLLOW=tail -f $LOG"
    echo "SSH=UNCHANGED"
    exit 0
fi
[[ "$#" -eq 2 && "$1" == --worker ]] || exit 2
LOG="$2"
finish() {
    code=$?
    echo "WORKER_EXIT=$code"
    echo "LOG=$LOG"
    echo "SSH=UNCHANGED"
}
trap finish EXIT
exec 9>"$LOGDIR/.got-descriptor-build.lock"
flock -n 9 || { echo "OTHER_BUILD_ACTIVE"; exit 4; }
[[ -d "$SRC" ]] || { echo "ISOLATED_SOURCE_MISSING"; exit 4; }
[[ "$(git -C "$SRC" rev-parse HEAD)" == "$EXPECTED" ]] || {
    echo "SOURCE_COMMIT_CHANGED; NO_BUILD"; exit 4;
}
[[ -z "$(git -C "$SRC" diff --name-only)" ]] || {
    echo "SOURCE_HAS_TRACKED_CHANGES; NO_BUILD"; exit 4;
}
echo "SOURCE_PIN=$EXPECTED"
echo "INITIALIZING_EXACT_GIT_SUBMODULE_REVISIONS"
git -C "$SRC" -c http.version=HTTP/1.1 \
    submodule update --init --recursive --jobs 4 || {
    echo "SUBMODULE_INITIALIZATION_FAILED"
    exit 5
}
git -C "$SRC" submodule status --recursive >"$LOG.submodules"
if grep -qE '^[+-U]' "$LOG.submodules"; then
    echo "SUBMODULE_REVISION_MISMATCH; NO_BUILD"
    grep -E '^[+-U]' "$LOG.submodules" | head -n 30 || true
    exit 5
fi
for dep in fmt spdlog ffmpeg-core zlib-ng; do
    [[ -f "$SRC/externals/$dep/CMakeLists.txt" ]] || {
        echo "REQUIRED_SUBMODULE_MISSING=$dep"; exit 5;
    }
done
echo "SUBMODULES=READY"
cmake -S "$SRC" -B "$BUILD" -G Ninja -DCMAKE_BUILD_TYPE=RelWithDebInfo
echo "BUILDING_VULKAN_DESCRIPTOR_FIX"
cmake --build "$BUILD" --target shadps4 --parallel 4
echo "BUILD=PASS"
echo "SOURCE=$(git -C "$SRC" rev-parse HEAD)"
echo "BINARY=$BUILD/shadps4"
sha256sum "$BUILD/shadps4"
echo "NO_GAME_LAUNCH; EXISTING_EMULATORS_DRIVERS_UNCHANGED"
