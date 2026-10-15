#!/usr/bin/env bash
# Capture the REAL native crash after the emulator exits. No ptrace/GDB while gaming.
# Scoped to a portable run of one checksum-verified, previously built executable.
set -Eeuo pipefail
umask 077
ROOT="$HOME/projects/shadps4/GhostOfTsushima"
LOG_ROOT="$HOME/projects/shadps4/logs"
SOURCE="$ROOT/tests/got-generic-srt-680029f3/source"
BIN="$ROOT/tests/got-generic-srt-680029f3/build/shadps4"
BASE="$ROOT/play/np-init-affinity-main-20261015-195552"
GAME="/mnt/roms-all/ps4-games/CUSA11456/eboot.bin"
EXPECTED_BINARY="9112ef8b199f0d57c67cf939057260c80cf5c77ff532bc1ec74eb6cf78904476"
EXPECTED_SOURCE="93544d2cec2ca0ab5b48886a6ad46027668eec19"
EXPECTED_GAME="0324f71fd64f80dfe2ad3adbd7e8c00155c4b110f4703354f58ffbd269a00de6"
mkdir -p "$LOG_ROOT"
if [[ "$#" -eq 0 ]]; then
    RUN="$LOG_ROOT/got-native-core-$(date +%Y%m%d-%H%M%S)"
    mkdir -p "$RUN/info" "$RUN/screenshots"
    nohup bash "$0" --worker "$RUN" >"$RUN/runner.log" 2>&1 </dev/null &
    echo "CORE_CAPTURE_PID=$!"
    echo "FOLLOW=tail -f $RUN/runner.log"
    echo "ARCHIVE=$RUN.tar.gz"
    echo "NATIVE_SPEED=YES; NO_LIVE_DEBUGGING; SSH=UNCHANGED"
    exit 0
fi
[[ "$#" -eq 2 && "$1" == --worker ]] || exit 2
RUN="$2"
PLAY="$ROOT/play/$(basename "$RUN")"
ARCHIVE="$RUN.tar.gz"
touch "$RUN/info/status.txt" "$RUN/info/preflight.txt" "$RUN/game.log" "$RUN/info/postmortem.txt"
pack() {
    set +e
    # Snapshot dynamic logs before writing tar. Never archive the binary core:
    # even a filtered core contains data the user may not want to upload.
    [[ ! -f "$RUN/game.log" ]] || tail -c 7000000 "$RUN/game.log" >"$RUN/info/game-last.log" 2>/dev/null
    [[ ! -f "$RUN/runner.log" ]] || tail -c 300000 "$RUN/runner.log" >"$RUN/info/runner-last.log" 2>/dev/null
    local partial="$ARCHIVE.$BASHPID.partial"
    if tar -czf "$partial" -C "$RUN" info screenshots 2>/dev/null; then
        mv -f -- "$partial" "$ARCHIVE"
    else
        rm -f -- "$partial"
    fi
    echo "UPLOAD_ARCHIVE=$ARCHIVE"
}
trap pack EXIT
exec 9>"$LOG_ROOT/.got-native-core-capture.lock"
flock -n 9 || { echo "OTHER_CAPTURE_ACTIVE"; exit 4; }
for tool in git sha256sum gdb tar df; do
    command -v "$tool" >/dev/null || { echo "MISSING_TOOL=$tool"; exit 4; }
done
[[ -x "$BIN" && -s "$GAME" && -d "$BASE/user" ]] || {
    echo "BINARY_GAME_OR_PORTABLE_PROFILE_MISSING"; exit 4;
}
if pgrep -u "$(id -u)" -x shadps4 >/dev/null 2>&1; then
    echo "OTHER_EMULATOR_RUNNING; NOTHING_STOPPED"; exit 4
fi
ACTUAL_BINARY="$(sha256sum "$BIN" | cut -d' ' -f1)"
ACTUAL_SOURCE="$(git -C "$SOURCE" rev-parse HEAD)"
ACTUAL_GAME="$(sha256sum "$GAME" | cut -d' ' -f1)"
CORE_PATTERN="$(cat /proc/sys/kernel/core_pattern)"
{
    echo "BINARY_SHA=$ACTUAL_BINARY"
    echo "SOURCE_SHA=$ACTUAL_SOURCE"
    echo "GAME_SHA=$ACTUAL_GAME"
    echo "CORE_PATTERN=$CORE_PATTERN"
    echo "CORE_SOFT_LIMIT_BEFORE=$(ulimit -Sc)"
    echo "CORE_HARD_LIMIT=$(ulimit -Hc)"
    echo "CORE_FILTER_BEFORE=$(cat /proc/self/coredump_filter)"
    echo "CWD_REQUESTED=$PLAY"
    echo "NO_SUNSHINE_OR_X11_CHANGES=YES"
    echo "GOW_FP64_PHI_TRIAL_NOT_INCLUDED=YES"
} >"$RUN/info/preflight.txt"
if [[ "$ACTUAL_BINARY" != "$EXPECTED_BINARY" ||
      "$ACTUAL_SOURCE" != "$EXPECTED_SOURCE" ||
      "$ACTUAL_GAME" != "$EXPECTED_GAME" ]]; then
    echo "SOURCE_OR_BINARY_MISMATCH; GAME_NOT_LAUNCHED"; exit 4
fi
case "$CORE_PATTERN" in
    core|core.*) ;;
    *) echo "CORE_PATTERN_NOT_LOCAL_FILE; NO_GAME_LAUNCH"; exit 4 ;;
esac
# Only this worker and its child inherit the changed soft core limit.
# No changes to sysctl, PAM limits, system services or global core behavior.
if ! ulimit -S -c 262144; then
    echo "CANNOT_ENABLE_CHILD_ONLY_CORE_WITH_EXISTING_HARD_LIMIT; NO_GAME_LAUNCH"
    exit 4
fi
# Only ELF headers and kernel ELF notes. Exclude guest RAM / Vulkan objects /
# anonymous/private heaps to avoid multi-gigabyte dumps and private saves.
printf '0x10\n' > /proc/self/coredump_filter || {
    echo "CORE_FILTER_NOT_WRITABLE; NO_GAME_LAUNCH"; exit 4
}
FILTER="$(cat /proc/self/coredump_filter)"
if [[ "$FILTER" != "00000010" && "$FILTER" != "0x10" ]]; then
    echo "CORE_FILTER_UNEXPECTED=$FILTER; NO_GAME_LAUNCH"; exit 4
fi
echo "CORE_SOFT_LIMIT_AFTER=$(ulimit -Sc)" >>"$RUN/info/preflight.txt"
echo "CORE_FILTER_AFTER=$FILTER" >>"$RUN/info/preflight.txt"
if [[ ! -d "$PLAY" ]]; then mkdir -p "$PLAY"; else
    echo "ISOLATED_PROFILE_EXISTS; NO_OVERWRITE"; exit 4
fi
cp -a "$BASE/user" "$PLAY/user"
for name in config.json users.json keys.json input_config savedata save_data; do
    [[ ! -e "$BASE/$name" ]] || cp -a "$BASE/$name" "$PLAY/$name"
done
cp -a "$BIN" "$PLAY/shadps4"
cd "$PLAY"
unset VK_DRIVER_FILES VK_ICD_FILENAMES SHADPS4_GHOST_BINDLESS_EXPERIMENT
export HOME="$PLAY"
export RADV_DEBUG="hang,noumr"
export MESA_SHADER_CACHE_DISABLE=true
if [[ -z "$(printenv DISPLAY || true)" ]]; then export DISPLAY=:0; fi
start="$(date +%s)"
echo "NATIVE_GAME_STARTED=$(date --iso-8601=seconds)"
./shadps4 --game "$GAME" --fullscreen true >"$RUN/game.log" 2>&1 &
GAME_PID=$!
echo "GAME_PID=$GAME_PID"
echo "$GAME_PID" >"$RUN/info/game-pid.txt"
(
    last=0
    for mark in 3 7 12 20 35 60 100; do
        sleep "$((mark-last))"
        last="$mark"
        kill -0 "$GAME_PID" 2>/dev/null || break
        echo "$mark $(ps -p "$GAME_PID" -o etimes=,time=,pcpu=,rss=,stat= 2>/dev/null)" \
            >>"$RUN/info/runtime-samples.txt"
        if command -v xdotool >/dev/null && command -v import >/dev/null; then
            wid="$(xdotool search --onlyvisible --pid "$GAME_PID" 2>/dev/null | tail -n1 || true)"
            if [[ -n "$wid" ]]; then
                timeout 4s import -window "$wid" "$RUN/screenshots/$(printf '%03d' "$mark")s.png" 2>/dev/null || true
            fi
        fi
        if [[ "$mark" == 100 ]]; then pack; fi
    done
) &
SAMPLE_PID=$!
set +e
wait "$GAME_PID"
RC=$?
kill "$SAMPLE_PID" 2>/dev/null
wait "$SAMPLE_PID" 2>/dev/null
set -e
duration="$(($(date +%s)-start))"
{
    echo "GAME_EXIT_CODE=$RC"
    echo "GAME_DURATION_SECONDS=$duration"
    echo "NP_CALLBACKS=$(grep -cE 'sceUserServiceRegisterEventCallback|sceNpRegisterStateCallbackForToolkit' "$RUN/game.log" || true)"
    echo "GRAPHICS_PIPELINES=$(grep -c 'Compiling graphics pipeline' "$RUN/game.log" || true)"
    echo "COMPUTE_PIPELINES=$(grep -c 'Compiling compute pipeline' "$RUN/game.log" || true)"
    echo "CORE_SOFT_LIMIT=$(ulimit -Sc)"
    echo "NO_LIVE_GDB=YES"
} >"$RUN/info/status.txt"
echo "GAME_EXIT=$RC DURATION_SECONDS=$duration"
if [[ "$RC" == 139 ]]; then
    sleep 1
    corefile=""
    while IFS= read -r -d '' item; do
        corefile="$item"
        break
    done < <(find "$PLAY" -maxdepth 1 -type f -name 'core*' -print0)
    if [[ -n "$corefile" && -s "$corefile" ]]; then
        echo "CORE_FILE_CREATED=YES" >>"$RUN/info/status.txt"
        echo "CORE_FILE_BYTES=$(stat -c '%s' "$corefile")" >>"$RUN/info/status.txt"
        echo "POSTMORTEM_GDB=STARTED"
        set +e
        timeout 25s gdb -nx -q -batch \
          -ex 'set pagination off' \
          -ex 'set print thread-events off' \
          -ex 'info threads' \
          -ex 'info registers rip rsp rbp rax rdi rsi' \
          -ex 'thread apply all bt 3' \
          -ex 'info sharedlibrary' \
          "$PLAY/shadps4" "$corefile" \
          >"$RUN/info/postmortem.txt" 2>&1
        GDB_RC=$?
        set -e
        echo "POSTMORTEM_GDB_EXIT=$GDB_RC" >>"$RUN/info/status.txt"
        # The restricted core only contains register notes and ELF headers.
        # A full stack trace is not guaranteed, but the faulting PC is captured.
        head -c 2500000 "$RUN/info/postmortem.txt" >"$RUN/info/postmortem-top.txt"
        rm -f "$RUN/info/postmortem.txt"
        mv "$RUN/info/postmortem-top.txt" "$RUN/info/postmortem.txt"
        rm -f -- "$corefile"
        echo "CORE_PROCESSED_AND_DELETED=YES" >>"$RUN/info/status.txt"
    else
        echo "CORE_FILE_CREATED=NO" >>"$RUN/info/status.txt"
        journalctl -k --since "-3 minutes" --no-pager 2>/dev/null |
          grep -Ei '(shadps4|segfault|trap)' | tail -n 20 \
          >"$RUN/info/kernel-crash-lines.txt" || true
    fi
fi
echo "COMPLETE; NO_DEBUGGER_DURING_EXECUTION; SSH_AND_PROVEN_RELEASES_UNCHANGED"
