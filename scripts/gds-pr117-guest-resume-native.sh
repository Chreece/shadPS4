#!/usr/bin/env bash
# Isolated source/native acceptance for real ordered-GDS register-state
# handoff. Does NOT launch any game, touch the protected executable or
# change display, audio, network or shell/session state.
set -Eeuo pipefail

PROJECT="${HOME}/projects/shadps4"
ROOT="${PROJECT}/tests/gds-pr117-native-20261029T164137Z-3996104"
SRC="${ROOT}/source"
BUILD="${ROOT}/build"
TAG="gds-pr117-guest-resume-native-$(date -u +%Y%m%dT%H%M%SZ)-$$"
LOG="${PROJECT}/logs/${TAG}"
STATUS="FAILED_OR_INCOMPLETE"
STAGE="PRECHECK"
mkdir -p "${LOG}"

finish() {
    local rc=$?
    trap - EXIT
    set +e
    {
        printf 'STATUS=%s\n' "${STATUS}"
        printf 'STAGE=%s\n' "${STAGE}"
        printf 'EXIT_CODE=%s\n' "${rc}"
        printf 'GUEST_SHADER_REENTRY=NOT_IMPLEMENTED\nGAME_TEST_READY=NO\n'
    } > "${LOG}/result.txt"
    tar -C "${PROJECT}/logs" -czf "${PROJECT}/logs/${TAG}.tar.gz" "${TAG}" \
        2> "${LOG}/archive-stderr.log"
    printf '\nGDS_CHECKPOINT_NATIVE=%s EXIT_CODE=%s STAGE=%s\n' "${STATUS}" "${rc}" "${STAGE}"
    printf 'UPLOAD_THIS=%s\n' "${PROJECT}/logs/${TAG}.tar.gz"
    exit "${rc}"
}
trap finish EXIT

for tool in git cmake ninja python3 g++ clang++-17 nm sha256sum tar flock; do
    command -v "${tool}" >/dev/null || {
        printf 'MISSING_REQUIRED_TOOL=%s\n' "${tool}"
        exit 2
    }
done

[[ -d "${SRC}/.git" && -f "${BUILD}/build.ninja" ]] || {
    echo 'ISOLATED_SOURCE_OR_BUILD_MISSING'
    exit 3
}
git -C "${SRC}" rev-parse HEAD > "${LOG}/source-head.txt"
git -C "${SRC}" status --porcelain=v1 --ignore-submodules=untracked > "${LOG}/checkout-status.txt"
[[ ! -s "${LOG}/checkout-status.txt" ]] || {
    echo 'ISOLATED_GIT_DIRTY_REFUSE_BUILD'
    exit 4
}

STAGE="SOURCE_CONTRACTS"
if ! (cd "${SRC}" && python3 tests/check_ordered_gds_ir.py) \
    > "${LOG}/source-contracts.log" 2>&1; then
    tail -90 "${LOG}/source-contracts.log"
    exit 5
fi
grep -Fq 'GCN_VCC_SCC_AND_RESUME_WAVE_IDENTITY_VERIFIED' "${LOG}/source-contracts.log"
grep -Fq 'PRODUCTION_GPU_REGISTER_STATE_RESUME_UPLOAD_GUARDED' "${LOG}/source-contracts.log"
grep -Fq 'PRODUCTION_GCN_LDS_STATE_TRANSFER_AND_GUARD' "${LOG}/source-contracts.log"
grep -Fq 'ORDERED_GDS_TYPED_IR_CONTRACT=PASS' "${LOG}/source-contracts.log"

STAGE="EXTRACTED_PRODUCTION_FRONTEND_GCC_CLANG"
for compiler in g++ clang++-17; do
    case "${compiler}" in
        g++) name=gcc ;;
        clang++-17) name=clang ;;
    esac
    if ! (cd "${SRC}" &&
          python3 tests/check_ordered_gds_frontend.py --compiler "${compiler}") \
        > "${LOG}/frontend-${name}.log" 2>&1; then
        tail -100 "${LOG}/frontend-${name}.log"
        exit 6
    fi
    grep -Fq 'GDS_ORDERED_FRONTEND_EXTRACTED=PASS' \
        "${LOG}/frontend-${name}.log"
    grep -Fq 'invalid_vdst_rejected=2' "${LOG}/frontend-${name}.log"
    if ! (cd "${SRC}" &&
          python3 tests/check_ordered_gds_live_capture.py --compiler "${compiler}") \
        > "${LOG}/live-registers-${name}.log" 2>&1; then
        tail -100 "${LOG}/live-registers-${name}.log"
        exit 6
    fi
    grep -Fq 'GDS_ORDERED_LIVE_REGISTER_CAPTURE=PASS' \
        "${LOG}/live-registers-${name}.log"
done

STAGE="GCC_AND_CLANG_SANITIZERS"
for compiler in g++ clang++-17; do
    case "${compiler}" in
        g++) name=gcc ;;
        clang++-17) name=clang ;;
    esac
    if ! (cd "${SRC}" && CXX="${compiler}" RUN_SANITIZERS=1 \
          bash tests/run_ordered_gds_cpu.sh) > "${LOG}/cpu-${name}.log" 2>&1; then
        tail -110 "${LOG}/cpu-${name}.log"
        exit 6
    fi
    for required in \
        ORDERED_GDS_CHECKPOINT_CONTRACT=PASS \
        ORDERED_GDS_GUEST_LDS_CHECKPOINT=PASS \
        ORDERED_GDS_CIK_M0_GUEST_ORDER=PASS \
        ORDERED_GDS_CIK_CAPTURE_PAYLOAD=PASS \
        ORDERED_GDS_GPU_PACKET_ROUNDTRIP=PASS \
        ORDERED_GDS_OFFLINE_REGRESSION=PASS; do
        grep -Fq "${required}" "${LOG}/cpu-${name}.log" || {
            printf 'MISSING_%s=%s\n' "${name}" "${required}"
            exit 7
        }
    done
done

STAGE="NATIVE_SHADPS4_BUILD"
if ! cmake --build "${BUILD}" --parallel 6 > "${LOG}/build.log" 2>&1; then
    tail -100 "${LOG}/build.log"
    exit 8
fi
[[ -s "${BUILD}/shadps4" ]] || { echo "BUILT_SHADPS4_MISSING"; exit 9; }
printf 'BUILD_COMPILER_WARNING_LINES=%s\n' \
    "$(grep -c 'warning:' "${LOG}/build.log" || true)" > "${LOG}/warning-count.txt"

STAGE="CHECK_PRODUCTION_BRIDGE_LINK_AND_SAFETY_GATE"
OBJECT="${BUILD}/CMakeFiles/shadps4.dir/src/video_core/renderer_vulkan/vk_rasterizer.cpp.o"
[[ -s "${OBJECT}" ]] || { echo 'NATIVE_RASTERIZER_OBJECT_NOT_LINKED'; exit 10; }
nm -C "${OBJECT}" > "${LOG}/rasterizer-symbols.txt"
grep -Fq 'Vulkan::Rasterizer::ResolveCompleteCikComputeOrderedGdsEpoch(' \
    "${LOG}/rasterizer-symbols.txt" || {
    echo 'REAL_GCN_RESUME_REGISTER_BRIDGE_NOT_COMPILED'
    exit 10
}
grep -Fq 'ASSERT_MSG(!program.info.uses_ordered_gds' \
    "${SRC}/src/shader_recompiler/backend/spirv/emit_spirv.cpp" || {
    echo 'DANGEROUS_UNSUPPORTED_GUEST_OPCODE_UNGATED'
    exit 11
}
sha256sum "${BUILD}/shadps4" > "${LOG}/isolated-elf.sha256"
STATUS="PASS_NATIVE_GUEST_CHECKPOINT_HANDOFF"
STAGE="COMPLETE_NO_GUEST_RUNTIME_ENABLED"
printf 'GDS_GUEST_STATE_HANDOFF_NATIVE=PASS GUEST_SHADER_REENTRY=NOT_IMPLEMENTED GAME_TEST_READY=NO\n'
