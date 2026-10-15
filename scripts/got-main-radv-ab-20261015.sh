#!/usr/bin/env bash
# Same 173717 candidate binary and profile source; change only Vulkan ICD to the v55 private
# driver for root-cause differential evidence. No system modifications or workarounds.
set -Eeuo pipefail
umask 077
ROOT="$HOME/projects/shadps4/GhostOfTsushima"
TREE="$ROOT/tests/got-generic-srt-680029f3"
# The previous 381dd455 session confirms source/ and build/ are siblings.
SOURCE="$TREE/source"
BUILD="$TREE/build"
LOG_ROOT="$HOME/projects/shadps4/logs"
COMMIT="3802579368e737744b82e456c332eb419afb1c95"
EXPECTED_BINARY_SHA="f72cb12b08d3d6dc24ddd4a6a061bbfdedec960ac6265c0fd25d2524c451155d"
DRIVER="$ROOT/tools/radv-nuw-complete-25.0.7/libvulkan_radeon.so"
ICD="$ROOT/tools/radv-nuw-complete-25.0.7/radeon_icd.json"
EXPECTED_DRIVER_SHA="87908e0c6833c0f6dc86cb079cb0f34de0dee7e970da89b5303f0e6b29e326f7"
GAME="/mnt/roms-all/ps4-games/CUSA11456/eboot.bin"
mkdir -p "$LOG_ROOT"
if [[ "$#" -eq 0 ]]; then
    LABEL="got-main-radv-ab-$(date +%Y%m%d-%H%M%S)"
    RUN="$LOG_ROOT/$LABEL"
    mkdir -p "$RUN/info" "$RUN/screenshots"
    touch "$RUN/build.log" "$RUN/game.log"
    nohup bash "$0" --worker "$RUN" >"$RUN/runner.log" 2>&1 </dev/null &
    echo "PROOF_LAUNCHED_PID=$!"
    echo "FOLLOW=tail -f $RUN/runner.log"
    echo "ARCHIVE=$RUN.tar.gz"
    echo "GAMEPAD=MANUAL; SSH=UNCHANGED; X11=UNCHANGED; V55=UNCHANGED"
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
            for name in registers.log pipeline.log bo_ranges.log bo_history.log addr_binding_report.log gpu_info.log app_info.log dmesg.log; do
                if [[ -f "$dir/$name" ]]; then
                    cp -f "$dir/$name" "$RUN/info/$(basename "$dir")-$name"
                fi
            done
            for shader in "$dir"/*.spv; do
                [[ ! -f "$shader" ]] || cp -f "$shader" "$RUN/info/$(basename "$dir")-$(basename "$shader")"
            done
        done < <(find "$PLAY" -maxdepth 7 -type d -name 'radv_dumps_*' -print0 2>/dev/null)
    fi
    # Atomic archive publication also supports downloading while the game is still running.
    tar -czf "$ARCHIVE.tmp" -C "$RUN" info screenshots build.log game.log runner.log 2>/dev/null &&
        mv -f "$ARCHIVE.tmp" "$ARCHIVE"
    echo "ARCHIVE=$ARCHIVE"
    set -e
}
trap pack EXIT
exec 9>"$LOG_ROOT/.got-radv-ab-proof.lock"
flock -n 9 || { echo "ANOTHER_GOT_PROOF=RUNNING; stopping this attempt only"; exit 3; }
echo "BINARY_SOURCE_CANDIDATE=$COMMIT"
echo "DATE=$(date --iso-8601=seconds)"
# Do not rebuild or change the source worktree: binary identity is the control.
[[ -f "$BUILD/shadps4" && -f "$GAME" && -f "$ICD" && -f "$DRIVER" ]] || {
    echo "MISSING_CANDIDATE_OR_PRIVATE_DRIVER; NO_LAUNCH"
    for input in "$BUILD/shadps4" "$GAME" "$ICD" "$DRIVER"; do
        [[ -e "$input" ]] || echo "MISSING=$input"
    done
    exit 4
}
if pgrep -x shadps4 >/dev/null 2>&1; then
    echo "SHADPS4_ALREADY_RUNNING; nothing stopped or replaced"
    exit 4
fi
BINARY_SHA="$(sha256sum "$BUILD/shadps4" | cut -d' ' -f1)"
DRIVER_SHA="$(sha256sum "$DRIVER" | cut -d' ' -f1)"
[[ "$BINARY_SHA" == "$EXPECTED_BINARY_SHA" ]] || {
    echo "BIN_MISMATCH: expected=$EXPECTED_BINARY_SHA actual=$BINARY_SHA; NO_LAUNCH"
    exit 4
}
[[ "$DRIVER_SHA" == "$EXPECTED_DRIVER_SHA" ]] || {
    echo "DRIVER_MISMATCH: expected=$EXPECTED_DRIVER_SHA actual=$DRIVER_SHA; NO_LAUNCH"
    exit 4
}
printf '%s  %s\n' "$BINARY_SHA" "$BUILD/shadps4" >"$RUN/info/candidate-build.sha256"
printf '%s  %s\n' "$DRIVER_SHA" "$DRIVER" >"$RUN/info/private-driver.sha256"
printf 'SOURCE_COMMIT=%s\nCHANGED_VARIABLE=VULKAN_ICD_ONLY\nPREVIOUS_173717=SYSTEM_RADV\nCURRENT=PRIVATE_RADV\nCPU_ID_MODE=UNCHANGED_DEFAULT\nGHOST_BINDLESS_EXPERIMENT=OFF\n' "$COMMIT" >"$RUN/info/driver-ab-contract.txt"
echo "BINARY=EXACT_173717; BUILD=REUSED_UNCHANGED; V55_PRIVATE_RADV=DIAGNOSTIC_ONLY"
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
    echo "PROFILE_SOURCE_MISSING; refusing to compare different profiles" >"$RUN/info/profile-source.txt"
    exit 4
fi
cp -a "$BUILD/shadps4" "$PLAY/shadps4"
cd "$PLAY"
# Only one diagnostic variable changes from 173717: Vulkan ICD.
# The private v55 driver is an A/B control, not a proposed final emulator fix.
export VK_DRIVER_FILES="$ICD" VK_ICD_FILENAMES="$ICD"
unset SHADPS4_GHOST_BINDLESS_EXPERIMENT
export HOME="$PLAY"
if [[ -z "$(printenv DISPLAY || true)" ]]; then export DISPLAY=:0; fi
export RADV_DEBUG="hang,noumr"
export MESA_SHADER_CACHE_DISABLE=true
export MESA_SPIRV_FAIL_DUMP_PATH="$RUN/info/spirv-failures"
mkdir -p "$MESA_SPIRV_FAIL_DUMP_PATH"
echo "SYSTEM_RADV=NO; PRIVATE_RADV=YES_DIAGNOSTIC_ONLY; GHOST_BINDLESS_EXPERIMENT=DISABLED"
echo "NO_AUTOMATIC_INPUT; NO_RUNTIME_TIMEOUT; NO_X11_CHANGES"
./shadps4 --game "$GAME" --fullscreen true >"$RUN/game.log" 2>&1 &
GAME_PID=$!
echo "GAME_PID=$GAME_PID"
(
    last=0
    for mark in 3 7 11 16 20 25 35 50 75 120 180 240 300 360 480; do
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
echo "No unrelated processes were signalled or stopped."
