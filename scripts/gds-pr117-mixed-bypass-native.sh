#!/usr/bin/env bash
# PR117: original terminal-bypass packets through mixed checkpoint + resume validation.
# Safe native acceptance ONLY: no guest GPU shader dispatch, X11/Sunshine changes or game launch.
set -Eeuo pipefail
PROJECT="$HOME/projects/shadps4"
ROOT="$PROJECT/tests/gds-pr117-native-20261029T164137Z-3996104"
SRC="$ROOT/source"
BUILD="$ROOT/build"
TAG="gds-pr117-mixed-bypass-$(date -u +%Y%m%dT%H%M%SZ)-$$"
OUT="$PROJECT/logs/$TAG"
STAGE=PRECHECK
STATUS=FAILED_OR_INCOMPLETE
mkdir -p "$OUT"

finish() {
    local rc=$?
    trap - EXIT
    set +e
    printf 'STATUS=%s\nSTAGE=%s\nEXIT_CODE=%s\nGAME_TEST_READY=NO\n' \
        "$STATUS" "$STAGE" "$rc" > "$OUT/result.txt"
    printf 'GUEST_GPU_CAPTURE=NOT_DISPATCHED\n' >> "$OUT/result.txt"
    printf 'GUEST_SHADER_REENTRY=NOT_IMPLEMENTED\n' >> "$OUT/result.txt"
    tar -C "$PROJECT/logs" -czf "$OUT.tar.gz" "$TAG" \
        2>"$OUT/archive-errors.txt"
    echo "PR117_STATUS=$STATUS STAGE=$STAGE EXIT=$rc"
    echo "UPLOAD_THIS=$OUT.tar.gz"
}
trap finish EXIT
for cmd in git g++ clang++-17 python3 cmake flock sha256sum tar; do
    command -v "$cmd" >/dev/null || { echo "MISSING=$cmd"; exit 2; }
done
[[ -d "$SRC/.git" && -f "$BUILD/build.ninja" ]] || {
    echo "PINNED_PR117_ISOLATED_SOURCE_OR_BUILD_MISSING"; exit 3;
}
git -C "$SRC" rev-parse HEAD > "$OUT/source-head.txt"
git -C "$SRC" status --porcelain=v1 --untracked-files=no > "$OUT/source-status.txt"
[[ ! -s "$OUT/source-status.txt" ]] || {
    echo "PR117_CHECKOUT_DIRTY_REFUSE_OVERWRITE"; exit 4;
}
grep -Fq 'ASSERT_MSG(!program.info.uses_ordered_gds' \
    "$SRC/src/shader_recompiler/backend/spirv/emit_spirv.cpp" || {
    echo "NORMAL_GUEST_GDS_EXECUTION_GATE_MISSING"; exit 5;
}
grep -Fq 'real_terminal_mixed_state_resume_guard=PASS' \
    "$SRC/tests/gcn_ordered_gds_resume_state.cpp" || {
    echo "MIXED_TERMINAL_SOURCE_NOT_PRESENT"; exit 6;
}

STAGE=POST_SSA_GCN_SOURCE_CONTRACT
if ! (cd "$SRC" && python3 tests/check_ordered_gds_ir.py) \
    > "$OUT/contracts.log" 2>&1; then
    tail -100 "$OUT/contracts.log"; exit 7
fi
grep -Fq 'REAL_GCN_TERMINAL_BYPASS_WRITES_AUTHENTIC_WAVE_COVERAGE' \
    "$OUT/contracts.log" || exit 8

FMT=""
for dir in "$SRC/externals/fmt/include" "$BUILD/_deps/fmt-src/include" /usr/include; do
    if [[ -f "$dir/fmt/base.h" ]]; then FMT="$dir"; break; fi
done
[[ -n "$FMT" ]] || { echo "FMT_HEADER_NOT_FOUND"; exit 9; }
STAGE=MIXED_TERMINAL_GCC_AND_CLANG
for compiler in g++ clang++-17; do
    case "$compiler" in g++) label=gcc;; *) label=clang;; esac
    if ! (
        cd "$SRC"
        for test in resume_state checkpoint epoch_auth; do
            "$compiler" -std=c++23 -O2 -UNDEBUG -Wall -Wextra -Werror -pedantic \
                -pthread -DFMT_HEADER_ONLY -Isrc "-I$FMT" \
                "tests/gcn_ordered_gds_$test.cpp" -o "$OUT/$label-$test"
            "$OUT/$label-$test"
        done
    ) > "$OUT/$label-tests.log" 2>&1; then
        tail -120 "$OUT/$label-tests.log"; exit 10
    fi
    grep -Fq 'real_terminal_mixed_state_resume_guard=PASS' \
        "$OUT/$label-tests.log" || exit 11
    grep -Fq 'ORDERED_GDS_CHECKPOINT_CONTRACT=PASS' \
        "$OUT/$label-tests.log" || exit 12
    grep -Fq 'real_terminal_bypass_epoch_authority=PASS' \
        "$OUT/$label-tests.log" || exit 13
done

STAGE=MIXED_TERMINAL_CLANG_ASAN_UBSAN
if ! (
    cd "$SRC"
    clang++-17 -std=c++23 -O1 -g -UNDEBUG -Wall -Wextra -Werror -pedantic \
        -pthread -fsanitize=address,undefined -fno-omit-frame-pointer \
        -DFMT_HEADER_ONLY -Isrc "-I$FMT" \
        tests/gcn_ordered_gds_resume_state.cpp -o "$OUT/resume-sanitized"
    ASAN_OPTIONS=detect_leaks=0 UBSAN_OPTIONS=halt_on_error=1 \
        "$OUT/resume-sanitized"
) > "$OUT/resume-sanitized.log" 2>&1; then
    tail -120 "$OUT/resume-sanitized.log"; exit 14
fi
grep -Fq 'real_terminal_mixed_state_resume_guard=PASS' \
    "$OUT/resume-sanitized.log" || exit 15

STAGE=REAL_SHADPS4_ISOLATED_NATIVE_LINK
exec 9>"$ROOT/.indirect-native-build.lock"
flock -x 9
if ! nice -n 10 cmake --build "$BUILD" --target shadps4 --parallel 2 \
    >"$OUT/native-build.log" 2>&1; then
    tail -160 "$OUT/native-build.log"; exit 16
fi
[[ -x "$BUILD/shadps4" ]] || { echo "SHADPS4_NATIVE_BINARY_MISSING"; exit 17; }
sha256sum "$BUILD/shadps4" > "$OUT/elf.sha256"
grep -E 'warning:|error:|fatal error:' "$OUT/native-build.log" \
    > "$OUT/compile-diagnostics.txt" || true
[[ ! -s "$OUT/compile-diagnostics.txt" ]] || { echo "NATIVE_COMPILER_DIAGNOSTICS"; exit 18; }

STATUS=PASS_MIXED_GCN_TERMINAL_BYPASS_HOST_NATIVE
STAGE=FIRST_GUEST_GPU_CAPTURE_AND_GCN_REENTRY_STILL_GATED
echo "MIXED_TERMINAL_BYPASS=PASS NATIVE_LINK=PASS GAME_TEST_READY=NO"
