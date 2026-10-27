#!/usr/bin/env bash
# Official upstream main + GPU indirect-grid bounds (PR 5479). Build only; never launch a game.
set -Eeuo pipefail
umask 077

ROOT="$HOME/projects/shadps4"
LOGS="$ROOT/logs"
TESTROOT="$ROOT/Upstream/tests"
BRANCH="test/official-main-pr5479-indirect-compute-20261027"
SOURCE_PIN="3017c9a179cb5f773317ac768173db230a2d7691"
UPSTREAM_MAIN="3eecea94ddaca360b19f4b800523b609a8b2ffcc"
mkdir -p "$LOGS" "$TESTROOT"

if (( $# == 0 )); then
    command -v nohup >/dev/null && command -v setsid >/dev/null || exit 3
    TAG="shadps4-pr5479-$(date +%Y%m%d-%H%M%S)-$BASHPID"
    [[ "$TAG" =~ ^shadps4-pr5479-[0-9]{8}-[0-9]{6}-[0-9]+$ ]] || exit 3
    RUNLOG="$LOGS/$TAG.log"
    (set -C; : > "$RUNLOG") || exit 3
    nohup setsid -w bash "$0" --worker "$TAG" </dev/null > "$RUNLOG" 2>&1 &
    WORKER_PID=$!
    disown "$WORKER_PID" 2>/dev/null || true
    echo "WORKER_PID=$WORKER_PID"
    echo "EXACT_UPSTREAM_MAIN=$UPSTREAM_MAIN"
    echo "PINNED_EXPERIMENT=$SOURCE_PIN"
    echo "GAME_PROCESS=NOT_LAUNCHED"
    echo "DISPLAY_SERVICES=NOT_TOUCHED"
    echo "MONITOR=tail -f '$RUNLOG'"
    echo "BUILD_ARCHIVE=$LOGS/$TAG-build.tar.gz"
    exit 0
fi

[[ $# == 2 && "$1" == "--worker" ]] || exit 3
TAG="$2"
[[ "$TAG" =~ ^shadps4-pr5479-[0-9]{8}-[0-9]{6}-[0-9]+$ ]] || exit 3
WORK="$TESTROOT/$TAG"
SRC="$WORK/source"
BUILD="$WORK/build"
TESTBUILD="$WORK/build-tests"
EVIDENCE="$WORK/evidence"
RUNLOG="$LOGS/$TAG.log"
ARCHIVE="$LOGS/$TAG-build.tar.gz"

[[ ! -e "$WORK" && ! -L "$WORK" ]] || {
    echo "EXPERIMENT_DIRECTORY_ALREADY_EXISTS=$WORK"
    exit 3
}
mkdir -p "$EVIDENCE"
STATE="STARTED"
finish() {
    local rc=$?
    trap - EXIT
    {
        echo "EXIT_CODE=$rc"
        echo "STATE=$STATE"
        echo "OFFICIAL_UPSTREAM_MAIN=$UPSTREAM_MAIN"
        echo "EXPERIMENT_SOURCE=$SOURCE_PIN"
        echo "GAME_LAUNCHED=NO"
        echo "SUNSHINE_XORG_MOONLIGHT_ESDE_NETWORK_SAVES=UNTOUCHED_BY_SCRIPT"
    } | tee "$EVIDENCE/exit.txt"
    tar -czf "$ARCHIVE" -C "$WORK" evidence -C "$LOGS" "$TAG.log" 2>/dev/null ||
        echo "EVIDENCE_ARCHIVE_FAILED_RETAINED=$EVIDENCE"
    echo "UPLOAD_BUILD_EVIDENCE=$ARCHIVE"
    echo "SSH_SESSION=OPEN"
    exit "$rc"
}
trap finish EXIT

# No cross-game build collisions and absolutely no signaling of foreign processes.
exec 9>"$LOGS/gow-upstream-got-build.lock"
exec 8>"$LOGS/shadps4-gow-srt-phi.lock"
exec 7>"$LOGS/shadps4-pr5479-build.lock"
flock -n 7 && flock -n 9 && flock -n 8 || {
    echo "ANOTHER_BUILD_OWNS_A_LOCK_NO_ACTION"
    exit 3
}

for cmd in git cmake ninja sha256sum tar timeout nice flock; do
    command -v "$cmd" >/dev/null || { echo "MISSING_COMMAND=$cmd"; exit 3; }
done
STATE="FETCH_PINNED_OFFICIAL_UPSTREAM_DERIVATIVE"
echo "STAGE=$STATE"
git -c http.version=HTTP/1.1 clone -q --filter=blob:none --depth 5 \
    --single-branch --branch "$BRANCH" \
    https://github.com/Chreece/shadPS4.git "$SRC" \
    >"$EVIDENCE/clone.log" 2>&1 || {
        tail -80 "$EVIDENCE/clone.log"; exit 4;
    }
git -C "$SRC" checkout -q --detach "$SOURCE_PIN"
ACTUAL="$(git -C "$SRC" rev-parse HEAD)"
[[ "$ACTUAL" == "$SOURCE_PIN" ]] || { echo "PIN_MISMATCH=$ACTUAL"; exit 4; }
ANCESTOR="$(git -C "$SRC" rev-parse HEAD~3)"
[[ "$ANCESTOR" == "$UPSTREAM_MAIN" ]] || {
    echo "UPSTREAM_MAIN_ANCESTRY_MISMATCH=$ANCESTOR"; exit 4;
}
echo "$SOURCE_PIN" >"$EVIDENCE/source-sha.txt"
echo "$UPSTREAM_MAIN" >"$EVIDENCE/upstream-main-sha.txt"
echo "SOURCE_PROVENANCE=OFFICIAL_MAIN_PLUS_THREE_PR5479_PORT_COMMITS"

STATE="RESTORE_EXACT_UPSTREAM_SUBMODULES"
echo "STAGE=$STATE"
git -C "$SRC" -c http.version=HTTP/1.1 -c submodule.fetchJobs=2 \
    submodule update --init --recursive --depth 1 --jobs 2 \
    >"$EVIDENCE/submodules.log" 2>&1 || {
        tail -100 "$EVIDENCE/submodules.log"; exit 5;
    }
git -C "$SRC" submodule status --recursive >"$EVIDENCE/submodule-status.txt"
if grep -Eq '^[-+U]' "$EVIDENCE/submodule-status.txt"; then
    echo "SUBMODULE_PIN_MISMATCH"; exit 5
fi
if [[ -n "$(git -C "$SRC" status --porcelain --untracked-files=no)" ]]; then
    echo "TRACKED_SOURCE_DIRTY"; exit 5
fi
for path in \
    src/video_core/host_shaders/prepare_dispatch.comp \
    src/video_core/renderer_vulkan/vk_dispatch_helper.cpp \
    src/shader_recompiler/dispatch_data.h; do
    [[ -f "$SRC/$path" ]] || { echo "MISSING_PR_SOURCE=$path"; exit 6; }
done
grep -Fq 'supports_dispatch_metadata' "$SRC/src/shader_recompiler/profile.h" &&
grep -Fq 'bool support_bda{};' "$SRC/src/shader_recompiler/profile.h" &&
grep -Fq '.support_bda = instance_.IsBufferDeviceAddressSupported(),' \
    "$SRC/src/video_core/renderer_vulkan/vk_pipeline_cache.cpp" || {
    echo "OFFICIAL_BDA_RECONCILIATION_FAILED"; exit 6;
}

STATE="CAPTURE_READ_ONLY_RADV_DEVICE_DESCRIPTOR_LIMITS"
echo "STAGE=$STATE"
if command -v vulkaninfo >/dev/null; then
    timeout 18 vulkaninfo > "$EVIDENCE/vulkaninfo.txt" 2>&1 || true
    grep -E 'deviceName[[:space:]]*=|maxPerStageResources[[:space:]]*=|maxPerStageDescriptorStorageBuffers[[:space:]]*=|maxDescriptorSetStorageBuffers[[:space:]]*=' \
        "$EVIDENCE/vulkaninfo.txt" | head -45 > "$EVIDENCE/descriptor-limits.txt" || true
else
    echo "VULKANINFO_NOT_INSTALLED" >"$EVIDENCE/descriptor-limits.txt"
fi

STATE="CONFIGURE_AND_BUILD_NATIVE_EMULATOR"
echo "STAGE=$STATE"
cmake -S "$SRC" -B "$BUILD" -G Ninja -DCMAKE_BUILD_TYPE=Release \
    -DENABLE_TESTS=OFF -DENABLE_DISCORD_RPC=OFF \
    >"$EVIDENCE/configure-native.log" 2>&1 || {
        tail -110 "$EVIDENCE/configure-native.log"; exit 7;
    }
nice -n 10 cmake --build "$BUILD" --target shadps4 --parallel 2 \
    >"$EVIDENCE/build-native.log" 2>&1 || {
        tail -130 "$EVIDENCE/build-native.log"; exit 8;
    }
[[ -x "$BUILD/shadps4" ]] || { echo "NATIVE_BINARY_MISSING"; exit 8; }
sha256sum "$BUILD/shadps4" > "$EVIDENCE/native-binary-sha256.txt"
echo "NATIVE_BUILD_AND_LINK=PASS"
# Compiler warnings are evidence, not silently ignored.
grep -Ein '(^|[ :])warning:' "$EVIDENCE/build-native.log" \
    >"$EVIDENCE/native-warnings.txt" || true
echo "NATIVE_COMPILER_WARNINGS=$(wc -l < "$EVIDENCE/native-warnings.txt")"

STATE="BUILD_AND_RUN_IN_TREE_GCN_REGRESSIONS"
echo "STAGE=$STATE"
# Preserve the native binary even if optional GTest acquisition is unavailable.
if cmake -S "$SRC" -B "$TESTBUILD" -G Ninja -DCMAKE_BUILD_TYPE=Release \
          -DENABLE_TESTS=ON -DENABLE_DISCORD_RPC=OFF \
          >"$EVIDENCE/configure-tests.log" 2>&1 &&
   nice -n 10 cmake --build "$TESTBUILD" --target shadps4_gcn_test --parallel 2 \
          >"$EVIDENCE/build-tests.log" 2>&1; then
    # Run the built GCN executable itself: CTest also lists settings/NGS2
    # targets that are intentionally not built in this isolated check.
    [[ -x "$TESTBUILD/tests/shadps4_gcn_test" ]] || {
        echo "GCN_TEST_BINARY_MISSING"; exit 9;
    }
    "$TESTBUILD/tests/shadps4_gcn_test" --gtest_brief=1 \
        >"$EVIDENCE/gcn-tests.txt" 2>&1 || {
            tail -120 "$EVIDENCE/gcn-tests.txt"
            echo "NATIVE_BUILD_PASS_GCN_TESTS_FAIL"
            exit 9
        }
    echo "GCN_ALL_TESTS=PASS"
else
    echo "GCN_TESTS_CONFIGURE_OR_BUILD_FAIL" \
        | tee "$EVIDENCE/gcn-tests-status.txt"
    if [[ -f "$EVIDENCE/build-tests.log" ]]; then
        tail -100 "$EVIDENCE/build-tests.log"
    else
        tail -100 "$EVIDENCE/configure-tests.log"
    fi
    exit 9
fi

STATE="BUILD_AND_SELECTED_REGRESSIONS_PASS_NO_GAME_RUN"
echo "RESULT=$STATE"
echo "CANDIDATE_BINARY=$BUILD/shadps4"
echo "ACTUAL_GPU_INDIRECT_DISPATCH_TEST=NOT_YET_RUN"
echo "UNCHANGED=SUNSHINE_XORG_MOONLIGHT_ESDE_SAVES_SSH"
