#!/usr/bin/env bash
# Linux PS4 CPU identity universal emulator regression proof on upstream main.
# Game use is an acceptance test, never a game-specific runtime correction.
set -Eeuo pipefail
umask 077
ROOT="$HOME/projects/shadps4/GhostOfTsushima"
TREE="$ROOT/tests/got-generic-srt-680029f3"
# The previous 381dd455 session confirms source/ and build/ are siblings.
SOURCE="$TREE/source"
BUILD="$TREE/build"
LOG_ROOT="$HOME/projects/shadps4/logs"
PARENT="3802579368e737744b82e456c332eb419afb1c95"
COMMIT="cadbc0cec5e0bf7665ac1177c0664b82f4729e80"
GAME="/mnt/roms-all/ps4-games/CUSA11456/eboot.bin"
mkdir -p "$LOG_ROOT"
if [[ "$#" -eq 0 ]]; then
    LABEL="linux-guest-cpuid-main-$(date +%Y%m%d-%H%M%S)"
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
    local tmp="$ARCHIVE.$.partial"
    if tar -czf "$tmp" -C "$RUN" info screenshots build.log game.log runner.log 2>/dev/null; then
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
echo "BUILD_UNIVERSAL_LINUX_GUEST_CPU_ID_ON_LATEST_MAIN"
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
# Copy only user/profile data from the failed 381 baseline for an A/B run.
# Never share saves or credentials with the uploaded diagnostic archive.
BASE="$ROOT/play/auto-upstream-main-381dd455-spv"
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
./shadps4 --game "$GAME" --fullscreen true >"$RUN/game.log" 2>&1 &
GAME_PID=$!
echo "GAME_PID=$GAME_PID"
(
    last=0
    for mark in 3 7 11 16 20 25 35 50 75 120 180 240 300 480; do
        sleep "$((mark-last))"
        last="$mark"
        kill -0 "$GAME_PID" 2>/dev/null || break
        if command -v xdotool >/dev/null && command -v import >/dev/null; then
            wid="$(xdotool search --onlyvisible --pid "$GAME_PID" 2>/dev/null | tail -n 1 || true)"
            if [[ -n "$wid" ]]; then
                import -window "$wid" "$RUN/screenshots/$(printf '%03d' "$mark")s.png" 2>/dev/null || true
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
echo "CPU_ID_STATIC_PATCH_RESULT:"
grep -m1 -E 'Static CPU patching for eboot.bin' "$RUN/game.log" || true
echo "GPU_HANG_EVENTS=$(grep -c 'GPU hang detected' "$RUN/game.log" || true)"
echo "GRAPHICS_PIPELINES=$(grep -c 'Compiling graphics pipeline' "$RUN/game.log" || true)"
echo "COMPUTE_PIPELINES=$(grep -c 'Compiling compute pipeline' "$RUN/game.log" || true)"
echo "No unrelated processes were signalled or stopped."
