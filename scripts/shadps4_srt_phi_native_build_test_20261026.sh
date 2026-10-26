#!/usr/bin/env bash
# Native SRT/Phi source and test runner. No stable binary is modified.
set -Eeuo pipefail
umask 077
export GIT_TERMINAL_PROMPT=0
ROOT="$HOME/projects/shadps4"
LOGS="$ROOT/logs"
TESTS="$ROOT/GodOfWarRagnarok/tests"
BASE="$TESTS/gow-upstream-got-20261025-145442-451241"
OLD="$BASE/build/shadps4"
OLD_SHA=7604bae00c78b72e163138327f61a2f92bf549ac66446487d1ab7370a2f9b4d1
PROTECTED="$ROOT/GodOfWarRagnarok/build/shadps4"
PROTECTED_SHA=8de2106a3e0d410904d9c8d6a2903ba6f1f65986af3c848b0bf274a4f4b45c8e
SOURCE=5bde94974bdf59973568ece89b308982b9aafebe
OBSERVER_SHA=223a8e6ef7916e06bfd4f0ccefc90c92d5b097a0
mkdir -p "$LOGS" "$TESTS"
if (( $# == 0 )); then
    for tool in git cmake ninja curl flock nohup setsid python3 sha256sum tar; do
        command -v "$tool" >/dev/null || { echo "MISSING_TOOL=$tool"; exit 2; }
    done
    TAG="shadps4-srt-phi-build-$(date +%Y%m%d-%H%M%S)-$$"
    LOG="$LOGS/$TAG.log"
    (set -C; : > "$LOG") || { echo "LOG_ALREADY_EXISTS=$LOG"; exit 2; }
    nohup setsid -w bash "$0" --worker "$TAG" </dev/null >"$LOG" 2>&1 &
    PID=$!
    disown "$PID" 2>/dev/null || true
    echo "ISOLATED_NATIVE_PHI_BUILD=$SOURCE"
    echo "LIVE_LOG=$LOG"
    echo "MONITOR=tail -f $LOG"
    echo "PID=$PID"
    echo "SSH_UNCHANGED=YES"
    exit 0
fi
[[ $# == 2 && "$1" == --worker ]] || { echo "USAGE=bash $0"; exit 2; }
TAG="$2"
[[ "$TAG" =~ ^shadps4-srt-phi-build-[0-9]{8}-[0-9]{6}-[0-9]+$ ]] || exit 2
exec 9>"$LOGS/gow-upstream-got-build.lock"
flock -n 9 || { echo "ANOTHER_EMULATOR_BUILD_ACTIVE"; exit 2; }
WORK="$TESTS/$TAG"
SRC="$WORK/source"
BUILD="$WORK/build"
BIN="$BUILD/shadps4"
BUILD_LOG="$LOGS/$TAG-build.log"
TEST_LOG="$LOGS/$TAG-tests.log"
SUMMARY="$LOGS/$TAG-summary.txt"
ARCHIVE="$LOGS/$TAG-build.tar.gz"
RUN_TAG="shadps4-srt-phi-run-$(date +%Y%m%d-%H%M%S)-$$"
OLD_BEFORE="$(sha256sum "$OLD" | cut -d' ' -f1)"
PROTECTED_BEFORE="$(sha256sum "$PROTECTED" | cut -d' ' -f1)"
mkdir -p "$WORK"
: > "$BUILD_LOG"
: > "$TEST_LOG"
printf 'SOURCE=%s\nOLD_SHA=%s\nPROTECTED_SHA=%s\n' "$SOURCE" "$OLD_BEFORE" "$PROTECTED_BEFORE" >"$SUMMARY"
finish() {
    local rc=$?
    trap - EXIT
    local old_after=missing protected_after=missing
    [[ ! -f "$OLD" ]] || old_after="$(sha256sum "$OLD" | cut -d' ' -f1)"
    [[ ! -f "$PROTECTED" ]] || protected_after="$(sha256sum "$PROTECTED" | cut -d' ' -f1)"
    echo "EXIT=$rc" >>"$SUMMARY"
    echo "STABLE_UNCHANGED=$([[ "$OLD_BEFORE" == "$old_after" ]] && echo YES || echo NO)" >>"$SUMMARY"
    echo "PROTECTED_UNCHANGED=$([[ "$PROTECTED_BEFORE" == "$protected_after" ]] && echo YES || echo NO)" >>"$SUMMARY"
    [[ ! -f "$BIN" ]] || sha256sum "$BIN" >>"$SUMMARY"
    echo "RUNTIME_LOG=$LOGS/$RUN_TAG.log" >>"$SUMMARY"
    echo "RUNTIME_ARCHIVE=$LOGS/$RUN_TAG.tar.gz" >>"$SUMMARY"
    tar -czf "$ARCHIVE" -C "$LOGS" "$(basename "$TAG.log")" "$(basename "$SUMMARY")" "$(basename "$BUILD_LOG")" "$(basename "$TEST_LOG")" || true
    echo "BUILD_UPLOAD_THIS=$ARCHIVE"
    echo "RUNTIME_UPLOAD_THIS_IF_CREATED=$LOGS/$RUN_TAG.tar.gz"
    echo "SSH_AND_PRODUCTION_SAVES=UNMODIFIED"
    exit "$rc"
}
trap finish EXIT
[[ "$OLD_BEFORE" == "$OLD_SHA" && "$PROTECTED_BEFORE" == "$PROTECTED_SHA" ]] ||
    { echo "PROTECTED_BINARY_CHECK_FAILED_NO_BUILD"; exit 3; }
[[ ! -e "$SRC" && ! -e "$BUILD" ]] || { echo "REFUSE_EXISTING_SOURCE_DIR"; exit 3; }
git -C "$BASE/source" rev-parse --is-inside-work-tree >/dev/null ||
    { echo "BASE_SOURCE_MISSING"; exit 3; }
echo "STAGE=FETCH_PINNED_SOURCE"
git -C "$BASE/source" -c http.version=HTTP/1.1 fetch --no-tags --no-recurse-submodules \
    https://github.com/Chreece/shadPS4.git "$SOURCE"
[[ "$(git -C "$BASE/source" rev-parse FETCH_HEAD)" == "$SOURCE" ]] ||
    { echo "SOURCE_COMMIT_MISMATCH"; exit 4; }
git -C "$BASE/source" worktree add --detach "$SRC" "$SOURCE"
git -C "$SRC" -c http.version=HTTP/1.1 submodule update --init --recursive --jobs 2
[[ "$(git -C "$SRC" rev-parse HEAD)" == "$SOURCE" ]] ||
    { echo "WORKTREE_HEAD_MISMATCH"; exit 4; }
echo "STAGE=VERIFY_INVARIANT_PHI_SOURCE"
python3 -B - "$SRC" <<'PYVERIFY'
from pathlib import Path
import sys
p=Path(sys.argv[1])
f=(p/"src/shader_recompiler/ir/passes/flatten_extended_userdata_pass.cpp").read_text()
r=(p/"src/shader_recompiler/recompiler.cpp").read_text()
t=(p/"tests/gcn/test_srt_phi.cpp").read_text()
assert "ResolveInvariantSrtPhi(off_dw, pass_info.gvn_table)" in f
assert f.count("case IR::Opcode::Phi:") == 2
assert r.index("PhiSimplificationPass(program)") < r.index("FlattenExtendedUserdataPass(program)")
assert r.count("PhiSimplificationPass(program)") == 2
assert t.count("TEST(SrtInvariantPhi,") == 6
print("STATIC_GENERIC_SOURCE_TESTS=PASS",flush=True)
PYVERIFY
echo "STAGE=CMAKE_WITH_GCN_TESTS"
cmake -S "$SRC" -B "$BUILD" -G Ninja -DCMAKE_BUILD_TYPE=Release -DENABLE_TESTS=ON >"$BUILD_LOG" 2>&1 ||
    { tail -80 "$BUILD_LOG"; exit 5; }
echo "STAGE=BUILD_GCN_REGRESSION_TESTS"
nice -n 10 cmake --build "$BUILD" --target shadps4_gcn_test --parallel 2 >>"$BUILD_LOG" 2>&1 ||
    { tail -100 "$BUILD_LOG"; exit 6; }
GCN_TEST="$(find "$BUILD" -type f -name shadps4_gcn_test -perm /111 -print -quit)"
[[ -n "$GCN_TEST" ]] || { echo "GCN_TEST_MISSING"; exit 6; }
"$GCN_TEST" --gtest_filter='SrtInvariantPhi.*' >"$TEST_LOG" 2>&1 ||
    { cat "$TEST_LOG"; exit 7; }
cat "$TEST_LOG"
grep -Eq '\[  PASSED  \] 6 tests' "$TEST_LOG" ||
    { echo "NOT_ALL_SIX_TESTS_PASSED"; exit 7; }
echo "STAGE=BUILD_NATIVE_GENERIC_EMULATOR"
nice -n 10 cmake --build "$BUILD" --target shadps4 --parallel 2 >>"$BUILD_LOG" 2>&1 ||
    { tail -120 "$BUILD_LOG"; exit 8; }
[[ -x "$BIN" && ! -L "$BIN" ]] || { echo "NEW_EMULATOR_BINARY_MISSING"; exit 8; }
NEW_SHA="$(sha256sum "$BIN" | cut -d' ' -f1)"
echo "NEW_CANDIDATE_SHA256=$NEW_SHA" | tee -a "$SUMMARY"
"$BIN" --help >"$WORK/cli-help.txt" 2>&1 || { echo "NATIVE_CLI_ERROR"; exit 8; }
! grep -Fq -- --cpu-id-mode "$WORK/cli-help.txt" ||
    { echo "UNEXPECTED_DYNAMORIO_OR_CPU_TRANSLATOR"; exit 8; }
echo "STAGE=FETCH_AND_ADAPT_PINNED_UNIVERSAL_OBSERVER"
STOCK="$WORK/stock-runner.sh"
PATCHED="$WORK/srt-phi-runner.sh"
curl -4fsSL --retry 3 \
    https://raw.githubusercontent.com/Chreece/shadPS4/448c20c756accf1b9148acde0622bf7db4a48b63/scripts/shadps4_gpu_headroom_ab_20261026.sh \
    -o "$STOCK"
[[ "$(git hash-object "$STOCK")" == "$OBSERVER_SHA" ]] ||
    { echo "PINNED_OBSERVER_SHA_MISMATCH"; exit 9; }
python3 -B - "$STOCK" "$PATCHED" "$SRC" "$BIN" "$NEW_SHA" "$SOURCE" <<'PYPATCH'
from pathlib import Path
import sys
old,new,src,bin,sha,commit=sys.argv[1:]
s=Path(old).read_text()
replacements={
 'BIN="$ROOT/GodOfWarRagnarok/tests/gow-upstream-got-20261025-145442-451241/build/shadps4"':f'BIN="{bin}"',
 'EXPECTED=7604bae00c78b72e163138327f61a2f92bf549ac66446487d1ab7370a2f9b4d1':f'EXPECTED={sha}',
 'SRC="$ROOT/GodOfWarRagnarok/tests/gow-upstream-got-20261025-145442-451241/source"':f'SRC="{src}"',
 'SOURCE_REV="803bb428731be794bdc44e38b56313d030a7403a"':f'SOURCE_REV="{commit}"',
 '--fix-id gpu-device-lost-after-first-frame':'--fix-id generic-invariant-phi-before-srt',
 '--change "Same exact native emulator binary, host Ollama VRAM headroom A/B only; 11/21/26s frames and read-only kernel faults"':'--change "New early Phi simplification and invariant-only SRT offsets; no graphics skips or runtime compatibility modes"',
 '--next "Compare whether GPU context lost persists with additional free VRAM; inspect first-frame screenshots, amdgpu kernel events and real resource lifetime before general renderer changes"':'--next "Compare SRT Phi warnings and gameplay screenshots against previous stable 120-second baseline"'
}
for anchor,replacement in replacements.items():
    if s.count(anchor)!=1: raise SystemExit("OBSERVER_PATCH_ANCHOR_FAILED:"+anchor)
    s=s.replace(anchor,replacement)
s=s.replace('shadps4-gpu-headroom-ab','shadps4-srt-phi-run')
Path(new).write_text(s)
PYPATCH
bash -n "$PATCHED"
[[ "$(sha256sum "$OLD" | cut -d' ' -f1)" == "$OLD_SHA" ]] ||
    { echo "STABLE_NATIVE_BINARY_CHANGED"; exit 10; }
[[ "$(sha256sum "$PROTECTED" | cut -d' ' -f1)" == "$PROTECTED_SHA" ]] ||
    { echo "PROTECTED_BINARY_CHANGED"; exit 10; }
echo "STAGE=STANDARD_GOW_118S_PLUS_2S_NATIVE_RUNTIME"
echo "RUNTIME_LOG=$LOGS/$RUN_TAG.log"
set +e
bash "$PATCHED" --worker "$RUN_TAG" CUSA34384 >"$LOGS/$RUN_TAG.log" 2>&1
RUN_RC=$?
set -e
tail -n 85 "$LOGS/$RUN_TAG.log"
echo "RUNTIME_EXIT=$RUN_RC" >>"$SUMMARY"
exit "$RUN_RC"
