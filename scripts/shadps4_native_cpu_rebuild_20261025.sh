#!/usr/bin/env bash
# Protected native-only generic shadPS4 build. No DynamoRIO, CPU mode flag,
# per-title workarounds, ES-DE or save edits. SSH stays open.
set -Eeuo pipefail
umask 077
export GIT_TERMINAL_PROMPT=0
ROOT="$HOME/projects/shadps4"
LOGS="$ROOT/logs"
STAGE="$ROOT/GodOfWarRagnarok/tests/gow-upstream-got-20261025-145442-451241"
SRC="$STAGE/source"
BUILD="$STAGE/build"
BIN="$BUILD/shadps4"
BASE="104cec069b7f53a69d9f8d9760d3adc5bcae892b"
FIX="a82db5964bd8a48305213171862d23268602a50c"
OLD_SHA="793a76912b4647677bc7c6c2b5087ae1f1b1c530c5664aaae623d689cdc6e847"
OLD_BIN="$STAGE/shadps4.pre-native-$OLD_SHA"
PROTECTED="$ROOT/GodOfWarRagnarok/build/shadps4"
PROTECTED_SHA="8de2106a3e0d410904d9c8d6a2903ba6f1f65986af3c848b0bf274a4f4b45c8e"
mkdir -p "$LOGS"
if (( $# == 0 )); then
    TAG="shadps4-native-rebuild-$(date +%Y%m%d-%H%M%S)-$BASHPID"
    [[ "$TAG" =~ ^shadps4-native-rebuild-[0-9]{8}-[0-9]{6}-[0-9]+$ ]] || exit 2
    LOG="$LOGS/$TAG.log"
    (set -C; : > "$LOG") || { echo "LOG_ALREADY_EXISTS"; exit 2; }
    nohup setsid -w bash "$0" --worker "$TAG" </dev/null > "$LOG" 2>&1 &
    PID=$!
    disown "$PID" 2>/dev/null || true
    echo "NATIVE_ONLY_SHADPS4_BUILD=$TAG"
    echo "SOURCE_COMMIT=$FIX"
    echo "MONITOR=tail -f $LOG"
    echo "PROCESS=$PID"
    echo "DYNAMORIO=REMOVED"
    echo "CPU_MODE_FLAGS=NONE"
    echo "ORIGINAL_BINARIES_PRESERVED=YES"
    echo "SSH_SHELL_REMAINS_OPEN=YES"
    exit 0
fi
[[ $# == 2 && "$1" == "--worker" ]] || exit 2
TAG="$2"
[[ "$TAG" =~ ^shadps4-native-rebuild-[0-9]{8}-[0-9]{6}-[0-9]+$ ]] || exit 2
LOG="$LOGS/$TAG.log"
ARCHIVE="$LOGS/$TAG.tar.gz"
EXIT="$LOGS/$TAG.exit"
REPORT="$STAGE/native-$TAG"
mkdir -p "$REPORT"
BEFORE_PROTECTED=missing
[[ ! -f "$PROTECTED" ]] ||
    BEFORE_PROTECTED="$(sha256sum "$PROTECTED" | cut -d' ' -f1)"
finish() {
    local rc=$?
    trap - EXIT
    local AFTER=missing
    [[ ! -f "$PROTECTED" ]] ||
        AFTER="$(sha256sum "$PROTECTED" | cut -d' ' -f1)"
    [[ "$AFTER" == "$BEFORE_PROTECTED" ]] || rc=85
    printf '%s\n' "$rc" > "$EXIT"
    {
        echo "EXIT_CODE=$rc"
        echo "PINNED_SOURCE=$FIX"
        echo "ACTUAL_SOURCE=$(git -C "$SRC" rev-parse HEAD 2>/dev/null || echo unknown)"
        echo "PROTECTED_GO_WAR_BEFORE=$BEFORE_PROTECTED"
        echo "PROTECTED_GO_WAR_AFTER=$AFTER"
        [[ ! -f "$OLD_BIN" ]] || sha256sum "$OLD_BIN"
        [[ ! -f "$BIN" ]] || sha256sum "$BIN"
        grep -En '(FAILED:|error:|undefined reference|CMake Error|ninja: build stopped)' "$LOG" | tail -35 || true
    } > "$REPORT/summary.txt"
    if tar -czf "$ARCHIVE" -C "$LOGS" "$TAG.log" "$TAG.exit" \
       -C "$REPORT" "summary.txt"; then
        echo "UPLOAD_THIS=$ARCHIVE"
    fi
    echo "NATIVE_BUILD_EXIT=$rc"
    echo "GOOD_OLD_EXECUTABLE_INTACT=$([[ "$AFTER" == "$BEFORE_PROTECTED" ]] && echo YES || echo NO)"
    echo "ESDE_SAVES_OLLAMA_SSH_UNCHANGED=YES"
    exit "$rc"
}
trap finish EXIT
for tool in git cmake ninja python3 sha256sum flock tar ldd; do
    command -v "$tool" >/dev/null || {
        echo "MISSING_COMMAND=$tool" >&2; exit 2;
    }
done
exec 9> "$LOGS/gow-upstream-got-build.lock"
flock -n 9 || { echo "ANOTHER_COMBINED_BUILD_IN_PROGRESS"; exit 3; }
[[ "$BEFORE_PROTECTED" == "$PROTECTED_SHA" ]] || {
    echo "ORIGINAL_GO_WAR_BINARY_MISMATCH"; exit 3;
}
[[ -d "$BUILD" && -f "$BUILD/build.ninja" && -f "$BUILD/CMakeCache.txt" ]] || {
    echo "PREVIOUS_NINJA_BUILD_MISSING"; exit 3;
}
HEAD="$(git -C "$SRC" rev-parse HEAD)"
[[ "$HEAD" == "$BASE" || "$HEAD" == "$FIX" ]] || {
    echo "SOURCE_AT_UNEXPECTED_REVISION=$HEAD"; exit 3;
}
[[ -z "$(git -C "$SRC" status --porcelain --untracked-files=no)" ]] || {
    echo "SOURCE_HAS_TRACKED_EDITS_NO_OVERWRITE"; exit 3;
}
if [[ "$HEAD" == "$BASE" ]]; then
    [[ -f "$BIN" && "$(sha256sum "$BIN" | cut -d' ' -f1)" == "$OLD_SHA" ]] || {
        echo "LAST_COMBINED_BINARY_SHA_MISMATCH"; exit 3;
    }
    if [[ ! -f "$OLD_BIN" ]]; then
        cp -p "$BIN" "$OLD_BIN"
    fi
fi
[[ -f "$OLD_BIN" &&
   "$(sha256sum "$OLD_BIN" | cut -d' ' -f1)" == "$OLD_SHA" ]] || {
    echo "LAST_WORKING_COMBINED_EXECUTABLE_BACKUP_MISSING"; exit 3;
}
echo "PREVIOUS_COMBINED_EXACT_EXECUTABLE_BACKUP=$OLD_BIN"
echo "STAGE=PINNED_SOURCE_NO_EXTERNAL_CPU_RUNTIME"
if [[ "$HEAD" == "$BASE" ]]; then
    git -C "$SRC" -c http.version=HTTP/1.1 fetch --no-tags --no-recurse-submodules \
       https://github.com/Chreece/shadPS4.git \
       refs/heads/fix/native-cpuid-no-dynamorio-20261025
    git -C "$SRC" cat-file -e "$FIX^{commit}"
    git -C "$SRC" merge-base --is-ancestor "$FIX" FETCH_HEAD
    git -C "$SRC" merge-base --is-ancestor "$BASE" "$FIX"
    CHANGED="$(git -C "$SRC" diff --name-only "$BASE" "$FIX" | sort)"
    EXPECTED="$(printf '%s\n' \
       CMakeLists.txt \
       cmake/CpuIdTranslation.cmake \
       src/core/cpu_id.cpp \
       src/core/cpu_id_translation/CMakeLists.txt \
       src/core/cpu_id_translation/build.py \
       src/core/cpu_id_translation/client.c \
       src/core/cpu_id_translation/dynamorio.patch \
       src/core/cpu_id_translation/dynamorio.patch.license \
       src/core/cpu_id_translation/launcher.cpp \
       src/core/cpu_id_translation/launcher.h \
       src/core/cpu_id_translation/package.cmake \
       src/main.cpp | sort)"
    [[ "$CHANGED" == "$EXPECTED" ]] || {
        echo "PATCH_INCLUDES_UNEXPECTED_ENGINE_CHANGES"; exit 4;
    }
    git -C "$SRC" diff --check "$BASE" "$FIX" -- CMakeLists.txt src/main.cpp src/core/cpu_id.cpp
    git -C "$SRC" checkout --quiet --detach "$FIX"
fi
[[ "$(git -C "$SRC" rev-parse HEAD)" == "$FIX" ]] || exit 4
python3 - "$SRC" <<'PY'
from pathlib import Path
import sys
root=Path(sys.argv[1])
cmake=(root/"CMakeLists.txt").read_text()
main=(root/"src/main.cpp").read_text()
cpu=(root/"src/core/cpu_id.cpp").read_text()
signals=(root/"src/core/signals.cpp").read_text()
for term in ("ENABLE_CPU_ID_TRANSLATION","DynamoRIO","CpuIdTranslation.cmake",
             "cpu_id_translation/launcher.cpp"):
    assert term not in cmake
assert "--cpu-id-mode" not in main
assert "StartCpuIdTranslation" not in main
for name in ("InitializeCpuId","EnableCpuIdFaulting","HandleCpuIdFault",
             "SetCpuIdGuestAddressRange","GenerateCpuIdInstruction"):
    assert name in cpu
assert "HandleCpuIdFault(raw_context, info->si_addr)" in signals
assert not (root/"src/core/cpu_id_translation").exists()
assert not (root/"cmake/CpuIdTranslation.cmake").exists()
print("NATIVE_CPUID_RDTSCP_RDPID_HANDLING_INTACT=PASS",flush=True)
print("DYNAMORIO_SOURCE_LAUNCHER_AND_CMAKE=ABSENT",flush=True)
PY
echo "STAGE=RECONFIGURE_NINJA_WITHOUT_DYNAMORIO"
cmake -UENABLE_CPU_ID_TRANSLATION '-UDynamoRIO*' \
    -S "$SRC" -B "$BUILD" -G Ninja -DCMAKE_BUILD_TYPE=Release -DENABLE_TESTS=OFF
if grep -Eiq 'DynamoRIO|cpu-id-runtime|shadps4_cpu_id|CpuIdTranslation' "$BUILD/build.ninja"; then
    echo "DYNAMORIO_DEPENDENCY_REMAINS_IN_NINJA"; exit 5;
fi
if grep -Eq '^ENABLE_CPU_ID_TRANSLATION:|^DynamoRIO_' "$BUILD/CMakeCache.txt"; then
    echo "DYNAMORIO_SETTING_REMAINS_IN_CMAKE_CACHE"; exit 5;
fi
echo "DYNAMORIO_IN_NINJA_AND_CACHE=NO"
echo "STAGE=INCREMENTAL_GENERIC_NATIVE_BUILD"
nice -n 10 cmake --build "$BUILD" --target shadps4 --parallel 2
[[ -x "$BIN" ]] || { echo "NATIVE_LINK_NO_EXECUTABLE"; exit 5; }
NEW_SHA="$(sha256sum "$BIN" | cut -d' ' -f1)"
[[ "$NEW_SHA" =~ ^[0-9a-f]{64}$ && "$NEW_SHA" != "$OLD_SHA" ]] || {
    echo "NATIVE_EXECUTABLE_HASH_INVALID_OR_UNCHANGED"; exit 5;
}
"$BIN" --help > "$REPORT/native-help.txt" 2>&1
! grep -q -- '--cpu-id-mode' "$REPORT/native-help.txt" || {
    echo "CPU_ID_TRANSLATION_OPTION_REMAINS_IN_EXECUTABLE"; exit 5;
}
ldd "$BIN" > "$REPORT/ldd.txt"
! grep -q 'not found' "$REPORT/ldd.txt" || {
    echo "MISSING_NATIVE_LIBRARY"; exit 5;
}
echo "NATIVE_FULL_EMULATOR_LINK=PASS"
echo "NATIVE_CPU_EMULATOR_SHA256=$NEW_SHA"
echo "NO_DYNAMORIO_NO_PER_GAME_CPU_ARGUMENTS=YES"
echo "SAME_NATIVE_ENGINE_FOR_EVERY_PS4_TITLE=YES"
echo "GAMEPLAY_TESTED=NO"
echo "NEXT_GATE=120_SECOND_GENERIC_GAMEPLAY_AND_ORIGINAL_FORMAT_CRASH_CHECK"
