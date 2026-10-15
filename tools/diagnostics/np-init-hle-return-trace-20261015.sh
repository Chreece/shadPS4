#!/usr/bin/env bash
# Read-only debugger probe of a previously successful NP Toolkit initialization.
# The PS4 title-specific guest address is ONLY a SHA-guarded diagnostic breakpoint,
# never an emulator source code change, workaround, altered return, or injected input.
set -Eeuo pipefail
umask 077

ROOT="$HOME/projects/shadps4/GhostOfTsushima"
LOG_ROOT="$HOME/projects/shadps4/logs"
BIN="$ROOT/tests/got-generic-srt-680029f3/build/shadps4"
SOURCE="$ROOT/tests/got-generic-srt-680029f3/source"
PREVIOUS="$ROOT/play/linux-guest-cpu-mask-main-20261015-185121"
GAME="/mnt/roms-all/ps4-games/CUSA11456/eboot.bin"
EXPECTED_BINARY="b5834e985560fca46cdddb6af09dbb96b0cbd1466f0b18666b7a7c4d106ff56a"
EXPECTED_SOURCE="dc71bfa1d7ca988425fc1e09142ac7835d79c127"
EXPECTED_GAME="0324f71fd64f80dfe2ad3adbd7e8c00155c4b110f4703354f58ffbd269a00de6"

if [[ "$#" == 0 ]]; then
    mkdir -p "$LOG_ROOT"
    OUT="$LOG_ROOT/np-init-hle-returns-$(date +%Y%m%d-%H%M%S)"
    mkdir -p "$OUT/info" "$OUT/screenshots"
    nohup bash "$0" --worker "$OUT" >"$OUT/runner.log" 2>&1 </dev/null &
    echo "PROBE_PID=$!"
    echo "FOLLOW=tail -f $OUT/runner.log"
    echo "ARCHIVE=$OUT.tar.gz"
    echo "RUN_MODE=READ_ONLY_EXECUTABLE; SSH=UNCHANGED; NO_BYPASSES"
    exit 0
fi
[[ "$#" == 2 && "$1" == --worker ]] || exit 2
OUT="$2"
touch "$OUT/gdb.log"
PLAY="$OUT/play"
ARCHIVE="$OUT.tar.gz"
pack() {
    set +e
    local temp="$ARCHIVE.$$.partial"
    if tar -czf "$temp" -C "$OUT" info screenshots gdb.log runner.log 2>/dev/null; then
        mv -f -- "$temp" "$ARCHIVE"
    else
        tar -czf "$temp" -C "$OUT" info screenshots gdb.log runner.log 2>/dev/null &&
            mv -f -- "$temp" "$ARCHIVE"
    fi
    printf 'ARCHIVE=%s\n' "$ARCHIVE"
    set -e
}
trap pack EXIT
exec 9>"$LOG_ROOT/.np-init-main-return-trace.lock"
flock -n 9 || { echo 'OTHER_PROBE_ACTIVE'; exit 3; }
command -v gdb >/dev/null || { echo 'GDB_NOT_INSTALLED'; exit 4; }
command -v sha256sum >/dev/null || exit 4
command -v git >/dev/null || exit 4
[[ -f "$BIN" && -f "$GAME" && -d "$PREVIOUS/user" ]] || {
    echo 'MISSING_BINARY_GAME_OR_PREVIOUS_PORTABLE_USER_PROFILE'; exit 4;
}
if pgrep -u "$(id -u)" -x shadps4 >/dev/null; then
    echo 'OTHER_SHADPS4_RUNNING; NOTHING_STOPPED'
    exit 4
fi
ACTUAL_BINARY="$(sha256sum "$BIN" | cut -d' ' -f1)"
ACTUAL_GAME="$(sha256sum "$GAME" | cut -d' ' -f1)"
ACTUAL_SOURCE="$(git -C "$SOURCE" rev-parse HEAD)"
{
    echo "CANDIDATE_BINARY_SHA=$ACTUAL_BINARY"
    echo "GAME_SHA=$ACTUAL_GAME"
    echo "SOURCE_SHA=$ACTUAL_SOURCE"
    echo "REFERENCE=got-v31-np-init-return-20261013-114637 NP_INIT_RETURN=0x0"
    echo "COMPARISON=20261015-185121, SCREEN_ERROR=0x80020016"
    echo "BINARY_AND_GAME=UNMODIFIED"
    echo "DEBUG_ONLY=YES; NO_RETURN_OVERRIDE; NO_GAME_FIX"
} >"$OUT/info/checks.txt"
[[ "$ACTUAL_BINARY" == "$EXPECTED_BINARY" && "$ACTUAL_GAME" == "$EXPECTED_GAME" &&
   "$ACTUAL_SOURCE" == "$EXPECTED_SOURCE" ]] || {
    echo "BASELINE_MISMATCH: see $OUT/info/checks.txt; game not launched"; exit 4;
}
[[ ! -e "$PLAY" ]] || { echo 'PROBE_PROFILE_ALREADY_EXISTS'; exit 4; }
mkdir -p "$PLAY"
# Preserve exact user data layout; copy, never modify the prior profile.
cp -a "$PREVIOUS/user" "$PLAY/user"
for name in config.json users.json keys.json input_config; do
    [[ ! -e "$PREVIOUS/$name" ]] || cp -a "$PREVIOUS/$name" "$PLAY/$name"
done
cp -a "$BIN" "$PLAY/shadps4"

cat > "$OUT/info/probe.gdb" <<'GDB'
set pagination off
set height 0
set confirm off
set print thread-events off
set disable-randomization off
set breakpoint pending on
handle SIGSEGV nostop noprint pass
handle SIGBUS nostop noprint pass
handle SIGILL nostop noprint pass
handle SIGUSR1 nostop noprint pass
handle SIGPIPE nostop noprint pass

# Keep all native-library probes independent of game IDs and resource hashes.
# Host functions are observed, not modified. Only the end-of-init guest
# address is game-specific, guarded by exact source/binary/eboot hashes.
python
import gdb
active = False
records = 0
limit = 240

def note(message):
    gdb.write(message + "\n", gdb.STDOUT)

def reg(name):
    try:
        return int(gdb.parse_and_eval('$' + name)) & 0xffffffffffffffff
    except Exception:
        return 0

class HleReturn(gdb.FinishBreakpoint):
    def __init__(self, frame, label, number, arguments):
        super().__init__(frame, internal=True)
        self.silent = True
        self.label = label
        self.number = number
        self.arguments = arguments
    def stop(self):
        try:
            value = self.return_value
            result = (int(value) if value is not None else reg("rax")) & 0xffffffffffffffff
            note("HLE_RESULT #%d %s return=0x%x args=%s" %
                 (self.number, self.label, result, self.arguments))
        except Exception as error:
            note("HLE_RESULT_UNAVAILABLE #%d %s %s" %
                 (self.number, self.label, str(error)))
        return False

class HleEnter(gdb.Breakpoint):
    def __init__(self, name, starts=False):
        super().__init__(name, internal=True)
        self.silent = True
        self.label = name
        self.starts = starts
    def stop(self):
        global active, records
        if self.starts and not active:
            active = True
            note("TRACE_STAGE=NP_TOOLKIT_ALLOCATOR_REACHED")
        if not active or records >= limit:
            return False
        records += 1
        args = "rdi=0x%x rsi=0x%x rdx=0x%x" % (reg("rdi"), reg("rsi"), reg("rdx"))
        note("HLE_ENTER #%d %s %s" % (records, self.label, args))
        try:
            HleReturn(gdb.newest_frame(), self.label, records, args)
        except Exception as error:
            note("HLE_FINISH_UNAVAILABLE #%d %s %s" %
                 (records, self.label, str(error)))
        return False

functions = [
    ('Libraries::LibcInternal::internal_mspace_create', True),
    ('Libraries::LibcInternal::internal_mspace_malloc', False),
    ('Libraries::LibcInternal::internal_mspace_calloc', False),
    ('Libraries::LibcInternal::internal_mspace_memalign', False),
    ('Libraries::LibcInternal::internal_mspace_realloc', False),
    ('Libraries::LibcInternal::internal_mspace_reallocalign', False),
    ('Libraries::LibcInternal::internal_mspace_posix_memalign', False),
    ('Libraries::LibcInternal::internal_mspace_malloc_usable_size', False),
    ('Libraries::LibcInternal::internal_mspace_stats', False),
    ('Libraries::LibcInternal::internal__Mtxinit', False),
    ('Libraries::LibcInternal::internal__Mtxlock', False),
    ('Libraries::LibcInternal::internal__Mtxunlock', False),
    ('Libraries::LibcInternal::internal_cxa_guard_acquire', False),
    ('Libraries::LibcInternal::internal_cxa_guard_release', False),
    ('Libraries::LibcInternal::internal_cxa_guard_abort', False),
    ('Libraries::Kernel::posix_pthread_mutexattr_init', False),
    ('Libraries::Kernel::posix_pthread_mutexattr_settype', False),
    ('Libraries::Kernel::posix_pthread_mutexattr_destroy', False),
    ('Libraries::Kernel::scePthreadMutexInit', False),
    ('Libraries::Kernel::posix_pthread_create_name_np', False),
    ('Libraries::Kernel::posix_pthread_create', False),
    ('Libraries::Kernel::posix_pthread_setaffinity_np', False),
    ('Libraries::Kernel::sceKernelGetCurrentCpu', False),
    ('Libraries::UserService::sceUserServiceRegisterEventCallback', False),
    ('Libraries::NpManager::sceNpRegisterStateCallbackForToolkit', False),
]
for name, starts in functions:
    try:
        HleEnter(name, starts)
    except Exception as error:
        note("TRACE_BREAKPOINT_UNAVAILABLE %s: %s" % (name, str(error)))
note("TRACE_HLE_BREAKPOINTS_ARMED=%d MAX_CALLS=%d" % (len(functions), limit))
end

break 'Core::Module::Start'
commands
  silent
  disable $_hit_bpnum
  printf "STAGE=EBOOT_MAPPED\n"
  x/2xb 0xc97c0f
  break *0xc97c0f
  printf "STAGE=NP_TOOLKIT_RETURN_ARMED\n"
  continue
end

break 'Libraries::ErrorDialog::sceErrorDialogOpen'
commands
  silent
  printf "STAGE=ERROR_DIALOG_OPEN_BEFORE_NP_RETURN\n"
  info registers rip rdi rsi rdx rax rsp
  bt 12
  printf "CAPTURE_COMPLETE=ERROR_DIALOG\n"
  detach
  quit
end

printf "STAGE=STARTING_DEBUGGER\n"
run
printf "STAGE=GUEST_NP_TOOLKIT_CORE_INIT_RETURN\n"
printf "NP_INIT_RETURN=0x%x\n", $eax
printf "HLE_TRACING_FINAL_REGISTERS\n"
info registers rip rax rbx rcx rdx rdi rsi r8 r9 rsp
bt 12
x/10i $pc
printf "CAPTURE_COMPLETE=NP_INIT_RETURN\n"
detach
quit
GDB

cd "$PLAY"
unset VK_DRIVER_FILES VK_ICD_FILENAMES SHADPS4_GHOST_BINDLESS_EXPERIMENT
export HOME="$PLAY"
export RADV_DEBUG="hang,noumr"
export MESA_SHADER_CACHE_DISABLE=true
if [[ -z "$(printenv DISPLAY || true)" ]]; then export DISPLAY=:0; fi
echo 'BASELINE=UNCHANGED; SOURCE_MODIFICATIONS=NONE; GDB_GUEST_RETURN_ONLY'
set +e
gdb --batch --quiet -x "$OUT/info/probe.gdb" --args "$PLAY/shadps4" \
    --game "$GAME" --fullscreen true >"$OUT/gdb.log" 2>&1
status=$?
set -e
echo "DEBUGGER_EXIT=$status"
printf 'DEBUGGER_EXIT=%s\n' "$status" >>"$OUT/info/checks.txt"
grep -E 'STAGE=|NP_INIT_RETURN=|CAPTURE_COMPLETE=|warning: Cannot insert breakpoint|Cannot access memory' "$OUT/gdb.log" | tail -18 || true
if command -v xdotool >/dev/null && command -v import >/dev/null; then
    wid="$(xdotool search --onlyvisible --name 'shadPS4' 2>/dev/null | tail -n 1 || true)"
    if [[ -n "$wid" ]]; then
        import -window "$wid" "$OUT/screenshots/post-detach.png" 2>/dev/null || true
    fi
fi
{
  const trace="$(grep -c '^HLE_RESULT ' "$OUT/gdb.log" 2>/dev/null || true)"
  printf 'NP_HLE_RETURN_RECORDS=%s\n' "$trace"
  grep -E 'TRACE_STAGE=|HLE_RESULT |NP_INIT_RETURN=|CAPTURE_COMPLETE=' "$OUT/gdb.log" | tail -30 || true
} >"$OUT/info/trace-summary.txt"
echo 'NO_UNRELATED_PROCESSES_STOPPED; ORIGINAL_BUILDS_AND_PROFILES_UNTOUCHED'
