#!/usr/bin/env bash
# Real guest GCN first-pass Vulkan compiler/dispatch integration acceptance.
# Does not launch the game or alter SSH, X11, Sunshine or Moonlight.
set -Eeuo pipefail
PROJECT="$HOME/projects/shadps4"
ROOT="$PROJECT/tests/gds-pr117-native-20261029T164137Z-3996104"
SRC="$ROOT/source"
BUILD="$ROOT/build"
TAG="gds-pr117-real-gcn-firstpass-$(date -u +%Y%m%dT%H%M%SZ)-$$"
OUT="$PROJECT/logs/$TAG"
STAGE=PRECHECK
STATUS=FAILED_OR_INCOMPLETE
mkdir -p "$OUT"
finish() {
    local rc=$?
    trap - EXIT
    set +e
    printf 'STATUS=%s\nSTAGE=%s\nEXIT_CODE=%s\n' "$STATUS" "$STAGE" "$rc" \
        > "$OUT/result.txt"
    printf 'REAL_GUEST_GPU_DISPATCH=NOT_RUN\nGUEST_SHADER_REENTRY=NOT_IMPLEMENTED\n' \
        >> "$OUT/result.txt"
    printf 'GAME_TEST_READY=NO\n' >> "$OUT/result.txt"
    tar -C "$PROJECT/logs" -czf "$OUT.tar.gz" "$TAG" \
        2> "$OUT/archive-errors.txt"
    echo "PR117_STATUS=$STATUS STAGE=$STAGE EXIT=$rc"
    echo "UPLOAD_THIS=$OUT.tar.gz"
}
trap finish EXIT

for cmd in git cmake ninja python3 g++ clang++-17 sha256sum tar flock; do
    command -v "$cmd" >/dev/null || { echo "MISSING=$cmd"; exit 2; }
done
[[ -d "$SRC/.git" && -f "$BUILD/build.ninja" ]] || {
    echo "PR117_ISOLATED_BUILD_NOT_FOUND"; exit 3;
}
git -C "$SRC" rev-parse HEAD > "$OUT/source-head.txt"
git -C "$SRC" status --porcelain=v1 --untracked-files=no \
    > "$OUT/source-status.txt"
[[ ! -s "$OUT/source-status.txt" ]] || {
    echo "ISOLATED_SOURCE_DIRTY_NOT_OVERWRITING"; exit 4;
}
grep -Fq 'ASSERT_MSG(!program.info.uses_ordered_gds' \
    "$SRC/src/shader_recompiler/backend/spirv/emit_spirv.cpp" || {
    echo "GUEST_NORMAL_EXECUTION_GATE_REMOVED"; exit 5;
}
STAGE=REAL_GCN_FIRSTPASS_SOURCE_CONTRACTS
if ! (
    set -Eeuo pipefail
    cd "$SRC"
    python3 tests/check_ordered_gds_ir.py
) > "$OUT/contracts.log" 2>&1; then
    tail -140 "$OUT/contracts.log"; exit 6
fi
grep -Fq 'REAL_ORIGINAL_GCN_VULKAN_FIRST_PASS_COMPILED_AND_DISPATCH_API_GATED' \
    "$OUT/contracts.log" || exit 7

FMT=""
for dir in "$SRC/externals/fmt/include" "$BUILD/_deps/fmt-src/include" /usr/include; do
    if [[ -f "$dir/fmt/base.h" ]]; then FMT="$dir"; break; fi
done
[[ -n "$FMT" ]] || { echo "FMT_HEADER_NOT_FOUND"; exit 8; }
STAGE=MIXED_TERMINAL_REGRESSION
for compiler in g++ clang++-17; do
    case "$compiler" in g++) label=gcc;; *) label=clang;; esac
    if ! (
        set -Eeuo pipefail
        cd "$SRC"
        for test in resume_state checkpoint epoch_auth device_plan; do
            "$compiler" -std=c++23 -O2 -UNDEBUG -Wall -Wextra -Werror -pedantic \
                -pthread -DFMT_HEADER_ONLY -Isrc "-I$FMT" \
                "tests/gcn_ordered_gds_$test.cpp" -o "$OUT/$label-$test"
            "$OUT/$label-$test"
        done
    ) >"$OUT/$label-tests.log" 2>&1; then
        tail -130 "$OUT/$label-tests.log"; exit 9
    fi
    grep -Fq 'real_terminal_mixed_state_resume_guard=PASS' \
        "$OUT/$label-tests.log" || exit 10
    grep -Fq 'ORDERED_GDS_CHECKPOINT_CONTRACT=PASS' \
        "$OUT/$label-tests.log" || exit 11
    grep -Fq 'real_terminal_bypass_epoch_authority=PASS' \
        "$OUT/$label-tests.log" || exit 12
done
STAGE=REAL_GCN_MIXED_PASS_SANITIZERS
if ! (
    set -Eeuo pipefail
    cd "$SRC"
    clang++-17 -std=c++23 -O1 -g -UNDEBUG -Wall -Wextra -Werror -pedantic \
        -pthread -fsanitize=address,undefined -fno-omit-frame-pointer \
        -DFMT_HEADER_ONLY -Isrc "-I$FMT" \
        tests/gcn_ordered_gds_resume_state.cpp -o "$OUT/mixed-sanitized"
    ASAN_OPTIONS=detect_leaks=0 UBSAN_OPTIONS=halt_on_error=1 \
        "$OUT/mixed-sanitized"
) > "$OUT/sanitized.log" 2>&1; then
    tail -130 "$OUT/sanitized.log"; exit 13
fi
grep -Fq 'real_terminal_mixed_state_resume_guard=PASS' "$OUT/sanitized.log" || exit 14

STAGE=REAL_GCN_VULKAN_PIPELINE_AND_CAPTURE_LINK
exec 9>"$ROOT/.indirect-native-build.lock"
flock -x 9
if ! nice -n 10 cmake --build "$BUILD" --target shadps4 --parallel 2 \
    > "$OUT/native-build.log" 2>&1; then
    tail -150 "$OUT/native-build.log"; exit 15
fi
[[ -x "$BUILD/shadps4" ]] || { echo "MISSING_NATIVE_SHADPS4_ELF"; exit 16; }
sha256sum "$BUILD/shadps4" > "$OUT/elf.sha256"
grep -E '(^|[^A-Za-z])(warning:|error:|fatal error:)' "$OUT/native-build.log" \
    > "$OUT/compile-diagnostics.txt" || true
[[ ! -s "$OUT/compile-diagnostics.txt" ]] || {
    cat "$OUT/compile-diagnostics.txt"; exit 17;
}
STATUS=PASS_REAL_FIRSTPASS_PIPELINE_AND_DISPATCH_NATIVE
STAGE=GPU_GUEST_CAPTURE_RUNTIME_AND_SECOND_PASS_NOT_YET_EXERCISED
echo 'REAL_GCN_VULKAN_FIRST_PASS_COMPILED=PASS GUEST_GPU_DISPATCH=NOT_RUN GAME_TEST_READY=NO'
