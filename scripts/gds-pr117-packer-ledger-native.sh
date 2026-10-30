#!/usr/bin/env bash
# PR117 verified dedicated CIK packer lifetime: focused source/native acceptance.
# No guest execution, game, GPU resource allocation, Ollama, X11 or Sunshine changes.
set -Eeuo pipefail
PROJECT="$HOME/projects/shadps4"
BASE="$PROJECT/tests/gds-pr117-native-20261029T164137Z-3996104"
SRC="$BASE/source"
BUILD="$BASE/build"
TAG="gds-pr117-packer-ledger-$(date -u +%Y%m%dT%H%M%SZ)-$$"
OUT="$PROJECT/logs/$TAG"
STAGE=PRECHECK
STATUS=FAILED_OR_INCOMPLETE
mkdir -p "$OUT"

finish() {
    local rc=$?
    trap - EXIT
    set +e
    printf 'STATUS=%s\nSTAGE=%s\nEXIT_CODE=%s\n' "$STATUS" "$STAGE" "$rc" >"$OUT/result.txt"
    printf 'GAME_TEST_READY=NO\nGPU_GUEST_CAPTURE=NOT_DISPATCHED\n' >>"$OUT/result.txt"
    printf 'GUEST_SHADER_REENTRY=NOT_IMPLEMENTED\n' >>"$OUT/result.txt"
    tar -C "$PROJECT/logs" -czf "$OUT.tar.gz" "$TAG" \
        2>"$OUT/tar-errors.txt"
    echo "PR117_STATUS=$STATUS STAGE=$STAGE EXIT=$rc"
    echo "EVIDENCE=$OUT.tar.gz"
}
trap finish EXIT

for cmd in git g++ clang++-17 python3 cmake flock sha256sum tar; do
    command -v "$cmd" >/dev/null || {
        echo "MISSING_TOOL=$cmd"
        exit 2
    }
done
[[ -d "$SRC/.git" && -f "$BUILD/build.ninja" ]] || {
    echo "ISOLATED_PR117_BUILD_MISSING"; exit 3;
}
git -C "$SRC" status --porcelain=v1 --ignore-submodules=untracked >"$OUT/status.txt"
[[ ! -s "$OUT/status.txt" ]] || {
    echo "ISOLATED_SOURCE_DIRTY_NO_RESET"; exit 4;
}
git -C "$SRC" rev-parse HEAD >"$OUT/commit.txt"
grep -Fq 'ASSERT_MSG(!program.info.uses_ordered_gds' \
    "$SRC/src/shader_recompiler/backend/spirv/emit_spirv.cpp" || {
    echo "GAMEPLAY_SHADER_GUARD_REMOVED"; exit 5;
}
if grep -Eq 'DynamoRIO|ENABLE_CPU_ID_TRANSLATION' \
    "$SRC/CMakeLists.txt" "$SRC/src/main.cpp"; then
    echo "UNAPPROVED_CPU_RUNTIME_TRANSLATION"; exit 6
fi

STAGE=NEW_PACKER_LIFETIME_SOURCE_CONTRACT
(cd "$SRC" && python3 tests/check_ordered_gds_ir.py) \
    >"$OUT/contracts.log" 2>&1 || {
        tail -90 "$OUT/contracts.log"; exit 7;
    }
grep -Fq 'REAL_CIK_PACKER_STATE_PERSISTS_ONLY_AFTER_AUTHENTIC_EPOCH_RESOLVE' \
    "$OUT/contracts.log" || exit 8

FMT_INCLUDE=""
for d in "$SRC/externals/fmt/include" "$BUILD/_deps/fmt-src/include" /usr/include; do
    if [[ -f "$d/fmt/base.h" ]]; then FMT_INCLUDE="$d"; break; fi
done
[[ -n "$FMT_INCLUDE" ]] || { echo "VENDORED_FMT_HEADER_MISSING"; exit 9; }

STAGE=LEDGER_GCC_AND_CLANG_NATIVE_HOST_CHECKS
for compiler in g++ clang++-17; do
    case "$compiler" in g++) label=gcc;; *) label=clang;; esac
    (
       cd "$SRC"
       "$compiler" -std=c++23 -Wall -Wextra -Werror -pedantic -O2 -pthread \
          -DFMT_HEADER_ONLY -Isrc "-I$FMT_INCLUDE" \
          tests/gcn_ordered_gds_legacy_packer.cpp -o "$OUT/ledger-$label"
       "$OUT/ledger-$label"
    ) >"$OUT/ledger-$label.log" 2>&1 || {
       tail -85 "$OUT/ledger-$label.log"; exit 10;
    }
    grep -Fq 'GDS_CIK_VERIFIED_PACKER_LEDGER=PASS' \
        "$OUT/ledger-$label.log" || exit 11
done

STAGE=LEDGER_CLANG_ASAN_UBSAN
(
    cd "$SRC"
    clang++-17 -std=c++23 -Wall -Wextra -Werror -pedantic -O1 -g -pthread \
        -fsanitize=address,undefined -fno-omit-frame-pointer \
        -DFMT_HEADER_ONLY -Isrc "-I$FMT_INCLUDE" \
        tests/gcn_ordered_gds_legacy_packer.cpp -o "$OUT/ledger-sanitized"
    ASAN_OPTIONS=detect_leaks=0 UBSAN_OPTIONS=halt_on_error=1 \
        "$OUT/ledger-sanitized"
) >"$OUT/ledger-sanitized.log" 2>&1 || {
    tail -85 "$OUT/ledger-sanitized.log"; exit 12;
}
grep -Fq 'GDS_CIK_VERIFIED_PACKER_LEDGER=PASS' \
    "$OUT/ledger-sanitized.log" || exit 13

STAGE=INCREMENTAL_NATIVE_RASTERIZER_LINK
exec 9>"$BASE/.indirect-native-build.lock"
flock -x 9
if ! nice -n 10 cmake --build "$BUILD" --target shadps4 --parallel 2 \
        >"$OUT/build.log" 2>&1; then
    tail -110 "$OUT/build.log"; exit 14;
fi
[[ -x "$BUILD/shadps4" ]] || { echo "NATIVE_SHADPS4_ELF_MISSING"; exit 15; }
sha256sum "$BUILD/shadps4" >"$OUT/elf.sha256"

STAGE=DONE_HARDWARE_PACKER_SOURCE_AND_GPU_CONTINUATION_UNIMPLEMENTED
STATUS=PASS_CIK_PACKER_LEDGER_NATIVE
echo "PACKER_LEDGER_NATIVE=PASS GAME_TEST_READY=NO"
