#!/usr/bin/env bash
# Verify NEW real host-authored guest continuation-token GPU transport.
# Tests changed production GCN IR, ABI, host token/packet validation and
# incremental native SPIR-V/Vulkan compile/link. No actual game dispatch.
# Never modify the running emulator, X11, Sunshine, network or SSH session.
set -Eeuo pipefail

PROJECT="${HOME}/projects/shadps4"
ROOT="${PROJECT}/tests/gds-pr117-native-20261029T164137Z-3996104"
SRC="${ROOT}/source"
BUILD="${ROOT}/build"
TAG="gds-pr117-epoch-auth-native-$(date -u +%Y%m%dT%H%M%SZ)-$$"
LOG="${PROJECT}/logs/${TAG}"
STATUS="FAILED_OR_INCOMPLETE"
STAGE="SOURCE_AND_BUILD_CHECK"
mkdir -p "${LOG}"
finish() {
    local rc=$?
    trap - EXIT
    set +e
    {
        printf 'STATUS=%s\nSTAGE=%s\nEXIT_CODE=%s\n' "${STATUS}" "${STAGE}" "${rc}"
        printf 'GPU_AUTH_TOKEN_TRANSPORT=UNDER_TEST\n'
        printf 'GUEST_SHADER_REENTRY=NOT_IMPLEMENTED\n'
        printf 'GAME_TEST_READY=NO\n'
    } > "${LOG}/result.txt"
    tar -C "${PROJECT}/logs" -czf "${PROJECT}/logs/${TAG}.tar.gz" "${TAG}" \
        2> "${LOG}/tar-stderr.txt"
    printf 'PR117_EPOCH_AUTH_STATUS=%s STAGE=%s EXIT_CODE=%s\n' \
        "${STATUS}" "${STAGE}" "${rc}"
    printf 'UPLOAD_THIS=%s\n' "${PROJECT}/logs/${TAG}.tar.gz"
    exit "${rc}"
}
trap finish EXIT

for command in git cmake ninja g++ clang++-17 python3 tar nm sha256sum; do
    command -v "${command}" >/dev/null || {
        echo "REQUIRED_COMMAND_MISSING=${command}"; exit 2;
    }
done
[[ -d "${SRC}/.git" && -f "${BUILD}/build.ninja" ]] || {
    echo "ISOLATED_SOURCE_OR_BUILD_MISSING"; exit 3;
}
git -C "${SRC}" rev-parse HEAD > "${LOG}/source-head.txt"
git -C "${SRC}" status --porcelain=v1 --ignore-submodules=untracked \
    > "${LOG}/source-status.txt"
[[ ! -s "${LOG}/source-status.txt" ]] || {
    echo "SOURCE_DIRTY_REFUSE_TO_MODIFY"; exit 4;
}
grep -Fq 'ASSERT_MSG(!program.info.uses_ordered_gds' \
    "${SRC}/src/shader_recompiler/backend/spirv/emit_spirv.cpp" || {
    echo "UNSAFE_GDS_OPCODE_GATE_REMOVED"; exit 5;
}
if [[ -e "${PROJECT}/.git" ]]; then
    echo "ISOLATED_SOURCE_ONLY=YES"
fi

STAGE="AUTHENTIC_GCN_SOURCE_AND_GPU_SPIRV_CONTRACTS"
if ! (cd "${SRC}" && python3 tests/check_ordered_gds_ir.py) \
    > "${LOG}/source-contracts.log" 2>&1; then
    tail -100 "${LOG}/source-contracts.log"; exit 6
fi
grep -Fq 'TRUSTED_DISPATCH_EPOCH_TOKEN_GPU_TRANSPORT_STILL_FAIL_CLOSED' \
    "${LOG}/source-contracts.log" || exit 7

STAGE="EXTRACTED_REAL_GUEST_CAPTURE_GCC_AND_CLANG"
for compiler in g++ clang++-17; do
    case "${compiler}" in g++) label=gcc;; *) label=clang;; esac
    if ! (cd "${SRC}" &&
          python3 tests/check_ordered_gds_live_capture.py --compiler "${compiler}") \
        > "${LOG}/live-${label}.log" 2>&1; then
        tail -100 "${LOG}/live-${label}.log"; exit 8
    fi
    grep -Fq 'host_auth_identity_ir_no_fabrication=PASS' \
        "${LOG}/live-${label}.log" || exit 9
done

# Vendored fmt is needed by actual IR/reg headers. Prefer the preinstalled
# dependency from the isolated source, never install or touch system packages.
FMT_INCLUDE=""
for candidate in "${SRC}/externals/fmt/include" "${BUILD}/_deps/fmt-src/include" /usr/include; do
    if [[ -f "${candidate}/fmt/base.h" ]]; then
        FMT_INCLUDE="${candidate}"
        break
    fi
done
[[ -n "${FMT_INCLUDE}" ]] || { echo "FMT_HEADER_NOT_FOUND"; exit 10; }
printf 'FMT_INCLUDE=%s\n' "${FMT_INCLUDE}" > "${LOG}/dependencies.txt"

STAGE="REAL_EPOCH_AUTH_AND_GUEST_HEADER_ABI_GCC_CLANG_ASAN_UBSAN"
for compiler in g++ clang++-17; do
    case "${compiler}" in g++) label=gcc;; *) label=clang;; esac
    for mode in strict sanitized; do
        flags=(-std=c++23 -Wall -Wextra -Werror -pedantic -pthread -Isrc \
               "-I${FMT_INCLUDE}" -DFMT_HEADER_ONLY)
        if [[ "${mode}" == strict ]]; then
            flags+=(-O2)
        else
            flags+=(-O1 -g -fno-omit-frame-pointer -fsanitize=address,undefined)
        fi
        for test in \
            gcn_ordered_gds_epoch_auth \
            gcn_ordered_gds_word_layout \
            gcn_ordered_gds_live_capture_coverage \
            gcn_ordered_gds_device_plan; do
            exe="${LOG}/${test}-${label}-${mode}"
            extra=()
            case "${test}" in
                gcn_ordered_gds_word_layout|gcn_ordered_gds_device_plan)
                    extra+=(-Wno-pedantic) ;;
            esac
            if ! (cd "${SRC}" && "${compiler}" "${flags[@]}" "${extra[@]}" \
                  "tests/${test}.cpp" -o "${exe}" &&
                  ASAN_OPTIONS=detect_leaks=0 UBSAN_OPTIONS=halt_on_error=1 "${exe}") \
                > "${LOG}/${test}-${label}-${mode}.log" 2>&1; then
                tail -120 "${LOG}/${test}-${label}-${mode}.log"
                exit 11
            fi
            rm -f -- "${exe}"
        done
        grep -Fq 'GDS_VERIFIED_HOST_EPOCH_TOKENS=PASS' \
            "${LOG}/gcn_ordered_gds_epoch_auth-${label}-${mode}.log" || exit 12
        grep -Fq 'host_epoch_token_header_no_alias=PASS' \
            "${LOG}/gcn_ordered_gds_word_layout-${label}-${mode}.log" || exit 13
        grep -Fq 'missing_auth_identity_rejected=PASS' \
            "${LOG}/gcn_ordered_gds_live_capture_coverage-${label}-${mode}.log" || exit 14
        grep -Fq 'GDS_DEVICE_DISPATCH_PLAN=PASS' \
            "${LOG}/gcn_ordered_gds_device_plan-${label}-${mode}.log" || exit 15
    done
done

STAGE="INCREMENTAL_NATIVE_GCN_TOKEN_SPIRV_AND_VULKAN_LINK"
if ! cmake --build "${BUILD}" --parallel 6 \
    > "${LOG}/native-build.log" 2>&1; then
    tail -140 "${LOG}/native-build.log"; exit 16
fi
[[ -s "${BUILD}/shadps4" ]] || {
    echo "NATIVE_ELF_MISSING"; exit 17;
}
obj="${BUILD}/CMakeFiles/shadps4.dir/src/video_core/renderer_vulkan/vk_rasterizer.cpp.o"
[[ -s "${obj}" ]] || { echo "NATIVE_RASTERIZER_MISSING"; exit 18; }
nm -C "${obj}" > "${LOG}/native-rasterizer-symbols.txt"
grep -Fq 'Vulkan::Rasterizer::ResolveCompleteCikComputeOrderedGdsEpoch(' \
    "${LOG}/native-rasterizer-symbols.txt" || {
    echo "NATIVE_GCN_RESOLVER_SYMBOL_MISSING"; exit 19;
}
sha256sum "${BUILD}/shadps4" > "${LOG}/isolated-elf.sha256"
printf 'NATIVE_WARNING_LINES=%s\n' \
    "$(grep -c 'warning:' "${LOG}/native-build.log" || true)" \
    > "${LOG}/warning-count.txt"
STATUS="PASS_GCN_HOST_AUTH_TOKEN_TRANSPORT_NATIVE"
STAGE="COMPLETE_GUEST_SHADER_CONTINUATION_STILL_GATED"
echo "GCN_AUTH_GPU_TRANSFER_NATIVE=PASS GAME_TEST_READY=NO"
