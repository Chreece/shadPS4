#!/usr/bin/env bash
# Native-speed execution and AFTER-EXIT crash metadata. No ptrace or signal tracing.
# Use exact saved build for an apples-to-apples comparison with the slow GDB run.
set -Eeuo pipefail
umask 077
ROOT="$HOME/projects/shadps4/GhostOfTsushima"
TREE="$ROOT/tests/got-generic-srt-680029f3"
SOURCE="$TREE/source"
BIN="$TREE/build/shadps4"
BASE="$ROOT/play/np-init-affinity-main-20261015-195552"
GAME="/mnt/roms-all/ps4-games/CUSA11456/eboot.bin"
LOG_ROOT="$HOME/projects/shadps4/logs"
EXPECTED_BINARY="9112ef8b199f0d57c67cf939057260c80cf5c77ff532bc1ec74eb6cf78904476"
EXPECTED_SOURCE="93544d2cec2ca0ab5b48886a6ad46027668eec19"
EXPECTED_GAME="0324f71fd64f80dfe2ad3adbd7e8c00155c4b110f4703354f58ffbd269a00de6"
mkdir -p "$LOG_ROOT"
if [[ "$#" -eq 0 ]]; then
    RUN="$LOG_ROOT/got-native-speed-$(date +%Y%m%d-%H%M%S)"
    mkdir -p "$RUN/info" "$RUN/screenshots"
    touch "$RUN/game.log" "$RUN/info/status.txt"
    nohup bash "$0" --worker "$RUN" >"$RUN/runner.log" 2>&1 </dev/null &
    echo "NORMAL_SPEED_LAUNCHED_PID=$!"
    echo "FOLLOW=tail -f $RUN/runner.log"
    echo "ARCHIVE=$RUN.tar.gz"
    echo "NO_GDB_OR_PTRACE; SSH=UNCHANGED; GAMEPAD=MANUAL"
    exit 0
fi
[[ "$#" -eq 2 && "$1" == --worker ]] || exit 2
RUN="$2"
ARCHIVE="$RUN.tar.gz"
PLAY="$ROOT/play/$(basename "$RUN")"
package_report() {
    set +e
    local temp="$ARCHIVE.$BASHPID.partial"
    if tar -czf "$temp" -C "$RUN" info screenshots game.log runner.log 2>/dev/null; then
        mv -f -- "$temp" "$ARCHIVE"
        echo "ARCHIVE=$ARCHIVE"
    else
        rm -f -- "$temp"
        echo "ARCHIVE_ERROR=$RUN"
    fi
    set -e
}
trap package_report EXIT
exec 9>"$LOG_ROOT/.got-native-speed-coredump.lock"
flock -n 9 || { echo "OTHER_NATIVE_SPEED_TEST_ACTIVE"; exit 4; }
for tool in git sha256sum tar; do
    command -v "$tool" >/dev/null || { echo "MISSING_TOOL=$tool"; exit 4; }
done
[[ -x "$BIN" && -s "$GAME" && -d "$BASE/user" ]] || {
    echo "PINNED_BINARY_GAME_OR_PORTABLE_PROFILE_MISSING"; exit 4;
}
if pgrep -u "$(id -u)" -x shadps4 >/dev/null 2>&1; then
    echo "ANOTHER_EMULATOR_RUNNING; NOTHING_KILLED"
    exit 4
fi
ACTUAL_BINARY="$(sha256sum "$BIN" | cut -d' ' -f1)"
ACTUAL_SOURCE="$(git -C "$SOURCE" rev-parse HEAD)"
ACTUAL_GAME="$(sha256sum "$GAME" | cut -d' ' -f1)"
{
    echo "BINARY_SHA=$ACTUAL_BINARY"
    echo "SOURCE_SHA=$ACTUAL_SOURCE"
    echo "GAME_SHA=$ACTUAL_GAME"
    echo "BASE_PROFILE=$BASE"
    echo "REASON=COMPARE_NATIVE_SPEED_WITH_GDB_154727_SIGNALS"
    echo "NO_GDB=YES"
    echo "NO_PRODUCTION_CODE_CHANGED=YES"
    echo "CORE_PATTERN=$(cat /proc/sys/kernel/core_pattern 2>/dev/null || echo unknown)"
    echo "CORE_LIMIT=$(ulimit -c)"
    echo "START_TIME=$(date --iso-8601=seconds)"
} >"$RUN/info/provenance.txt"
if [[ "$ACTUAL_BINARY" != "$EXPECTED_BINARY" ||
      "$ACTUAL_SOURCE" != "$EXPECTED_SOURCE" ||
      "$ACTUAL_GAME" != "$EXPECTED_GAME" ]]; then
    echo "PINNED_SOURCE_OR_BINARY_CHANGED; NO_GAME_LAUNCH"
    exit 4
fi
[[ ! -e "$PLAY" ]] || { echo "PLAY_PATH_EXISTS; REFUSING_OVERWRITE"; exit 4; }
mkdir -p "$PLAY"
cp -a -- "$BASE/user" "$PLAY/user"
for item in config.json users.json keys.json input_config savedata save_data; do
    [[ ! -e "$BASE/$item" ]] || cp -a -- "$BASE/$item" "$PLAY/$item"
done
cp -a -- "$BIN" "$PLAY/shadps4"

cd "$PLAY"
unset VK_DRIVER_FILES VK_ICD_FILENAMES SHADPS4_GHOST_BINDLESS_EXPERIMENT
export HOME="$PLAY"
export RADV_DEBUG="hang,noumr"
export MESA_SHADER_CACHE_DISABLE=true
if [[ -z "$(printenv DISPLAY || true)" ]]; then export DISPLAY=:0; fi
START_EPOCH="$(date +%s)"
echo "NATIVE_SPEED=START; NO_DEBUGGER; NO_RUNTIME_TIMEOUT; NO_AUTO_INPUT"
./shadps4 --game "$GAME" --fullscreen true >"$RUN/game.log" 2>&1 &
GAME_PID=$!
echo "GAME_PID=$GAME_PID"
echo "$GAME_PID" >"$RUN/info/game-pid.txt"
echo "GAME_LAUNCH_EPOCH=$START_EPOCH" >>"$RUN/info/provenance.txt"
(
    last=0
    for mark in 3 7 12 20 35 50 70 100 140 180; do
        sleep "$((mark-last))"
        last="$mark"
        kill -0 "$GAME_PID" 2>/dev/null || break
        {
            echo "SAMPLE_ELAPSED_SECONDS=$mark"
            ps -p "$GAME_PID" -o pid,etimes,time,pcpu,rss,vsz,stat --no-headers || true
            echo "GRAPHICS_PIPELINES=$(grep -c 'Compiling graphics pipeline' "$RUN/game.log" || true)"
            echo "COMPUTE_PIPELINES=$(grep -c 'Compiling compute pipeline' "$RUN/game.log" || true)"
            echo "NP_CALLBACKS=$(grep -cE 'sceUserServiceRegisterEventCallback|sceNpRegisterStateCallbackForToolkit' "$RUN/game.log" || true)"
        } >>"$RUN/info/live-samples.txt"
        if command -v xdotool >/dev/null 2>&1 && command -v import >/dev/null 2>&1; then
            wid="$(xdotool search --onlyvisible --pid "$GAME_PID" 2>/dev/null | tail -n 1 || true)"
            if [[ -n "$wid" ]]; then
                timeout 4s import -window "$wid" "$RUN/screenshots/$(printf '%03d' "$mark")s.png" 2>/dev/null || true
            fi
        fi
        if [[ "$mark" == 100 ]]; then
            echo "SCREENSHOTS_100_SECONDS=CAPTURED; GAME_CONTINUES"
            package_report
        fi
    done
) &
SAMPLE_PID=$!
set +e
wait "$GAME_PID"
RC=$?
kill "$SAMPLE_PID" 2>/dev/null
wait "$SAMPLE_PID" 2>/dev/null
set -e
elapsed="$(($(date +%s)-START_EPOCH))"
{
    echo "GAME_EXIT_CODE=$RC"
    echo "GAME_RUNTIME_SECONDS=$elapsed"
    echo "ENDED_AT=$(date --iso-8601=seconds)"
    echo "GRAPHICS_PIPELINES=$(grep -c 'Compiling graphics pipeline' "$RUN/game.log" || true)"
    echo "COMPUTE_PIPELINES=$(grep -c 'Compiling compute pipeline' "$RUN/game.log" || true)"
    echo "NP_CALLBACKS=$(grep -cE 'sceUserServiceRegisterEventCallback|sceNpRegisterStateCallbackForToolkit' "$RUN/game.log" || true)"
} >"$RUN/info/status.txt"
echo "GAME_EXIT=$RC RUNTIME_SECONDS=$elapsed"
if (( RC > 128 )) && command -v coredumpctl >/dev/null 2>&1; then
    echo "POST_EXIT_CORE_METADATA=CHECKING"
    sleep 1
    timeout 8s coredumpctl --no-pager info "$GAME_PID" >"$RUN/info/coredumpctl-info.txt" 2>&1 || true
else
    echo "POST_EXIT_CORE_METADATA=NOT_NEEDED_OR_UNAVAILABLE"
fi
if [[ -d "$PLAY" ]]; then
    for log in "$PLAY/shadps4.log" "$PLAY/user/log/shadps4.log"; do
        if [[ -f "$log" ]]; then
            tail -c 1500000 "$log" >"$RUN/info/$(basename "$(dirname "$log")")-game-last.log" 2>/dev/null || true
        fi
    done
fi
echo "NORMAL_SPEED_COMPLETE; NO_GDB; WORKING_BUILDS_AND_SSH_UNCHANGED"
