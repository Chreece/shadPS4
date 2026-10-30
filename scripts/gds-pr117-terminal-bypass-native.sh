#!/usr/bin/env bash
# PR117: real original-GCN S_ENDPGM bypass GPU packet emitter acceptance.
# Compiles the actual backend and verifies canonical host-side bypass auth.
# Does not dispatch a game/guest GPU shader, alter X11/Sunshine, or restart SSH.
set -Eeuo pipefail
PROJECT="$HOME/projects/shadps4"
ROOT="$PROJECT/tests/gds-pr117-native-20261029T164137Z-3996104"
SRC="$ROOT/source"
BUILD="$ROOT/build"
TAG="gds-pr117-terminal-bypass-$(date -u +%Y%m%dT%H%M%SZ)-$$"
OUT="$PROJECT/logs/$TAG"
STATUS=FAILED_OR_INCOMPLETE
STAGE=PRECHECK
mkdir -p "$OUT"

finish() {
    local rc=$?
    trap - EXIT
    set +e
    printf 'STATUS=%s\nSTAGE=%s\nEXIT_CODE=%s\n' \
        "$STATUS" "$STAGE" "$rc" > "$OUT/result.txt"
    printf 'GAME_TEST_READY=NO\nREAL_GUEST_GPU_CAPTURE=NOT_DISPATCHED\n' \
        >> "$OUT/result.txt"
    printf 'REAL_GUEST_SHADER_REENTRY=NOT_IMPLEMENTED\n' \
        >> "$OUT/result.txt"
    tar -C "$PROJECT/logs" -czf "$OUT.tar.gz" "$TAG" \
        2>"$OUT/archive-errors.txt"
    printf 'PR117_STATUS=%s STAGE=%s EXIT=%s\n' "$STATUS" "$STAGE" "$rc"
    printf 'UPLOAD_THIS=%s\n' "$OUT.tar.gz"
}
trap finish EXIT

for cmd in git g++ clang++-17 python3 cmake flock sha256sum tar; do
    command -v "$cmd" >/dev/null || {
        echo "MISSING=$cmd"; exit 2;
    }
done
[[ -d "$SRC/.git" && -f "$BUILD/build.ninja" ]] || {
    echo "ISOLATED_PR117_SOURCE_OR_BUILD_MISSING"; exit 3;
}
git -C "$SRC" rev-parse HEAD > "$OUT/source-head.txt"
git -C "$SRC" status --porcelain=v1 --untracked-files=no \
    > "$OUT/source-status.txt"
[[ ! -s "$OUT/source-status.txt" ]] || {
    echo "ISOLATED_PR117_SOURCE_DIRTY_NOT_OVERWRITING"; exit 4;
}
grep -Fq 'ASSERT_MSG(!program.info.uses_ordered_gds' \
    "$SRC/src/shader_recompiler/backend/spirv/emit_spirv.cpp" || {
    echo "NORMAL_GUEST_GDS_EXECUTION_GATE_MISSING"; exit 5;
}

STAGE=EXACT_GCN_TERMINAL_CAPTURE_SOURCE_CONTRACT
if ! (cd "$SRC" && python3 tests/check_ordered_gds_ir.py) \
    > "$OUT/contracts.log" 2>&1; then
    tail -95 "$OUT/contracts.log"; exit 6;
fi
grep -Fq 'REAL_GCN_TERMINAL_BYPASS_WRITES_AUTHENTIC_WAVE_COVERAGE' \
    "$OUT/contracts.log" || exit 7

FMT=""
for d in "$SRC/externals/fmt/include" \
         "$BUILD/_deps/fmt-src/include" /usr/include; do
    if [[ -f "$d/fmt/base.h" ]]; then FMT="$d"; break; fi
done
[[ -n "$FMT" ]] || { echo "FMT_HEADER_NOT_FOUND"; exit 8; }

STAGE=CANONICAL_TERMINAL_AUTH_GCC_AND_CLANG
for compiler in g++ clang++-17; do
    case "$compiler" in g++) label=gcc;; *) label=clang;; esac
    if ! (
        cd "$SRC"
        "$compiler" -std=c++23 -O2 -Wall -Wextra -Werror -pedantic \
            -pthread -DFMT_HEADER_ONLY -Isrc "-I$FMT" \
            tests/gcn_ordered_gds_epoch_auth.cpp \
            -o "$OUT/terminal-$label"
        "$OUT/terminal-$label"
    ) >"$OUT/terminal-$label.log" 2>&1; then
        tail -95 "$OUT/terminal-$label.log"; exit 9
    fi
    grep -Fq 'real_terminal_bypass_epoch_authority=PASS' \
        "$OUT/terminal-$label.log" || exit 10
    grep -Fq 'no_fabricated_M0_ranks=PASS' \
        "$OUT/terminal-$label.log" || exit 11
done

STAGE=TERMINAL_AUTH_CLANG_ASAN_UBSAN
if ! (
    cd "$SRC"
    clang++-17 -std=c++23 -O1 -g -Wall -Wextra -Werror -pedantic \
        -pthread -fsanitize=address,undefined -fno-omit-frame-pointer \
        -DFMT_HEADER_ONLY -Isrc "-I$FMT" \
        tests/gcn_ordered_gds_epoch_auth.cpp \
        -o "$OUT/terminal-sanitized"
    ASAN_OPTIONS=detect_leaks=0 UBSAN_OPTIONS=halt_on_error=1 \
        "$OUT/terminal-sanitized"
) >"$OUT/terminal-sanitized.log" 2>&1; then
    tail -95 "$OUT/terminal-sanitized.log"; exit 12
fi
grep -Fq 'real_terminal_bypass_epoch_authority=PASS' \
    "$OUT/terminal-sanitized.log" || exit 13

STAGE=REAL_GCN_TERMINAL_SPIRV_AND_RASTERIZER_NATIVE_LINK
exec 9>"$ROOT/.indirect-native-build.lock"
flock -x 9
if ! nice -n 10 cmake --build "$BUILD" --target shadps4 --parallel 2 \
    >"$OUT/native-build.log" 2>&1; then
    tail -155 "$OUT/native-build.log"; exit 14
fi
[[ -x "$BUILD/shadps4" ]] || {
    echo "ISOLATED_PR117_NATIVE_ELF_MISSING"; exit 15;
}
sha256sum "$BUILD/shadps4" >"$OUT/elf.sha256"
grep -E 'warning:|error:|fatal error:' "$OUT/native-build.log" \
    > "$OUT/compile-diagnostics.txt" || true
STATUS=PASS_GCN_REAL_TERMINAL_BYPASS_SOURCE_NATIVE
STAGE=GUEST_GPU_FIRST_PASS_AND_CONTINUATION_STILL_GATED
echo "REAL_GCN_TERMINAL_BYPASS_SOURCE_NATIVE=PASS GAME_TEST_READY=NO"
