#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
# SPDX-License-Identifier: GPL-2.0-or-later
# Correct generic host idle loading redraw starvation after proven VideoOut flip enqueue.
# Strict frame FIFO ownership, native baseline protection and shared emulator diary.
set -Eeuo pipefail
umask 077
export GIT_TERMINAL_PROMPT=0

ROOT="$HOME/projects/shadps4"
TESTS="$ROOT/GodOfWarRagnarok/tests"
LOGS="$ROOT/logs"
OLD="$TESTS/gow-upstream-got-20261025-145442-451241"
BASE_SOURCE="$OLD/source"
BASE_ELF="$OLD/build/shadps4"
BASE_SHA="7604bae00c78b72e163138327f61a2f92bf549ac66446487d1ab7370a2f9b4d1"
PROTECTED="$ROOT/GodOfWarRagnarok/build/shadps4"
PROTECTED_SHA="8de2106a3e0d410904d9c8d6a2903ba6f1f65986af3c848b0bf274a4f4b45c8e"
SOURCE_REV="37d55cacbaf1f9fc75c696a679eb8609965a680c"
RUNNER_REV="448c20c756accf1b9148acde0622bf7db4a48b63"
RUNNER_BLOB="223a8e6ef7916e06bfd4f0ccefc90c92d5b097a0"
mkdir -p "$LOGS" "$TESTS"

if (( $# == 0 )) || [[ "$1" != "--worker" ]]; then
    [[ $# -le 1 ]] || { echo "USAGE: bash $0 [game-id-or-path]" >&2; exit 2; }
    GAME="CUSA34384"
    if (( $# == 1 )); then GAME="$1"; fi
    [[ -n "$GAME" && "$GAME" != -* && "$GAME" != *$'\n'* ]] || exit 2
    command -v nohup >/dev/null && command -v setsid >/dev/null || exit 2
    TAG="shadps4-guest-present-$(date +%Y%m%d-%H%M%S)-${BASHPID}"
    # Validate before creating an empty file or claiming the worker started.
    # Keep this identical to the worker-side tag contract.
    [[ "$TAG" =~ ^shadps4-guest-present-[0-9]{8}-[0-9]{6}-[0-9]+$ ]] || {
        echo "ERROR_INVALID_RUN_TAG=$TAG" >&2; exit 2;
    }
    LOG="$LOGS/$TAG.log"
    (set -C; : > "$LOG") || { echo "LOG_ALREADY_EXISTS=$LOG" >&2; exit 2; }
    nohup setsid -w bash "$0" --worker "$TAG" "$GAME" </dev/null >"$LOG" 2>&1 &
    PID=$!
    disown "$PID" 2>/dev/null || true
    echo "TEST=GENERIC_NONBLOCKING_IDLE_FRAME_OWNERSHIP"
    echo "SOURCE=$SOURCE_REV"
    echo "WORKER_PID=$PID"
    echo "MONITOR=tail -f '$LOG'"
    echo "BUILD_ARCHIVE=$LOGS/$TAG-build.tar.gz"
    echo "RUNTIME_ARCHIVE=$LOGS/$TAG.tar.gz"
    echo "GAME=$GAME"
    echo "SSH_AND_PROTECTED_BINARIES=UNCHANGED"
    exit 0
fi

[[ $# -eq 3 ]] || exit 2
TAG="$2"
GAME="$3"
[[ "$TAG" =~ ^shadps4-guest-present-[0-9]{8}-[0-9]{6}-[0-9]+$ ]] || exit 2
[[ -n "$GAME" && "$GAME" != -* && "$GAME" != *$'\n'* ]] || exit 2
WORK="$TESTS/shadps4-srt-findilsb-20261026-160302-916073"
EXPECTED_OLD_SOURCE="77287fc18f5bb06137927be4863bdd66d584336e"
SRC="$WORK/source"
TEST_BUILD="$WORK/build-tests"
BUILD="$WORK/build"
REPORT="$WORK/idle-flip-$TAG"
BINARY="$BUILD/shadps4"
ARCHIVE="$LOGS/$TAG-build.tar.gz"
mkdir -p "$REPORT"
RECORDER="$REPORT/record-emulator-trial.py"
RECORDER_SHA="1593a0f720d1e7ab0d2392746693839b67162959"
UPSTREAM_SHA="2cdefaa1298c6e4ece6c74bd84e90f3906282d51"

finish() {
    local rc=$?
    trap - EXIT
    set +e
    echo "WORKER_EXIT=$rc"
    if [[ -f "$BASE_ELF" ]]; then
        local sha
        sha="$(sha256sum "$BASE_ELF" | cut -d' ' -f1)"
        echo "STABLE_BASELINE_AFTER=$sha"
        if [[ "$sha" != "$BASE_SHA" ]]; then rc=86; echo "ERROR_STABLE_BASELINE_MODIFIED"; fi
    fi
    if [[ -f "$PROTECTED" ]]; then
        local sha
        sha="$(sha256sum "$PROTECTED" | cut -d' ' -f1)"
        echo "PROTECTED_BINARY_AFTER=$sha"
        if [[ "$sha" != "$PROTECTED_SHA" ]]; then rc=87; echo "ERROR_PROTECTED_BINARY_MODIFIED"; fi
    fi
    printf '%s\n' "$rc" > "$REPORT/exit-code"
    {
        echo "SOURCE_PIN=$SOURCE_REV"
        echo "STABLE_BASE_SHA=$BASE_SHA"
        echo "RUNTIME_ARCHIVE=$LOGS/$TAG.tar.gz"
        [[ ! -f "$BINARY" ]] || sha256sum "$BINARY"
        [[ ! -f "$REPORT/ctest.txt" ]] || cat "$REPORT/ctest.txt"
    } > "$REPORT/summary.txt"
    tar -czf "$ARCHIVE" -C "$LOGS" "$TAG.log" \
        -C "$REPORT" . \
        > /dev/null 2>&1
    echo "BUILD_UPLOAD_THIS=$ARCHIVE"
    # Pre-build lock contention is a real failed trial too. The normal
    # recorder is downloaded only after source prechecks, so recover its
    # exact SHA-pinned copy here when an earlier stage exits.
    if [[ ! -s "$RECORDER" ]]; then
        curl -4fsSL --retry 2 --retry-delay 1 \
          "https://raw.githubusercontent.com/Chreece/shadPS4/178376d393051d8026319bc0154d3b36d0fcc087/tools/development/record_emulator_trial.py" \
          -o "$RECORDER" || echo "PINNED_DIARY_RECORDER_UNAVAILABLE"
    fi
    if [[ -f "$RECORDER" && -s "$ARCHIVE" &&
          "$(git hash-object "$RECORDER")" == "$RECORDER_SHA" ]]; then
        local decision="BLOCKED"
        [[ "$rc" -ne 0 ]] || decision="BUILD_PASS_RUNTIME_PENDING"
        python3 -B "$RECORDER" --archive "$ARCHIVE" --game "$GAME" \
          --source "$SOURCE_REV" --upstream "$UPSTREAM_SHA" \
          --fix-id "guest-swapchain-timeout-safe-retirement" \
          --change "Remove idle presentation thread GPU waits; take exclusive ready frame ownership and preserve guest video presentation" \
          --emulator-effect "BUILD_OR_OBSERVER_EXIT_$rc" --game-effect NOT_OBSERVED \
          --decision "$decision" \
          --next "Check prepared game frames enter presenter and become real pixels after host idle correction; do not repeat previous tests" \
          || echo "BUILD_DIARY_RECORDER_FAILED_INSPECT_ARCHIVE"
    fi
    if [[ -f "$LOGS/$TAG.tar.gz" ]]; then
        echo "GAMEPLAY_UPLOAD_THIS=$LOGS/$TAG.tar.gz"
    fi
    echo "SSH_SESSION=OPEN"
    exit "$rc"
}
trap finish EXIT

# Two global build locks serialize native GoW/GoT revisions; never bypass
# them, delete their files or signal competing build/game processes.
# Acquire BOTH only if immediately available. If one is busy, release any
# partial acquisition before retrying: this prevents opposite-order deadlocks.
exec 9>"$LOGS/gow-upstream-got-build.lock"
exec 8>"$LOGS/shadps4-gow-srt-phi.lock"
# Prevent duplicate queued uniform-lane workers from compiling the same ELF.
exec 7>"$LOGS/shadps4-guest-present-queue.lock"
flock -n 7 || {
    echo "ANOTHER_IDLE_FRAME_RUN_ALREADY_QUEUED_OR_RUNNING"; exit 3;
}
echo "BUILD_LOCK_POLICY=SERIALIZED_NO_KILLS_NO_BYPASS"
LOCK_ACQUIRED=NO
LOCK_WAIT_ATTEMPTS=240
for (( attempt=1; attempt<=LOCK_WAIT_ATTEMPTS; ++attempt )); do
    if flock -n 9; then
        if flock -n 8; then
            LOCK_ACQUIRED=YES
            break
        fi
        # Never hold lock 9 while waiting on lock 8.
        flock -u 9
        busy_lock="shadps4-gow-srt-phi.lock"
    else
        busy_lock="gow-upstream-got-build.lock"
    fi
    if (( attempt == 1 || attempt % 6 == 0 )); then
        echo "BUILD_LOCK_WAITING_FOR=$busy_lock RETRY=$attempt/$LOCK_WAIT_ATTEMPTS"
        echo "OTHER_BUILD_PRESERVED=YES; NOTHING_KILLED=YES"
    fi
    sleep 10
done
[[ "$LOCK_ACQUIRED" == YES ]] || {
    echo "BUILD_LOCK_TIMEOUT_NO_COMPILATION_OR_GAME"; exit 3;
}
echo "BOTH_BUILD_LOCKS_ACQUIRED=YES"
echo "STAGE=RESUME_CACHED_UNIFORM_LANE_BUILD"

for command in git cmake ninja ctest python3 curl flock tar sha256sum nice timeout ldd cmp; do
    command -v "$command" >/dev/null || { echo "MISSING_COMMAND=$command"; exit 3; }
done
for command in identify pgrep xdpyinfo; do
    command -v "$command" >/dev/null || { echo "MISSING_VISUAL_LIVENESS_COMMAND=$command"; exit 3; }
done
[[ -d "$BASE_SOURCE" && -x "$BASE_ELF" ]] || {
    echo "MISSING_RETAINED_120_SECOND_SOURCE_OR_ELF"; exit 3;
}
[[ "$(sha256sum "$BASE_ELF" | cut -d' ' -f1)" == "$BASE_SHA" ]] || {
    echo "STABLE_120_SECOND_CANDIDATE_HASH_MISMATCH_NO_CHANGE"; exit 3;
}
[[ ! -f "$PROTECTED" || "$(sha256sum "$PROTECTED" | cut -d' ' -f1)" == "$PROTECTED_SHA" ]] || {
    echo "PROTECTED_ORIGINAL_HASH_MISMATCH_NO_CHANGE"; exit 3;
}
[[ -f "$SRC/.git" && -f "$SRC/tests/gcn/test_srt_phi.cpp" ]] || {
    echo "CACHED_SOURCE_WORKTREE_MISSING_NO_REBUILD"; exit 3;
}
[[ -f "$BUILD/build.ninja" && -x "$TEST_BUILD/tests/shadps4_gcn_test" ]] || {
    echo "CACHED_BUILD_MISSING_NO_REBUILD"; exit 3;
}
CURRENT_CACHED_SOURCE="$(git -C "$SRC" rev-parse HEAD)"
[[ "$CURRENT_CACHED_SOURCE" == "$EXPECTED_OLD_SOURCE" ||
   "$CURRENT_CACHED_SOURCE" == "26b7b29c21b9eb97404d58812fb13b2411b1fbef" ||
   "$CURRENT_CACHED_SOURCE" == "770a6113829e368056e42e94cac07844024ef468" ||
   "$CURRENT_CACHED_SOURCE" == "8984f89b748ee1f11de2e8caac2af7819e22484e" ||
   "$CURRENT_CACHED_SOURCE" == "ea37150522af3052bbe685f39976b36d80bc20f9" ||
   "$CURRENT_CACHED_SOURCE" == "$SOURCE_REV" ]] || {
    echo "CACHED_SOURCE_NOT_AN_ALLOWED_PINNED_REVISION=$CURRENT_CACHED_SOURCE"; exit 3;
}
[[ -z "$(git -C "$SRC" status --porcelain --untracked-files=no)" ]] || {
    echo "CACHED_SOURCE_HAS_TRACKED_CHANGES"; exit 3;
}
[[ -s "$LOGS/shadps4-srt-findilsb-20261026-160302-916073-build.tar.gz" ]] || {
    echo "OLD_FAILED_RUN_EVIDENCE_MISSING"; exit 3;
}
# Retain the known 120-second successful FindILsb executable before this
# incremental build overwrites its prior path. This is an isolated test copy,
# never a production or ES-DE executable update.
PRIOR_FINDILSB_SHA="8316d6cb2243f0d4ffd8f838a02046738d67bff426e6f344698b25b6f421cf28"
PRIOR_SNAPSHOT="$TESTS/preserved-native-candidates/findilsb-14975c44/shadps4"
# The proven 120s ELF was separately snapshotted before the rejected
# 33-second GPU Phi candidate. Never copy that unverified ELF as baseline.

[[ -f "$PRIOR_SNAPSHOT" &&
   "$(sha256sum "$PRIOR_SNAPSHOT" | cut -d' ' -f1)" == "$PRIOR_FINDILSB_SHA" ]] || {
    echo "PREVIOUS_GOOD_CANDIDATE_SNAPSHOT_INVALID"; exit 3;
}
echo "PRESERVED_FINDILSB_120_SECOND_CANDIDATE=$PRIOR_SNAPSHOT"
echo "PRESERVED_FINDILSB_SHA256=$PRIOR_FINDILSB_SHA"
echo "BASELINE_120_SECOND_ELF_UNTOUCHED=PASS"
echo "STAGE=PRESERVE_PREVIOUS_PRESENTATION_CANDIDATE"
[[ -x "$BINARY" && ! -L "$BINARY" ]] || {
    echo "CURRENT_CACHED_ELF_MISSING_OR_SYMLINK"; exit 3;
}
SNAP_DIR="$TESTS/preserved-native-candidates/before-guest-present-$TAG"
mkdir -p "$SNAP_DIR"
cp --reflink=auto -- "$BINARY" "$SNAP_DIR/shadps4"
cmp -s -- "$BINARY" "$SNAP_DIR/shadps4" || {
    echo "PREVIOUS_PRESENTATION_CANDIDATE_SNAPSHOT_MISMATCH"; exit 3;
}
printf '%s\n' "$CURRENT_CACHED_SOURCE" > "$SNAP_DIR/source-commit"
sha256sum "$SNAP_DIR/shadps4" > "$REPORT/previous-native-candidate-sha256.txt"
echo "PRESERVED_PREVIOUS_CANDIDATE=$SNAP_DIR/shadps4"
echo "PREVIOUS_CACHED_SOURCE=$CURRENT_CACHED_SOURCE"
echo "STAGE=UNIVERSAL_EMULATOR_DIARY_PRECHECK"
curl -4fsSL --retry 3 --retry-delay 2 \
  "https://raw.githubusercontent.com/Chreece/shadPS4/178376d393051d8026319bc0154d3b36d0fcc087/tools/development/record_emulator_trial.py" \
  -o "$RECORDER"
[[ "$(git hash-object "$RECORDER")" == "$RECORDER_SHA" ]] || {
    echo "UNIVERSAL_DIARY_WRITER_HASH_MISMATCH"; exit 3;
}
# The 17:55 uniform-lane attempt ended before compilation because another
# GoW/GoT worker held the shared build lock. The old launcher did not
# download the diary recorder before the lock check. Append that real
# failed trial now; the recorder deduplicates the immutable archive.
# Prior failed lock trial is already immutably recorded from the 18:01 run.
# Prior failures already recorded with their original SHA; avoid duplicate entries.
echo "STAGE=PIN_SUNSHINE_X11_MONITOR_NO_SESSION_RESTART"
if ! DISPLAY=:0 timeout 4 xdpyinfo -display :0 >/dev/null 2>&1; then
    echo "SUNSHINE_X11_DISPLAY_0_INACCESSIBLE_NO_GAME_OR_MONITOR_CHANGE"; exit 8;
fi
export DISPLAY=:0
{
    echo "REUSE_EXISTING_SUNSHINE_DISPLAY=:0"
    echo "MONITOR_CHANGE=NONE"
    echo "SUNSHINE_PROCESS:"
    if command -v pgrep >/dev/null; then pgrep -ax sunshine || :; fi
    echo "XORG_PROCESS:"
    if command -v pgrep >/dev/null; then pgrep -ax Xorg || :; fi
    echo "X11_MONITORS:"
    if command -v xrandr >/dev/null; then timeout 4 xrandr --listmonitors || :; fi
    echo "X11_OUTPUT_MODES:"
    if command -v xrandr >/dev/null; then timeout 4 xrandr --query || :; fi
} > "$REPORT/sunshine-monitor-before.txt" 2>&1
echo "SUNSHINE_MONITOR_SNAPSHOT_BEFORE=$REPORT/sunshine-monitor-before.txt"
echo "STAGE=FETCH_PINNED_NONBLOCKING_IDLE_FRAME_SOURCE"
if ! git -C "$BASE_SOURCE" cat-file -e "$SOURCE_REV^{commit}" 2>/dev/null; then
    git -c http.version=HTTP/1.1 -C "$BASE_SOURCE" fetch -q --no-tags --no-recurse-submodules \
        https://github.com/Chreece/shadPS4.git "$SOURCE_REV"
fi
git -C "$BASE_SOURCE" cat-file -e "$SOURCE_REV^{commit}"
if [[ "$CURRENT_CACHED_SOURCE" != "$SOURCE_REV" ]]; then
    git -C "$SRC" switch --detach "$SOURCE_REV"
fi
[[ "$(git -C "$SRC" rev-parse HEAD)" == "$SOURCE_REV" ]] || {
    echo "RESUME_GIT_SOURCE_PIN_MISMATCH"; exit 4;
}
grep -Fqx 'class Logger final : public BaseDevice {' "$SRC/src/core/file_sys/devices/logger.h" || {
    echo "LOGGER_PUBLIC_INHERITANCE_FIX_MISSING"; exit 4;
}
grep -Fq 'std::is_convertible_v<Devices::Logger*, Devices::BaseDevice*>' "$SRC/src/core/file_sys/fs.cpp" || {
    echo "LOGGER_COMPILE_TIME_CONVERSION_TEST_MISSING"; exit 4;
}
echo "SOURCE_SWITCH_FLIP_TRACE_TO_NONBLOCKING_IDLE_FRAME_CACHED=PASS"
echo "ORIGINAL_TRIAL_ARCHIVES_AND_DIARY_UNCHANGED=YES"
echo "STAGE=VERIFY_CACHED_SUBMODULES"
git -C "$SRC" submodule status --recursive > "$REPORT/submodules.txt"
if grep -Eq '^[-+U]' "$REPORT/submodules.txt"; then
    echo "CACHED_SUBMODULE_REVISION_MISMATCH"; exit 4;
fi
[[ -z "$(git -C "$SRC" status --porcelain --untracked-files=no)" ]] || {
    echo "SOURCE_WORKTREE_TRACKED_FILES_DIRTY"; exit 4;
}
[[ -f "$SRC/tests/gcn/test_srt_phi.cpp" ]] || {
    echo "DYNAMIC_SRT_GTEST_MISSING"; exit 4;
}
grep -Fq 'OpAtomicOr(U32[1], fault_ptr' "$SRC/src/shader_recompiler/backend/spirv/spirv_emit_context.cpp" || {
    echo "ATOMIC_FAULT_FIX_MISSING"; exit 4;
}
grep -Fq 'dma_policy.NeedsGpuRead()' "$SRC/src/shader_recompiler/ir/passes/shader_info_collection_pass.cpp" || {
    echo "UNIVERSAL_DYNAMIC_SRT_DMA_MISSING"; exit 4;
}
grep -Fq 'GuestGpuAddressLimit - sizeof(u32) + 1' "$SRC/src/shader_recompiler/backend/spirv/spirv_emit_context.cpp" || {
    echo "BDA_GUEST_RANGE_GUARD_MISSING"; exit 4;
}
echo "SOURCE_COMMIT_VERIFIED=$SOURCE_REV"
echo "STAGE=VERIFY_DISPLAY_LIVENESS_OBSERVER_BEFORE_BUILD"
DISPLAY_OBSERVER_URL="https://raw.githubusercontent.com/Chreece/shadPS4/37d55cacbaf1f9fc75c696a679eb8609965a680c/tools/development/shadps4_native_display_liveness_observer_20261027.py"
curl -4fsSL --retry 3 --retry-delay 2 "$DISPLAY_OBSERVER_URL" -o "$REPORT/display-liveness-observer.py"
[[ "$(git hash-object "$REPORT/display-liveness-observer.py")" == "4b741d644c869b1806349e8893aba2bad2d56a62" ]] || {
    echo "DISPLAY_LIVENESS_OBSERVER_HASH_MISMATCH"; exit 5;
}
python3 -B -m py_compile "$REPORT/display-liveness-observer.py" || {
    echo "DISPLAY_LIVENESS_OBSERVER_PYTHON_SYNTAX_FAILED"; exit 5;
}
echo "DISPLAY_LIVENESS_OBSERVER_PREFLIGHT=PASS"

echo "STAGE=REUSE_FFMPEG_FROM_PROVEN_BASELINE"
[[ -f "$SRC/externals/ffmpeg-core/CMakeLists.txt" ]] || {
    echo "CHECKPOINT_FFMPEG_SUBMODULE_MISSING"; exit 4;
}
FFMPEG_COMMIT="$(git -C "$SRC/externals/ffmpeg-core" rev-parse HEAD)"
FFMPEG_SHORT="$(git -C "$SRC/externals/ffmpeg-core" rev-parse --short HEAD)"
BASE_FFMPEG_COMMIT="$(git -C "$BASE_SOURCE/externals/ffmpeg-core" rev-parse HEAD)"
[[ "$FFMPEG_COMMIT" == "$BASE_FFMPEG_COMMIT" ]] || {
    echo "BASELINE_FFMPEG_COMMIT_DIFFERS_NO_CACHE_REUSE"; exit 4;
}
FFMPEG_ASSET="$WORK/ffmpeg-$FFMPEG_SHORT.zip"
BASE_ASSET="$OLD/build/externals/ffmpeg-$FFMPEG_SHORT.zip"
BASE_LIBS="$OLD/build/externals/ffmpeg-$FFMPEG_SHORT/lib"
python3 - "$BASE_ASSET" "$BASE_LIBS" "$FFMPEG_ASSET" <<'PYFF'
from pathlib import Path, PurePosixPath
import hashlib, sys, zipfile
old_zip, libs, target = map(Path, sys.argv[1:])
names = ["lib"+n+".a" for n in ("avformat","avcodec","swscale","avutil","avfilter","swresample")]
def verify(archive):
    with zipfile.ZipFile(archive) as z:
        listing = set(z.namelist())
        assert all(n in listing and z.getinfo(n).file_size > 4096 for n in names)
        assert all(not n.startswith("/") and ".." not in PurePosixPath(n).parts for n in listing)
        assert z.testzip() is None
if old_zip.is_file():
    verify(old_zip)
    target.write_bytes(old_zip.read_bytes())
    origin="original_matching_ffmpeg_prebuilt_zip"
elif all((libs/n).is_file() and not (libs/n).is_symlink() for n in names):
    with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_STORED) as z:
        for n in names:
            with (libs/n).open("rb") as fd:
                assert fd.read(8) in (b"!<arch>\n",b"!<thin>\n"),n
            z.write(libs/n,n)
    verify(target)
    origin="original_matching_ffmpeg_static_archives"
else:
    origin="cache_missing"
if target.exists():
    print("FFMPEG_REUSED_FROM="+origin)
    print("FFMPEG_PREBUILT_SHA256="+hashlib.sha256(target.read_bytes()).hexdigest())
else:
    print("FFMPEG_CACHE_MISSING_WILL_FETCH_OFFICIAL_RELEASE")
PYFF
if [[ ! -s "$FFMPEG_ASSET" ]]; then
    echo "STAGE=FETCH_OFFICIAL_FFMPEG_RELEASE_IPV4"
    curl -4fsSL --retry 3 --retry-delay 2 \
        "https://github.com/shadps4-emu/ext-ffmpeg-core/releases/download/$FFMPEG_SHORT/ffmpeg-linux-x64.zip" \
        -o "$FFMPEG_ASSET" || {
        echo "NO_CACHED_EXACT_FFMPEG_ARCHIVES_AND_OFFICIAL_RELEASE_UNAVAILABLE"; exit 4;
    }
fi
python3 - "$FFMPEG_ASSET" <<'PYZIP'
import sys,zipfile
names={"lib"+x+".a" for x in ("avformat","avcodec","swscale","avutil","avfilter","swresample")}
with zipfile.ZipFile(sys.argv[1]) as z:
    assert names.issubset(z.namelist()) and z.testzip() is None
print("FFMPEG_ASSET_VALIDATED",flush=True)
PYZIP
for build in "$BUILD" "$TEST_BUILD"; do
    mkdir -p "$build/externals"
    target="$build/externals/ffmpeg-$FFMPEG_SHORT.zip"
    # Do not rewrite identical cached archives: keep Ninja/CMake download
    # timestamps stable and reuse objects from the previously failed run.
    if [[ ! -s "$target" ]] || ! cmp -s "$FFMPEG_ASSET" "$target"; then
        cp "$FFMPEG_ASSET" "$target"
    fi
done
echo "STAGE=REUSE_EXISTING_GOOGLETEST"
GTEST_SRC="$WORK/googletest-v1.17.0"
if [[ ! -f "$GTEST_SRC/CMakeLists.txt" ]]; then
    echo "CACHED_GOOGLETEST_MISSING_NO_CLONE"; exit 5
fi
git -C "$GTEST_SRC" describe --tags --exact-match | grep -Fxq v1.17.0 || {
    echo "GOOGLETEST_NOT_TAG_V1_17_0"; exit 5;
}
echo "STAGE=RECONFIGURE_CACHED_GCN_TEST"
cmake -S "$SRC" -B "$TEST_BUILD" -G Ninja -DCMAKE_BUILD_TYPE=Release \
    -DENABLE_TESTS=ON -DFETCHCONTENT_SOURCE_DIR_GOOGLETEST="$GTEST_SRC" \
    > "$REPORT/configure-tests.log" 2>&1 || {
    tail -120 "$REPORT/configure-tests.log"; exit 5;
}
echo "STAGE=REUSE_CACHED_GCN_TEST_OBJECTS"
echo "BUILD_TEST_LOG=$REPORT/build-tests.log"
nice -n 10 cmake --build "$TEST_BUILD" --target shadps4_gcn_test --parallel 2 \
    > "$REPORT/build-tests.log" 2>&1 || {
    tail -120 "$REPORT/build-tests.log"; exit 5;
}
[[ -x "$TEST_BUILD/tests/shadps4_gcn_test" ]] || {
    echo "GCN_TEST_BINARY_NOT_BUILT"; exit 6;
}
echo "STAGE=RUN_56_IDLE_FRAME_AND_SRT_REGRESSIONS"
ctest --test-dir "$TEST_BUILD" -N -R '^(SrtInvariantPhi|SrtDynamicDma|NativeWarningContracts|SrtFindILsb32|GpuFaultRingReuse|Pm4AcquireMemBarrier|ScalarExecDivergence|VideoOutFirstFrameTrace|HostIdleFrameReuse)\.' > "$REPORT/ctest-list.txt"
grep -Eq 'Total Tests: 56([[:space:]]|$)' "$REPORT/ctest-list.txt" || {
    cat "$REPORT/ctest-list.txt"; echo "EXPECTED_56_GENERIC_TESTS_MISSING"; exit 6;
}
ctest --test-dir "$TEST_BUILD" --output-on-failure --no-tests=error \
    -R '^(SrtInvariantPhi|SrtDynamicDma|NativeWarningContracts|SrtFindILsb32|GpuFaultRingReuse|Pm4AcquireMemBarrier|ScalarExecDivergence|VideoOutFirstFrameTrace|HostIdleFrameReuse)\.' | tee "$REPORT/ctest.txt"
echo "ALL_56_IDLE_FRAME_AND_SRT_REGRESSIONS=PASS"
echo "STAGE=RECONFIGURE_CACHED_EMULATOR"
cmake -S "$SRC" -B "$BUILD" -G Ninja -DCMAKE_BUILD_TYPE=Release -DENABLE_TESTS=OFF \
    > "$REPORT/configure-native.log" 2>&1 || {
    tail -120 "$REPORT/configure-native.log"; exit 7;
}
grep -Fq 'TryPopMatchingReadyIdleFrame(' "$SRC/src/video_core/renderer_vulkan/vk_presenter.cpp" &&
grep -Fq 'TryGetRenderFrameForHostIdle()' "$SRC/src/video_core/renderer_vulkan/vk_presenter.cpp" &&
grep -Fq 'IsRealGuestFrameSubmission(' "$SRC/src/video_core/renderer_vulkan/vk_presenter.cpp" &&
grep -Fq 'presenter->Present(frame, false, false);' "$SRC/src/core/libraries/videoout/driver.cpp" &&
grep -Fq 'TryPopMatchingReadyIdleFrame' "$SRC/src/video_core/renderer_vulkan/idle_frame_reuse.h" &&
grep -Fq 'HostIdleFrameReuse, EmptyPoolNeverQueriesGpuOrBlocks' "$SRC/tests/gcn/test_idle_frame_reuse.cpp" &&
grep -Fq 'AcquireNextImage(bool wait_for_image)' "$SRC/src/video_core/renderer_vulkan/vk_swapchain.cpp" &&
grep -Fq 'if (needs_recreation)' "$SRC/src/video_core/renderer_vulkan/vk_swapchain.cpp" &&
grep -Fq "guest_acquire_timeout_ns = 32'000'000" "$SRC/src/video_core/renderer_vulkan/vk_swapchain.cpp" &&
grep -Fq 'swapchain.NeedsRecreation()' "$SRC/src/video_core/renderer_vulkan/vk_presenter.cpp" &&
grep -Fq 'stage=guest_acquire_timeout' "$SRC/src/video_core/renderer_vulkan/vk_presenter.cpp" &&
grep -Fq 'stage=guest_flush_returned' "$SRC/src/video_core/renderer_vulkan/vk_presenter.cpp" &&
grep -Fq 'swapchain.AcquireNextImage(is_game_frame)' "$SRC/src/video_core/renderer_vulkan/vk_presenter.cpp" &&
grep -Fq 'release_skipped_frame();' "$SRC/src/video_core/renderer_vulkan/vk_presenter.cpp" &&
grep -Fq 'VIDEOOUT_PRESENT_LOOP tick=' "$SRC/src/core/libraries/videoout/driver.cpp" || {
    echo "HOST_IDLE_FRAME_SOURCE_NOT_PINNED"; exit 7;
}
echo "STAGE=PRECOMPILE_VIDEOOUT_IDLE_AND_PRESENTER"
echo "HOST_IDLE_PRECHECK_LOG=$REPORT/preflight-idle-flip.log"
nice -n 10 ninja -C "$BUILD" -j 2 CMakeFiles/shadps4.dir/src/core/libraries/videoout/driver.cpp.o CMakeFiles/shadps4.dir/src/video_core/renderer_vulkan/vk_presenter.cpp.o CMakeFiles/shadps4.dir/src/video_core/renderer_vulkan/vk_swapchain.cpp.o \
    > "$REPORT/preflight-idle-flip.log" 2>&1 || {
    echo "HOST_IDLE_FRAME_COMPILE_PRECHECK_FAILED"; tail -120 "$REPORT/preflight-idle-flip.log"; exit 7;
}
echo "HOST_IDLE_FRAME_COMPILE_PRECHECK=PASS"
echo "STAGE=COMPLETE_INCREMENTAL_NATIVE_IDLE_FRAME_BUILD"
echo "NATIVE_BUILD_LOG=$REPORT/build-native.log"
nice -n 10 cmake --build "$BUILD" --target shadps4 --parallel 2 \
    > "$REPORT/build-native.log" 2>&1 || {
    tail -140 "$REPORT/build-native.log"; exit 7;
}
[[ -x "$BINARY" ]] || { echo "NATIVE_BINARY_NOT_LINKED"; exit 7; }
echo "STAGE=CLASSIFY_NATIVE_AND_GCN_COMPILER_WARNINGS"
python3 - "$REPORT/build-native.log" "$REPORT/build-tests.log" "$REPORT/warnings-audit.txt" <<'PYWARN'
from pathlib import Path
from collections import Counter
import re
import sys

result = []
warning = re.compile(r'^(.+?):(\d+):(\d+): warning: (.*)$')
for label, path in (("native", Path(sys.argv[1])), ("tests", Path(sys.argv[2]))):
    counts = Counter()
    detail = []
    for line in path.read_text(errors="replace").splitlines():
        match = warning.match(line)
        if not match:
            continue
        file_path, lineno, column, message = match.groups()
        kind = ("third_party" if "/externals/" in file_path else
                "first_party" if "/source/src/" in file_path else
                "test_code" if "/source/tests/" in file_path else
                "system_or_generated")
        counts[kind] += 1
        detail.append(f"{kind}: {file_path}:{lineno}:{column}: {message}")
    result.append(f"{label.upper()} WARNINGS={sum(counts.values())}")
    result.extend(f"{key}={counts[key]}" for key in
                  ("first_party", "third_party", "test_code", "system_or_generated"))
    result.extend(detail)
    print(f"{label.upper()}_WARNING_COUNT={sum(counts.values())}")
    print(f"{label.upper()}_FIRST_PARTY_WARNING_COUNT={counts['first_party']}")
Path(sys.argv[3]).write_text("\n".join(result) + "\n")
PYWARN
echo "WARNING_AUDIT_REPORT=$REPORT/warnings-audit.txt"
"$BINARY" --help > "$REPORT/native-help.txt" 2>&1 || exit 5
! grep -Fq -- '--cpu-id-mode' "$REPORT/native-help.txt" || {
    echo "DYNAMORIO_COMPATIBILITY_FLAG_PRESENT"; exit 5;
}
ldd "$BINARY" > "$REPORT/ldd.txt"
! grep -q 'not found' "$REPORT/ldd.txt" || {
    echo "MISSING_NATIVE_LIBRARY"; exit 5;
}
CANDIDATE_SHA="$(sha256sum "$BINARY" | cut -d' ' -f1)"
echo "NEW_NATIVE_CANDIDATE_SHA=$CANDIDATE_SHA"
echo "STAGE=INSTALL_PINNED_EXISTING_UNIVERSAL_118_PLUS_2_OBSERVER"
RUNNER_ORIG="$WORK/pinned-gpu-headroom-observer.sh"
RUNNER="$WORK/idle-flip-observer.sh"
curl -4fsSL --retry 3 --retry-delay 2 \
    "https://raw.githubusercontent.com/Chreece/shadPS4/$RUNNER_REV/scripts/shadps4_gpu_headroom_ab_20261026.sh" \
    -o "$RUNNER_ORIG"
[[ "$(git hash-object "$RUNNER_ORIG")" == "$RUNNER_BLOB" ]] || {
    echo "OBSERVER_TEMPLATE_HASH_MISMATCH_NO_GAME"; exit 6;
}
python3 - "$RUNNER_ORIG" "$RUNNER" "$BINARY" "$CANDIDATE_SHA" "$SOURCE_REV" "$SRC" <<'PY'
from pathlib import Path
import sys
template, dest, binary, checksum, source_rev, source_dir = sys.argv[1:]
data = Path(template).read_text()
def exactly_once(old, new):
    global data
    assert data.count(old) == 1, f"Expected exactly one launcher field: {old[:75]}"
    data = data.replace(old, new)
exactly_once(
    'BIN="$ROOT/GodOfWarRagnarok/tests/gow-upstream-got-20261025-145442-451241/build/shadps4"',
    'BIN="' + binary + '"')
exactly_once(
    'EXPECTED=7604bae00c78b72e163138327f61a2f92bf549ac66446487d1ab7370a2f9b4d1',
    'EXPECTED=' + checksum)
exactly_once(
    'SOURCE_REV="803bb428731be794bdc44e38b56313d030a7403a"',
    'SOURCE_REV="' + source_rev + '"')
exactly_once(
    'SRC="$ROOT/GodOfWarRagnarok/tests/gow-upstream-got-20261025-145442-451241/source"',
    'SRC="' + source_dir + '"')
assert 'shadps4-gpu-headroom-ab-' in data
data = data.replace('shadps4-gpu-headroom-ab-', 'shadps4-guest-present-')
exactly_once('--fix-id gpu-device-lost-after-first-frame',
             '--fix-id guest-swapchain-timeout-safe-retirement')
exactly_once(
    'Same exact native emulator binary, host Ollama VRAM headroom A/B only; 11/21/26s frames and read-only kernel faults',
    'Nonblocking host loading redraw and frame FIFO ownership; no title hacks, fake frames or GPU shader overrides')
exactly_once(
    'Compare whether GPU context lost persists with additional free VRAM; inspect first-frame screenshots, amdgpu kernel events and real resource lifetime before general renderer changes',
    'Compare first present_begin, guest swapchain result, idle busy events, and authentic captured game frames')
exactly_once('--pr 73 --pr 71', '--pr 73')
exactly_once('OBSERVER_SHA=188deeb0a25e671e2308ac0acfe8ad81bc0adfc1',
             'OBSERVER_SHA=4b741d644c869b1806349e8893aba2bad2d56a62')
exactly_once(
    'https://raw.githubusercontent.com/Chreece/shadPS4/d9c87b7224bb8c20ac72282f6cf16a49565223c5/tools/development/shadps4_gpu_loss_early_observer_20261026.py',
    'https://raw.githubusercontent.com/Chreece/shadPS4/37d55cacbaf1f9fc75c696a679eb8609965a680c/tools/development/shadps4_native_display_liveness_observer_20261027.py')
exactly_once('target=16 if loaded and free<16*GIB else 8', 'target=8')
Path(dest).write_text(data)
PY
bash -n "$RUNNER"
echo "NATIVE_FLAGS=FULLSCREEN_AND_FPS_ONLY"
echo "VRAM=UNLOAD_OLLAMA_IF_REQUIRED_AND_RESTORE_AFTER_GAME"
echo "EXISTING_SAVES_ESDE_SSH=UNCHANGED"
echo "STAGE=118_PLUS_2_SECOND_NATIVE_RAGNAROK_GAMEPLAY"
echo "SUNSHINE_AND_MOONLIGHT_PROCESSES_WILL_NOT_BE_SIGNALED=YES"
echo "DISPLAY_PINNED_TO_EXISTING_SUNSHINE_MONITOR=$DISPLAY"
echo "FPS_RULE=ALL_BLACK_OR_MISSING_X11_WINDOW_IS_FAILURE"
echo "GUEST_ACQUIRE_MAX_WAIT_MS=32"
# Call the original verified observer worker directly: its source audits,
# isolated user profile, screenshot schedule and diary append all remain active.
# It owns only the game process group; the outer SSH session stays open.
set +e
bash "$RUNNER" --worker "$TAG" "$GAME"
rc=$?
set -e
echo "NATIVE_GAMEPLAY_OBSERVER_EXIT=$rc"
{
    echo "REUSE_EXISTING_SUNSHINE_DISPLAY=:0"
    echo "MONITOR_CHANGE=NONE"
    echo "SUNSHINE_PROCESS:"
    if command -v pgrep >/dev/null; then pgrep -ax sunshine || :; fi
    echo "XORG_PROCESS:"
    if command -v pgrep >/dev/null; then pgrep -ax Xorg || :; fi
    echo "X11_MONITORS:"
    if command -v xrandr >/dev/null; then timeout 4 xrandr --listmonitors || :; fi
    echo "X11_OUTPUT_MODES:"
    if command -v xrandr >/dev/null; then timeout 4 xrandr --query || :; fi
} > "$REPORT/sunshine-monitor-after.txt" 2>&1
echo "SUNSHINE_MONITOR_SNAPSHOT_AFTER=$REPORT/sunshine-monitor-after.txt"
# Quantify the full runtime resource-table logs, not merely 1.5 MB excerpts.
SRT_FULL_CONSOLE="$LOGS/$TAG-report/full-console.log"
if [[ -f "$SRT_FULL_CONSOLE" && ! -L "$SRT_FULL_CONSOLE" ]]; then
    {
        echo "SOURCE=$SOURCE_REV"
        echo "PRIOR_EXCERPT_FINDILSB_UNSUPPORTED=2357"
        echo "PRIOR_EXCERPT_DYNAMIC_PHI=635"
        echo "PRIOR_FULL_DYNAMIC_PHI=3906"
        echo "PRIOR_FULL_FAILED_SRT_OFFSETS=5591"
        echo "PRIOR_EXCERPT_FAILED_SRT_OFFSETS=2993"
        echo "CURRENT_FULL_CONSOLE_BYTES=$(stat -c %s "$SRT_FULL_CONSOLE")"
        echo "CURRENT_FINDILSB32_UNSUPPORTED=$(grep -Fc 'Unexpected instruction for offset computation, FindILsb32' "$SRT_FULL_CONSOLE" || :)"
        echo "CURRENT_IDLE_FREE_POOL_GPU_BUSY_SKIPS=$(grep -Fc 'VIDEOOUT_IDLE_FRAME_SKIP reason=free_frame_gpu_busy' "$SRT_FULL_CONSOLE" || :)"
        echo "CURRENT_IDLE_LAST_FRAME_BUSY_OR_UNOWNED_SKIPS=$(grep -Fc 'VIDEOOUT_IDLE_FRAME_SKIP reason=not_free_or_gpu_busy' "$SRT_FULL_CONSOLE" || :)"
        echo "VIDEO_FLIP_STAGE_GUEST_REQUESTED_LOG_MESSAGES=$(grep -Fc 'VIDEO_FLIP_STAGE stage=guest_requested' "$SRT_FULL_CONSOLE" || :)"
        echo "VIDEO_FLIP_STAGE_EOP_FLIP_IRQ_REGISTERED_LOG_MESSAGES=$(grep -Fc 'VIDEO_FLIP_STAGE stage=eop_flip_irq_registered' "$SRT_FULL_CONSOLE" || :)"
        echo "VIDEO_FLIP_STAGE_EOP_FLIP_IRQ_DELIVERED_LOG_MESSAGES=$(grep -Fc 'VIDEO_FLIP_STAGE stage=eop_flip_irq_delivered' "$SRT_FULL_CONSOLE" || :)"
        echo "VIDEO_FLIP_STAGE_PREPARE_REQUESTED_LOG_MESSAGES=$(grep -Fc 'VIDEO_FLIP_STAGE stage=prepare_requested' "$SRT_FULL_CONSOLE" || :)"
        echo "VIDEO_FLIP_STAGE_GUEST_FRAME_PREPARE_BEGIN_LOG_MESSAGES=$(grep -Fc 'VIDEO_FLIP_STAGE stage=guest_frame_prepare_begin' "$SRT_FULL_CONSOLE" || :)"
        echo "VIDEO_FLIP_STAGE_GUEST_FRAME_PREPARE_READY_LOG_MESSAGES=$(grep -Fc 'VIDEO_FLIP_STAGE stage=guest_frame_prepare_ready' "$SRT_FULL_CONSOLE" || :)"
        echo "VIDEO_FLIP_STAGE_PREPARED_LOG_MESSAGES=$(grep -Fc 'VIDEO_FLIP_STAGE stage=prepared' "$SRT_FULL_CONSOLE" || :)"
        echo "VIDEO_FLIP_STAGE_GUEST_PRESENT_ENTER_LOG_MESSAGES=$(grep -Fc 'VIDEO_FLIP_STAGE stage=guest_present_enter' "$SRT_FULL_CONSOLE" || :)"
        echo "VIDEO_FLIP_STAGE_PRESENT_BEGIN_LOG_MESSAGES=$(grep -Fc 'VIDEO_FLIP_STAGE stage=present_begin' "$SRT_FULL_CONSOLE" || :)"
        echo "VIDEO_FLIP_STAGE_GUEST_SWAPCHAIN_RESULT_LOG_MESSAGES=$(grep -Fc 'VIDEO_FLIP_STAGE stage=guest_swapchain_result' "$SRT_FULL_CONSOLE" || :)"
        echo "VIDEO_FLIP_STAGE_PRESENT_RETURNED_LOG_MESSAGES=$(grep -Fc 'VIDEO_FLIP_STAGE stage=present_returned' "$SRT_FULL_CONSOLE" || :)"
        echo "CURRENT_SCALAR_EXEC_SCOPE_SPLIT_EVENTS=$(grep -Fc 'Whole-wave SALU EXEC scope splits=' "$SRT_FULL_CONSOLE" || :)"
        echo "CURRENT_PM4_ACQUIRE_MARKER_EVENTS=$(grep -Fc 'PM4 AcquireMem Vulkan barriers=' "$SRT_FULL_CONSOLE" || :)"
        echo "CURRENT_FIRST_GAME_FRAME_EVENTS=$(grep -Fc 'STARTUP_UI event=first_game_frame' "$SRT_FULL_CONSOLE" || :)"
        echo "CURRENT_VISIBLE_GAME_FRAME_EVENTS=$(grep -Fc 'STARTUP_UI event=visible_game_frame' "$SRT_FULL_CONSOLE" || :)"
        echo "CURRENT_GUEST_LARGE_BUFFER_CLAMPS=$(grep -Fc 'Clamped size from 4294967295' "$SRT_FULL_CONSOLE" || :)"
        echo "CURRENT_DETECTED_GPU_FIRST_TOUCH_PAGES=$(grep -Fc 'Accessed non-GPU cached memory at' "$SRT_FULL_CONSOLE" || :)"
        echo "CURRENT_FAULT_RING_DEFER_LOGS=$(grep -Fc 'Fault readback ring saturated:' "$SRT_FULL_CONSOLE" || :)"
        echo "CURRENT_FAULT_RING_RECOVERY_LOGS=$(grep -Fc 'Fault readback ring recovered after' "$SRT_FULL_CONSOLE" || :)"
        echo "CURRENT_UNSUPPORTED_READFIRSTLANE=$(grep -Fc 'Unexpected instruction for offset computation, ReadFirstLane' "$SRT_FULL_CONSOLE" || :)"
        echo "CURRENT_UNSUPPORTED_GETATTRIBUTE_U32=$(grep -Fc 'Unexpected instruction for offset computation, GetAttributeU32' "$SRT_FULL_CONSOLE" || :)"
        echo "CURRENT_DYNAMIC_PHI=$(grep -Fc 'SRT offset Phi depends on execution flow' "$SRT_FULL_CONSOLE" || :)"
        echo "CURRENT_FAILED_SRT_OFFSETS=$(grep -Fc 'Failed to compute offset for SRT walker' "$SRT_FULL_CONSOLE" || :)"
        echo "CURRENT_INVALID_SHARP_SOURCES=$(grep -Fc 'Sharp source was not flatenned' "$SRT_FULL_CONSOLE" || :)"
        echo "CURRENT_DMA_SHADERS=$(grep -Fc 'uses demand-paged GPU reads for unflattened SRT offsets' "$SRT_FULL_CONSOLE" || :)"
        echo "CURRENT_DEVICE_LOSS=$(grep -Fci 'DeviceLost' "$SRT_FULL_CONSOLE" || :)"
        echo "CURRENT_X11_LOSS=$(grep -Fci 'XIO:  fatal IO error' "$SRT_FULL_CONSOLE" || :)"
        echo "DURATION_AND_SHADER_COVERAGE_MUST_MATCH_FOR_FAIR_AB=YES"
        echo "REAL_VISUAL_ACCEPTANCE_PENDING=YES"
    } | tee "$REPORT/srt-resource-progress.txt"
    # The shader logger reports aggregate metrics per compile. Parse the
    # actual FULL console (the observer excerpt deliberately drops its middle).
    python3 -B - "$SRT_FULL_CONSOLE" <<'PYPM4' | tee -a "$REPORT/srt-resource-progress.txt"
from pathlib import Path
import re
import sys
data = Path(sys.argv[1]).read_text(errors="replace")
# Log counts are sampled at powers of two; maximum is the proved lower bound.
stages = ("guest_requested", "eop_flip_irq_registered", "eop_flip_irq_delivered",
          "prepare_requested", "guest_frame_prepare_begin", "guest_frame_prepare_ready",
          "prepared", "guest_present_enter", "present_begin",
          "guest_swapchain_result", "present_returned")
for stage in stages:
    found = [int(x) for x in re.findall(
        r"VIDEO_FLIP_STAGE stage=" + stage + r" count=(\d+)", data)]
    print("VIDEO_FLIP_STAGE_"+stage.upper()+"_OBSERVED_MINIMUM="+str(max(found, default=0)))
print("VIDEO_FLIP_STAGE_COUNT_SAMPLES_ARE_POWERS_OF_TWO=YES")
for stage in ("guest_acquire_begin", "guest_acquire_timeout", "guest_acquire_ready",
              "guest_imgui_begin", "guest_flush_begin", "guest_flush_returned",
              "guest_queue_present_begin", "guest_recreate_begin",
              "guest_recreate_returned", "guest_released"):
    samples = [int(x) for x in re.findall(
        r"VIDEO_FLIP_STAGE stage=" + stage + r" count=(\d+)", data)]
    print("VIDEO_FLIP_STAGE_"+stage.upper()+"_MAX_SAMPLE="+str(max(samples,default=0)))
for stage in ("enter", "acquire_begin", "acquire_skipped", "acquire_ready",
              "imgui_begin", "flush_begin", "flush_returned",
              "queue_present_begin", "queue_present_returned", "resize_deferred"):
    samples = [int(x) for x in re.findall(
        r"VIDEOOUT_HOST_PRESENT stage=" + stage + r" count=(\d+)", data)]
    print("VIDEOOUT_HOST_PRESENT_"+stage.upper()+"_MAX_SAMPLE="+str(max(samples,default=0)))
ticks = [int(x) for x in re.findall(r"VIDEOOUT_PRESENT_LOOP tick=(\d+)", data)]
print("VIDEOOUT_PRESENTER_LOOP_MAX_TICK="+str(max(ticks, default=0)))
salu = [int(v) for v in re.findall(r"Whole-wave SALU EXEC scope splits=(\d+)",data)]
print("CURRENT_REAL_WHOLE_WAVE_SALU_SPLITS=" + str(sum(salu)))
print("CURRENT_SHADERS_WITH_WHOLE_WAVE_SALU_SPLITS=" + str(len(salu)))
pm4 = [int(v) for v in re.findall(r"PM4 AcquireMem Vulkan barriers=(\d+)",data)]
ring = [int(v) for v in re.findall(r"Fault readback ring recovered after (\d+) deferred submissions",data)]
print("CURRENT_PM4_ACQUIRE_LAST_REPORTED_BARRIER_COUNT=" + str(max(pm4, default=0)))
print("CURRENT_FAULT_RING_RECOVERED_DEFERRED_SUBMISSIONS=" + str(sum(ring)))
print("FIRST_TOUCH_GPU_DMA_ZERO_FALLBACK_STILL_UNRESOLVED=YES")
PYPM4
else
    echo "SRT_FULL_CONSOLE_UNAVAILABLE_NO_COUNTERS" | tee "$REPORT/srt-resource-progress.txt"
fi
exit "$rc"
