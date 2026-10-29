#!/usr/bin/env bash
set -Eeuo pipefail

PROJECT="$HOME/projects/shadps4"
BASE="$PROJECT/tests/gds-pr117-native-20261029T164137Z-3996104"
SRC="$BASE/source"
BUILD="$BASE/build"
BRANCH='feature/ordered-gds-wave-executor-20261029'
MINIMUM_COMMIT='11e9b9c44c994e5fa2b3d819850695f66e32ba54'
TAG="gds-pr117-preflight-native-$(date -u +%Y%m%dT%H%M%SZ)-$$"
LOG="$PROJECT/logs/$TAG"
STAGE='PREFLIGHT'
STATUS='FAILED_OR_INCOMPLETE'

mkdir -p "$LOG"

finish() {
    local rc=$?
    trap - EXIT
    set +e
    {
        printf 'stage=%s\nstatus=%s\nexit_code=%s\n' "$STAGE" "$STATUS" "$rc"
        printf 'src=%s\nbuild=%s\n' "$SRC" "$BUILD"
    } > "$LOG/result.txt"
    git -C "$SRC" rev-parse HEAD > "$LOG/final-source-head.txt" 2>/dev/null || true
    if [[ -s "$BUILD/shadps4" ]]; then
        sha256sum "$BUILD/shadps4" > "$LOG/candidate-elf.sha256"
    fi
    tar -C "$PROJECT/logs" -czf "$PROJECT/logs/$TAG.tar.gz" "$TAG" \
        2> "$LOG/tar.stderr"
    printf '\nPR117_PREFLIGHT_NATIVE=%s EXIT_CODE=%s STAGE=%s\n' "$STATUS" "$rc" "$STAGE"
    printf 'UPLOAD_THIS=%s\n' "$PROJECT/logs/$TAG.tar.gz"
    printf 'ISOLATED_SOURCE=%s\nISOLATED_BUILD=%s\n' "$SRC" "$BUILD"
    exit "$rc"
}
trap finish EXIT

for tool in git cmake ninja python3 g++ clang++-17 tar sha256sum; do
    command -v "$tool" >/dev/null 2>&1 ||
        { printf 'MISSING_TOOL=%s\n' "$tool"; exit 2; }
done

[[ -d "$SRC/.git" && -f "$BUILD/build.ninja" ]] ||
    { echo 'MISSING_PREVIOUS_ISOLATED_NATIVE_BUILD'; exit 3; }
url="$(git -C "$SRC" config --get remote.origin.url)"
case "$url" in
    https://github.com/Chreece/shadPS4|https://github.com/Chreece/shadPS4.git) ;;
    *) printf 'UNEXPECTED_SOURCE_REMOTE=%s\n' "$url"; exit 4 ;;
esac
if [[ -n "$(git -C "$SRC" status --porcelain=v1 --ignore-submodules=untracked)" ]]; then
    echo 'ISOLATED_SOURCE_HAS_LOCAL_EDITS_REFUSE_MERGE'
    git -C "$SRC" status --short
    exit 5
fi

STAGE='FETCH_CURRENT_PR117'
GIT_TERMINAL_PROMPT=0 git -C "$SRC" fetch --no-tags --depth=100 origin \
    "refs/heads/$BRANCH" > "$LOG/fetch.log" 2>&1 || {
    tail -50 "$LOG/fetch.log"
    exit 6
}
git -C "$SRC" merge-base --is-ancestor HEAD FETCH_HEAD ||
    { echo 'BRANCH_NOT_FAST_FORWARD_STOP'; exit 7; }
git -C "$SRC" merge --ff-only FETCH_HEAD > "$LOG/merge.log" 2>&1 || {
    tail -70 "$LOG/merge.log"
    exit 8
}
HEAD_SHA="$(git -C "$SRC" rev-parse HEAD)"
git -C "$SRC" merge-base --is-ancestor "$MINIMUM_COMMIT" HEAD ||
    { printf 'MISSING_REQUIRED_PREFLIGHT_COMMIT=%s\n' "$MINIMUM_COMMIT"; exit 9; }
printf '%s\n' "$HEAD_SHA" > "$LOG/source-head.txt"
git -C "$SRC" status --porcelain=v1 --ignore-submodules=untracked \
    > "$LOG/checkout-status.txt"

STAGE='TEST_GCC_AND_CLANG_WITH_SANITIZERS'
for compiler in g++ clang++-17; do
    name="${compiler//+/p}"
    if ! (cd "$SRC" && CXX="$compiler" RUN_SANITIZERS=1 \
          bash tests/run_ordered_gds_cpu.sh) > "$LOG/cpu-$name.log" 2>&1; then
        tail -90 "$LOG/cpu-$name.log"
        exit 10
    fi
    grep -Fq 'ORDERED_GDS_OFFLINE_REGRESSION=PASS' "$LOG/cpu-$name.log"
    grep -Fq 'ORDERED_GDS_LOGICAL_CAPTURE_SLOTS=PASS' "$LOG/cpu-$name.log"
    grep -Fq 'ORDERED_GDS_MIXED_STATIC_SITES=PASS' "$LOG/cpu-$name.log"
    grep -Fq 'ORDERED_GDS_GPU_PACKET_ROUNDTRIP=PASS' "$LOG/cpu-$name.log"
done

STAGE='VERIFY_BACKEND_REMAINS_GATED'
grep -Fq 'ASSERT_MSG(!program.info.uses_ordered_gds' \
    "$SRC/src/shader_recompiler/backend/spirv/emit_spirv.cpp" ||
    { echo 'ORDERED_GDS_BACKEND_GATE_MISSING'; exit 11; }
grep -Fq 'logical capture slots NOT' \
    "$SRC/src/video_core/renderer_vulkan/vk_pipeline_cache.cpp" ||
    { echo 'GDS_PREFLIGHT_LOGGING_MISSING'; exit 12; }
grep -Fq 'Ordered GDS GPU packet ABI' \
    "$SRC/src/video_core/renderer_vulkan/vk_pipeline_cache.cpp" ||
    { echo 'ORDERED_GDS_CAPTURE_ABI_NOT_LINKED_TO_COMPILER'; exit 12; }

STAGE='BUILD_INCREMENTAL_NATIVE'
if ! cmake --build "$BUILD" --parallel 6 > "$LOG/build.log" 2>&1; then
    tail -100 "$LOG/build.log"
    exit 13
fi
[[ -s "$BUILD/shadps4" ]] ||
    { echo 'ISOLATED_NATIVE_ELF_MISSING'; exit 14; }
printf 'BUILD_WARNING_LINES=%s\n' "$(grep -c 'warning:' "$LOG/build.log" || true)" \
    > "$LOG/build-warning-count.txt"
STAGE='NATIVE_PREFLIGHT_COMPILE_ONLY'
STATUS='PASS_GCC_CLANG_ASAN_UBSAN_AND_NATIVE_BUILD'
printf 'PR117_NATIVE_PREFLIGHT=PASS GPU_DISPATCH=NOT_RUN GOW_VISUAL=NOT_TESTED\n'
