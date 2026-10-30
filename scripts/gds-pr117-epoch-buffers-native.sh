#!/usr/bin/env bash
# Validate the NEW ordered-GDS dispatch-owned GPU storage and renderer binding.
# Prior native GDS metadata, zero-LDS and compiler tests already passed.
# No game launch, no display/audio/network modification, no process killing.
set -Eeuo pipefail

PROJECT="${HOME}/projects/shadps4"
ROOT="${PROJECT}/tests/gds-pr117-native-20261029T164137Z-3996104"
SRC="${ROOT}/source"
BUILD="${ROOT}/build"
TAG="gds-pr117-epoch-buffers-native-$(date -u +%Y%m%dT%H%M%SZ)-$$"
LOG="${PROJECT}/logs/${TAG}"
STATUS="FAILED_OR_INCOMPLETE"
STAGE="PRECHECK"
mkdir -p "${LOG}"

finish() {
    local rc=$?
    trap - EXIT
    set +e
    printf 'STATUS=%s\nSTAGE=%s\nEXIT_CODE=%s\nGAME_TEST_READY=NO\n' \
        "${STATUS}" "${STAGE}" "${rc}" > "${LOG}/result.txt"
    printf 'AUTHENTIC_GPU_CAPTURE=NOT_CONNECTED\nGUEST_SHADER_REENTRY=NOT_IMPLEMENTED\n' \
        >> "${LOG}/result.txt"
    tar -C "${PROJECT}/logs" -czf "${PROJECT}/logs/${TAG}.tar.gz" "${TAG}" \
        2> "${LOG}/archive-stderr.log"
    echo "GDS_EPOCH_BUFFERS_NATIVE=${STATUS} EXIT_CODE=${rc} STAGE=${STAGE}"
    echo "UPLOAD_THIS=${PROJECT}/logs/${TAG}.tar.gz"
    exit "${rc}"
}
trap finish EXIT

for command in git cmake ninja g++ clang++-17 python3 nm sha256sum tar; do
    command -v "${command}" >/dev/null || {
        echo "MISSING_REQUIRED_COMMAND=${command}"
        exit 2
    }
done
[[ -d "${SRC}/.git" && -f "${BUILD}/build.ninja" ]] || {
    echo ISOLATED_SOURCE_OR_NATIVE_BUILD_MISSING
    exit 3
}
git -C "${SRC}" rev-parse HEAD > "${LOG}/source-head.txt"
git -C "${SRC}" status --porcelain=v1 --ignore-submodules=untracked \
    > "${LOG}/source-status.txt"
[[ ! -s "${LOG}/source-status.txt" ]] || {
    echo ISOLATED_SOURCE_DIRTY_REFUSING_TEST
    exit 4
}

STAGE="REAL_RECOMPILED_SOURCE_AND_SAFETY_CONTRACTS"
if ! (cd "${SRC}" && python3 tests/check_ordered_gds_ir.py) \
    > "${LOG}/source-contracts.log" 2>&1; then
    tail -100 "${LOG}/source-contracts.log"
    exit 5
fi
for marker in \
    ORDERED_GDS_TYPED_IR_CONTRACT=PASS \
    REAL_GPU_DISPATCH_OWNED_GDS_RESOURCES_BOUNDED_AND_RUNTIME_GATED \
    GUEST_CHECKPOINT_DESCRIPTOR_SEPARATE_AND_RUNTIME_GATED; do
    grep -Fq "${marker}" "${LOG}/source-contracts.log" || {
        echo "MISSING_SOURCE_MARKER=${marker}"
        exit 6
    }
done
grep -Fq 'ASSERT_MSG(!program.info.uses_ordered_gds' \
    "${SRC}/src/shader_recompiler/backend/spirv/emit_spirv.cpp" || {
    echo UNVERIFIED_GUEST_GDS_EXECUTION_ENABLED
    exit 7
}

STAGE="NEW_BOUNDED_CIK_DISPATCH_PLAN_GCC_CLANG"
for compiler in g++ clang++-17; do
    case "${compiler}" in g++) label=gcc;; *) label=clang;; esac
    for mode in strict asan-ubsan; do
        flags=(-std=c++23 -Wall -Wextra -Werror -pedantic -pthread -Isrc)
        if [[ "${mode}" == strict ]]; then
            flags+=(-O2)
        else
            flags+=(-O1 -g -fsanitize=address,undefined -fno-omit-frame-pointer)
        fi
        exe="${LOG}/device-plan-${label}-${mode}"
        if ! (cd "${SRC}" &&
              "${compiler}" "${flags[@]}" \
                  tests/gcn_ordered_gds_device_plan.cpp -o "${exe}" &&
              ASAN_OPTIONS=detect_leaks=0 UBSAN_OPTIONS=halt_on_error=1 "${exe}") \
              > "${LOG}/device-plan-${label}-${mode}.log" 2>&1; then
            tail -100 "${LOG}/device-plan-${label}-${mode}.log"
            exit 8
        fi
        grep -Fq 'GDS_DEVICE_DISPATCH_PLAN=PASS' \
            "${LOG}/device-plan-${label}-${mode}.log" || exit 9
        rm -f -- "${exe}"
    done
done

STAGE="INCREMENTAL_NATIVE_GPU_BUFFER_OWNER_AND_DESCRIPTOR_BUILD"
if ! cmake --build "${BUILD}" --parallel 6 \
        > "${LOG}/native-build.log" 2>&1; then
    tail -150 "${LOG}/native-build.log"
    exit 10
fi
[[ -s "${BUILD}/shadps4" ]] || { echo NATIVE_ELF_NOT_FOUND; exit 11; }

STAGE="NATIVE_GPU_EPOCH_OWNER_SYMBOLS"
obj="${BUILD}/CMakeFiles/shadps4.dir/src/video_core/renderer_vulkan/vk_rasterizer.cpp.o"
[[ -s "${obj}" ]] || { echo RASTERIZER_OBJECT_NOT_FOUND; exit 12; }
nm -C "${obj}" > "${LOG}/native-rasterizer-symbols.txt"
for symbol in \
    'Vulkan::Rasterizer::AllocateOrderedGdsEpochBuffers(' \
    'Vulkan::Rasterizer::ResolveOwnedCikComputeOrderedGdsEpoch(' \
    'Vulkan::Rasterizer::OrderedGdsCaptureBinding::OrderedGdsCaptureBinding('; do
    grep -Fq "${symbol}" "${LOG}/native-rasterizer-symbols.txt" || {
        echo "NATIVE_SYMBOL_NOT_LINKED=${symbol}"
        exit 13
    }
done
sha256sum "${BUILD}/shadps4" > "${LOG}/isolated-elf.sha256"
printf 'CXX_WARNING_LINES=%s\n' \
    "$(grep -c 'warning:' "${LOG}/native-build.log" || true)" > "${LOG}/warnings.txt"
STATUS="PASS_GCN_BOUNDED_GPU_EPOCH_BUFFERS_NATIVE"
STAGE="COMPLETE_GPU_CAPTURE_AND_RESUME_STILL_GATED"
echo 'REAL_DISPATCH_OWNED_GPU_STORAGE_AND_BINDING_NATIVE=PASS GUEST_REENTRY=NOT_IMPLEMENTED'
