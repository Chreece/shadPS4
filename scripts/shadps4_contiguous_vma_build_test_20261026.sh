#!/usr/bin/env bash
# Exact native source/binary guarded: test genuine guest VMA adjacency repair,
# build generic shadPS4 and launch the same universal 118+2s test on GoW.
set -Eeuo pipefail
umask 077
export GIT_TERMINAL_PROMPT=0

ROOT="$HOME/projects/shadps4"
LOGS="$ROOT/logs"
STAGE="$ROOT/GodOfWarRagnarok/tests/gow-upstream-got-20261025-145442-451241"
SRC="$STAGE/source"
BUILD="$STAGE/build"
BIN="$BUILD/shadps4"
OLD_SOURCE="803bb428731be794bdc44e38b56313d030a7403a"
NEW_SOURCE="da528b540fdbe09e3642570f8d953077adaba691"
UPSTREAM="2cdefaa1298c6e4ece6c74bd84e90f3906282d51"
OLD_SHA="7604bae00c78b72e163138327f61a2f92bf549ac66446487d1ab7370a2f9b4d1"
BACKUP="$STAGE/shadps4.pre-contiguous-vma-$OLD_SHA"
GOOD="$ROOT/GodOfWarRagnarok/build/shadps4"
GOOD_SHA="8de2106a3e0d410904d9c8d6a2903ba6f1f65986af3c848b0bf274a4f4b45c8e"
TEMPLATE_SHA="223a8e6ef7916e06bfd4f0ccefc90c92d5b097a0"
mkdir -p "$LOGS"

if (( $# == 0 )); then
    TAG="shadps4-vma-build-$(date +%Y%m%d-%H%M%S)-$BASHPID"
    [[ "$TAG" =~ ^shadps4-vma-build-[0-9]{8}-[0-9]{6}-[0-9]+$ ]] || exit 2
    LIVE="$LOGS/$TAG.log"
    (set -C; : > "$LIVE") || exit 2
    nohup setsid -w bash "$0" --worker "$TAG" </dev/null >"$LIVE" 2>&1 &
    PID=$!
    disown "$PID" 2>/dev/null || true
    echo "VMA_NATIVE_BUILD=$TAG"
    echo "SOURCE=$NEW_SOURCE"
    echo "LIVE_LOG=$LIVE"
    echo "MONITOR=tail -f $LIVE"
    echo "PID=$PID"
    echo "SSH_SESSION=OPEN"
    exit 0
fi

[[ $# == 2 && "$1" == --worker ]] || exit 2
TAG="$2"
[[ "$TAG" =~ ^shadps4-vma-build-[0-9]{8}-[0-9]{6}-[0-9]+$ ]] || exit 2
LOG="$LOGS/$TAG.log"
ARCHIVE="$LOGS/$TAG.tar.gz"
WORK="$STAGE/test-$TAG"
mkdir -p "$WORK"
OLD_GO_WAR=missing
[[ ! -f "$GOOD" ]] || OLD_GO_WAR="$(sha256sum "$GOOD" | cut -d' ' -f1)"
PLAYTEST=NOT_STARTED

finish() {
    local rc=$?
    trap - EXIT
    local now=missing
    [[ ! -f "$GOOD" ]] || now="$(sha256sum "$GOOD" | cut -d' ' -f1)"
    [[ "$now" == "$OLD_GO_WAR" ]] || rc=85
    printf '%s\n' "$rc" > "$LOGS/$TAG.exit"
    {
        echo "EXIT=$rc"
        echo "PREVIOUS_STABLE_ELF=$BACKUP"
        [[ ! -f "$BACKUP" ]] || sha256sum "$BACKUP"
        [[ ! -f "$BIN" ]] || sha256sum "$BIN"
        echo "NEW_SOURCE_EXPECTED=$NEW_SOURCE"
        echo "NEW_SOURCE_ACTUAL=$(git -C "$SRC" rev-parse HEAD 2>/dev/null || echo unknown)"
        echo "PROTECTED_GO_WAR_BEFORE=$OLD_GO_WAR"
        echo "PROTECTED_GO_WAR_AFTER=$now"
        echo "GAME_TEST=$PLAYTEST"
        grep -En '(FAILED:|error:|CMake Error|undefined reference|ninja: build stopped)' "$LOG" | tail -45 || true
    } > "$WORK/summary.txt"
    if tar -czf "$ARCHIVE" -C "$LOGS" "$TAG.log" "$TAG.exit" \
        -C "$WORK" summary.txt; then
        echo "BUILD_UPLOAD_THIS=$ARCHIVE"
    fi
    echo "RESULT=$rc"
    echo "PROTECTED_GO_WAR_UNTOUCHED=$([[ "$now" == "$OLD_GO_WAR" ]] && echo YES || echo NO)"
    echo "SSH_ESDE_SAVES_UNCHANGED=YES"
    exit "$rc"
}
trap finish EXIT

for command_name in git cmake ninja ctest curl python3 sha256sum flock ldd tar; do
    command -v "$command_name" >/dev/null || { echo "MISSING_COMMAND=$command_name"; exit 2; }
done
exec 9>"$LOGS/gow-upstream-got-build.lock"
flock -n 9 || { echo "OTHER_NATIVE_BUILD_RUNNING"; exit 3; }
[[ "$OLD_GO_WAR" == "$GOOD_SHA" ]] || { echo "PROTECTED_OLD_GO_WAR_CHANGED"; exit 3; }
[[ -f "$BUILD/build.ninja" && -f "$BUILD/CMakeCache.txt" ]] || {
    echo "INCREMENTAL_BUILD_NOT_FOUND"; exit 3;
}
HEAD="$(git -C "$SRC" rev-parse HEAD)"
[[ "$HEAD" == "$OLD_SOURCE" || "$HEAD" == "$NEW_SOURCE" ]] || {
    echo "UNEXPECTED_SOURCE_HEAD=$HEAD"; exit 3;
}
[[ -z "$(git -C "$SRC" status --porcelain --untracked-files=no)" ]] || {
    echo "UNCOMMITTED_SOURCE_CHANGES_STOP"; exit 3;
}
if [[ "$HEAD" == "$OLD_SOURCE" ]]; then
    [[ -x "$BIN" && "$(sha256sum "$BIN" | cut -d' ' -f1)" == "$OLD_SHA" ]] || {
        echo "PREVIOUS_120S_NATIVE_BINARY_MISMATCH"; exit 3;
    }
    [[ -f "$BACKUP" ]] || cp -p "$BIN" "$BACKUP"
fi
[[ -f "$BACKUP" &&
   "$(sha256sum "$BACKUP" | cut -d' ' -f1)" == "$OLD_SHA" ]] || {
    echo "PREVIOUS_120S_BINARY_BACKUP_MISMATCH"; exit 3;
}
echo "PREVIOUS_120S_NATIVE_ELF_SAVED=$BACKUP"

echo "STAGE=VERIFY_LATEST_UPSTREAM"
curl -4fsSL --retry 3 \
  "https://api.github.com/repos/shadps4-emu/shadPS4/branches/main" \
  -o "$WORK/upstream.json"
LIVE_UPSTREAM="$(python3 - "$WORK/upstream.json" <<'PYUP'
import json,sys
print(json.load(open(sys.argv[1]))["commit"]["sha"])
PYUP
)"
[[ "$LIVE_UPSTREAM" == "$UPSTREAM" ]] || {
    echo "UPSTREAM_MOVED_REBASE_REQUIRED=$LIVE_UPSTREAM"; exit 4;
}
if [[ "$HEAD" == "$OLD_SOURCE" ]]; then
    echo "STAGE=FETCH_ONLY_REVIEWED_UNIVERSAL_MEMORY_PATCH"
    git -C "$SRC" -c http.version=HTTP/1.1 fetch --no-tags --no-recurse-submodules \
       https://github.com/Chreece/shadPS4.git \
       refs/heads/fix/contiguous-guest-gpu-vma-ranges-20261026
    git -C "$SRC" merge-base --is-ancestor "$NEW_SOURCE" FETCH_HEAD || exit 4
    [[ "$(git -C "$SRC" rev-parse "$NEW_SOURCE^")" == "$OLD_SOURCE" ]] || exit 4
    GOT="$(git -C "$SRC" diff --name-only "$OLD_SOURCE" "$NEW_SOURCE" | sort)"
    WANT="$(printf '%s\n' CMakeLists.txt src/core/mapped_guest_range.h \
       src/core/memory.cpp tests/guest_mapped_range/CMakeLists.txt \
       tests/guest_mapped_range/test_guest_mapped_range.cpp | sort)"
    [[ "$GOT" == "$WANT" ]] || { echo "UNREVIEWED_ENGINE_SOURCE_CHANGES"; exit 4; }
    git -C "$SRC" diff --check "$OLD_SOURCE" "$NEW_SOURCE"
    git -C "$SRC" checkout --quiet --detach "$NEW_SOURCE"
fi
[[ "$(git -C "$SRC" rev-parse HEAD)" == "$NEW_SOURCE" ]] || exit 4
git -C "$SRC" merge-base --is-ancestor "$UPSTREAM" "$NEW_SOURCE"

echo "STAGE=SOURCE_INVARIANTS_NO_DYNAMORIO"
python3 - "$SRC" <<'PYSRC'
from pathlib import Path
import sys
root=Path(sys.argv[1])
src=(root/"src/core/memory.cpp").read_text()
helper=(root/"src/core/mapped_guest_range.h").read_text()
assert "ClampContiguousMappedRange(virtual_addr, size, vma, vma_map.end())" in src
assert "next.base - before.base != before.size" in helper
assert "next.IsMapped()" in helper
assert "std::min(requested_size - available, next.size)" in helper
assert "force_align_arg_pointer" in (root/"src/core/libraries/kernel/process.cpp").read_text()
assert "ENABLE_CPU_ID_TRANSLATION" not in (root/"CMakeLists.txt").read_text()
assert "--cpu-id-mode" not in (root/"src/main.cpp").read_text()
assert "GpuReadbacksMode::Precise" in (
    root/"src/video_core/texture_cache/texture_cache.cpp").read_text()
print("NATIVE_GENERIC_SOURCE_AND_CONTIGUOUS_GUEST_RANGE=PASS",flush=True)
PYSRC

echo "STAGE=10_CASE_PRODUCTION_MAPPED_VMA_CTEST"
cmake -S "$SRC/tests/guest_mapped_range" -B "$WORK/guest-vma-build" \
    -G Ninja -DCMAKE_BUILD_TYPE=Release
cmake --build "$WORK/guest-vma-build" --parallel 2
ctest --test-dir "$WORK/guest-vma-build" --output-on-failure --no-tests=error
echo "GUEST_VMA_CONTIGUITY_REGRESSION=PASS"

echo "STAGE=INCREMENTAL_FULL_NATIVE_BUILD"
cmake -S "$SRC" -B "$BUILD" -G Ninja -DCMAKE_BUILD_TYPE=Release -DENABLE_TESTS=OFF
nice -n 10 cmake --build "$BUILD" --target shadps4 --parallel 2
[[ -x "$BIN" ]] || { echo "NEW_NATIVE_ELF_MISSING"; exit 5; }
NEW_SHA="$(sha256sum "$BIN" | cut -d' ' -f1)"
[[ "$NEW_SHA" =~ ^[a-f0-9]{64}$ && "$NEW_SHA" != "$OLD_SHA" ]] || exit 5
"$BIN" --help > "$WORK/help.txt" 2>&1
! grep -q -- '--cpu-id-mode' "$WORK/help.txt" || exit 5
ldd "$BIN" > "$WORK/ldd.txt"
! grep -q 'not found' "$WORK/ldd.txt" || exit 5
echo "NATIVE_FULL_ELF_LINK=PASS"
echo "NEW_NATIVE_SHA256=$NEW_SHA"

echo "STAGE=STAGE_EXACT_GENERIC_GO_WAR_REGRESSION"
TEMPLATE="$WORK/common-vram-test.sh"
RUNNER="$WORK/new-native-go-war-test.sh"
curl -4fsSL --retry 3 \
 "https://raw.githubusercontent.com/Chreece/shadPS4/448c20c756accf1b9148acde0622bf7db4a48b63/scripts/shadps4_gpu_headroom_ab_20261026.sh" \
 -o "$TEMPLATE"
[[ "$(git hash-object "$TEMPLATE")" == "$TEMPLATE_SHA" ]] || {
    echo "GENERIC_TEST_TEMPLATE_GIT_BLOB_CHANGED"; exit 6;
}
python3 - "$TEMPLATE" "$RUNNER" "$OLD_SHA" "$NEW_SHA" "$OLD_SOURCE" "$NEW_SOURCE" <<'PYRUN'
from pathlib import Path
import sys
src=Path(sys.argv[1]).read_text()
old_sha,new_sha,old_source,new_source=sys.argv[3:7]
changes={"EXPECTED="+old_sha:"EXPECTED="+new_sha,
         'SOURCE_REV="'+old_source+'"':'SOURCE_REV="'+new_source+'"'}
for before,after in changes.items():
    if src.count(before)!=1:
        raise SystemExit("GENERIC_TEMPLATE_PIN_MISMATCH="+before)
    src=src.replace(before,after)
assert "DURATION=118" in src
assert "GPU_HEADROOM_AB_FREE_GIB_TARGET=" in src
assert "OBSERVER_SHA=188deeb0a25e671e2308ac0acfe8ad81bc0adfc1" in src
assert "RECORDER_SHA=1593a0f720d1e7ab0d2392746693839b67162959" in src
Path(sys.argv[2]).write_text(src)
print("NATIVE_GAME_TEST_BINARY_SOURCE_PIN=PASS",flush=True)
PYRUN
bash -n "$RUNNER"
echo "READY=TEST_SAME_GPU_HEADROOM_AND_NORMAL_FULLSCREEN_FPS"
echo "GAME_PROCESS_CAP=118S_TERM_PLUS_2S_KILL"
bash "$RUNNER" CUSA34384
PLAYTEST=STARTED_DETACHED
echo "GAME_TEST_SESSION_STARTED_DETACHED=YES"
echo "NEXT=UPLOAD_BUILD_ARCHIVE_AND_SEPARATE_TEST_ARCHIVE"
