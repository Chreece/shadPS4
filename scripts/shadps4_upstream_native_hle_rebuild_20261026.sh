#!/usr/bin/env bash
# Native combined emulator: latest upstream + retained generic fixes +
# recovered guest/HLE stack alignment. Incremental rebuild and one gameplay
# test with no special CPU/GPU arguments or game-specific logic.
set -Eeuo pipefail
umask 077
export GIT_TERMINAL_PROMPT=0

ROOT="$HOME/projects/shadps4"
LOGS="$ROOT/logs"
STAGE="$ROOT/GodOfWarRagnarok/tests/gow-upstream-got-20261025-145442-451241"
SRC="$STAGE/source"
BUILD="$STAGE/build"
ELF="$BUILD/shadps4"
OLD_SRC="a82db5964bd8a48305213171862d23268602a50c"
NEW_SRC="803bb428731be794bdc44e38b56313d030a7403a"
UPSTREAM="2cdefaa1298c6e4ece6c74bd84e90f3906282d51"
OLD_SHA="3e5c8c3f15d29313f20db584673c10fd52d581ed1f370ecf62fb8bc5dfbc3947"
OLD_BACKUP="$STAGE/shadps4.pre-upstream-hle-$OLD_SHA"
PROTECTED="$ROOT/GodOfWarRagnarok/build/shadps4"
PROTECTED_SHA="8de2106a3e0d410904d9c8d6a2903ba6f1f65986af3c848b0bf274a4f4b45c8e"
GAME="CUSA34384"
TEST_TEMPLATE_SHA="7b12f75fe61124c20dd034e1ff110d17867d349e"
mkdir -p "$LOGS"

if (( $# == 0 )); then
    TAG="shadps4-hle-upstream-build-$(date +%Y%m%d-%H%M%S)-$BASHPID"
    [[ "$TAG" =~ ^shadps4-hle-upstream-build-[0-9]{8}-[0-9]{6}-[0-9]+$ ]] || exit 2
    LOG="$LOGS/$TAG.log"
    (set -C; : > "$LOG") || { echo "RUN_LOG_ALREADY_EXISTS=$LOG"; exit 2; }
    nohup setsid -w bash "$0" --worker "$TAG" </dev/null >"$LOG" 2>&1 &
    PID=$!
    disown "$PID" 2>/dev/null || true
    echo "UNIVERSAL_HLE_UPSTREAM_BUILD=$TAG"
    echo "UPSTREAM_MAIN_PARENT=$UPSTREAM"
    echo "CANDIDATE_SOURCE=$NEW_SRC"
    echo "LIVE_LOG=$LOG"
    echo "MONITOR=tail -f $LOG"
    echo "SUPERVISOR_PID=$PID"
    echo "STANDARD_GAME_FLAGS=FULLSCREEN_FPS_ONLY"
    echo "CPU=NATIVE_ONLY_NO_DYNAMORIO"
    echo "SSH_SESSION_REMAINS_OPEN=YES"
    exit 0
fi

[[ $# == 2 && "$1" == "--worker" ]] || exit 2
TAG="$2"
[[ "$TAG" =~ ^shadps4-hle-upstream-build-[0-9]{8}-[0-9]{6}-[0-9]+$ ]] || exit 2
LOG="$LOGS/$TAG.log"
EXIT="$LOGS/$TAG.exit"
ARCHIVE="$LOGS/$TAG.tar.gz"
WORK="$STAGE/test-$TAG"
TEMPLATE="$WORK/universal-runner-original.sh"
RUNNER="$WORK/universal-native-tested.sh"
mkdir -p "$WORK"
OLD_PROTECTED=missing
[[ ! -f "$PROTECTED" ]] || OLD_PROTECTED="$(sha256sum "$PROTECTED" | cut -d' ' -f1)"
GAMEPLAY_LAUNCHED=NO

finish() {
    local rc=$?
    trap - EXIT
    local cur=missing
    [[ ! -f "$PROTECTED" ]] || cur="$(sha256sum "$PROTECTED" | cut -d' ' -f1)"
    if [[ "$cur" != "$OLD_PROTECTED" ]]; then
        echo "ERROR_KNOWN_GOOD_GAMEPLAY_BINARY_CHANGED" >&2
        rc=85
    fi
    printf '%s\n' "$rc" > "$EXIT"
    {
        echo "RESULT=$rc"
        echo "UPSTREAM_MAIN_PARENT=$UPSTREAM"
        echo "SOURCE_EXPECTED=$NEW_SRC"
        echo "SOURCE_CURRENT=$(git -C "$SRC" rev-parse HEAD 2>/dev/null || echo unknown)"
        echo "PROTECTED_GAMEPLAY_BEFORE=$OLD_PROTECTED"
        echo "PROTECTED_GAMEPLAY_AFTER=$cur"
        echo "PREVIOUS_COMBINED_BACKUP=$OLD_BACKUP"
        [[ ! -f "$OLD_BACKUP" ]] || sha256sum "$OLD_BACKUP"
        [[ ! -f "$ELF" ]] || sha256sum "$ELF"
        echo "GAMEPLAY_TEST_LAUNCHED=$GAMEPLAY_LAUNCHED"
        grep -En 'FAILED:|error:|CMake Error|undefined reference|stopped:|SIGSEGV' "$LOG" | tail -45 || true
    } > "$WORK/build-summary.txt"
    if tar -czf "$ARCHIVE" -C "$LOGS" "$TAG.log" "$TAG.exit" \
         -C "$WORK" "build-summary.txt"; then
        echo "BUILD_UPLOAD_THIS=$ARCHIVE"
    else
        echo "BUILD_ARCHIVE_FAILED=$ARCHIVE" >&2
    fi
    echo "BUILD_STATUS=$rc"
    echo "ORIGINAL_GO_WAR_ELF_UNCHANGED=$([[ "$cur" == "$OLD_PROTECTED" ]] && echo YES || echo NO)"
    echo "SSH_SUNSHINE_PRODUCTION_SAVES_UNCHANGED=YES"
    exit "$rc"
}
trap finish EXIT

for tool in git cmake ctest ninja python3 curl sha256sum flock tar ldd; do
    command -v "$tool" >/dev/null || { echo "TOOL_MISSING=$tool"; exit 2; }
done
exec 9>"$LOGS/gow-upstream-got-build.lock"
flock -n 9 || { echo "ANOTHER_GOW_GOT_BUILD_ACTIVE"; exit 2; }
[[ "$OLD_PROTECTED" == "$PROTECTED_SHA" ]] || {
    echo "PROTECTED_GAMEPLAY_ELF_HASH_CHANGED"; exit 3;
}
[[ -f "$BUILD/build.ninja" && -f "$BUILD/CMakeCache.txt" ]] || {
    echo "EXISTING_INCREMENTAL_NINJA_BUILD_MISSING"; exit 3;
}
HEAD="$(git -C "$SRC" rev-parse HEAD)"
[[ "$HEAD" == "$OLD_SRC" || "$HEAD" == "$NEW_SRC" ]] || {
    echo "UNEXPECTED_SOURCE_REVISION_NO_OVERWRITE=$HEAD"; exit 3;
}
[[ -z "$(git -C "$SRC" status --porcelain --untracked-files=no)" ]] || {
    echo "SOURCE_HAS_LOCAL_TRACKED_CHANGES_NO_OVERWRITE"; exit 3;
}
if [[ "$HEAD" == "$OLD_SRC" ]]; then
    [[ -f "$ELF" && "$(sha256sum "$ELF" | cut -d' ' -f1)" == "$OLD_SHA" ]] || {
        echo "PREVIOUS_NATIVE_BINARY_CHANGED_NO_OVERWRITE"; exit 3;
    }
    if [[ ! -f "$OLD_BACKUP" ]]; then
        cp -p "$ELF" "$OLD_BACKUP"
    fi
fi
[[ -f "$OLD_BACKUP" &&
   "$(sha256sum "$OLD_BACKUP" | cut -d' ' -f1)" == "$OLD_SHA" ]] || {
    echo "NATIVE_OLD_BINARY_BACKUP_MISSING_OR_CHANGED"; exit 3;
}
echo "LAST_NATIVE_COMBINED_SHA_PRESERVED=$OLD_SHA"
echo "PREVIOUS_NATIVE_ELF=$OLD_BACKUP"
echo "EARLIER_WORKING_GO_WAR_BINARY_PROTECTED=YES"

# All hardware game tests must use the actual live upstream main. If it
# advances, update the integration branch first rather than running stale.
echo "STAGE=LATEST_UPSTREAM_MAIN_GATE"
curl -4fsSL --retry 3 "https://api.github.com/repos/shadps4-emu/shadPS4/branches/main" \
  -o "$WORK/live-upstream-main.json"
ACTUAL_UPSTREAM="$(python3 - "$WORK/live-upstream-main.json" <<'PYLIVE'
import json,sys
value=json.load(open(sys.argv[1]))
print(value["commit"]["sha"])
PYLIVE
)"
[[ "$ACTUAL_UPSTREAM" == "$UPSTREAM" ]] || {
    echo "UPSTREAM_MOVED_REBASE_REQUIRED=$ACTUAL_UPSTREAM"; exit 4;
}
echo "LATEST_UPSTREAM_SOURCE_PARENT_VERIFIED=$UPSTREAM"

if [[ "$HEAD" == "$OLD_SRC" ]]; then
    echo "STAGE=FETCH_SHA_PINNED_LATEST_UPSTREAM_INTEGRATION"
    git -C "$SRC" -c http.version=HTTP/1.1 fetch --no-tags --no-recurse-submodules \
       https://github.com/Chreece/shadPS4.git \
       refs/heads/integration/native-universal-upstream-hle-20261026
    git -C "$SRC" cat-file -e "$NEW_SRC^{commit}"
    git -C "$SRC" merge-base --is-ancestor "$NEW_SRC" FETCH_HEAD || {
        echo "INTEGRATION_BRANCH_DOES_NOT_INCLUDE_PINNED_SOURCE"; exit 4;
    }
    PARENT="$(git -C "$SRC" rev-parse "$NEW_SRC^")"
    [[ "$PARENT" == "$UPSTREAM" || "$PARENT" == "5885081595dd715a64ecd77d3466ba172040ce8c" ]] || {
        echo "UNEXPECTED_UPSTREAM_INTEGRATION_PARENT=$PARENT"; exit 4;
    }
    git -C "$SRC" checkout --quiet --detach "$NEW_SRC"
fi
[[ "$(git -C "$SRC" rev-parse HEAD)" == "$NEW_SRC" ]] || {
    echo "INTEGRATED_SOURCE_REVISION_UNEXPECTED"; exit 4;
}
git -C "$SRC" merge-base --is-ancestor "$UPSTREAM" "$NEW_SRC" || {
    echo "LATEST_UPSTREAM_NOT_INCLUDED"; exit 4;
}
echo "STAGE=STATIC_GENERAL_ABI_AND_GENERIC_FIX_AUDIT"
python3 - "$SRC" <<'PYSOURCE'
from pathlib import Path
import sys
src=Path(sys.argv[1])
module=(src/"src/core/libraries/kernel/process.cpp").read_text()
marker="s32 PS4_SYSV_ABI sceKernelLoadStartModule("
pos=module.index(marker)
assert "__attribute__((force_align_arg_pointer))" in module[max(0,pos-520):pos]
assert marker in module
assert not (src/"src/core/cpu_id_translation/launcher.cpp").exists()
cmake=(src/"CMakeLists.txt").read_text()
main=(src/"src/main.cpp").read_text()
assert "ENABLE_CPU_ID_TRANSLATION" not in cmake
assert "DynamoRIO" not in cmake
assert "--cpu-id-mode" not in main
assert "HandleCpuIdFault" in (src/"src/core/cpu_id.cpp").read_text()
srt=(src/"src/shader_recompiler/ir/passes/flatten_extended_userdata_pass.cpp").read_text()
assert srt.count("c.setSize(checkpoint)")>=2
image=(src/"src/video_core/texture_cache/texture_cache.cpp").read_text()
assert "GetReadbacksMode() == GpuReadbacksMode::Precise" in image
assert "IsSplashVisible()" in (src/"src/video_core/renderer_vulkan/vk_presenter.cpp").read_text()
assert "SDL_GAMEPAD_BUTTON_GUIDE" in (src/"src/sdl_window.cpp").read_text()
assert (src/"tests/kernel_stack_alignment/CMakeLists.txt").is_file()
print("UNIVERSAL_GUEST_HLE_STACK_ALIGN=SOURCE_PRESENT",flush=True)
print("UPSTREAM_VULKAN_BDA_AND_SRT_AND_LINEAR_GPU_FIXES=SOURCE_PRESENT",flush=True)
print("NATIVE_CPU_SPLASH_AND_GAMEPAD_QUIT=SOURCE_PRESENT",flush=True)
PYSOURCE

echo "STAGE=NATIVE_GUEST_HLE_STACK_ALIGNMENT_REGRESSION"
cmake -S "$SRC/tests/kernel_stack_alignment" -B "$WORK/alignment-build" -G Ninja \
  -DCMAKE_BUILD_TYPE=Release
cmake --build "$WORK/alignment-build" --parallel 2
ctest --test-dir "$WORK/alignment-build" --output-on-failure --no-tests=error
echo "GUEST_HLE_ALIGNMENT_TEST=PASS"
for dir in kernel_sanitizer kernel_thread_atexit; do
    if [[ -f "$SRC/tests/$dir/CMakeLists.txt" ]]; then
        cmake -S "$SRC/tests/$dir" -B "$WORK/$dir" -G Ninja -DCMAKE_BUILD_TYPE=Release
        cmake --build "$WORK/$dir" --parallel 2
        ctest --test-dir "$WORK/$dir" --output-on-failure --no-tests=error
        echo "GENERIC_NATIVE_TEST=$dir PASS"
    fi
done

echo "STAGE=INCREMENTAL_FULL_NATIVE_SHADPS4_BUILD"
cmake -S "$SRC" -B "$BUILD" -G Ninja -DCMAKE_BUILD_TYPE=Release -DENABLE_TESTS=OFF
nice -n 10 cmake --build "$BUILD" --target shadps4 --parallel 2
[[ -x "$ELF" ]] || { echo "NEW_COMBINED_ELF_LINK_FAILED"; exit 5; }
NEW_SHA="$(sha256sum "$ELF" | cut -d' ' -f1)"
[[ "$NEW_SHA" =~ ^[0-9a-f]{64}$ && "$NEW_SHA" != "$OLD_SHA" ]] || {
    echo "NEW_ENGINE_NOT_RELINKED"; exit 5;
}
"$ELF" --help > "$WORK/new-native-help.txt" 2>&1
! grep -q -- '--cpu-id-mode' "$WORK/new-native-help.txt" || {
    echo "REJECTED_CPU_TRANSLATION_FLAG_PRESENT"; exit 5;
}
ldd "$ELF" > "$WORK/ldd.txt"
! grep -q "not found" "$WORK/ldd.txt" || {
    echo "MISSING_SHADPS4_SHARED_LIBRARY"; exit 5;
}
echo "LATEST_UPSTREAM_PLUS_ALL_INTEGRATED_GENERIC_FIXES_BUILD=PASS"
echo "NEW_COMBINED_NATIVE_SHA=$NEW_SHA"

echo "STAGE=STAGE_PINNED_UNIVERSAL_NATIVE_GAME_TEST"
curl -4fsSL --retry 3 \
  "https://raw.githubusercontent.com/Chreece/shadPS4/658d7a08ff48a299f4c35e58c7f3b717bd554dc0/scripts/shadps4_universal_native_120s_test_20261026.sh" \
  -o "$TEMPLATE"
[[ "$(git hash-object "$TEMPLATE")" == "$TEST_TEMPLATE_SHA" ]] || {
    echo "TEST_TEMPLATE_HASH_UNEXPECTED"; exit 6;
}
python3 - "$TEMPLATE" "$RUNNER" "$OLD_SHA" "$NEW_SHA" "$OLD_SRC" "$NEW_SRC" <<'PYRUNNER'
from pathlib import Path
import sys
base=Path(sys.argv[1]).read_text()
expect_old,expect_new,source_old,source_new=sys.argv[3:7]
anchors={
    "EXPECTED="+expect_old:"EXPECTED="+expect_new,
    'SOURCE_REV="'+source_old+'"':'SOURCE_REV="'+source_new+'"'
}
for original,changed in anchors.items():
    if base.count(original)!=1:
        raise SystemExit("PINNED_GENERIC_TEST_ANCHOR_CHANGED: "+original)
    base=base.replace(original,changed)
assert 'DURATION=118' in base
assert 'OLLAMA_HELPER_SHA=9623031ffc736c270ccce0cad247b52d5b3dd30a' in base
assert 'RECORDER_SHA=1593a0f720d1e7ab0d2392746693839b67162959' in base
assert 'OBSERVER_SHA=8a7a329675db1704bf39c5c3aa26de859cf094df' in base
assert 'CUSA34384' in base  # default selector, not special engine behavior
Path(sys.argv[2]).write_text(base)
print("GENERIC_GAME_TEST_PINNED_TO_NEW_NATIVE_BINARY=PASS",flush=True)
PYRUNNER
bash -n "$RUNNER"
echo "READY=MANUAL_GAMEPLAY_VIA_MOONLIGHT"
echo "UNIVERSAL_GAME_FLAGS=GAME_FULLSCREEN_FPS_ONLY"
echo "NO_CPU_GPU_COMPATIBILITY_OPTIONS=YES"
echo "TEST_HARD_CAP=118S_TERM_PLUS_2S_KILL"
bash "$RUNNER" "$GAME"
GAMEPLAY_LAUNCHED=YES
echo "LAUNCHED_SEPARATE_GENERIC_GAMEPLAY_SESSION=YES"
echo "NEXT=FOLLOW_SHADPS4_NATIVE_UNIVERSAL_LOG_AND_UPLOAD_ITS_UPLOAD_THIS_ARCHIVE"
