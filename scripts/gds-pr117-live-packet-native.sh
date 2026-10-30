#!/usr/bin/env bash
# Native shadPS4 integration test for ACTUAL per-wave GCN packet-body GPU writer.
# No game launch, no unsafe execution gate removal, no SSH/Sunshine/X11 action.
# Previous full GDS/owned GPU-memory tests were PASS: don't rerun them.
set -Eeuo pipefail

PROJECT="${HOME}/projects/shadps4"
ROOT="${PROJECT}/tests/gds-pr117-native-20261029T164137Z-3996104"
SRC="${ROOT}/source"
BUILD="${ROOT}/build"
TAG="gds-pr117-live-packet-native-$(date -u +%Y%m%dT%H%M%SZ)-$$"
LOG="${PROJECT}/logs/${TAG}"
STAGE="PRECHECK"
STATUS="FAILED_OR_INCOMPLETE"
mkdir -p "${LOG}"

finish() {
    local rc=$?
    trap - EXIT
    set +e
    {
        printf 'STATUS=%s\nSTAGE=%s\nEXIT_CODE=%s\n' "${STATUS}" "${STAGE}" "${rc}"
        printf 'GPU_PACKET_AUTH=NOT_CONNECTED\nGUEST_SHADER_REENTRY=NOT_IMPLEMENTED\nGAME_TEST_READY=NO\n'
    } > "${LOG}/result.txt"
    tar -C "${PROJECT}/logs" -czf "${PROJECT}/logs/${TAG}.tar.gz" "${TAG}" \
        2> "${LOG}/archive-stderr.log"
    printf 'GDS_LIVE_PACKET_NATIVE=%s EXIT_CODE=%s STAGE=%s\n' "${STATUS}" "${rc}" "${STAGE}"
    printf 'UPLOAD_THIS=%s\n' "${PROJECT}/logs/${TAG}.tar.gz"
    exit "${rc}"
}
trap finish EXIT

for command in git cmake ninja g++ clang++-17 python3 tar nm sha256sum; do
    command -v "${command}" >/dev/null || {
        echo "MISSING_REQUIRED_COMMAND=${command}"; exit 2;
    }
done
[[ -d "${SRC}/.git" && -f "${BUILD}/build.ninja" ]] || {
    echo "ISOLATED_SOURCE_OR_BUILD_MISSING"; exit 3;
}
git -C "${SRC}" rev-parse HEAD > "${LOG}/source-head.txt"
git -C "${SRC}" status --porcelain=v1 --ignore-submodules=untracked \
    > "${LOG}/source-status.txt"
[[ ! -s "${LOG}/source-status.txt" ]] || { echo "SOURCE_DIRTY_REFUSE_BUILD"; exit 4; }

STAGE="REAL_GCN_PACKET_IR_AND_SPIRV_CONTRACTS"
if ! (cd "${SRC}" && python3 tests/check_ordered_gds_ir.py) \
    > "${LOG}/source-contracts.log" 2>&1; then
    tail -110 "${LOG}/source-contracts.log"; exit 5
fi
grep -Fq 'REAL_GCN_GPU_CAPTURE_PACKET_BODY_FROM_LIVE_ISA_FAIL_CLOSED' \
    "${LOG}/source-contracts.log" || exit 6
grep -Fq 'ASSERT_MSG(!program.info.uses_ordered_gds' \
    "${SRC}/src/shader_recompiler/backend/spirv/emit_spirv.cpp" || {
    echo "DANGEROUS_GUEST_GDS_RUNTIME_UNGATED"; exit 7;
}

STAGE="ACTUAL_EXTRACTED_GUEST_GCN_FRONTEND_AND_LIVE_PACKET"
for compiler in g++ clang++-17; do
    case "${compiler}" in g++) label=gcc;; *) label=clang;; esac
    if ! (cd "${SRC}" &&
          python3 tests/check_ordered_gds_frontend.py --compiler "${compiler}") \
        > "${LOG}/frontend-${label}.log" 2>&1; then
        tail -110 "${LOG}/frontend-${label}.log"; exit 8
    fi
    grep -Fq 'decoded_site_vdst_next_pc_control=PASS' "${LOG}/frontend-${label}.log" || exit 9
    if ! (cd "${SRC}" &&
          python3 tests/check_ordered_gds_live_capture.py --compiler "${compiler}") \
        > "${LOG}/live-packet-${label}.log" 2>&1; then
        tail -110 "${LOG}/live-packet-${label}.log"; exit 10
    fi
    grep -Fq 'live_gpu_packet_fields=PASS' "${LOG}/live-packet-${label}.log" || exit 11
    grep -Fq 'inactive_exec_no_fabrication=PASS' "${LOG}/live-packet-${label}.log" || exit 12
done

STAGE="PACKET_GPU_WORD_LAYOUT_AND_CLONED_SSA_COVERAGE"
for compiler in g++ clang++-17; do
    case "${compiler}" in g++) label=gcc;; *) label=clang;; esac
    for mode in strict asan-ubsan; do
        flags=(-std=c++23 -Wall -Wextra -Werror -pedantic -pthread -Isrc)
        if [[ "${mode}" == strict ]]; then
            flags+=(-O2)
        else
            flags+=(-O1 -g -fsanitize=address,undefined -fno-omit-frame-pointer)
        fi
        for test in gcn_ordered_gds_packet_word_layout gcn_ordered_gds_live_capture_coverage; do
            exe="${LOG}/${test}-${label}-${mode}"
            if ! (cd "${SRC}" &&
                  "${compiler}" "${flags[@]}" "tests/${test}.cpp" -o "${exe}" &&
                  ASAN_OPTIONS=detect_leaks=0 UBSAN_OPTIONS=halt_on_error=1 "${exe}") \
                  > "${LOG}/${test}-${label}-${mode}.log" 2>&1; then
                tail -105 "${LOG}/${test}-${label}-${mode}.log"; exit 13
            fi
            case "${test}" in
                gcn_ordered_gds_packet_word_layout)
                    grep -Fq 'ORDERED_GDS_REAL_GPU_PACKET_LAYOUT=PASS' \
                        "${LOG}/${test}-${label}-${mode}.log" || exit 14 ;;
                *)
                    grep -Fq 'incomplete_gpu_packet_rejected=PASS' \
                        "${LOG}/${test}-${label}-${mode}.log" || exit 15 ;;
            esac
            rm -f -- "${exe}"
        done
    done
done

STAGE="NATIVE_GPU_PACKET_SSBO_EMITTER_AND_BINDING_LINK"
if ! cmake --build "${BUILD}" --parallel 6 > "${LOG}/native-build.log" 2>&1; then
    tail -145 "${LOG}/native-build.log"; exit 16
fi
[[ -s "${BUILD}/shadps4" ]] || { echo "ISOLATED_NATIVE_ELF_MISSING"; exit 17; }
sha256sum "${BUILD}/shadps4" > "${LOG}/isolated-elf.sha256"
printf 'CXX_WARNING_LINES=%s\n' "$(grep -c 'warning:' "${LOG}/native-build.log" || true)" \
    > "${LOG}/warnings.txt"

STATUS="PASS_GPU_LIVE_CIK_PACKET_BODY_NATIVE"
STAGE="COMPLETE_AUTHENTIC_EPOCH_AND_GUEST_REENTRY_NOT_ENABLED"
echo 'REAL_LIVE_GCN_PACKET_SSBO_COMPILER_AND_NATIVE=PASS GAME_TEST_READY=NO'
