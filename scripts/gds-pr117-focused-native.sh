#!/usr/bin/env bash
# Source-specific real CIK ordered-GDS host bridge validation.
# Prior full GCC/Clang+ASan/UBSan checkpoint suite already passed at
# 1874c5095faf64a704efc3b5d593c9ffc0215a36. Do NOT replay it.
# This runner checks every changed producer/consumer, then relinks shadPS4.
# No game launch, display/audio/network changes, process killing or logout.
set -Eeuo pipefail

PROJECT="${HOME}/projects/shadps4"
ROOT="${PROJECT}/tests/gds-pr117-native-20261029T164137Z-3996104"
SRC="${ROOT}/source"
BUILD="${ROOT}/build"
TAG="gds-pr117-focused-native-$(date -u +%Y%m%dT%H%M%SZ)-$$"
LOG="${PROJECT}/logs/${TAG}"
STATUS="FAILED_OR_INCOMPLETE"
STAGE="PRECHECK"
mkdir -p "${LOG}"

finish() {
    local rc=$?
    trap - EXIT
    set +e
    {
        printf 'STATUS=%s\nSTAGE=%s\nEXIT_CODE=%s\n' "${STATUS}" "${STAGE}" "${rc}"
        printf 'GUEST_SHADER_REENTRY=NOT_IMPLEMENTED\nGAME_TEST_READY=NO\n'
    } > "${LOG}/result.txt"
    tar -C "${PROJECT}/logs" -czf "${PROJECT}/logs/${TAG}.tar.gz" "${TAG}" 2> "${LOG}/archive-stderr.log"
    printf 'GDS_FOCUSED_NATIVE=%s EXIT_CODE=%s STAGE=%s\n' "${STATUS}" "${rc}" "${STAGE}"
    printf 'UPLOAD_THIS=%s\n' "${PROJECT}/logs/${TAG}.tar.gz"
    exit "${rc}"
}
trap finish EXIT

for tool in g++ clang++-17 python3 git cmake ninja tar nm sha256sum; do
    command -v "${tool}" >/dev/null || { echo "MISSING_REQUIRED_TOOL=${tool}"; exit 2; }
done
[[ -d "${SRC}/.git" && -f "${BUILD}/build.ninja" ]] || {
    echo "ISOLATED_SOURCE_OR_BUILD_MISSING"; exit 3;
}
git -C "${SRC}" rev-parse HEAD > "${LOG}/source-head.txt"
git -C "${SRC}" status --porcelain=v1 --ignore-submodules=untracked > "${LOG}/checkout-status.txt"
[[ ! -s "${LOG}/checkout-status.txt" ]] || {
    echo "ISOLATED_GIT_DIRTY: refusing build"; exit 4;
}
grep -Fq 'ASSERT_MSG(!program.info.uses_ordered_gds' \
    "${SRC}/src/shader_recompiler/backend/spirv/emit_spirv.cpp" || {
    echo "DANGEROUS_GDS_GPU_OPCODE_GATE_REMOVED"; exit 5;
}

STAGE="PRODUCTION_SOURCE_CONTRACTS"
if ! (cd "${SRC}" && python3 tests/check_ordered_gds_ir.py) \
    > "${LOG}/source-contracts.log" 2>&1; then
    tail -90 "${LOG}/source-contracts.log"; exit 6
fi
for marker in \
    ORDERED_GDS_TYPED_IR_CONTRACT=PASS \
    REAL_GCN_SITE_CHECKPOINT_METADATA_UNAUTHENTICATED_AND_FAIL_CLOSED \
    PRODUCTION_GCN_LDS_STATE_TRANSFER_AND_GUARD; do
    grep -Fq "${marker}" "${LOG}/source-contracts.log" || {
        echo "SOURCE_CONTRACT_MISSING=${marker}"; exit 7;
    }
done

STAGE="EXTRACTED_ACTUAL_GCN_TRANSLATORS_GCC_CLANG"
for compiler in g++ clang++-17; do
    case "${compiler}" in g++) label=gcc;; *) label=clang;; esac
    if ! (cd "${SRC}" &&
          python3 tests/check_ordered_gds_frontend.py --compiler "${compiler}") \
        > "${LOG}/frontend-${label}.log" 2>&1; then
        tail -90 "${LOG}/frontend-${label}.log"; exit 8
    fi
    grep -Fq 'decoded_site_vdst_next_pc=PASS' "${LOG}/frontend-${label}.log" || exit 9
    if ! (cd "${SRC}" &&
          python3 tests/check_ordered_gds_live_capture.py --compiler "${compiler}") \
        > "${LOG}/live-${label}.log" 2>&1; then
        tail -90 "${LOG}/live-${label}.log"; exit 10
    fi
    grep -Fq 'decoded_site_header_invalid_auth=PASS' "${LOG}/live-${label}.log" || exit 11
done

STAGE="ACTUAL_ZERO_AND_NONZERO_LDS_CONTRACT"
for compiler in g++ clang++-17; do
    case "${compiler}" in g++) label=gcc;; *) label=clang;; esac
    for mode in strict sanitized; do
        flags=(-std=c++23 -Wall -Wextra -Werror -pedantic -pthread -Isrc)
        if [[ "${mode}" == sanitized ]]; then
            flags+=(-O1 -g -fsanitize=address,undefined -fno-omit-frame-pointer)
        else
            flags+=(-O2)
        fi
        binary="${LOG}/lds-${label}-${mode}"
        if ! (cd "${SRC}" && "${compiler}" "${flags[@]}" \
              tests/gcn_ordered_gds_lds_checkpoint.cpp -o "${binary}" &&
              ASAN_OPTIONS=detect_leaks=0 UBSAN_OPTIONS=halt_on_error=1 "${binary}") \
              > "${LOG}/lds-${label}-${mode}.log" 2>&1; then
            tail -90 "${LOG}/lds-${label}-${mode}.log"; exit 12
        fi
        grep -Fq 'zero_lds_has_no_fake_state=PASS' "${LOG}/lds-${label}-${mode}.log" || exit 13
        grep -Fq 'required_lds_fail_closed=PASS' "${LOG}/lds-${label}-${mode}.log" || exit 14
        rm -f -- "${binary}"
    done
done

STAGE="INCREMENTAL_NATIVE_SHADPS4_LINK"
if ! cmake --build "${BUILD}" --parallel 6 \
    > "${LOG}/native-build.log" 2>&1; then
    tail -130 "${LOG}/native-build.log"; exit 15
fi
[[ -s "${BUILD}/shadps4" ]] || { echo "SHADPS4_NATIVE_ELF_MISSING"; exit 16; }

STAGE="NATIVE_SYMBOL_AND_RUNTIME_SAFETY"
object="${BUILD}/CMakeFiles/shadps4.dir/src/video_core/renderer_vulkan/vk_rasterizer.cpp.o"
[[ -s "${object}" ]] || { echo "NATIVE_RASTERIZER_OBJECT_MISSING"; exit 17; }
nm -C "${object}" > "${LOG}/rasterizer-symbols.txt"
grep -Fq 'Vulkan::Rasterizer::ResolveCompleteCikComputeOrderedGdsEpoch(' \
    "${LOG}/rasterizer-symbols.txt" || {
    echo "GDS_NATIVE_BRIDGE_NOT_LINKED"; exit 18;
}
sha256sum "${BUILD}/shadps4" > "${LOG}/isolated-elf.sha256"
printf 'NATIVE_WARNING_LINES=%s\n' \
    "$(grep -c 'warning:' "${LOG}/native-build.log" || true)" \
    > "${LOG}/warning-count.txt"
STATUS="PASS_NATIVE_ZERO_LDS_GUEST_HANDOFF"
STAGE="COMPLETE_GUEST_REENTRY_STILL_GATED"
echo "REAL_GUEST_ZERO_LDS_OPTIONAL_HANDOFF=PASS NATIVE_LINK=PASS GAME_TEST_READY=NO"
