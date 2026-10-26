#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
# SPDX-License-Identifier: GPL-2.0-or-later
# Resume isolated FindILsb native build after Logger inheritance compile repair.
# Reuse cached Ninja objects; preserve original failed-trial log and diary.
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
SOURCE_REV="14975c44f2ab4840a2d73f15e60ef72314f6cef5"
RUNNER_REV="448c20c756accf1b9148acde0622bf7db4a48b63"
RUNNER_BLOB="223a8e6ef7916e06bfd4f0ccefc90c92d5b097a0"
mkdir -p "$LOGS" "$TESTS"

if (( $# == 0 )) || [[ "$1" != "--worker" ]]; then
    [[ $# -le 1 ]] || { echo "USAGE: bash $0 [game-id-or-path]" >&2; exit 2; }
    GAME="CUSA34384"
    if (( $# == 1 )); then GAME="$1"; fi
    [[ -n "$GAME" && "$GAME" != -* && "$GAME" != *$'\n'* ]] || exit 2
    command -v nohup >/dev/null && command -v setsid >/dev/null || exit 2
    TAG="shadps4-srt-findilsb-$(date +%Y%m%d-%H%M%S)-${BASHPID}"
    # Validate before creating an empty file or claiming the worker started.
    # Keep this identical to the worker-side tag contract.
    [[ "$TAG" =~ ^shadps4-srt-findilsb-[0-9]{8}-[0-9]{6}-[0-9]+$ ]] || {
        echo "ERROR_INVALID_RUN_TAG=$TAG" >&2; exit 2;
    }
    LOG="$LOGS/$TAG.log"
    (set -C; : > "$LOG") || { echo "LOG_ALREADY_EXISTS=$LOG" >&2; exit 2; }
    nohup setsid -w bash "$0" --worker "$TAG" "$GAME" </dev/null >"$LOG" 2>&1 &
    PID=$!
    disown "$PID" 2>/dev/null || true
    echo "TEST=GENERIC_SRT_FINDILSB32_CACHED_BUILD_RESUME"
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
[[ "$TAG" =~ ^shadps4-srt-findilsb-[0-9]{8}-[0-9]{6}-[0-9]+$ ]] || exit 2
[[ -n "$GAME" && "$GAME" != -* && "$GAME" != *$'\n'* ]] || exit 2
WORK="$TESTS/shadps4-srt-findilsb-20261026-160302-916073"
EXPECTED_OLD_SOURCE="60384c87bd83a90a8b41141d60a64a8dc579436e"
SRC="$WORK/source"
TEST_BUILD="$WORK/build-tests"
BUILD="$WORK/build"
REPORT="$WORK/resume-$TAG"
BINARY="$BUILD/shadps4"
ARCHIVE="$LOGS/$TAG-build.tar.gz"
mkdir -p "$REPORT"
RECORDER="$REPORT/record-emulator-trial.py"
RECORDER_SHA="1593a0f720d1e7ab0d2392746693839b67162959"
UPSTREAM_SHA="2cdefaa1298c6e4ece6c74bd84e90f3906282d51"

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
        -C "$REPORT" . \
        > /dev/null 2>&1
    echo "BUILD_UPLOAD_THIS=$ARCHIVE"
    if [[ -f "$RECORDER" && -s "$ARCHIVE" ]]; then
        local decision="BLOCKED"
        [[ "$rc" -ne 0 ]] || decision="BUILD_PASS_RUNTIME_PENDING"
        python3 -B "$RECORDER" --archive "$ARCHIVE" --game "$GAME" \
          --source "$SOURCE_REV" --upstream "$UPSTREAM_SHA" \
          --fix-id "srt-findilsb32-native-fs-inheritance-resume" \
          --change "Resume exact 25-pass FindILsb JIT candidate after safe public BaseDevice Logger fix; retain existing cached native build and warnings audit" \
          --emulator-effect "BUILD_OR_OBSERVER_EXIT_$rc" --game-effect NOT_OBSERVED \
          --decision "$decision" \
          --next "Verify failed fs.cpp object compiled, native ELF linked, then compare new GPU graphics and SRT diagnostic counts" \
          || echo "BUILD_DIARY_RECORDER_FAILED_INSPECT_ARCHIVE"
    fi
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
[[ -f "$SRC/.git" && -f "$SRC/tests/gcn/test_srt_phi.cpp" ]] || {
    echo "CACHED_SOURCE_WORKTREE_MISSING_NO_REBUILD"; exit 3;
}
[[ -f "$BUILD/build.ninja" && -x "$TEST_BUILD/tests/shadps4_gcn_test" ]] || {
    echo "CACHED_BUILD_MISSING_NO_REBUILD"; exit 3;
}
[[ "$(git -C "$SRC" rev-parse HEAD)" == "$EXPECTED_OLD_SOURCE" ]] || {
    echo "CACHED_SOURCE_NOT_AT_RECORDED_FAILED_REVISION"; exit 3;
}
[[ -z "$(git -C "$SRC" status --porcelain --untracked-files=no)" ]] || {
    echo "CACHED_SOURCE_HAS_TRACKED_CHANGES"; exit 3;
}
[[ -s "$LOGS/shadps4-srt-findilsb-20261026-160302-916073-build.tar.gz" ]] || {
    echo "OLD_FAILED_RUN_EVIDENCE_MISSING"; exit 3;
}
echo "BASELINE_120_SECOND_ELF_UNTOUCHED=PASS"
echo "STAGE=UNIVERSAL_EMULATOR_DIARY_PRECHECK"
curl -4fsSL --retry 3 --retry-delay 2 \
  "https://raw.githubusercontent.com/Chreece/shadPS4/178376d393051d8026319bc0154d3b36d0fcc087/tools/development/record_emulator_trial.py" \
  -o "$RECORDER"
[[ "$(git hash-object "$RECORDER")" == "$RECORDER_SHA" ]] || {
    echo "UNIVERSAL_DIARY_WRITER_HASH_MISMATCH"; exit 3;
}
# Prior failures already recorded with their original SHA; avoid duplicate entries.
echo "STAGE=FETCH_PINNED_LOGGER_TYPE_FIX"
if ! git -C "$BASE_SOURCE" cat-file -e "$SOURCE_REV^{commit}" 2>/dev/null; then
    git -c http.version=HTTP/1.1 -C "$BASE_SOURCE" fetch -q --no-tags --no-recurse-submodules \
        https://github.com/Chreece/shadPS4.git "$SOURCE_REV"
fi
git -C "$BASE_SOURCE" cat-file -e "$SOURCE_REV^{commit}"
git -C "$SRC" switch --detach "$SOURCE_REV"
[[ "$(git -C "$SRC" rev-parse HEAD)" == "$SOURCE_REV" ]] || {
    echo "RESUME_GIT_SOURCE_PIN_MISMATCH"; exit 4;
}
grep -Fqx 'class Logger final : public BaseDevice {' "$SRC/src/core/file_sys/devices/logger.h" || {
    echo "LOGGER_PUBLIC_INHERITANCE_FIX_MISSING"; exit 4;
}
grep -Fq 'std::is_convertible_v<Devices::Logger*, Devices::BaseDevice*>' "$SRC/src/core/file_sys/fs.cpp" || {
    echo "LOGGER_COMPILE_TIME_CONVERSION_TEST_MISSING"; exit 4;
}
echo "SOURCE_SWITCH_OLD_TO_FIXED_WITH_CACHED_OBJECTS=PASS"
echo "ORIGINAL_FAILED_ARCHIVE_AND_DIARY_UNCHANGED=YES"
echo "STAGE=VERIFY_CACHED_SUBMODULES"
git -C "$SRC" submodule status --recursive > "$REPORT/submodules.txt"
if grep -Eq '^[-+U]' "$REPORT/submodules.txt"; then
    echo "CACHED_SUBMODULE_REVISION_MISMATCH"; exit 4;
fi
[[ -z "$(git -C "$SRC" status --porcelain --untracked-files=no)" ]] || {
    echo "SOURCE_WORKTREE_TRACKED_FILES_DIRTY"; exit 4;
}
[[ -f "$SRC/tests/gcn/test_srt_phi.cpp" ]] || {
    echo "DYNAMIC_SRT_GTEST_MISSING"; exit 4;
}
grep -Fq 'OpAtomicOr(U32[1], fault_ptr' "$SRC/src/shader_recompiler/backend/spirv/spirv_emit_context.cpp" || {
    echo "ATOMIC_FAULT_FIX_MISSING"; exit 4;
}
grep -Fq 'dma_policy.NeedsGpuRead()' "$SRC/src/shader_recompiler/ir/passes/shader_info_collection_pass.cpp" || {
    echo "UNIVERSAL_DYNAMIC_SRT_DMA_MISSING"; exit 4;
}
grep -Fq 'GuestGpuAddressLimit - sizeof(u32) + 1' "$SRC/src/shader_recompiler/backend/spirv/spirv_emit_context.cpp" || {
    echo "BDA_GUEST_RANGE_GUARD_MISSING"; exit 4;
}
echo "SOURCE_COMMIT_VERIFIED=$SOURCE_REV"

echo "STAGE=REUSE_FFMPEG_FROM_PROVEN_BASELINE"
[[ -f "$SRC/externals/ffmpeg-core/CMakeLists.txt" ]] || {
    echo "CHECKPOINT_FFMPEG_SUBMODULE_MISSING"; exit 4;
}
FFMPEG_COMMIT="$(git -C "$SRC/externals/ffmpeg-core" rev-parse HEAD)"
FFMPEG_SHORT="$(git -C "$SRC/externals/ffmpeg-core" rev-parse --short HEAD)"
BASE_FFMPEG_COMMIT="$(git -C "$BASE_SOURCE/externals/ffmpeg-core" rev-parse HEAD)"
[[ "$FFMPEG_COMMIT" == "$BASE_FFMPEG_COMMIT" ]] || {
    echo "BASELINE_FFMPEG_COMMIT_DIFFERS_NO_CACHE_REUSE"; exit 4;
}
FFMPEG_ASSET="$WORK/ffmpeg-$FFMPEG_SHORT.zip"
BASE_ASSET="$OLD/build/externals/ffmpeg-$FFMPEG_SHORT.zip"
BASE_LIBS="$OLD/build/externals/ffmpeg-$FFMPEG_SHORT/lib"
python3 - "$BASE_ASSET" "$BASE_LIBS" "$FFMPEG_ASSET" <<'PYFF'
from pathlib import Path, PurePosixPath
import hashlib, sys, zipfile
old_zip, libs, target = map(Path, sys.argv[1:])
names = ["lib"+n+".a" for n in ("avformat","avcodec","swscale","avutil","avfilter","swresample")]
def verify(archive):
    with zipfile.ZipFile(archive) as z:
        listing = set(z.namelist())
        assert all(n in listing and z.getinfo(n).file_size > 4096 for n in names)
        assert all(not n.startswith("/") and ".." not in PurePosixPath(n).parts for n in listing)
        assert z.testzip() is None
if old_zip.is_file():
    verify(old_zip)
    target.write_bytes(old_zip.read_bytes())
    origin="original_matching_ffmpeg_prebuilt_zip"
elif all((libs/n).is_file() and not (libs/n).is_symlink() for n in names):
    with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_STORED) as z:
        for n in names:
            with (libs/n).open("rb") as fd:
                assert fd.read(8) in (b"!<arch>\n",b"!<thin>\n"),n
            z.write(libs/n,n)
    verify(target)
    origin="original_matching_ffmpeg_static_archives"
else:
    origin="cache_missing"
if target.exists():
    print("FFMPEG_REUSED_FROM="+origin)
    print("FFMPEG_PREBUILT_SHA256="+hashlib.sha256(target.read_bytes()).hexdigest())
else:
    print("FFMPEG_CACHE_MISSING_WILL_FETCH_OFFICIAL_RELEASE")
PYFF
if [[ ! -s "$FFMPEG_ASSET" ]]; then
    echo "STAGE=FETCH_OFFICIAL_FFMPEG_RELEASE_IPV4"
    curl -4fsSL --retry 3 --retry-delay 2 \
        "https://github.com/shadps4-emu/ext-ffmpeg-core/releases/download/$FFMPEG_SHORT/ffmpeg-linux-x64.zip" \
        -o "$FFMPEG_ASSET" || {
        echo "NO_CACHED_EXACT_FFMPEG_ARCHIVES_AND_OFFICIAL_RELEASE_UNAVAILABLE"; exit 4;
    }
fi
python3 - "$FFMPEG_ASSET" <<'PYZIP'
import sys,zipfile
names={"lib"+x+".a" for x in ("avformat","avcodec","swscale","avutil","avfilter","swresample")}
with zipfile.ZipFile(sys.argv[1]) as z:
    assert names.issubset(z.namelist()) and z.testzip() is None
print("FFMPEG_ASSET_VALIDATED",flush=True)
PYZIP
for build in "$BUILD" "$TEST_BUILD"; do
    mkdir -p "$build/externals"
    cp "$FFMPEG_ASSET" "$build/externals/ffmpeg-$FFMPEG_SHORT.zip"
done
echo "STAGE=REUSE_EXISTING_GOOGLETEST"
GTEST_SRC="$WORK/googletest-v1.17.0"
if [[ ! -f "$GTEST_SRC/CMakeLists.txt" ]]; then
    echo "CACHED_GOOGLETEST_MISSING_NO_CLONE"; exit 5
fi
git -C "$GTEST_SRC" describe --tags --exact-match | grep -Fxq v1.17.0 || {
    echo "GOOGLETEST_NOT_TAG_V1_17_0"; exit 5;
}
echo "STAGE=RECONFIGURE_CACHED_GCN_TEST"
cmake -S "$SRC" -B "$TEST_BUILD" -G Ninja -DCMAKE_BUILD_TYPE=Release \
    -DENABLE_TESTS=ON -DFETCHCONTENT_SOURCE_DIR_GOOGLETEST="$GTEST_SRC" \
    > "$REPORT/configure-tests.log" 2>&1 || {
    tail -120 "$REPORT/configure-tests.log"; exit 5;
}
echo "STAGE=REUSE_CACHED_GCN_TEST_OBJECTS"
echo "BUILD_TEST_LOG=$REPORT/build-tests.log"
nice -n 10 cmake --build "$TEST_BUILD" --target shadps4_gcn_test --parallel 2 \
    > "$REPORT/build-tests.log" 2>&1 || {
    tail -120 "$REPORT/build-tests.log"; exit 5;
}
[[ -x "$TEST_BUILD/tests/shadps4_gcn_test" ]] || {
    echo "GCN_TEST_BINARY_NOT_BUILT"; exit 6;
}
echo "STAGE=RUN_25_GENERIC_SRT_AND_JIT_REGRESSIONS"
ctest --test-dir "$TEST_BUILD" -N -R '^(SrtInvariantPhi|SrtDynamicDma|NativeWarningContracts|SrtFindILsb32)\.' > "$REPORT/ctest-list.txt"
grep -Eq 'Total Tests: 25([[:space:]]|$)' "$REPORT/ctest-list.txt" || {
    cat "$REPORT/ctest-list.txt"; echo "EXPECTED_25_GENERIC_TESTS_MISSING"; exit 6;
}
ctest --test-dir "$TEST_BUILD" --output-on-failure --no-tests=error \
    -R '^(SrtInvariantPhi|SrtDynamicDma|NativeWarningContracts|SrtFindILsb32)\.' | tee "$REPORT/ctest.txt"
echo "ALL_25_GENERIC_PHI_DMA_FINDILSB_JIT_TESTS=PASS"
echo "STAGE=RECONFIGURE_CACHED_EMULATOR"
cmake -S "$SRC" -B "$BUILD" -G Ninja -DCMAKE_BUILD_TYPE=Release -DENABLE_TESTS=OFF \
    > "$REPORT/configure-native.log" 2>&1 || {
    tail -120 "$REPORT/configure-native.log"; exit 7;
}
echo "STAGE=COMPILE_PREVIOUSLY_FAILED_FS_CPP_FIRST"
echo "FS_CPP_PRECHECK_LOG=$REPORT/preflight-fs-object.log"
nice -n 10 ninja -C "$BUILD" -j 2 CMakeFiles/shadps4.dir/src/core/file_sys/fs.cpp.o \
    > "$REPORT/preflight-fs-object.log" 2>&1 || {
    echo "FS_CPP_COMPILE_PRECHECK_FAILED"; tail -120 "$REPORT/preflight-fs-object.log"; exit 7;
}
echo "FS_CPP_COMPILE_PRECHECK=PASS"
echo "STAGE=CONTINUE_ONLY_UNBUILT_NATIVE_OBJECTS"
echo "NATIVE_BUILD_LOG=$REPORT/build-native.log"
nice -n 10 cmake --build "$BUILD" --target shadps4 --parallel 2 \
    > "$REPORT/build-native.log" 2>&1 || {
    tail -140 "$REPORT/build-native.log"; exit 7;
}
[[ -x "$BINARY" ]] || { echo "NATIVE_BINARY_NOT_LINKED"; exit 7; }
echo "STAGE=CLASSIFY_NATIVE_AND_GCN_COMPILER_WARNINGS"
python3 - "$REPORT/build-native.log" "$REPORT/build-tests.log" "$REPORT/warnings-audit.txt" <<'PYWARN'
from pathlib import Path
from collections import Counter
import re
import sys

result = []
warning = re.compile(r'^(.+?):(\d+):(\d+): warning: (.*)$')
for label, path in (("native", Path(sys.argv[1])), ("tests", Path(sys.argv[2]))):
    counts = Counter()
    detail = []
    for line in path.read_text(errors="replace").splitlines():
        match = warning.match(line)
        if not match:
            continue
        file_path, lineno, column, message = match.groups()
        kind = ("third_party" if "/externals/" in file_path else
                "first_party" if "/source/src/" in file_path else
                "test_code" if "/source/tests/" in file_path else
                "system_or_generated")
        counts[kind] += 1
        detail.append(f"{kind}: {file_path}:{lineno}:{column}: {message}")
    result.append(f"{label.upper()} WARNINGS={sum(counts.values())}")
    result.extend(f"{key}={counts[key]}" for key in
                  ("first_party", "third_party", "test_code", "system_or_generated"))
    result.extend(detail)
    print(f"{label.upper()}_WARNING_COUNT={sum(counts.values())}")
    print(f"{label.upper()}_FIRST_PARTY_WARNING_COUNT={counts['first_party']}")
Path(sys.argv[3]).write_text("\n".join(result) + "\n")
PYWARN
echo "WARNING_AUDIT_REPORT=$REPORT/warnings-audit.txt"
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
RUNNER="$WORK/findilsb-observer.sh"
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
data = data.replace('shadps4-gpu-headroom-ab-', 'shadps4-srt-findilsb-')
exactly_once('--fix-id gpu-device-lost-after-first-frame',
             '--fix-id srt-findilsb32-native-fs-inheritance-resume')
exactly_once(
    'Same exact native emulator binary, host Ollama VRAM headroom A/B only; 11/21/26s frames and read-only kernel faults',
    'Exact FindILsb32 SRT GPU resource fix and safe Logger inheritance, resuming cached native build; no title hacks')
exactly_once(
    'Compare whether GPU context lost persists with additional free VRAM; inspect first-frame screenshots, amdgpu kernel events and real resource lifetime before general renderer changes',
    'Compare old 2357 unsupported FindILsb errors and 635 dynamic Phi with new resource shader logs and graphics')
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
# Quantify the full runtime resource-table logs, not merely 1.5 MB excerpts.
SRT_FULL_CONSOLE="$LOGS/$TAG-report/full-console.log"
if [[ -f "$SRT_FULL_CONSOLE" && ! -L "$SRT_FULL_CONSOLE" ]]; then
    {
        echo "SOURCE=$SOURCE_REV"
        echo "PRIOR_EXCERPT_FINDILSB_UNSUPPORTED=2357"
        echo "PRIOR_EXCERPT_DYNAMIC_PHI=635"
        echo "PRIOR_EXCERPT_FAILED_SRT_OFFSETS=2993"
        echo "CURRENT_FULL_CONSOLE_BYTES=$(stat -c %s "$SRT_FULL_CONSOLE")"
        echo "CURRENT_FINDILSB32_UNSUPPORTED=$(grep -Fc 'Unexpected instruction for offset computation, FindILsb32' "$SRT_FULL_CONSOLE" || :)"
        echo "CURRENT_DYNAMIC_PHI=$(grep -Fc 'SRT offset Phi depends on execution flow' "$SRT_FULL_CONSOLE" || :)"
        echo "CURRENT_FAILED_SRT_OFFSETS=$(grep -Fc 'Failed to compute offset for SRT walker' "$SRT_FULL_CONSOLE" || :)"
        echo "CURRENT_INVALID_SHARP_SOURCES=$(grep -Fc 'Sharp source was not flatenned' "$SRT_FULL_CONSOLE" || :)"
        echo "CURRENT_DMA_SHADERS=$(grep -Fc 'uses demand-paged GPU reads for unflattened SRT offsets' "$SRT_FULL_CONSOLE" || :)"
        echo "CURRENT_DEVICE_LOSS=$(grep -Fci 'DeviceLost' "$SRT_FULL_CONSOLE" || :)"
        echo "CURRENT_X11_LOSS=$(grep -Fci 'XIO:  fatal IO error' "$SRT_FULL_CONSOLE" || :)"
        echo "DURATION_AND_SHADER_COVERAGE_MUST_MATCH_FOR_FAIR_AB=YES"
        echo "REAL_VISUAL_ACCEPTANCE_PENDING=YES"
    } | tee "$REPORT/srt-resource-progress.txt"
else
    echo "SRT_FULL_CONSOLE_UNAVAILABLE_NO_COUNTERS" | tee "$REPORT/srt-resource-progress.txt"
fi
exit "$rc"
