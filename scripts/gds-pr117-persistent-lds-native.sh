#!/usr/bin/env bash
# PR117 real ordered-GDS persistent LDS: exact native shader/backend acceptance.
# No game, display, Sunshine, Ollama unload, GPU resets or network restarts.
set -Eeuo pipefail
PROJECT="$HOME/projects/shadps4"
ROOT="$PROJECT/tests/gds-pr117-native-20261029T164137Z-3996104"
SRC="$ROOT/source"
BUILD="$ROOT/build"
TAG="gds-pr117-persistent-lds-$(date -u +%Y%m%dT%H%M%SZ)-$$"
OUT="$PROJECT/logs/$TAG"
STATUS=FAILED_OR_INCOMPLETE
STAGE=PRECHECK
mkdir -p "$OUT"
finish() {
    local rc=$?
    trap - EXIT
    set +e
    printf 'STATUS=%s\nSTAGE=%s\nEXIT_CODE=%s\n' "$STATUS" "$STAGE" "$rc" >"$OUT/result.txt"
    printf 'GAME_TEST_READY=NO\nGUEST_SPIRV_DISPATCH=NOT_ENABLED\nGUEST_SHADER_REENTRY=NOT_IMPLEMENTED\n' >>"$OUT/result.txt"
    tar -C "$PROJECT/logs" -czf "$OUT.tar.gz" "$TAG" \
        2>"$OUT/archive-error.txt"
    printf 'PR117_STATUS=%s STAGE=%s EXIT=%s\n' "$STATUS" "$STAGE" "$rc"
    printf 'EVIDENCE=%s\n' "$OUT.tar.gz"
}
trap finish EXIT

for cmd in git g++ clang++-17 cmake python3 flock sha256sum tar; do
    command -v "$cmd" >/dev/null || {
        echo "MISSING_REQUIRED_TOOL=$cmd"
        exit 2
    }
done
[[ -d "$SRC/.git" && -f "$BUILD/build.ninja" ]] || {
    echo "ISOLATED_PR117_SOURCE_OR_BUILD_MISSING"; exit 3;
}
git -C "$SRC" status --porcelain=v1 --untracked-files=no >"$OUT/source-status.txt"
[[ ! -s "$OUT/source-status.txt" ]] || {
    echo "ISOLATED_SOURCE_DIRTY_NOT_OVERWRITING"; exit 4;
}
git -C "$SRC" rev-parse HEAD >"$OUT/source-head.txt"
grep -Fq 'ASSERT_MSG(!program.info.uses_ordered_gds' \
  "$SRC/src/shader_recompiler/backend/spirv/emit_spirv.cpp" || {
    echo "NORMAL_ORDERED_GDS_GAME_EXECUTION_GATE_MISSING"; exit 5;
}

STAGE=REAL_GCN_LDS_LOWERING_AND_VULKAN_SOURCE_CONTRACTS
if ! (cd "$SRC" && python3 tests/check_ordered_gds_ir.py) \
       >"$OUT/contracts.log" 2>&1; then
    tail -100 "$OUT/contracts.log"
    exit 6
fi
grep -Fq 'REAL_ORDERED_GDS_LDS_PERSISTENT_GPU_BUFFER_AND_BARRIER_SEMANTICS' \
    "$OUT/contracts.log" || exit 7
grep -Fq 'EXPLICIT_GUEST_CAPTURE_ONLY_SHADER_CUT_NORMAL_EXECUTION_GATED' \
    "$OUT/contracts.log" || exit 8

FMT=""
for d in "$SRC/externals/fmt/include" "$BUILD/_deps/fmt-src/include" /usr/include; do
    if [[ -f "$d/fmt/base.h" ]]; then FMT="$d"; break; fi
done
[[ -n "$FMT" ]] || {
    echo "FMT_INCLUDE_UNAVAILABLE"; exit 9;
}

STAGE=NEW_PERSISTENT_LDS_PLAN_GCC_CLANG
for compiler in g++ clang++-17; do
    case "$compiler" in g++) label=gcc;; *) label=clang;; esac
    if ! (
        cd "$SRC"
        "$compiler" -std=c++23 -O2 -Wall -Wextra -Werror -pedantic \
            -pthread -DFMT_HEADER_ONLY -Isrc "-I$FMT" \
            tests/gcn_ordered_gds_device_plan.cpp \
            -o "$OUT/lds-plan-$label"
        "$OUT/lds-plan-$label"
    ) >"$OUT/lds-plan-$label.log" 2>&1; then
        tail -90 "$OUT/lds-plan-$label.log"
        exit 10
    fi
    grep -Fq 'persistent_lds_workgroup_offsets=PASS' \
        "$OUT/lds-plan-$label.log" || exit 11
done

STAGE=PERSISTENT_LDS_PLAN_ASAN_UBSAN
if ! (
    cd "$SRC"
    clang++-17 -std=c++23 -O1 -g -Wall -Wextra -Werror -pedantic \
        -pthread -fsanitize=address,undefined -fno-omit-frame-pointer \
        -DFMT_HEADER_ONLY -Isrc "-I$FMT" \
        tests/gcn_ordered_gds_device_plan.cpp \
        -o "$OUT/lds-plan-sanitized"
    ASAN_OPTIONS=detect_leaks=0 UBSAN_OPTIONS=halt_on_error=1 \
        "$OUT/lds-plan-sanitized"
) >"$OUT/lds-plan-sanitized.log" 2>&1; then
    tail -90 "$OUT/lds-plan-sanitized.log"
    exit 12
fi
grep -Fq 'persistent_lds_workgroup_offsets=PASS' \
    "$OUT/lds-plan-sanitized.log" || exit 13

STAGE=NATIVE_GCN_LDS_SHADER_AND_RASTERIZER_LINK
exec 9>"$ROOT/.indirect-native-build.lock"
flock -x 9
if ! nice -n 10 cmake --build "$BUILD" --target shadps4 --parallel 2 \
    >"$OUT/native-build.log" 2>&1; then
    tail -140 "$OUT/native-build.log"
    exit 14
fi
[[ -x "$BUILD/shadps4" ]] || {
    echo "ISOLATED_NATIVE_ELF_MISSING"; exit 15;
}
sha256sum "$BUILD/shadps4" >"$OUT/native-elf.sha256"
grep -E 'warning:|error:|fatal error' "$OUT/native-build.log" \
    >"$OUT/compiler-diagnostics.txt" || true
STATUS=PASS_PERSISTENT_GCN_LDS_SOURCE_NATIVE
STAGE=COMPLETE_GPU_GUEST_RESUME_STILL_GATED
echo "PERSISTENT_GCN_LDS_SOURCE_AND_NATIVE=PASS GAME_TEST_READY=NO"
