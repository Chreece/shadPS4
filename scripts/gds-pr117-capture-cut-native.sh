#!/usr/bin/env bash
# PR117: validate real first-pass GCN checkpoint-and-terminate emitter.
# Previously passing host epoch, VDST, LDS and packet suites are not rerun.
# Does not launch games, touch the protected emulator, X11, Sunshine or SSH.
set -Eeuo pipefail

PROJECT="${HOME}/projects/shadps4"
ROOT="${PROJECT}/tests/gds-pr117-native-20261029T164137Z-3996104"
SRC="${ROOT}/source"
BUILD="${ROOT}/build"
TAG="gds-pr117-capture-cut-native-$(date -u +%Y%m%dT%H%M%SZ)-$$"
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
        printf 'REAL_CAPTURE_ONLY_SHADERS=COMPILED_NOT_DISPATCHED\n'
        printf 'GUEST_SHADER_REENTRY=NOT_IMPLEMENTED\nGAME_TEST_READY=NO\n'
    } > "${LOG}/result.txt"
    tar -C "${PROJECT}/logs" -czf "${PROJECT}/logs/${TAG}.tar.gz" "${TAG}" 2>"${LOG}/archive-stderr.txt"
    printf 'GDS_FIRST_PASS_STATUS=%s STAGE=%s EXIT=%s\n' "${STATUS}" "${STAGE}" "${rc}"
    printf 'UPLOAD_THIS=%s\n' "${PROJECT}/logs/${TAG}.tar.gz"
    exit "${rc}"
}
trap finish EXIT

for exe in git g++ clang++-17 python3 cmake ninja nm sha256sum tar; do
    command -v "${exe}" >/dev/null || {
        echo "REQUIRED_TOOL_NOT_FOUND=${exe}"; exit 2;
    }
done
[[ -d "${SRC}/.git" && -f "${BUILD}/build.ninja" ]] || {
    echo "ISOLATED_SOURCE_OR_BUILD_MISSING"; exit 3;
}
git -C "${SRC}" rev-parse HEAD > "${LOG}/source-head.txt"
git -C "${SRC}" status --porcelain=v1 --ignore-submodules=untracked > "${LOG}/source-status.txt"
[[ ! -s "${LOG}/source-status.txt" ]] || {
    echo "ISOLATED_SOURCE_DIRTY_NOT_TOUCHING_IT"; exit 4;
}
grep -Fq 'ASSERT_MSG(!program.info.uses_ordered_gds' \
    "${SRC}/src/shader_recompiler/backend/spirv/emit_spirv.cpp" || {
    echo "NORMAL_GCN_EXECUTION_SAFETY_GATE_REMOVED"; exit 5;
}

STAGE="REAL_CAPTURE_CUT_SOURCE_AND_FAIL_CLOSED_CONTRACT"
if ! (cd "${SRC}" && python3 tests/check_ordered_gds_ir.py) \
    > "${LOG}/source-contracts.log" 2>&1; then
    tail -90 "${LOG}/source-contracts.log"; exit 6;
fi
grep -Fq 'EXPLICIT_GUEST_CAPTURE_ONLY_SHADER_CUT_NORMAL_EXECUTION_GATED' \
    "${LOG}/source-contracts.log" || exit 7

STAGE="EXACT_PRODUCTION_FIRST_PASS_GCC_AND_CLANG"
for compiler in g++ clang++-17; do
    case "${compiler}" in g++) label=gcc;; *) label=clang;; esac
    for mode in strict sanitized; do
        args=()
        if [[ "${mode}" == sanitized ]]; then args+=(--sanitize); fi
        if ! (cd "${SRC}" &&
              ASAN_OPTIONS=detect_leaks=0 UBSAN_OPTIONS=halt_on_error=1 \
              python3 tests/check_ordered_gds_capture_cut.py \
                  --compiler "${compiler}" "${args[@]}") \
             > "${LOG}/capture-cut-${label}-${mode}.log" 2>&1; then
            tail -105 "${LOG}/capture-cut-${label}-${mode}.log"; exit 8;
        fi
        grep -Fq 'REAL_FIRST_PASS_PRODUCTION_CAPTURE_CUT=PASS' \
            "${LOG}/capture-cut-${label}-${mode}.log" || exit 9
        grep -Fq 'normal_guest_execution_fail_closed=PASS' \
            "${LOG}/capture-cut-${label}-${mode}.log" || exit 10
    done
done

STAGE="NATIVE_FIRST_PASS_GUEST_SPIRV_COMPILER_LINK"
if ! cmake --build "${BUILD}" --parallel 6 > "${LOG}/native-build.log" 2>&1; then
    tail -145 "${LOG}/native-build.log"; exit 11;
fi
[[ -s "${BUILD}/shadps4" ]] || { echo "ISOLATED_NATIVE_ELF_MISSING"; exit 12; }
obj="${BUILD}/CMakeFiles/shadps4.dir/src/shader_recompiler/backend/spirv/emit_spirv.cpp.o"
[[ -s "${obj}" ]] || { echo "NATIVE_SPIRV_EMITTER_OBJECT_MISSING"; exit 13; }
nm -C "${obj}" > "${LOG}/native-emitter-symbols.txt"
grep -Fq 'EmitSPIRVOrderedGdsCapturePass(' "${LOG}/native-emitter-symbols.txt" || {
    echo "REAL_GUEST_CAPTURE_SHADER_COMPILE_ENTRY_MISSING"; exit 14;
}
sha256sum "${BUILD}/shadps4" > "${LOG}/isolated-elf.sha256"
printf 'COMPILER_WARNING_LINES=%s\n' \
    "$(grep -c 'warning:' "${LOG}/native-build.log" || true)" > "${LOG}/warnings.txt"
STATUS="PASS_REAL_GUEST_CAPTURE_ONLY_SPIRV_BACKEND_NATIVE"
STAGE="GUEST_RESUME_SHADER_AND_PACKER_SEED_STILL_GATED"
echo "ORIGINAL_GUEST_FIRST_PASS_CAPTURE_CUT_NATIVE=PASS GAME_TEST_READY=NO"
