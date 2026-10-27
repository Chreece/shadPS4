#!/usr/bin/env bash
# Source-pinned incremental unified GoW/GoT native compile-only validation.
# No Vulkan GPU dispatch or games, no services/display/network/saves modified.
set -Eeuo pipefail
umask 077
ROOT="$HOME/projects/shadps4"
LOGS="$ROOT/logs"
TREE="$ROOT/Upstream/tests/gow-hle-abi-20261027-194629-907086"
SRC="$TREE/source"
BUILD="$TREE/build"
BIN="$BUILD/shadps4"
BASE="564285c9fb43e5dd9c56e7c0c9e513f948f93384"
PIN="bbe1a72840733afccba5f48fb4b5eb29a1c96128"
BASE_ELF="46195c52e9dd3fb3921b0b5c99a233edf4b484ecceb7794c12ae864626d6b3af"
mkdir -p "$LOGS" "$ROOT/diary"
if (( $# == 0 )); then
    TAG="got-unified-pr94-$(date +%Y%m%d-%H%M%S)-$BASHPID"
    LOG="$LOGS/$TAG.log"
    (set -C; : > "$LOG") || exit 3
    nohup setsid -w bash "$0" --worker "$TAG" </dev/null > "$LOG" 2>&1 &
    PID=$!
    disown "$PID" 2>/dev/null || true
    echo "WORKER_PID=$PID"
    echo "SOURCE=$PIN"
    echo "NATIVE_BUILD_ONLY=YES NO_GAMES_NO_RADV=YES"
    echo "MONITOR=tail -F '$LOG'"
    echo "BUILD_LOG=tail -F '$LOGS/$TAG-evidence/build-native.log'"
    echo "UPLOAD_THIS=$LOGS/$TAG.tar.gz"
    echo "SSH_SESSION=OPEN"
    exit 0
fi
[[ $# == 2 && "$1" == --worker ]] || exit 3
TAG="$2"
[[ "$TAG" =~ ^got-unified-pr94-[0-9]{8}-[0-9]{6}-[0-9]+$ ]] || exit 3
EV="$LOGS/$TAG-evidence"
ARCHIVE="$LOGS/$TAG.tar.gz"
[[ ! -e "$EV" ]] || exit 3
mkdir -p "$EV"
STATE=PREFLIGHT
NEW_SHA=""
SNAP=""
finish() {
  local rc=$?
  trap - EXIT
  printf 'STATE=%s\nEXIT_CODE=%s\nSOURCE=%s\nNEW_SHA256=%s\nGAMES_RUN=NO\nSSH_SESSION=OPEN\n' "$STATE" "$rc" "$PIN" "$NEW_SHA" > "$EV/result.txt"
  [[ -z "$SNAP" || ! -f "$SNAP" ]] || sha256sum "$SNAP" > "$EV/pr91-old-elf-after.sha256" || true
  python3 - "$ROOT/diary/trials.jsonl" "$TAG" "$STATE" "$rc" "$ARCHIVE" "$NEW_SHA" <<'PY' || true
import fcntl,json,os,sys
p,tag,state,rc,archive,sha=sys.argv[1:]
row={"schema_version":2,"record_id":tag,"recorded_at":"2026-10-27","record_type":"UNIVERSAL_GOT_UPSTREAM_PR94_NATIVE_BUILD","source_commit":"bbe1a72840733afccba5f48fb4b5eb29a1c96128","game_id":"ALL_GAMES","tested_games":[],"game_effect":"NOT_RUN","result":state,"exit_code":int(rc),"archive":os.path.basename(archive),"binary_sha256":sha,"decision":"NATIVE_EVIDENCE_REVIEW_BEFORE_GPU"}
with open(p,"a",encoding="utf8") as f:
 fcntl.flock(f.fileno(),fcntl.LOCK_EX)
 f.write(json.dumps(row,separators=(",",":"))+"\n")
 f.flush();os.fsync(f.fileno())
PY
  tar -czf "$ARCHIVE" -C "$LOGS" "$TAG-evidence" "$TAG.log" 2>/dev/null || true
  echo "RESULT=$STATE"
  echo "UPLOAD_THIS=$ARCHIVE"
  echo "SSH_SESSION=OPEN"
  exit "$rc"
}
trap finish EXIT
for c in git cmake ninja g++ sha256sum flock python3 tar nice ldd df; do
    command -v "$c" >/dev/null || { echo "MISSING_COMMAND=$c"; exit 3; }
done
exec 6>"$LOGS/got-unified-pr94-exclusive.lock"
flock -n 6 || { echo "OTHER_PR94_WORKER_ACTIVE"; exit 3; }
exec 9>"$LOGS/gow-upstream-got-build.lock"
exec 8>"$LOGS/shadps4-gow-srt-phi.lock"
exec 7>"$LOGS/gow-hle-stack-build.lock"
STATE=WAIT_FOR_SHARED_GOW_GOT_BUILD_LOCKS
T0=$SECONDS
while :; do
    if flock -n 9 && flock -n 8 && flock -n 7; then break; fi
    flock -u 7 || true; flock -u 8 || true; flock -u 9 || true
    ELAPSED=$((SECONDS-T0))
    ((ELAPSED<2700)) || { echo "OTHER_BUILD_LOCK_WAIT_LIMIT";exit 3; }
    echo "QUEUED_SHARED_LOCK_SECONDS=$ELAPSED"
    sleep 15
done
STATE=VERIFY_SOURCE_AND_PRESERVE_LAST_NATIVE
echo "STAGE=$STATE"
[[ -d "$SRC/.git" && -f "$BUILD/build.ninja" && -f "$BUILD/CMakeCache.txt" ]] || exit 4
[[ "$(git -C "$SRC" rev-parse HEAD)" == "$BASE" ]] || { echo "PR91_SOURCE_NOT_PINNED"; exit 4; }
[[ -z "$(git -C "$SRC" status --porcelain --untracked-files=no)" ]] || exit 4
[[ -x "$BIN" && ! -L "$BIN" ]] || exit 4
[[ "$(sha256sum "$BIN" | awk '{print $1}')" == "$BASE_ELF" ]] || {
    echo "PR91_BINARY_SHA_CHANGED";exit 4;
}
grep -Fq "CMAKE_HOME_DIRECTORY:INTERNAL=$SRC" "$BUILD/CMakeCache.txt" || exit 4
grep -Fq "CMAKE_BUILD_TYPE:STRING=Release" "$BUILD/CMakeCache.txt" || exit 4
if command -v pgrep >/dev/null && pgrep -x shadps4 > "$EV/other-game-pids.txt"; then
    echo "ACTIVE_EMULATOR_NO_SOURCE_CHANGE";exit 4;
fi
FREE="$(df -Pk "$TREE" | awk 'END {print $4}')"
[[ "$FREE" =~ ^[0-9]+$ ]] && ((FREE>=2097152)) || exit 4
mkdir -p "$TREE/old-binaries"
SNAP="$TREE/old-binaries/pr91-46195c52-native-shadps4"
[[ -e "$SNAP" || -L "$SNAP" ]] || cp --reflink=auto "$BIN" "$SNAP"
[[ -x "$SNAP" && ! -L "$SNAP" ]] || exit 4
[[ "$(sha256sum "$SNAP" | awk '{print $1}')" == "$BASE_ELF" ]] || exit 4
sha256sum "$SNAP" > "$EV/pr91-old-elf-before.sha256"
echo "PR91_ELF_SNAPSHOT=$SNAP"
STATE=VERIFY_EXACT_13_GENERAL_GOT_UPSTREAM_CHANGES
echo "STAGE=$STATE"
if ! git -C "$SRC" cat-file -e "$PIN^{commit}" 2>/dev/null; then
    git -c http.version=HTTP/1.1 -C "$SRC" fetch -q --no-tags --no-recurse-submodules https://github.com/Chreece/shadPS4.git "$PIN" > "$EV/git-fetch.log" 2>&1 || {
       tail -50 "$EV/git-fetch.log";exit 5;
    }
fi
git -C "$SRC" merge-base --is-ancestor "$BASE" "$PIN" || exit 5
EXPECTED=$(printf '%s\n' src/core/mapped_guest_range.h src/core/memory.cpp src/video_core/amdgpu/buffer_size.h src/video_core/amdgpu/resource.h src/video_core/buffer_cache/buffer_cache.cpp src/video_core/buffer_cache/buffer_cache.h src/video_core/renderer_vulkan/vk_rasterizer.cpp src/video_core/renderer_vulkan/vk_rasterizer.h src/video_core/texture_cache/texture_cache.cpp src/video_core/texture_cache/types.h tests/gcn_buffer_size_64_test.cpp tests/guest_mapped_range/CMakeLists.txt tests/guest_mapped_range/test_guest_mapped_range.cpp)
ACTUAL="$(git -C "$SRC" diff --name-only "$BASE" "$PIN")"
[[ "$EXPECTED" == "$ACTUAL" ]] || { echo "UNEXPECTED_DIFF=$ACTUAL";exit 5; }
git -C "$SRC" diff --stat "$BASE" "$PIN" > "$EV/13-changed-files.txt"
git -C "$SRC" switch -q --detach "$PIN"
[[ "$(git -C "$SRC" rev-parse HEAD)" == "$PIN" ]] || exit 5
[[ -z "$(git -C "$SRC" status --porcelain --untracked-files=no)" ]] || exit 5
grep -Fq 'GetSize64() const noexcept' "$SRC/src/video_core/amdgpu/resource.h" || exit 5
grep -Fq 'set_writes.emplace_back()' "$SRC/src/video_core/renderer_vulkan/vk_rasterizer.cpp" || exit 5
if grep -Fq 'set_writes.resize(' "$SRC/src/video_core/renderer_vulkan/vk_rasterizer.cpp"; then
    echo "STALE_PUSH_DESCRIPTOR_RESIZE";exit 5;
fi
echo "EXACT_13_FILE_UPSTREAM_GOT_SOURCE=PASS"
STATE=RUN_GOT_NATIVE_VMA_AND_WIDE_GCN_REGRESSIONS
echo "STAGE=$STATE"
for TESTFILE in "$SRC/tests/guest_mapped_range/test_guest_mapped_range.cpp" "$SRC/tests/gcn_buffer_size_64_test.cpp"; do
    NAME="$(basename "$TESTFILE" .cpp)"
    g++ -std=c++23 -O2 -DNDEBUG -Wall -Wextra -Werror -pedantic -I"$SRC/src" "$TESTFILE" -o "$EV/$NAME" >> "$EV/fixture-build.log" 2>&1 || {
       tail -80 "$EV/fixture-build.log";exit 6;
    }
    "$EV/$NAME" >> "$EV/fixture-result.log" 2>&1 || exit 6
done
grep -Fq "VMA_RANGE_PASS=near_max_no_overflow" "$EV/fixture-result.log" || exit 6
grep -Fq "GCN_VSHARP_64BIT_RANGE=PASS" "$EV/fixture-result.log" || exit 6
echo "NATIVE_MEMORY_VMA_AND_VSHARP_TESTS=PASS"
STATE=COMPILE_CHANGED_NATIVE_OBJECTS_FIRST
echo "STAGE=$STATE"
for OBJECT in CMakeFiles/shadps4.dir/src/core/memory.cpp.o CMakeFiles/shadps4.dir/src/video_core/texture_cache/texture_cache.cpp.o CMakeFiles/shadps4.dir/src/video_core/buffer_cache/buffer_cache.cpp.o CMakeFiles/shadps4.dir/src/video_core/renderer_vulkan/vk_rasterizer.cpp.o; do
    nice -n 10 ninja -C "$BUILD" -j 2 "$OBJECT" >> "$EV/native-objects.log" 2>&1 || {
       tail -120 "$EV/native-objects.log";exit 7;
    }
done
echo "FOUR_NATIVE_PRODUCTION_OBJECTS=PASS"
STATE=INCREMENTAL_FULL_NATIVE_LINK
echo "STAGE=$STATE"
nice -n 10 cmake --build "$BUILD" --target shadps4 --parallel 2 > "$EV/build-native.log" 2>&1 || {
    tail -150 "$EV/build-native.log";exit 8;
}
[[ -x "$BIN" && ! -L "$BIN" ]] || exit 8
NEW_SHA="$(sha256sum "$BIN" | awk '{print $1}')"
[[ "$NEW_SHA" != "$BASE_ELF" ]] || exit 8
sha256sum "$BIN" > "$EV/unified-native-binary.sha256"
ldd "$BIN" > "$EV/ldd.txt" 2>&1 || exit 8
if grep -Fq 'not found' "$EV/ldd.txt"; then echo "MISSING_SHARED_LIB";exit 8; fi
[[ "$(sha256sum "$SNAP" | awk '{print $1}')" == "$BASE_ELF" ]] || exit 8
grep -Ein '(^|[ :])warning:' "$EV/build-native.log" > "$EV/compiler-warnings.txt" || true
STATE=PR94_GENERIC_GOT_UPSTREAM_NATIVE_LINK_PASS_GAME_NOT_RUN
echo "NEW_PR94_NATIVE_SHA256=$NEW_SHA"
echo "PR91_OLD_ELF_PROTECTED=YES"
echo "NO_GAME_LAUNCHED_NO_GPU_DISRUPTION=YES"
