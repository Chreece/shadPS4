#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
# SPDX-License-Identifier: GPL-2.0-or-later
# Build/test invariant SRT Phi in an isolated worktree, then run the universal
# native GoW observer; never overwrite the 120-second baseline or live profile.
set -Eeuo pipefail
umask 077
export GIT_TERMINAL_PROMPT=0

ROOT="$HOME/projects/shadps4"
TESTS="$ROOT/GodOfWarRagnarok/tests"
LOGS="$ROOT/logs"
OLD="$TESTS/gow-upstream-got-20261025-145442-451241"
BASE_SOURCE="$OLD/source"
BASE_ELF="$OLD/build/shadps4"
BASE_SHA="7604bae00c78b72e163138327f61a2f92bf549ac66446487d1ab7370a2f9b4d1"
PROTECTED="$ROOT/GodOfWarRagnarok/build/shadps4"
PROTECTED_SHA="8de2106a3e0d410904d9c8d6a2903ba6f1f65986af3c848b0bf274a4f4b45c8e"
SOURCE_REV="5bde94974bdf59973568ece89b308982b9aafebe"
RUNNER_REV="448c20c756accf1b9148acde0622bf7db4a48b63"
RUNNER_BLOB="223a8e6ef7916e06bfd4f0ccefc90c92d5b097a0"
mkdir -p "$LOGS" "$TESTS"

if [[ "$1" != "--worker" ]]; then
    [[ $# -le 1 ]] || { echo "USAGE: bash $0 [game-id-or-path]" >&2; exit 2; }
    GAME="CUSA34384"
    if (( $# == 1 )); then GAME="$1"; fi
    [[ -n "$GAME" && "$GAME" != -* && "$GAME" != *$'\n'* ]] || exit 2
    command -v nohup >/dev/null && command -v setsid >/dev/null || exit 2
    TAG="shadps4-gow-srt-phi-$(date +%Y%m%d-%H%M%S)-$$"
    LOG="$LOGS/$TAG.log"
    (set -C; : > "$LOG") || { echo "LOG_ALREADY_EXISTS=$LOG" >&2; exit 2; }
    nohup setsid -w bash "$0" --worker "$TAG" "$GAME" </dev/null >"$LOG" 2>&1 &
    PID=$!
    disown "$PID" 2>/dev/null || true
    echo "TEST=GENERIC_SRT_INVARIANT_PHI"
    echo "SOURCE=$SOURCE_REV"
    echo "WORKER_PID=$PID"
    echo "MONITOR=tail -f '$LOG'"
    echo "BUILD_ARCHIVE=$LOGS/$TAG-build.tar.gz"
    echo "RUNTIME_ARCHIVE=$LOGS/$TAG.tar.gz"
    echo "GAME=$GAME"
    echo "SSH_AND_PROTECTED_BINARIES=UNCHANGED"
    exit 0
fi

[[ $# -eq 3 ]] || exit 2
TAG="$2"
GAME="$3"
[[ "$TAG" =~ ^shadps4-gow-srt-phi-[0-9]{8}-[0-9]{6}-[0-9]+$ ]] || exit 2
[[ -n "$GAME" && "$GAME" != -* && "$GAME" != *$'\n'* ]] || exit 2
WORK="$TESTS/$TAG"
SRC="$WORK/source"
BUILD="$WORK/build"
REPORT="$WORK/verification"
BINARY="$BUILD/shadps4"
ARCHIVE="$LOGS/$TAG-build.tar.gz"
mkdir -p "$REPORT"

finish() {
    local rc=$?
    trap - EXIT
    set +e
    echo "WORKER_EXIT=$rc"
    if [[ -f "$BASE_ELF" ]]; then
        local sha
        sha="$(sha256sum "$BASE_ELF" | cut -d' ' -f1)"
        echo "STABLE_BASELINE_AFTER=$sha"
        if [[ "$sha" != "$BASE_SHA" ]]; then rc=86; echo "ERROR_STABLE_BASELINE_MODIFIED"; fi
    fi
    if [[ -f "$PROTECTED" ]]; then
        local sha
        sha="$(sha256sum "$PROTECTED" | cut -d' ' -f1)"
        echo "PROTECTED_BINARY_AFTER=$sha"
        if [[ "$sha" != "$PROTECTED_SHA" ]]; then rc=87; echo "ERROR_PROTECTED_BINARY_MODIFIED"; fi
    fi
    printf '%s\n' "$rc" > "$REPORT/exit-code"
    {
        echo "SOURCE_PIN=$SOURCE_REV"
        echo "STABLE_BASE_SHA=$BASE_SHA"
        echo "RUNTIME_ARCHIVE=$LOGS/$TAG.tar.gz"
        [[ ! -f "$BINARY" ]] || sha256sum "$BINARY"
        [[ ! -f "$REPORT/ctest.txt" ]] || cat "$REPORT/ctest.txt"
    } > "$REPORT/summary.txt"
    tar -czf "$ARCHIVE" -C "$LOGS" "$TAG.log" \
        -C "$REPORT" exit-code summary.txt \
        > /dev/null 2>&1
    echo "BUILD_UPLOAD_THIS=$ARCHIVE"
    if [[ -f "$LOGS/$TAG.tar.gz" ]]; then
        echo "GAMEPLAY_UPLOAD_THIS=$LOGS/$TAG.tar.gz"
    fi
    echo "SSH_SESSION=OPEN"
    exit "$rc"
}
trap finish EXIT

exec 9>"$LOGS/gow-upstream-got-build.lock"
flock -n 9 || { echo "ANOTHER_GOW_GOT_BUILD_ACTIVE"; exit 3; }
exec 8>"$LOGS/shadps4-gow-srt-phi.lock"
flock -n 8 || { echo "ANOTHER_SRT_PHI_BUILD_ACTIVE"; exit 3; }

for command in git cmake ninja ctest python3 curl flock tar sha256sum nice timeout ldd; do
    command -v "$command" >/dev/null || { echo "MISSING_COMMAND=$command"; exit 3; }
done
[[ -d "$BASE_SOURCE" && -x "$BASE_ELF" ]] || {
    echo "MISSING_RETAINED_120_SECOND_SOURCE_OR_ELF"; exit 3;
}
[[ "$(sha256sum "$BASE_ELF" | cut -d' ' -f1)" == "$BASE_SHA" ]] || {
    echo "STABLE_120_SECOND_CANDIDATE_HASH_MISMATCH_NO_CHANGE"; exit 3;
}
[[ ! -f "$PROTECTED" || "$(sha256sum "$PROTECTED" | cut -d' ' -f1)" == "$PROTECTED_SHA" ]] || {
    echo "PROTECTED_ORIGINAL_HASH_MISMATCH_NO_CHANGE"; exit 3;
}
[[ ! -e "$SRC" ]] || { echo "NEW_WORKTREE_PATH_ALREADY_EXISTS"; exit 3; }
echo "BASELINE_120_SECOND_ELF_UNTOUCHED=PASS"
echo "STAGE=FETCH_IMMUTABLE_GENERIC_PHI_SOURCE"
if ! git -C "$BASE_SOURCE" cat-file -e "$SOURCE_REV^{commit}" 2>/dev/null; then
    git -c http.version=HTTP/1.1 -C "$BASE_SOURCE" fetch -q --no-tags --no-recurse-submodules \
        https://github.com/Chreece/shadPS4.git "$SOURCE_REV"
fi
git -C "$BASE_SOURCE" cat-file -e "$SOURCE_REV^{commit}"
git -C "$BASE_SOURCE" worktree add --detach "$SRC" "$SOURCE_REV"
[[ "$(git -C "$SRC" rev-parse HEAD)" == "$SOURCE_REV" ]] || exit 4
echo "SOURCE_COMMIT_VERIFIED=$SOURCE_REV"

echo "STAGE=INITIALIZE_PINNED_SUBMODULES"
git -c http.version=HTTP/1.1 -C "$SRC" submodule update --init --recursive --jobs 3
echo "STAGE=CONFIGURE_SEPARATE_NATIVE_RELEASE_WITH_TESTS"
cmake -S "$SRC" -B "$BUILD" -G Ninja -DCMAKE_BUILD_TYPE=Release -DENABLE_TESTS=ON
echo "STAGE=BUILD_NATIVE_EMULATOR_AND_PHI_REGRESSIONS"
nice -n 10 cmake --build "$BUILD" --target shadps4 shadps4_gcn_test --parallel 2
[[ -x "$BINARY" && -x "$BUILD/tests/shadps4_gcn_test" ]] || {
    echo "EXPECTED_NATIVE_EMULATOR_OR_TEST_BINARY_MISSING"; exit 5;
}
echo "STAGE=RUN_ALL_SIX_INVARIANT_PHI_REGRESSIONS"
ctest --test-dir "$BUILD" -N -R '^SrtInvariantPhi\.' > "$REPORT/ctest-list.txt"
grep -Eq 'Total Tests: 6([[:space:]]|$)' "$REPORT/ctest-list.txt" || {
    cat "$REPORT/ctest-list.txt"; echo "EXPECTED_SIX_PHI_TESTS_NOT_DISCOVERED"; exit 5;
}
ctest --test-dir "$BUILD" --output-on-failure --no-tests=error -R '^SrtInvariantPhi\.' | tee "$REPORT/ctest.txt"
echo "PHI_REGRESSION_TESTS=PASS"
"$BINARY" --help > "$REPORT/native-help.txt" 2>&1 || exit 5
! grep -Fq -- '--cpu-id-mode' "$REPORT/native-help.txt" || {
    echo "DYNAMORIO_COMPATIBILITY_FLAG_PRESENT"; exit 5;
}
ldd "$BINARY" > "$REPORT/ldd.txt"
! grep -q 'not found' "$REPORT/ldd.txt" || {
    echo "MISSING_NATIVE_LIBRARY"; exit 5;
}
CANDIDATE_SHA="$(sha256sum "$BINARY" | cut -d' ' -f1)"
echo "NEW_NATIVE_CANDIDATE_SHA=$CANDIDATE_SHA"
echo "STAGE=INSTALL_PINNED_EXISTING_UNIVERSAL_118_PLUS_2_OBSERVER"
RUNNER_ORIG="$WORK/pinned-gpu-headroom-observer.sh"
RUNNER="$WORK/phi-observer.sh"
curl -4fsSL --retry 3 --retry-delay 2 \
    "https://raw.githubusercontent.com/Chreece/shadPS4/$RUNNER_REV/scripts/shadps4_gpu_headroom_ab_20261026.sh" \
    -o "$RUNNER_ORIG"
[[ "$(git hash-object "$RUNNER_ORIG")" == "$RUNNER_BLOB" ]] || {
    echo "OBSERVER_TEMPLATE_HASH_MISMATCH_NO_GAME"; exit 6;
}
python3 - "$RUNNER_ORIG" "$RUNNER" "$BINARY" "$CANDIDATE_SHA" "$SOURCE_REV" "$SRC" <<'PY'
from pathlib import Path
import sys
template, dest, binary, checksum, source_rev, source_dir = sys.argv[1:]
data = Path(template).read_text()
def exactly_once(old, new):
    global data
    assert data.count(old) == 1, f"Expected exactly one launcher field: {old[:75]}"
    data = data.replace(old, new)
exactly_once(
    'BIN="$ROOT/GodOfWarRagnarok/tests/gow-upstream-got-20261025-145442-451241/build/shadps4"',
    'BIN="' + binary + '"')
exactly_once(
    'EXPECTED=7604bae00c78b72e163138327f61a2f92bf549ac66446487d1ab7370a2f9b4d1',
    'EXPECTED=' + checksum)
exactly_once(
    'SOURCE_REV="803bb428731be794bdc44e38b56313d030a7403a"',
    'SOURCE_REV="' + source_rev + '"')
exactly_once(
    'SRC="$ROOT/GodOfWarRagnarok/tests/gow-upstream-got-20261025-145442-451241/source"',
    'SRC="' + source_dir + '"')
assert 'shadps4-gpu-headroom-ab-' in data
data = data.replace('shadps4-gpu-headroom-ab-', 'shadps4-gow-srt-phi-')
exactly_once('--fix-id gpu-device-lost-after-first-frame',
             '--fix-id generic-invariant-srt-phi-and-pre-discovery-folding')
exactly_once(
    'Same exact native emulator binary, host Ollama VRAM headroom A/B only; 11/21/26s frames and read-only kernel faults',
    'New isolated native build; invariant SRT Phi codegen plus early Phi simplification; pinned VRAM-headroom observer')
exactly_once(
    'Compare whether GPU context lost persists with additional free VRAM; inspect first-frame screenshots, amdgpu kernel events and real resource lifetime before general renderer changes',
    'Compare GoW screenshots, dynamic SRT offset diagnostics and black-screen behavior against the previous stable 120-second run')
exactly_once('--pr 73 --pr 71', '--pr 73')
Path(dest).write_text(data)
PY
bash -n "$RUNNER"
echo "NATIVE_FLAGS=FULLSCREEN_AND_FPS_ONLY"
echo "VRAM=UNLOAD_OLLAMA_IF_REQUIRED_AND_RESTORE_AFTER_GAME"
echo "EXISTING_SAVES_ESDE_SSH=UNCHANGED"
echo "STAGE=118_PLUS_2_SECOND_NATIVE_RAGNAROK_GAMEPLAY"
# Call the original verified observer worker directly: its source audits,
# isolated user profile, screenshot schedule and diary append all remain active.
# It owns only the game process group; the outer SSH session stays open.
set +e
bash "$RUNNER" --worker "$TAG" "$GAME"
rc=$?
set -e
echo "NATIVE_GAMEPLAY_OBSERVER_EXIT=$rc"
exit "$rc"
