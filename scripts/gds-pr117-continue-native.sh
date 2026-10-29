#!/usr/bin/env bash
set -Eeuo pipefail

PROJECT="$HOME/projects/shadps4"
BASE="$PROJECT/tests/gds-pr117-native-20261029T164137Z-3996104"
SRC="$BASE/source"
BUILD="$BASE/build"
BRANCH='feature/ordered-gds-wave-executor-20261029'
MINIMUM_COMMIT='820bd2b666b35dacb61f6daae99c531c9c867cc2'
TAG="gds-pr117-preflight-native-$(date -u +%Y%m%dT%H%M%SZ)-$$"
LOG="$PROJECT/logs/$TAG"
STAGE='PREFLIGHT'
STATUS='FAILED_OR_INCOMPLETE'

mkdir -p "$LOG"

finish() {
    local rc=$?
    trap - EXIT
    set +e
    {
        printf 'stage=%s\nstatus=%s\nexit_code=%s\n' "$STAGE" "$STATUS" "$rc"
        printf 'src=%s\nbuild=%s\n' "$SRC" "$BUILD"
    } > "$LOG/result.txt"
    git -C "$SRC" rev-parse HEAD > "$LOG/final-source-head.txt" 2>/dev/null || true
    if [[ -s "$BUILD/shadps4" ]]; then
        sha256sum "$BUILD/shadps4" > "$LOG/candidate-elf.sha256"
    fi
    tar -C "$PROJECT/logs" -czf "$PROJECT/logs/$TAG.tar.gz" "$TAG" \
        2> "$LOG/tar.stderr"
    printf '\nPR117_PREFLIGHT_NATIVE=%s EXIT_CODE=%s STAGE=%s\n' "$STATUS" "$rc" "$STAGE"
    printf 'UPLOAD_THIS=%s\n' "$PROJECT/logs/$TAG.tar.gz"
    printf 'ISOLATED_SOURCE=%s\nISOLATED_BUILD=%s\n' "$SRC" "$BUILD"
    exit "$rc"
}
trap finish EXIT

for tool in git cmake ninja python3 g++ clang++-17 tar sha256sum timeout nm; do
    command -v "$tool" >/dev/null 2>&1 ||
        { printf 'MISSING_TOOL=%s\n' "$tool"; exit 2; }
done

[[ -d "$SRC/.git" && -f "$BUILD/build.ninja" ]] ||
    { echo 'MISSING_PREVIOUS_ISOLATED_NATIVE_BUILD'; exit 3; }
url="$(git -C "$SRC" config --get remote.origin.url)"
case "$url" in
    https://github.com/Chreece/shadPS4|https://github.com/Chreece/shadPS4.git) ;;
    *) printf 'UNEXPECTED_SOURCE_REMOTE=%s\n' "$url"; exit 4 ;;
esac
if [[ -n "$(git -C "$SRC" status --porcelain=v1 --ignore-submodules=untracked)" ]]; then
    echo 'ISOLATED_SOURCE_HAS_LOCAL_EDITS_REFUSE_MERGE'
    git -C "$SRC" status --short
    exit 5
fi

STAGE='FETCH_CURRENT_PR117'
GIT_TERMINAL_PROMPT=0 git -C "$SRC" fetch --no-tags --depth=100 origin \
    "refs/heads/$BRANCH" > "$LOG/fetch.log" 2>&1 || {
    tail -50 "$LOG/fetch.log"
    exit 6
}
git -C "$SRC" merge-base --is-ancestor HEAD FETCH_HEAD ||
    { echo 'BRANCH_NOT_FAST_FORWARD_STOP'; exit 7; }
git -C "$SRC" merge --ff-only FETCH_HEAD > "$LOG/merge.log" 2>&1 || {
    tail -70 "$LOG/merge.log"
    exit 8
}
HEAD_SHA="$(git -C "$SRC" rev-parse HEAD)"
git -C "$SRC" merge-base --is-ancestor "$MINIMUM_COMMIT" HEAD ||
    { printf 'MISSING_REQUIRED_PREFLIGHT_COMMIT=%s\n' "$MINIMUM_COMMIT"; exit 9; }
printf '%s\n' "$HEAD_SHA" > "$LOG/source-head.txt"
git -C "$SRC" status --porcelain=v1 --ignore-submodules=untracked \
    > "$LOG/checkout-status.txt"

STAGE='TEST_GCC_AND_CLANG_WITH_SANITIZERS'
for compiler in g++ clang++-17; do
    name="${compiler//+/p}"
    if ! (cd "$SRC" && CXX="$compiler" RUN_SANITIZERS=1 \
          bash tests/run_ordered_gds_cpu.sh) > "$LOG/cpu-$name.log" 2>&1; then
        tail -90 "$LOG/cpu-$name.log"
        exit 10
    fi
    grep -Fq 'ORDERED_GDS_OFFLINE_REGRESSION=PASS' "$LOG/cpu-$name.log"
    grep -Fq 'ORDERED_GDS_LOGICAL_CAPTURE_SLOTS=PASS' "$LOG/cpu-$name.log"
    grep -Fq 'ORDERED_GDS_PHYSICAL_STORAGE_BOUNDS=PASS' "$LOG/cpu-$name.log"
    grep -Fq 'ORDERED_GDS_CIK_PACKER_SEPARATION=PASS' "$LOG/cpu-$name.log"
    grep -Fq 'ORDERED_GDS_MIXED_STATIC_SITES=PASS' "$LOG/cpu-$name.log"
    grep -Fq 'ORDERED_GDS_GPU_PACKET_ROUNDTRIP=PASS' "$LOG/cpu-$name.log"
    grep -Fq 'ORDERED_GDS_CIK_CAPTURE_PAYLOAD=PASS' "$LOG/cpu-$name.log"
    grep -Fq 'ORDERED_GDS_STATIC_FLOW=PASS' "$LOG/cpu-$name.log"
    grep -Fq 'ORDERED_GDS_SPARSE_WAVE_COVERAGE=PASS' "$LOG/cpu-$name.log"
    grep -Fq 'ORDERED_GDS_GPU_SPARSE_PACKET=PASS' "$LOG/cpu-$name.log"
done

STAGE='VERIFY_BACKEND_REMAINS_GATED'
grep -Fq 'ASSERT_MSG(!program.info.uses_ordered_gds' \
    "$SRC/src/shader_recompiler/backend/spirv/emit_spirv.cpp" ||
    { echo 'ORDERED_GDS_BACKEND_GATE_MISSING'; exit 11; }
grep -Fq 'logical capture slots NOT' \
    "$SRC/src/video_core/renderer_vulkan/vk_pipeline_cache.cpp" ||
    { echo 'GDS_PREFLIGHT_LOGGING_MISSING'; exit 12; }
grep -Fq 'Ordered GDS GPU packet ABI' \
    "$SRC/src/video_core/renderer_vulkan/vk_pipeline_cache.cpp" ||
    { echo 'ORDERED_GDS_CAPTURE_ABI_NOT_LINKED_TO_COMPILER'; exit 12; }
grep -Fq 'Ordered GDS decoded-ISA CFG:' \
    "$SRC/src/video_core/renderer_vulkan/vk_pipeline_cache.cpp" ||
    { echo 'ORDERED_GDS_REAL_GCN_CONTROL_FLOW_NOT_LINKED'; exit 12; }
grep -Fq 'CaptureBypassFlags' \
    "$SRC/src/shader_recompiler/frontend/ordered_gds_capture_packet.h" ||
    { echo 'ORDERED_GDS_BYPASS_PACKETS_MISSING'; exit 12; }
# CIK DS_ORDERED_COUNT has dedicated packer counters. The raw GDS SSBO
# must remain available to ordinary GDS instructions, but must not
# automatically stand in for this separate ordered counter state.
if grep -Fq 'case IR::Opcode::OrderedGdsCount:' \
    "$SRC/src/shader_recompiler/ir/passes/resource_pass.h"; then
    echo 'CIK_PACKER_ILLEGALLY_ALIASED_TO_RAW_64K_GDS_RAM'
    exit 12
fi
grep -Fq 'CikOrderedCountIncrement' \
    "$SRC/src/shader_recompiler/frontend/ordered_gds_legacy_packer.h" || {
    echo 'MISSING_LEGACY_GCN_EXEC_COUNT_SEMANTICS'
    exit 12
}
grep -Fq 'ValidatePhysicalGdsWrites(updates)' \
    "$SRC/src/video_core/renderer_vulkan/vk_ordered_gds_transfer.h" ||
    { echo 'ORDERED_GDS_PHYSICAL_WRITE_RANGE_GUARD_MISSING'; exit 12; }

STAGE='BUILD_INCREMENTAL_NATIVE'
if ! cmake --build "$BUILD" --parallel 6 > "$LOG/build.log" 2>&1; then
    tail -100 "$LOG/build.log"
    exit 13
fi
[[ -s "$BUILD/shadps4" ]] ||
    { echo 'ISOLATED_NATIVE_ELF_MISSING'; exit 14; }
printf 'BUILD_WARNING_LINES=%s\n' "$(grep -c 'warning:' "$LOG/build.log" || true)" \
    > "$LOG/build-warning-count.txt"
STAGE='VERIFY_NATIVE_TRANSFER_METHODS_LINKED'
RASTER_OBJECT="$BUILD/CMakeFiles/shadps4.dir/src/video_core/renderer_vulkan/vk_rasterizer.cpp.o"
[[ -s "$RASTER_OBJECT" ]] || { echo 'NATIVE_RASTERIZER_OBJECT_MISSING'; exit 15; }
nm -C "$RASTER_OBJECT" > "$LOG/ordered-gds-transfer-symbols.txt"
grep -Fq 'Vulkan::Rasterizer::DownloadOrderedGdsPackets(' \
    "$LOG/ordered-gds-transfer-symbols.txt" || {
    echo 'ORDERED_GDS_GPU_DOWNLOAD_NOT_COMPILED'; exit 15;
}
grep -Fq 'Vulkan::Rasterizer::UploadOrderedGdsPackets(' \
    "$LOG/ordered-gds-transfer-symbols.txt" || {
    echo 'ORDERED_GDS_GPU_UPLOAD_NOT_COMPILED'; exit 15;
}
grep -Fq 'Vulkan::Rasterizer::SnapshotOrderedGdsDwords(' \
    "$LOG/ordered-gds-transfer-symbols.txt" || {
    echo 'ORDERED_GDS_DEVICE_SNAPSHOT_NOT_COMPILED'; exit 15;
}
grep -Fq 'Vulkan::Rasterizer::CommitOrderedGdsDwords(' \
    "$LOG/ordered-gds-transfer-symbols.txt" || {
    echo 'ORDERED_GDS_SELECTIVE_DEVICE_COUNTER_COMMIT_NOT_COMPILED'; exit 15;
}
printf 'GDS_STORAGE_BINDING_AND_VULKAN_TRANSFER=PASS GPU_EXECUTION_NOT_ENABLED\n' \
    > "$LOG/gds-storage-symbol-check.txt"

STAGE='GENERATE_GPU_COMPUTE_PACKET_WRITER'
if ! python3 "$SRC/tests/generate_ordered_gds_probe_spirv.py" \
    "$LOG/gpu-capture-writer.spv" > "$LOG/gpu-writer-assemble.log" 2>&1; then
    cat "$LOG/gpu-writer-assemble.log"
    exit 15
fi
grep -Fq 'GPU_CAPTURE_WRITER_SPIRV_BUILD=PASS' \
    "$LOG/gpu-writer-assemble.log" || exit 15
if command -v spirv-val >/dev/null 2>&1; then
    spirv-val "$LOG/gpu-capture-writer.spv" \
        > "$LOG/gpu-writer-spirv-val.log" 2>&1 || {
        cat "$LOG/gpu-writer-spirv-val.log"
        exit 15
    }
fi

STAGE='HEADLESS_GPU_TRANSFER_SMOKE_COMPILE'
# Compile a separate tiny Vulkan C-API test, not the game/emulator runtime.
# No surface, X11/Wayland changes, host display, game saves or GPU spinlocks.
if ! g++ -std=c++23 -O2 -Wall -Wextra -Werror \
    -I "$SRC/src" -I "$SRC/externals/vulkan-headers/include" \
    "$SRC/tests/gcn_ordered_gds_vulkan_transfer_smoke.cpp" \
    -l:libvulkan.so.1 -o "$LOG/vulkan-transfer-smoke" \
    > "$LOG/gpu-smoke-build.log" 2>&1; then
    tail -90 "$LOG/gpu-smoke-build.log"
    exit 15
fi

STAGE='HEADLESS_GPU_TRANSFER_SMOKE_RUN'
# Only 256 bytes, AMD discrete, no guest shader, no display or service restarts.
if ! timeout --signal=TERM --kill-after=2s 20s \
    "$LOG/vulkan-transfer-smoke" "$LOG/gpu-capture-writer.spv" \
    > "$LOG/gpu-smoke-run.log" 2>&1; then
    tail -90 "$LOG/gpu-smoke-run.log"
    exit 16
fi
grep -Fq 'GPU_TRANSFER_SMOKE=PASS' "$LOG/gpu-smoke-run.log" || {
    cat "$LOG/gpu-smoke-run.log"
    exit 17
}
grep -Fq 'GPU_CAPTURE_COMPUTE_WRITER=PASS' "$LOG/gpu-smoke-run.log" || {
    cat "$LOG/gpu-smoke-run.log"
    exit 18
}
grep -Fq 'cik_exec_popcount=PASS' "$LOG/gpu-smoke-run.log" || {
    echo 'AMD_COMPUTE_PACKET_CIK_EXEC_PAYLOAD_MISSING'
    exit 18
}
STAGE='NATIVE_GPU_TRANSFER_SMOKE_VERIFIED_NO_GUEST_SHADER'
STATUS='PASS_CPU_NATIVE_AMD_TRANSFER_AND_COMPUTE_PACKET_WRITER'
printf 'PR117_NATIVE_PREFLIGHT=PASS AMD_GPU_TRANSFER=PASS GPU_CAPTURE_COMPUTE_WRITER=PASS GUEST_SHADER=NOT_RUN GO_W_VISUAL=NOT_TESTED\n'
