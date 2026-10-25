#!/usr/bin/env bash
# Restore only the proven generic kernel runtime hooks, verify, build, then
# stage the same generic no-workaround observer for a manual game test.
set -Eeuo pipefail
export GIT_TERMINAL_PROMPT=0
ROOT="$HOME/projects/shadps4"
LOGS="$ROOT/logs"
BASE="b0b6dcb254031f72eda14bb693e839e7e1f52264"
FAILED="ed2c09ecfc400fcd52308123c620ebe9fc4584fd"
FIX="104cec069b7f53a69d9f8d9760d3adc5bcae892b"
STAGE="$ROOT/GodOfWarRagnarok/tests/gow-upstream-got-20261025-145442-451241"
SRC="$STAGE/source"
BUILD="$STAGE/build"
BIN="$BUILD/shadps4"
OLD_SHA="d4d1a9c6d0262c81b368da562e892a78938be0320d8a5917cc1266adb7c47151"
PROTECTED="$ROOT/GodOfWarRagnarok/build/shadps4"
PROTECTED_SHA="8de2106a3e0d410904d9c8d6a2903ba6f1f65986af3c848b0bf274a4f4b45c8e"
mkdir -p "$LOGS"
if [[ $# -eq 0 || ( $# -eq 1 && "$1" != "--worker" ) ]]; then
    GAME=CUSA34384
    if [[ $# -eq 1 ]]; then GAME="$1"; fi
    [[ -n "$GAME" && "$GAME" != -* ]] || { echo "BAD_GAME_SELECTOR"; exit 2; }
    TAG="shadps4-kernel-rebuild-$(date +%Y%m%d-%H%M%S)-$$"
    LOG="$LOGS/$TAG.log"
    (set -C; : >"$LOG") || { echo "LOG_EXISTS"; exit 2; }
    nohup setsid -w bash "$0" --worker "$TAG" "$GAME" </dev/null >"$LOG" 2>&1 &
    pid=$!
    disown "$pid" 2>/dev/null || true
    echo "PINNED_SOURCE=$FIX"
    echo "LIVE_BUILD_LOG=$LOG"
    echo "MONITOR=tail -f $LOG"
    echo "WORKER_PID=$pid"
    echo "SSH_SHELL_REMAINS_OPEN=YES"
    exit 0
fi
[[ $# == 3 && "$1" == "--worker" ]] || { echo "USAGE: bash $0 [game]"; exit 2; }
TAG="$2"; GAME="$3"
[[ "$TAG" =~ ^shadps4-kernel-rebuild-[0-9]{8}-[0-9]{6}-[0-9]+$ ]] || exit 2
LOG="$LOGS/$TAG.log"
OUTPUT="$LOGS/$TAG.tar.gz"
RUN="$STAGE/kernelfix-$TAG"
mkdir -p "$RUN"
ORIGINAL_SHA=missing
[[ ! -f "$PROTECTED" ]] || ORIGINAL_SHA="$(sha256sum "$PROTECTED" | cut -d' ' -f1)"
finish() {
    rc=$?
    trap - EXIT
    current=missing
    [[ ! -f "$PROTECTED" ]] || current="$(sha256sum "$PROTECTED" | cut -d' ' -f1)"
    [[ "$ORIGINAL_SHA" == "$current" ]] || rc=85
    printf '%s\n' "$rc" >"$LOGS/$TAG.exit"
    {
        echo "SOURCE_HEAD=$(git -C "$SRC" rev-parse HEAD 2>/dev/null || true)"
        echo "ORIGINAL_GOW_BEFORE=$ORIGINAL_SHA AFTER=$current"
        echo "RESULT=$rc"
        [[ ! -f "$BIN" ]] || sha256sum "$BIN"
    } >"$RUN/summary.txt"
    if tar -czf "$OUTPUT" -C "$LOGS" "$TAG.log" "$TAG.exit" \
        -C "$RUN" "summary.txt"; then
        echo "BUILD_UPLOAD_THIS=$OUTPUT"
    else
        echo "BUILD_ARCHIVE_CREATION_FAILED=$OUTPUT" >&2
    fi
    echo "BUILD_RESULT=$rc"
    echo "ESDE_SAVES_SUNSHINE_UNCHANGED=YES"
    exit "$rc"
}
trap finish EXIT
for tool in git cmake ctest ninja curl python3 sha256sum flock nohup setsid; do
    command -v "$tool" >/dev/null || { echo "MISSING_TOOL=$tool"; exit 2; }
done
exec 9>"$LOGS/gow-upstream-got-build.lock"
flock -n 9 || { echo "ANOTHER_BUILD_IN_PROGRESS"; exit 2; }
[[ "$ORIGINAL_SHA" == "$PROTECTED_SHA" ]] || {
    echo "ORIGINAL_GAMEPLAY_ELF_HASH_MISMATCH"; exit 3;
}
[[ -f "$BUILD/build.ninja" && -f "$BUILD/CMakeCache.txt" ]] || {
    echo "COMBINED_NINJA_BUILD_MISSING"; exit 3;
}
# A failed link may delete $BIN; preserve the previously saved successful
# baseline instead of requiring a broken output file to exist.
OLD_HEAD="$(git -C "$SRC" rev-parse HEAD)"
[[ "$OLD_HEAD" == "$BASE" || "$OLD_HEAD" == "$FAILED" || "$OLD_HEAD" == "$FIX" ]] || {
    echo "SOURCE_REVISION_UNEXPECTED=$OLD_HEAD"; exit 3;
}
[[ -z "$(git -C "$SRC" status --porcelain --untracked-files=no)" ]] || {
    echo "SOURCE_HAS_UNCOMMITTED_CHANGES"; exit 3;
}
BACKUP="$STAGE/shadps4.pre-kernelfix-$OLD_SHA"
if [[ "$OLD_HEAD" == "$BASE" ]]; then
    [[ "$(sha256sum "$BIN" | cut -d' ' -f1)" == "$OLD_SHA" ]] || {
        echo "PREVIOUS_SUCCESSFUL_BUILD_CHANGED"; exit 3;
    }
    if [[ -f "$BACKUP" ]]; then
        [[ "$(sha256sum "$BACKUP" | cut -d' ' -f1)" == "$OLD_SHA" ]] || {
            echo "PREVIOUS_BUILD_BACKUP_CONFLICT"; exit 3;
        }
    else
        cp -p "$BIN" "$BACKUP"
    fi
    echo "PREVIOUS_LINKED_BINARY_PRESERVED=$BACKUP"
fi
# For a resumed failed linker run, the old object file and the verified
# backup are authoritative. GNU ld can remove build/shadps4 on failure.
[[ -f "$BACKUP" ]] &&
[[ "$(sha256sum "$BACKUP" | cut -d' ' -f1)" == "$OLD_SHA" ]] || {
    echo "PRESERVED_BASELINE_MISSING_OR_HASH_MISMATCH"; exit 3;
}
echo "PREVIOUS_LINKED_BASELINE_SHA_VERIFIED=YES"
if [[ "$OLD_HEAD" != "$FIX" ]]; then
    git -C "$SRC" -c http.version=HTTP/1.1 fetch --no-tags --no-recurse-submodules \
      https://github.com/Chreece/shadPS4.git \
      refs/heads/fix/restore-generic-kernel-hooks-20261025
    git -C "$SRC" cat-file -e "$FIX^{commit}"
    git -C "$SRC" merge-base --is-ancestor "$FIX" FETCH_HEAD
    git -C "$SRC" merge-base --is-ancestor "$BASE" "$FIX"
    CHANGED="$(git -C "$SRC" diff --name-only "$BASE" "$FIX" -- src/ | sort)"
    EXPECTED="$(printf '%s\n' \
      src/core/libraries/kernel/kernel.cpp \
      src/core/libraries/kernel/sanitizer.cpp \
      src/core/libraries/kernel/sanitizer.h \
      src/core/libraries/kernel/thread_atexit.cpp \
      src/core/libraries/kernel/thread_atexit.h \
      src/core/linker.cpp \
      src/core/linker_thread_atexit.cpp \
      src/core/module.h \
      src/core/module_thread_atexit.h | sort)"
    [[ "$CHANGED" == "$EXPECTED" ]] || {
        echo "KERNEL_PORT_CHANGED_UNEXPECTED_ENGINE_SOURCE"; exit 4;
    }
    git -C "$SRC" diff --check "$BASE" "$FIX" -- src/ tests/
    git -C "$SRC" checkout --quiet --detach "$FIX"
fi
echo "GENERIC_KERNEL_FIX_SOURCE=PASS"
python3 - "$SRC" <<'PYLINK'
from pathlib import Path
import sys
source=Path(sys.argv[1])
linker=(source/"src/core/linker.cpp").read_text()
adapter=(source/"src/core/linker_thread_atexit.cpp").read_text()
definition="Module* Linker::FindByAddress(VAddr address)"
assert linker.count(definition)==1, "Linker::FindByAddress not unique in main linker"
assert definition not in adapter, "Duplicate Linker::FindByAddress in HLE adapter"
assert "std::scoped_lock lock{mutex};" in linker.split(definition,1)[1].split("\\n}",1)[0], (
    "Module-address lookups lost synchronized loading safety")
assert "address - base < module->aligned_base_size" in linker, (
    "Module address arithmetic no longer avoids overflow")
assert "FindModuleThreadAtexitRefs(VAddr address)" in adapter
assert "->FindByAddress(address)" in adapter
print("SINGLE_LOCKED_GENERIC_MODULE_ADDRESS_RESOLVER=PASS",flush=True)
PYLINK

cmake -S "$SRC/tests/kernel_sanitizer" -B "$RUN/sanitizer" \
  -G Ninja -DCMAKE_BUILD_TYPE=Release
cmake --build "$RUN/sanitizer" --parallel 2
ctest --test-dir "$RUN/sanitizer" --output-on-failure --no-tests=error
echo "KERNEL_SANITIZER_REGRESSION=PASS"

cmake -S "$SRC/tests/kernel_thread_atexit" -B "$RUN/atexit" \
  -G Ninja -DCMAKE_BUILD_TYPE=Release
cmake --build "$RUN/atexit" --parallel 2
ctest --test-dir "$RUN/atexit" --output-on-failure --no-tests=error
echo "KERNEL_THREAD_ATEXIT_REGRESSION=PASS"

echo "STAGE=INCREMENTAL_COMPLETE_CORE_LINK"
cmake -S "$SRC" -B "$BUILD" -G Ninja -DCMAKE_BUILD_TYPE=Release -DENABLE_TESTS=OFF
nice -n 10 cmake --build "$BUILD" --target shadps4 --parallel 2
NEW_SHA="$(sha256sum "$BIN" | cut -d' ' -f1)"
[[ "$NEW_SHA" =~ ^[0-9a-f]{64}$ && "$NEW_SHA" != "$OLD_SHA" ]] || {
    echo "GENERIC_KERNEL_PATCH_BINARY_NOT_LINKED"; exit 5;
}
"$BIN" --help >"$RUN/cli-help.log" 2>&1
ldd "$BIN" >"$RUN/ldd.log"
! grep -q 'not found' "$RUN/ldd.log" || { echo "SHARED_LIBRARY_MISSING"; exit 5; }
echo "FULL_NATIVE_LINK_PASS=YES"
echo "CANDIDATE_SHA256=$NEW_SHA"

# Stage an immutable copy of the existing universal test. The only local
# edits are its candidate checksum and replacement of its evidence observer
# with the new generic maps-capturing revision; no emulator runtime flags.
TEMPLATE="$RUN/old-universal-test.sh"
RUNTIME="$RUN/universal-gameplay-test.sh"
curl -4fLsS --retry 3 \
  "https://raw.githubusercontent.com/Chreece/shadPS4/a4f6065bad9220467eb71933a229af94a7489375/scripts/gow_combined_native_gameplay_20261025.sh" \
  -o "$TEMPLATE"
[[ "$(git hash-object "$TEMPLATE")" == "10b6291852e67efcc683fbf3b80e3efab9687301" ]] || {
    echo "UNIVERSAL_TEST_SOURCE_CHANGED"; exit 6;
}
python3 - "$TEMPLATE" "$RUNTIME" "$OLD_SHA" "$NEW_SHA" <<'PY'
from pathlib import Path
import sys
src=Path(sys.argv[1]).read_text()
old,new=sys.argv[3:5]
swaps={
    "EXPECTED="+old: "EXPECTED="+new,
    "OBSERVER_SHA=db2cb4bade906145689a09fa34eafc75cd0194e1":
        "OBSERVER_SHA=64cd9c8998d0c93f3b065dbcca83d1f083803b6d",
    "/cb9c0e93fa8c1b27f88fe20edd98e27e53224c77/tools/development/shadps4_default_game_observer_20261025.py":
        "/ed2c09ecfc400fcd52308123c620ebe9fc4584fd/tools/development/shadps4_default_game_observer_20261025.py",
}
for a,b in swaps.items():
    if src.count(a)!=1: raise SystemExit("UNIVERSAL_TEST_PATCH_ANCHOR_CHANGED="+a)
    src=src.replace(a,b)
assert 'AUTO_CPU_ID_RUNTIME' not in src  # no custom CPU-ID mode forced
assert '["--fullscreen","true","--show-fps"]' in src
Path(sys.argv[2]).write_text(src)
print("GENERIC_TEST_PINNED_TO_NEW_BINARY_AND_CRASH_MAP_OBSERVER=PASS",flush=True)
PY
bash -n "$RUNTIME"
echo "READY_TO_LAUNCH_GENERIC_TITLE=$GAME"
bash "$RUNTIME" "$GAME"
echo "RUNTIME_TEST_STARTED_DETACHED=YES"
echo "NEXT=Follow gameplay runner log and upload its archive after exiting manually"
