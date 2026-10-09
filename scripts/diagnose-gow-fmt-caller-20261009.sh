#!/usr/bin/env bash
set -Eeuo pipefail
umask 077
ulimit -c 0 || true

stamp="$(date +%Y%m%d-%H%M%S)"
work="$(mktemp -d "$HOME/.gow-fmt-debug-XXXXXX")"
report="$HOME/shadps4-gow-fmt-caller-$stamp.tar.gz"
bin="$HOME/Applications/shadps4/shadps4"
expected_sha="faca7d7ec1b375afdfac3d35b396a33092d3d56fd917ced8a67c9dc6e8e99834"
status="INCOMPLETE"
phase="preflight"
finish() {
  rc=$?
  trap - EXIT
  set +e
  {
    echo "STATUS=$status"
    echo "LAST_PHASE=$phase"
    echo "EXIT_CODE=$rc"
    echo "EMULATOR_AND_ESDE=UNMODIFIED"
    echo "SSH_SESSION=UNCHANGED"
  } > "$work/summary.txt"
  tar -czf "$report" -C "$work" . || true
  rm -rf -- "$work"
  printf '\nREPORT=%s\nSTATUS=%s\nSSH_SESSION=UNCHANGED\n' "$report" "$status"
}
trap finish EXIT

for cmd in python3 gdb sha256sum timeout pgrep awk tar; do
  command -v "$cmd" >/dev/null || { echo "Missing $cmd" | tee "$work/preflight.txt"; exit 1; }
done
[[ -f "$bin" && -x "$bin" ]] || { echo 'Installed shadPS4 executable missing'; exit 1; }
actual_sha="$(sha256sum "$bin" | awk '{print $1}')"
printf 'EXPECTED_SHA256=%s\nACTUAL_SHA256=%s\n' "$expected_sha" "$actual_sha" > "$work/preflight.txt"
[[ "$actual_sha" == "$expected_sha" ]] || { echo 'Installed binary changed; refusing mismatched symbol capture'; exit 1; }
if pgrep -x shadps4 >/dev/null; then
  echo 'A game is already running. Close it normally before capturing another crash.'
  exit 1
fi

# Capture the *original SIGSEGV*, before shadPS4's own handler formats it.
# Do not change the executable, launch arguments, save files or ES-DE.
cat > "$work/commands.gdb" <<'GDB'
set pagination off
set confirm off
set print pretty on
set print asm-demangle on
set print thread-events off
set debuginfod enabled off
set follow-fork-mode child
set detach-on-fork on
set follow-exec-mode same
handle SIGILL nostop noprint pass
handle SIGBUS nostop noprint pass
handle SIGUSR1 nostop noprint pass
handle SIGUSR2 nostop noprint pass
catch signal SIGSEGV
commands
silent
python
import gdb, os, traceback
report_dir = os.environ.get('GOW_FMT_WORK', '/tmp')
try:
    pc = gdb.selected_frame().pc()
    sym = gdb.execute('info symbol %#x' % pc, to_string=True).strip()
    interested = ('parse_format_string' in sym and 'fmt::' in sym)
    out = os.path.join(report_dir, 'fmt-segv-stack.txt')
    if interested and not os.path.exists(out):
        chunks = ['SIGNAL=SIGSEGV\n', 'PC=%#x\nSYMBOL=%s\n' % (pc, sym)]
        for title, cmd in [
            ('STACK', 'bt 60'),
            ('REGISTERS', 'info registers'),
            ('STACK_MEMORY', 'x/24gx $rsp'),
            ('DISASSEMBLY', 'x/20i $rip-24'),
            ('ALL_THREADS', 'thread apply all bt 12'),
            ('MAPPINGS', 'info proc mappings')]:
            try:
                chunks.append('\n--- %s ---\n%s\n' % (title, gdb.execute(cmd, to_string=True)))
            except Exception as e:
                chunks.append('\n--- %s ERROR ---\n%s\n' % (title, str(e)))
        with open(out, 'w') as f:
            f.write(''.join(chunks))
        print('CAPTURED_EXACT_FMT_SIGSEGV_PC=%#x' % pc)
except Exception as e:
    with open(os.path.join(report_dir, 'gdb-handler-error.txt'), 'a') as f:
        f.write(repr(e) + '\n' + traceback.format_exc())
end
continue
end
run
echo \n--- STOP REASON / LAST FRAME ---\n
info program
bt 35
info registers
GDB

phase="reproduce God of War under debugger with original CPU-ID auto mode"
export GOW_FMT_WORK="$work"
echo 'Starting God of War Ragnarök (CUSA34384) under GDB.'
echo 'Keep Moonlight connected. The debugger stops after the crash or the safety limit.'
python3 - "$bin" "$work" <<'PY'
import glob, os, re, subprocess, sys
binary, work = sys.argv[1:]
env = os.environ.copy()
found = []
for proc_comm in glob.glob('/proc/[0-9]*/comm'):
    try:
        pid = int(proc_comm.split('/')[2])
        if os.stat('/proc/%s' % pid).st_uid != os.getuid():
            continue
        name = open(proc_comm).read().strip().lower()
        if not ('es-de' in name or 'sunshine' in name):
            continue
        raw = open('/proc/%s/environ' % pid, 'rb').read().split(b'\x00')
        entries = {}
        for item in raw:
            if b'=' in item:
                k, v = item.split(b'=', 1)
                entries[k.decode('utf-8', 'replace')] = v.decode('utf-8', 'surrogateescape')
        if entries.get('DISPLAY') or entries.get('WAYLAND_DISPLAY'):
            found.append((name, pid, entries))
    except (PermissionError, FileNotFoundError, ProcessLookupError, ValueError):
        continue
found.sort(key=lambda v: (0 if 'es-de' in v[0] else 1, v[1]))
allowed = re.compile(r'^(DISPLAY|WAYLAND_DISPLAY|XDG_RUNTIME_DIR|XAUTHORITY|DBUS_SESSION_BUS_ADDRESS|XDG_SESSION_TYPE|XDG_DATA_HOME|PATH|LD_LIBRARY_PATH|PULSE_SERVER|SDL_.*|VK_.*|RADV_.*|MESA_.*|AMD_.*)$')
if found:
    name, pid, values = found[0]
    for k, v in values.items():
        if allowed.match(k):
            env[k] = v
    print('GUI_ENV_SOURCE=%s PID=%s' % (name, pid), flush=True)
else:
    if not env.get('DISPLAY') and os.path.exists('/tmp/.X11-unix/X0'):
        env['DISPLAY'] = ':0'
    env.setdefault('XDG_RUNTIME_DIR', '/run/user/%s' % os.getuid())
    print('GUI_ENV_SOURCE=SSH_FALLBACK', flush=True)
if not env.get('DISPLAY') and not env.get('WAYLAND_DISPLAY'):
    print('ERROR: No graphical display. Keep Moonlight/ES-DE open and retry.', flush=True)
    raise SystemExit(2)
with open(os.path.join(work, 'graphics-environment.txt'), 'w') as f:
    for k in ('DISPLAY','WAYLAND_DISPLAY','XDG_RUNTIME_DIR','XAUTHORITY','VK_DRIVER_FILES','SDL_VIDEODRIVER'):
        f.write('%s=%s\n' % (k, env.get(k, '<unset>')))
cmd = ['timeout', '--signal=INT', '--kill-after=8s', '150s', 'gdb', '-nx', '-q', '-batch',
       '-x', os.path.join(work, 'commands.gdb'), '--args', binary,
       '--game', 'CUSA34384', '--fullscreen', 'true']
print('GDB_LAUNCH=GoW CUSA34384 (automatic CPU-ID translation)', flush=True)
with open(os.path.join(work, 'debugger-session.log'), 'w') as log:
    rc = subprocess.call(cmd, env=env, stdout=log, stderr=subprocess.STDOUT)
print('DEBUGGER_EXIT_CODE=%s' % rc, flush=True)
with open(os.path.join(work, 'debugger-exit.txt'), 'w') as f:
    f.write('EXIT_CODE=%s\n' % rc)
PY

phase="summarize exact crash"
logdir="${XDG_DATA_HOME:-$HOME/.local/share}/shadPS4/log"
if [[ -f "$logdir/CUSA34384.log" ]]; then
  tail -n 300 "$logdir/CUSA34384.log" > "$work/gow-game-log.txt"
fi
if [[ -s "$work/fmt-segv-stack.txt" ]]; then
  status="FMT_CALLER_STACK_CAPTURED"
  echo 'EXACT_FORMATTING_FAULT_STACK=CAPTURED'
  sed -n '1,26p' "$work/fmt-segv-stack.txt"
else
  status="DEBUGGER_RAN_NO_FMT_STACK"
  echo 'No exact formatting frame captured; debugger output records why.'
  tail -n 50 "$work/debugger-session.log"
fi
