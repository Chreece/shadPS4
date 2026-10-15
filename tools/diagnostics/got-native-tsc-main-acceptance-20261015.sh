#!/usr/bin/env bash
# Native Linux timestamp-counter inheritance correction on upstream main.
# Game use is an acceptance test, never a game-specific runtime correction.
set -Eeuo pipefail
umask 077
ROOT="$HOME/projects/shadps4/GhostOfTsushima"
TREE="$ROOT/tests/got-generic-srt-680029f3"
# The previous 381dd455 session confirms source/ and build/ are siblings.
SOURCE="$TREE/source"
BUILD="$TREE/build"
LOG_ROOT="$HOME/projects/shadps4/logs"
PARENT="93544d2cec2ca0ab5b48886a6ad46027668eec19"
COMMIT="319634130ac8098a5bb90fd588f3b5868fe71f9a"
GAME="/mnt/roms-all/ps4-games/CUSA11456/eboot.bin"
mkdir -p "$LOG_ROOT"
if [[ "$#" -eq 0 ]]; then
    LABEL="got-native-tsc-main-$(date +%Y%m%d-%H%M%S)"
    RUN="$LOG_ROOT/$LABEL"
    mkdir -p "$RUN/info" "$RUN/screenshots"
    touch "$RUN/build.log" "$RUN/game.log"
    nohup bash "$0" --worker "$RUN" >"$RUN/runner.log" 2>&1 </dev/null &
    echo "PROOF_LAUNCHED_PID=$!"
    echo "FOLLOW=tail -f $RUN/runner.log"
    echo "ARCHIVE=$RUN.tar.gz"
    echo "GAMEPAD=MANUAL; SSH=UNCHANGED; X11=UNCHANGED"
    exit 0
fi
[[ "$#" -eq 2 && "$1" == "--worker" ]] || { echo "Invalid launcher parameters"; exit 2; }
RUN="$2"
PLAY="$ROOT/play/$(basename "$RUN")"
ARCHIVE="$RUN.tar.gz"
pack() {
    set +e
    if [[ -d "$PLAY" ]]; then
        while IFS= read -r -d '' dir; do
            for name in registers.log pipeline.log bo_ranges.log bo_history.log addr_binding_report.log gpu_info.log app_info.log dmesg.log vm_fault.log; do
                if [[ -f "$dir/$name" ]]; then
                    cp -f "$dir/$name" "$RUN/info/$(basename "$dir")-$name"
                fi
            done
            for shader in "$dir"/*.spv; do
                [[ ! -f "$shader" ]] || cp -f "$shader" "$RUN/info/$(basename "$dir")-$(basename "$shader")"
            done
        done < <(find "$PLAY" -maxdepth 7 -type d -name 'radv_dumps_*' -print0 2>/dev/null)
    fi
    # Never package active logs directly: save bounded stable snapshots.
    [[ ! -f "$RUN/game.log" ]] || tail -c 13000000 "$RUN/game.log" >"$RUN/info/game-console-tail.log" 2>/dev/null
    [[ ! -f "$RUN/runner.log" ]] || tail -c 1000000 "$RUN/runner.log" >"$RUN/info/launcher-tail.log" 2>/dev/null
    local tmp="$ARCHIVE.$BASHPID.partial"
    if tar -czf "$tmp" -C "$RUN" info screenshots build.log 2>/dev/null; then
        mv -f -- "$tmp" "$ARCHIVE"
    else
        rm -f -- "$tmp"
    fi
    echo "ARCHIVE=$ARCHIVE"
    set -e
}
trap pack EXIT
exec 9>"$LOG_ROOT/.linux-guest-cpuid-main-proof.lock"
flock -n 9 || { echo "ANOTHER_GOT_PROOF=RUNNING; stopping this attempt only"; exit 3; }
echo "SOURCE_PARENT=$PARENT"
echo "SOURCE_CANDIDATE=$COMMIT"
echo "DATE=$(date --iso-8601=seconds)"
[[ -d "$SOURCE/.git" || -f "$SOURCE/.git" ]] || {
    echo "MISSING_ISOLATED_SOURCE=$SOURCE"
    exit 4
}
[[ -f "$BUILD/CMakeCache.txt" && -f "$BUILD/shadps4" ]] || {
    echo "MISSING_EXISTING_CMAKE_BUILD=$BUILD"; exit 4;
}
[[ -f "$GAME" ]] || { echo "MISSING_GAME=$GAME"; exit 4; }
if pgrep -x shadps4 >/dev/null 2>&1; then
    echo "SHADPS4_ALREADY_RUNNING; nothing stopped or replaced"
    exit 4
fi
[[ -z "$(git -C "$SOURCE" diff --name-only)" && -z "$(git -C "$SOURCE" diff --cached --name-only)" ]] || {
    echo "TEST_WORKTREE_HAS_TRACKED_CHANGES; refused to overwrite"
    exit 4
}
CURRENT="$(git -C "$SOURCE" rev-parse HEAD)"
echo "EXISTING_TEST_HEAD=$CURRENT"
[[ "$CURRENT" == "$PARENT" || "$CURRENT" == "$COMMIT" ]] || {
    echo "TEST_WORKTREE_MOVED; refused to replace newer/unknown work"; exit 4;
}
sha256sum "$BUILD/shadps4" >"$RUN/info/previous-disposable-build.sha256"
echo "FETCH_ISOLATED_RETAINED_COMMIT"
git -C "$SOURCE" -c http.version=HTTP/1.1 fetch --no-tags origin "$COMMIT" >>"$RUN/build.log" 2>&1
git -C "$SOURCE" checkout -q --detach "$COMMIT" >>"$RUN/build.log" 2>&1
[[ "$(git -C "$SOURCE" rev-parse HEAD)" == "$COMMIT" ]] || {
    echo "SOURCE_REVISION_MISMATCH; game not launched"; exit 4;
}
echo "RUN_GUEST_CPU_AFFINITY_REGRESSION_SUITE"
if ! g++ -std=c++20 -O2 -Wall -Wextra -Werror -pedantic \
    -I"$SOURCE/src" \
    "$SOURCE/tests/guest_cpu_selection.cpp" \
    -o "$RUN/info/guest-cpu-test" >>"$RUN/build.log" 2>&1 ||
    ! "$RUN/info/guest-cpu-test" >>"$RUN/build.log" 2>&1; then
    echo "GUEST_CPU_REGRESSION=FAILED; GAME_NOT_LAUNCHED"
    tail -n 40 "$RUN/build.log"
    exit 5
fi
rm -f -- "$RUN/info/guest-cpu-test"
echo "GUEST_CPU_AFFINITY_INTERSECTION_REGRESSION=PASS"
echo "RUN_LINUX_HOST_WORKER_TSC_INHERITANCE_REGRESSION"
if ! g++ -std=c++20 -O2 -Wall -Wextra -Werror -pedantic -pthread \
    -I"$SOURCE/src" \
    "$SOURCE/tests/core/linux_guest_tsc_inheritance.cpp" \
    -o "$RUN/info/native-tsc-test" >>"$RUN/build.log" 2>&1 ||
    ! "$RUN/info/native-tsc-test" >>"$RUN/build.log" 2>&1; then
    echo "HOST_WORKER_TSC_INHERITANCE_REGRESSION=FAILED; GAME_NOT_LAUNCHED"
    tail -n 50 "$RUN/build.log"
    exit 5
fi
rm -f -- "$RUN/info/native-tsc-test"
echo "HOST_WORKER_TSC_INHERITANCE_REGRESSION=PASS"
# Guest RDTSCP/RDPID/CPUID must remain handled by the ordinary guest-code
# patching path; removing the inherited host TSC restriction is not a bypass.
if ! grep -q 'ZYDIS_MNEMONIC_RDTSCP, {{FilterCpuId, GenerateRdtscp' "$SOURCE/src/core/cpu_patches.cpp"; then
    echo "GUEST_RDTSCP_STATIC_TRANSLATION_MISSING; GAME_NOT_LAUNCHED"
    exit 5
fi
echo "GUEST_RDTSCP_STATIC_TRANSLATION=RETAINED"
echo "BUILD_UNIVERSAL_NATIVE_TSC_GUEST_CPU_FIX_ON_UPSTREAM_MAIN"
if ! cmake --build "$BUILD" --target shadps4 --parallel 4 >>"$RUN/build.log" 2>&1; then
    echo "BUILD=FAILED; GAME_NOT_LAUNCHED"
    tail -n 50 "$RUN/build.log"
    exit 5
fi
sha256sum "$BUILD/shadps4" >"$RUN/info/candidate-build.sha256"
git -C "$SOURCE" show --stat --oneline HEAD >"$RUN/info/source-commit.txt"
git -C "$SOURCE" status --short >"$RUN/info/source-status.txt"
echo "BUILD=PASS"
[[ ! -e "$PLAY" ]] || { echo "PROOF_PROFILE_ALREADY_EXISTS=$PLAY"; exit 4; }
mkdir -p "$PLAY"
# Copy user/profile from the exact 19:55 baseline used by the 20:40 core run.
# Never share saves or credentials with the uploaded diagnostic archive.
BASE="$ROOT/play/np-init-affinity-main-20261015-195552"
if [[ -d "$BASE" ]]; then
    for item in config.json users.json keys input_config user save_data savedata; do
        [[ ! -e "$BASE/$item" ]] || cp -a "$BASE/$item" "$PLAY/"
    done
    echo "PROFILE_CLONED_FROM=$BASE" >"$RUN/info/profile-source.txt"
else
    echo "PROFILE_SOURCE_MISSING; refusing unmatched A/B comparison" >"$RUN/info/profile-source.txt"
    exit 4
fi
cp -a "$BUILD/shadps4" "$PLAY/shadps4"
cd "$PLAY"
# Force only the system Vulkan ICD. The old private RADV and bindless experiment
# are intentionally not reused in this generic emulator-fix proof.
unset VK_DRIVER_FILES VK_ICD_FILENAMES SHADPS4_GHOST_BINDLESS_EXPERIMENT
export HOME="$PLAY"
if [[ -z "$(printenv DISPLAY || true)" ]]; then export DISPLAY=:0; fi
export RADV_DEBUG="hang,noumr"
export MESA_SHADER_CACHE_DISABLE=true
export MESA_SPIRV_FAIL_DUMP_PATH="$RUN/info/spirv-failures"
mkdir -p "$MESA_SPIRV_FAIL_DUMP_PATH"
echo "SYSTEM_RADV=YES; PRIVATE_RADV=NO; GHOST_BINDLESS_EXPERIMENT=DISABLED"
echo "NO_AUTOMATIC_INPUT; NO_RUNTIME_TIMEOUT; NO_X11_CHANGES"
# Only this worker/guest inherit the restricted ELF-notes core policy.
# No sysctl, service or user shell changes; raw cores are never uploaded.
CORE_PATTERN="$(cat /proc/sys/kernel/core_pattern)"
[[ "$CORE_PATTERN" == core* ]] || {
    echo "NATIVE_CORE_PATTERN_UNSUPPORTED=$CORE_PATTERN; GAME_NOT_LAUNCHED"
    exit 4
}
if ! ulimit -S -c 262144; then
    echo "CHILD_ONLY_CORE_LIMIT_UNAVAILABLE; GAME_NOT_LAUNCHED"
    exit 4
fi
printf '0x10\n' > /proc/self/coredump_filter || {
    echo "CHILD_ONLY_CORE_FILTER_UNAVAILABLE; GAME_NOT_LAUNCHED"
    exit 4
}
{
    echo "CORE_PATTERN=$CORE_PATTERN"
    echo "CORE_LIMIT_KIB=$(ulimit -Sc)"
    echo "CORE_FILTER=$(cat /proc/self/coredump_filter)"
    echo "PREVIOUS_CRASH_INSTRUCTION=rdtsc"
    echo "CORE_CAPTURE_METHOD=POST_EXIT_ONLY"
} >"$RUN/info/core-policy.txt"
echo "NATIVE_RDTSC_POSTMORTEM=ARMED_FOR_CHILD_ONLY"
./shadps4 --game "$GAME" --fullscreen true >"$RUN/game.log" 2>&1 &
GAME_PID=$!
echo "GAME_PID=$GAME_PID"
(
    last=0
    for mark in 3 5 6 7 8 9 10 11 12 16 20 25 35 50 75 120 180 240 300 480; do
        sleep "$((mark-last))"
        last="$mark"
        kill -0 "$GAME_PID" 2>/dev/null || break
        echo "$mark $(ps -p "$GAME_PID" -o etimes=,time=,pcpu=,rss=,stat= 2>/dev/null || true)" >>"$RUN/info/runtime-samples.txt"
        if command -v xdotool >/dev/null && command -v import >/dev/null; then
            wid="$(xdotool search --onlyvisible --pid "$GAME_PID" 2>/dev/null | tail -n 1 || true)"
            if [[ -n "$wid" ]]; then
                timeout 5s import -window "$wid" "$RUN/screenshots/$(printf '%03d' "$mark")s.png" 2>/dev/null || true
            fi
            # Main game windows often remain black while a distinct error
            # dialog owns focus. Capture only focused windows whose title is
            # related to the emulator/error, not the user's entire desktop.
            active="$(xdotool getactivewindow 2>/dev/null || true)"
            if [[ -n "$active" ]]; then
                title="$(xdotool getwindowname "$active" 2>/dev/null || true)"
                if [[ "$title" =~ [Ss][Hh][Aa][Dd][Pp][Ss]4|[Gg]host[[:space:]]of[[:space:]]Tsushima|[Ee]rror ]]; then
                    printf '%s | %s\n' "$mark" "$title" >>"$RUN/info/active-window-titles.log"
                    timeout 5s import -window "$active" "$RUN/screenshots/active-$(printf '%03d' "$mark")s.png" 2>/dev/null || true
                fi
            fi
        fi
    done
    if kill -0 "$GAME_PID" 2>/dev/null; then
        echo "SCREENSHOT_WINDOW_COMPLETE; GAME_CONTINUES"
        pack
    fi
) &
SHOT_PID=$!
set +e
wait "$GAME_PID"
rc=$?
kill "$SHOT_PID" 2>/dev/null
wait "$SHOT_PID" 2>/dev/null
set -e
echo "$rc" >"$RUN/info/exit-code.txt"
echo "GAME_EXIT=$rc"
if [[ "$rc" == 139 ]]; then
    sleep 1
    corefile=""
    while IFS= read -r -d '' candidate; do
        corefile="$candidate"
        break
    done < <(find "$PLAY" -maxdepth 1 -type f -name 'core*' -print0)
    if [[ -n "$corefile" && -s "$corefile" ]]; then
        echo "CORE_FILE_BYTES=$(stat -c '%s' "$corefile")" >"$RUN/info/core-created.txt"
        if command -v gdb >/dev/null; then
            timeout 25s gdb -nx -q -batch \
                -ex 'set pagination off' \
                -ex 'info registers rip rsp rbp rax rdi rsi' \
                -ex 'info symbol $rip' \
                -ex 'x/8i $rip' \
                -ex 'thread apply all bt 2' \
                "$PLAY/shadps4" "$corefile" >"$RUN/info/native-crash.txt" 2>&1 || true
        fi
        rm -f -- "$corefile"
        echo "RAW_CORE_DELETED_AFTER_ANALYSIS=YES" >>"$RUN/info/core-created.txt"
    else
        echo "CORE_FILE_NOT_CREATED" >"$RUN/info/native-crash.txt"
    fi
fi
echo "NP_TOOLKIT_CALLBACKS=$(grep -cE 'sceUserServiceRegisterEventCallback|sceNpRegisterStateCallbackForToolkit' "$RUN/game.log" || true)"
echo "GUEST_CPU_AFFINITY_INTERSECTIONS=$(grep -c 'Guest pthread CPU affinity intersected' "$RUN/game.log" || true)"
echo "GUEST_CPU_AFFINITY_REJECTIONS=$(grep -cE 'Guest pthread CPU affinity has no permitted|Thread affinity requested reserved' "$RUN/game.log" || true)"
echo "CPU_ID_STATIC_PATCH_RESULT:"
grep -m1 -E 'Static CPU patching for eboot.bin' "$RUN/game.log" || true
echo "GPU_HANG_EVENTS=$(grep -c 'GPU hang detected' "$RUN/game.log" || true)"
echo "GRAPHICS_PIPELINES=$(grep -c 'Compiling graphics pipeline' "$RUN/game.log" || true)"
echo "COMPUTE_PIPELINES=$(grep -c 'Compiling compute pipeline' "$RUN/game.log" || true)"
echo "NATIVE_TSC_INHERITANCE_CANDIDATE=TESTED"
echo "GAME_EXIT_CODE=$rc"
echo "No unrelated processes were signalled or stopped."
