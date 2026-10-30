#!/usr/bin/env bash
# PR117: isolated focused native acceptance. No emulator/game launch or GPU reset.
set -Eeuo pipefail
ROOT="$HOME/projects/shadps4"
BASE="$ROOT/tests/gds-pr117-native-20261029T164137Z-3996104"
SRC="$BASE/source"
BUILD="$BASE/build"
LOGROOT="$ROOT/logs"
TAG="gds-pr117-rasterizer-epoch-$(date -u +%Y%m%dT%H%M%SZ)-$$"
OUT="$LOGROOT/$TAG"
STATUS=FAILED_OR_INCOMPLETE
STAGE=PRECHECK
mkdir -p "$OUT"
finish() {
    rc=$?
    trap - EXIT
    set +e
    printf 'STATUS=%s\nSTAGE=%s\nEXIT_CODE=%s\nGPU_GUEST_CAPTURE_DISPATCH=NOT_ENABLED\nGUEST_SHADER_REENTRY=NOT_IMPLEMENTED\nGAME_TEST_READY=NO\n' "$STATUS" "$STAGE" "$rc" > "$OUT/result.txt"
    tar -C "$LOGROOT" -czf "$LOGROOT/$TAG.tar.gz" "$TAG" 2>"$OUT/tar-error.txt"
    printf 'PR117_STATUS=%s STAGE=%s EXIT=%s\n' "$STATUS" "$STAGE" "$rc"
    printf 'EVIDENCE=%s\n' "$LOGROOT/$TAG.tar.gz"
}
trap finish EXIT

for cmd in git g++ clang++-17 python3 cmake sha256sum tar; do
    command -v "$cmd" >/dev/null || { echo "REQUIRED_TOOL_MISSING=$cmd"; exit 2; }
done
[[ -d "$SRC/.git" && -f "$BUILD/build.ninja" ]] || {
    echo "ISOLATED_NATIVE_SOURCE_OR_BUILD_MISSING"; exit 3;
}
git -C "$SRC" rev-parse HEAD > "$OUT/source-head.txt"
git -C "$SRC" status --porcelain=v1 --ignore-submodules=untracked > "$OUT/git-status.txt"
[[ ! -s "$OUT/git-status.txt" ]] || {
    echo "ISOLATED_SOURCE_DIRTY_NOT_OVERWRITING"; exit 4;
}
grep -Fq 'ASSERT_MSG(!program.info.uses_ordered_gds' \
    "$SRC/src/shader_recompiler/backend/spirv/emit_spirv.cpp" || {
    echo "ORDERED_GAME_EXECUTION_SAFETY_GATE_REMOVED"; exit 5;
}
STAGE=ISSUER_AND_LDS_STAMP_SOURCE_CONTRACT
(cd "$SRC" && python3 tests/check_ordered_gds_ir.py) >"$OUT/source-contract.log" 2>&1 || {
    tail -90 "$OUT/source-contract.log"; exit 6;
}
grep -Fq 'HOST_GPU_DISPATCH_TOKEN_ISSUER_AND_FAILED_EPOCH_INVALIDATION' "$OUT/source-contract.log" || exit 7
grep -Fq 'REAL_FIRST_PASS_VALIDITY_STAMPS_REQUIRE_VERIFIED_ZERO_LDS_WAVE64_AUTH' "$OUT/source-contract.log" || exit 8

FMT=""
for candidate in "$SRC/externals/fmt/include" "$BUILD/_deps/fmt-src/include" /usr/include; do
    if [[ -f "$candidate/fmt/base.h" ]]; then FMT="$candidate"; break; fi
done
[[ -n "$FMT" ]] || { echo "FMT_HEADER_NOT_FOUND"; exit 9; }

STAGE=NEW_HOST_EPOCH_AND_AUTH_TESTS
for compiler in g++ clang++-17; do
    case "$compiler" in g++) label=gcc;; *) label=clang;; esac
    ( cd "$SRC"
      "$compiler" -std=c++23 -O2 -Wall -Wextra -Werror -pedantic -pthread \
         -DFMT_HEADER_ONLY -Isrc "-I$FMT" tests/gcn_ordered_gds_epoch_auth.cpp -o "$OUT/auth-$label"
      "$OUT/auth-$label"
    ) >"$OUT/auth-$label.log" 2>&1 || { tail -75 "$OUT/auth-$label.log"; exit 10; }
    grep -Fq 'monotonic_host_dispatch_tickets=PASS' "$OUT/auth-$label.log" || exit 11
done

STAGE=INCREMENTAL_NATIVE_GDS_VULKAN_OWNER_LINK
if ! nice -n 10 cmake --build "$BUILD" --target shadps4 --parallel 2 >"$OUT/native-build.log" 2>&1; then
    tail -95 "$OUT/native-build.log"; exit 12
fi
[[ -s "$BUILD/shadps4" ]] || exit 13
sha256sum "$BUILD/shadps4" >"$OUT/native-elf.sha256"
STATUS=PASS_HOST_EPOCH_ISSUER_AND_NATIVE_OWNER
STAGE=DONE_REAL_GPU_CAPTURE_REENTRY_STILL_GATED
echo "HOST_ISSUER_AND_VULKAN_AUTH_SOURCE=PASS GAME_TEST_READY=NO"
