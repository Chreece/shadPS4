#!/usr/bin/env bash
# Read-only native crash capture of the already compiled upstream GoT candidate.
set -Eeuo pipefail
umask 077
ROOT="$HOME/projects/shadps4/GhostOfTsushima"
LOGS="$HOME/projects/shadps4/logs"
TREE="$ROOT/tests/got-generic-srt-680029f3"
BIN="$TREE/build/shadps4"
SOURCE="$TREE/source"
BASE="$ROOT/play/np-init-affinity-main-20261015-195552"
GAME="/mnt/roms-all/ps4-games/CUSA11456/eboot.bin"
EXPECTED_BINARY="9112ef8b199f0d57c67cf939057260c80cf5c77ff532bc1ec74eb6cf78904476"
EXPECTED_SOURCE="93544d2cec2ca0ab5b48886a6ad46027668eec19"
EXPECTED_GAME="0324f71fd64f80dfe2ad3adbd7e8c00155c4b110f4703354f58ffbd269a00de6"
mkdir -p "$LOGS"
if [[ "$#" -eq 0 ]]; then
  RUN="$LOGS/got-native-post-np-$(date +%Y%m%d-%H%M%S)"
  mkdir -p "$RUN/info"
  nohup bash "$0" --worker "$RUN" >"$RUN/runner.log" 2>&1 </dev/null &
  echo "PROBE_PID=$!"
  echo "FOLLOW=tail -f $RUN/runner.log"
  echo "ARCHIVE=$RUN.tar.gz"
  echo "SSH=UNCHANGED; EMULATOR=UNMODIFIED"
  exit 0
fi
[[ "$#" -eq 2 && "$1" == --worker ]] || exit 2
RUN="$2"
PLAY="$ROOT/play/$(basename "$RUN")"
ARCHIVE="$RUN.tar.gz"
touch "$RUN/gdb.log" "$RUN/info/provenance.txt"
finish() {
  set +e
  if [[ -f "$PLAY/shadps4.log" ]]; then
    tail -c 1200000 "$PLAY/shadps4.log" >"$RUN/info/game-last.log"
  fi
  if [[ -f "$PLAY/user/log/shadps4.log" ]]; then
    tail -c 1200000 "$PLAY/user/log/shadps4.log" >"$RUN/info/portable-game-last.log"
  fi
  tmp="$ARCHIVE.$$.partial"
  if tar -czf "$tmp" -C "$RUN" info gdb.log runner.log 2>/dev/null; then
    mv -f "$tmp" "$ARCHIVE"
  else
    rm -f "$tmp"
    echo "ARCHIVE_CREATION_FAILED=$RUN"
  fi
  echo "ARCHIVE=$ARCHIVE"
}
trap finish EXIT
exec 9>"$LOGS/.got-post-np-native-crash.lock"
flock -n 9 || { echo "OTHER_DIAGNOSTIC_RUNNING"; exit 4; }
for tool in gdb sha256sum git timeout tar; do
  command -v "$tool" >/dev/null 2>&1 || { echo "MISSING_TOOL=$tool"; exit 4; }
done
[[ -f "$BIN" && -f "$GAME" && -d "$BASE/user" ]] || {
  echo "VERIFIED_BINARY_GAME_OR_PROFILE_MISSING"; exit 4;
}
if pgrep -u "$(id -u)" -x shadps4 >/dev/null 2>&1; then
  echo "OTHER_GAME_RUNNING; NOTHING_STOPPED"; exit 4
fi
ACTUAL_BINARY="$(sha256sum "$BIN" | cut -d' ' -f1)"
ACTUAL_SOURCE="$(git -C "$SOURCE" rev-parse HEAD)"
ACTUAL_GAME="$(sha256sum "$GAME" | cut -d' ' -f1)"
{
  echo "BINARY_SHA=$ACTUAL_BINARY"
  echo "SOURCE_SHA=$ACTUAL_SOURCE"
  echo "GAME_SHA=$ACTUAL_GAME"
  echo "EXPECTED_RESULT=POST_NP_NATIVE_SIGSEGV_139"
  echo "NO_CODE_CHANGES_OR_WORKAROUNDS=YES"
} >"$RUN/info/provenance.txt"
if [[ "$ACTUAL_BINARY" != "$EXPECTED_BINARY" ||
      "$ACTUAL_SOURCE" != "$EXPECTED_SOURCE" ||
      "$ACTUAL_GAME" != "$EXPECTED_GAME" ]]; then
  echo "BASELINE_HASH_MISMATCH; NOTHING_LAUNCHED"; exit 4
fi
[[ ! -e "$PLAY" ]] || { echo "ISOLATED_PROFILE_ALREADY_EXISTS"; exit 4; }
mkdir -p "$PLAY"
cp -a "$BASE/user" "$PLAY/user"
for item in config.json users.json keys.json input_config savedata save_data; do
  [[ ! -e "$BASE/$item" ]] || cp -a "$BASE/$item" "$PLAY/"
done
cp -a "$BIN" "$PLAY/shadps4"
cat >"$RUN/info/crash.gdb" <<'GDB'
set pagination off
set height 0
set confirm off
set print thread-events off
set disable-randomization off
set breakpoint pending on
handle SIGSEGV nostop noprint pass
handle SIGILL nostop noprint pass
handle SIGBUS nostop noprint pass
handle SIGUSR1 nostop noprint pass
handle SIGPIPE nostop noprint pass
python
import gdb
from collections import deque
last_faults = deque(maxlen=36)
fault_count = 0
def capture_fault():
    global fault_count
    fault_count += 1
    try:
        pc = int(gdb.parse_and_eval("$pc"))
        sp = int(gdb.parse_and_eval("$rsp"))
        frames = []
        frame = gdb.newest_frame()
        for _ in range(9):
            if frame is None:
                break
            frames.append("%s:0x%x" % (frame.name() or "?",frame.pc()))
            frame = frame.older()
        entry = "event=%d pc=0x%x sp=0x%x stack=%s" % (
            fault_count,pc,sp," <- ".join(frames))
        last_faults.append(entry)
        if fault_count <= 2 or fault_count % 250 == 0:
            gdb.write("SIGNAL_SAMPLE " + entry[:230] + "\n")
    except Exception as exc:
        last_faults.append("event=%d trace_error=%s" % (fault_count,str(exc)))
def dump_faults():
    gdb.write("POST_NP_SIGSEGV_TOTAL=%d\n" % fault_count)
    for entry in last_faults:
        gdb.write("SIGNAL_RING " + entry + "\n")
    gdb.write("SIGNAL_RING_COMPLETE=1\n")
end
catch signal SIGSEGV
commands
  silent
  python capture_fault()
  continue
end
disable 1
break 'Libraries::SystemService::sceSystemServiceHideSplashScreen'
commands
  silent
  printf "POST_NP_SPLASH_HIDE_REACHED=YES\n"
  disable 2
  enable 1
  continue
end
printf "GDB_LAUNCHED=YES\n"
run
printf "INFERIOR_RUN_FINISHED=YES\n"
python dump_faults()
printf "CRASH_CAPTURE_COMPLETE=YES\n"
GDB
cd "$PLAY"
unset VK_DRIVER_FILES VK_ICD_FILENAMES SHADPS4_GHOST_BINDLESS_EXPERIMENT
export HOME="$PLAY"
export RADV_DEBUG="hang,noumr"
export MESA_SHADER_CACHE_DISABLE=true
if [[ -z "$(printenv DISPLAY || true)" ]]; then export DISPLAY=:0; fi
echo "UNCHANGED_19_55_BINARY=YES; NO_AUTOMATED_INPUT"
set +e
timeout --signal=INT --kill-after=10s 210s \
  gdb --batch --quiet -x "$RUN/info/crash.gdb" \
  --args "$PLAY/shadps4" --game "$GAME" --fullscreen true \
  >"$RUN/gdb.log" 2>&1
status=$?
set -e
echo "GDB_EXIT=$status"
echo "$status" >"$RUN/info/gdb-exit.txt"
grep -E 'SPLASH_HIDE_REACHED|POST_NP_SIGSEGV_TOTAL|SIGNAL_RING_COMPLETE|CRASH_CAPTURE_COMPLETE|Program terminated' "$RUN/gdb.log" | tail -12 || true
echo "SOURCE_AND_WORKING_CHECKPOINTS_PRESERVED; SSH_UNCHANGED"
