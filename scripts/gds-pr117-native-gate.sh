#!/usr/bin/env bash
set -Eeuo pipefail

PROJECT="$HOME/projects/shadps4"
BRANCH='feature/ordered-gds-wave-executor-20261029'
STAMP="gds-pr117-native-$(date -u +%Y%m%dT%H%M%SZ)-$$"
RUN="$PROJECT/tests/$STAMP"
LOG="$PROJECT/logs/$STAMP"
SRC="$RUN/source"
BUILD="$RUN/build"
mkdir -p "$LOG" "$RUN"
STAGE='START'
STATUS='FAIL_OR_INCOMPLETE'

finalize() {
    local rc=$?
    trap - EXIT
    set +e
    printf 'stage=%s\nexit_code=%s\nstatus=%s\n' "$STAGE" "$rc" "$STATUS" > "$LOG/result.txt"
    printf 'saved_source=%s\nsaved_build=%s\n' "$SRC" "$BUILD" >> "$LOG/result.txt"
    git -C "$SRC" rev-parse HEAD >> "$LOG/source-head.txt" 2>/dev/null || true
    if [[ -s "$BUILD/shadps4" ]]; then
        sha256sum "$BUILD/shadps4" > "$LOG/candidate-elf.sha256"
    fi
    tar -C "$PROJECT/logs" -czf "$PROJECT/logs/$STAMP.tar.gz" "$STAMP" 2> "$LOG/tar.stderr"
    printf '\nPR117_NATIVE_RESULT=%s EXIT=%s STAGE=%s\n' "$STATUS" "$rc" "$STAGE"
    printf 'UPLOAD_THIS=%s\n' "$PROJECT/logs/$STAMP.tar.gz"
    printf 'ISOLATED_BUILD=%s\n' "$BUILD"
    exit "$rc"
}
trap finalize EXIT

for cmd in git cmake ninja python3 g++ clang++-17 tar; do
    command -v "$cmd" >/dev/null || { echo "MISSING_REQUIRED_TOOL=$cmd"; exit 2; }
done
if [[ $(df -Pk "$PROJECT" | awk 'NR==2{print $4}') -lt 7000000 ]]; then
    echo 'LOW_DISK_FREE_KIB_FAIL_BEFORE_CLONE' | tee "$LOG/progress.log"
    exit 3
fi

STAGE='FETCH_ISOLATED_PR_HEAD'
# Do not modify any existing game worktrees, binaries, branches, X11 or services.
GIT_TERMINAL_PROMPT=0 git clone --depth=1 --single-branch \
    --branch "$BRANCH" --recurse-submodules --shallow-submodules \
    --jobs 6 'https://github.com/Chreece/shadPS4.git' "$SRC" \
    > "$LOG/clone.log" 2>&1
GIT_REV="$(git -C "$SRC" rev-parse HEAD)"
[[ -n "$GIT_REV" ]] || { echo 'EMPTY_GIT_REV'; exit 4; }
printf '%s\n' "$GIT_REV" | tee "$LOG/source-head.txt"
git -C "$SRC" status --porcelain=v1 > "$LOG/source-status.txt"
git -C "$SRC" submodule status --recursive > "$LOG/submodules.txt"

STAGE='CPU_REGRESSION_GCC_CLANG_SANITIZED'
for compiler in g++ clang++-17; do
    label="${compiler//+/p}"
    (cd "$SRC" && CXX="$compiler" RUN_SANITIZERS=1 bash tests/run_ordered_gds_cpu.sh) \
        > "$LOG/cpu-$label.log" 2>&1 || {
        cat "$LOG/cpu-$label.log" | tail -80
        exit 10
    }
    grep -Fq 'ORDERED_GDS_OFFLINE_REGRESSION=PASS' "$LOG/cpu-$label.log"
done

STAGE='VERIFY_GPU_GATE'
grep -Fq 'ASSERT_MSG(!program.info.uses_ordered_gds' \
    "$SRC/src/shader_recompiler/backend/spirv/emit_spirv.cpp" || {
    echo 'UNSAFE_ORDERED_GDS_BACKEND_GATE_MISSING'
    exit 11
}

STAGE='CONFIGURE_NATIVE_GCC'
cmake -S "$SRC" -B "$BUILD" -G Ninja \
    -DCMAKE_BUILD_TYPE=Release -DENABLE_TESTS=OFF \
    -DCMAKE_C_COMPILER=gcc -DCMAKE_CXX_COMPILER=g++ \
    -DCMAKE_INTERPROCEDURAL_OPTIMIZATION_RELEASE=OFF \
    > "$LOG/cmake.log" 2>&1 || {
    tail -100 "$LOG/cmake.log"
    exit 12
}

STAGE='BUILD_NATIVE_GCC'
cmake --build "$BUILD" --parallel 6 > "$LOG/build.log" 2>&1 || {
    tail -120 "$LOG/build.log"
    exit 13
}
[[ -s "$BUILD/shadps4" ]] || {
    echo 'BUILT_EXECUTABLE_NOT_FOUND'
    ls -la "$BUILD" > "$LOG/build-dir.txt"
    exit 14
}
STAGE='NATIVE_COMPILE_VERIFIED_GPU_NOT_RUN'
STATUS='PASS_CPU_AND_NATIVE_BUILD'
echo 'PR117_NATIVE_BUILD=PASS CPU_GCC_CLANG_ASAN_UBSAN=PASS GPU_EXECUTION=NOT_TESTED'
