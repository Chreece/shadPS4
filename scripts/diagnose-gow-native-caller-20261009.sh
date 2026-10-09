#!/usr/bin/env bash
set -Eeuo pipefail
umask 077
ulimit -c 0 || true

stamp="$(date +%Y%m%d-%H%M%S)"
work="$(mktemp -d "$HOME/.gow-native-debug-XXXXXX")"
archive="$HOME/shadps4-gow-native-stack-$stamp.tar.gz"
binary="$HOME/Applications/shadps4/shadps4"
expected_sha="faca7d7ec1b375afdfac3d35b396a33092d3d56fd917ced8a67c9dc6e8e99834"
status="FAILED"
phase="preflight"
finish() {
  rc=$?
  trap - EXIT
  set +e
  {
    printf 'STATUS=%s\nEXIT_CODE=%s\nLAST_PHASE=%s\n' "$status" "$rc" "$phase"
    echo 'EXECUTION_MODE=native (no DynamoRIO)'
    echo 'INSTALLED_BINARY=UNMODIFIED'
    echo 'ESDE=UNMODIFIED'
    echo 'SSH_SESSION=UNCHANGED'
  } > "$work/summary.txt"
  tar -czf "$archive" -C "$work" . || true
  rm -rf -- "$work"
  printf '\nRESULT=%s\nREPORT=%s\nSSH_SESSION=UNCHANGED\n' "$status" "$archive"
}
trap finish EXIT

for cmd in gdb python3 sha256sum timeout pgrep tar; do
  command -v "$cmd" >/dev/null || { echo "Missing $cmd" | tee "$work/preflight.txt"; exit 1; }
done
[[ -x "$binary" ]] || { echo "Missing executable: $binary"; exit 1; }
actual="$(sha256sum "$binary" | awk '{print $1}')"
printf 'EXPECTED_SHA256=%s\nACTUAL_SHA256=%s\n' "$expected_sha" "$actual" > "$work/preflight.txt"
[[ "$actual" == "$expected_sha" ]] || {
  echo 'Installed emulator changed; captured symbols would not match. Safe stop.'
  exit 1
}
if pgrep -x shadps4 >/dev/null || pgrep -f '[/]shadps4 --game' >/dev/null; then
  echo 'Another game is already running. Do not interrupt it; close it normally first.'
  exit 1
fi

# Native execution removes DynamoRIO's translated code from the call stack.
# Let ordinary guest SIGSEGVs reach shadPS4's own handler, then break exactly
# before the fatal assertion shuts down the emulator.
cat > "$work/native.gdb" <<'GDB'
set pagination off
set confirm off
set print pretty on
set print asm-demangle on
set print thread-events off
set debuginfod enabled off
set breakpoint pending on
set follow-fork-mode parent
set detach-on-fork on
set follow-exec-mode same
handle SIGSEGV nostop noprint pass
handle SIGBUS nostop noprint pass
handle SIGILL nostop noprint pass
handle SIGUSR1 nostop noprint pass
handle SIGUSR2 nostop noprint pass
break unreachable_impl
commands
silent
printf "\n===== NATIVE_FATAL_UNREACHABLE_BREAK =====\n"
info program
bt 70
printf "\n===== REGISTERS =====\n"
info registers
printf "\n===== CODE =====\n"
x/28i $rip
printf "\n===== STACK =====\n"
x/24gx $rsp
printf "\n===== THREADS =====\n"
thread apply all bt 8
printf "\n===== MAPS =====\n"
info proc mappings
printf "\n===== END_FATAL_CAPTURE =====\n"
quit
end
run
printf "\n===== PROGRAM_EXITED_WITHOUT_UNREACHABLE_BREAK =====\n"
info program
bt 20
GDB

phase="launch God of War under native CPU-ID mode and record the fatal caller"
echo 'Running God of War Ragnarök CUSA34384 under GDB, CPU-ID mode NATIVE.'
echo 'This uses your existing executable and game data; it will not alter ES-DE.'
echo 'Moonlight/ES-DE should remain connected for the graphical output.'
python3 - "$binary" "$work" <<'PY'
import glob, os, re, subprocess, sys
binary, work = sys.argv[1:]
env = os.environ.copy()
sources = []
for path in glob.glob('/proc/[0-9]*/comm'):
    try:
        pid = int(path.split('/')[2])
        if os.stat(path).st_uid != os.getuid():
            continue
        name = open(path, encoding='utf-8').read().strip().lower()
        if not ('es-de' in name or 'sunshine' in name):
            continue
        vals = {}
        with open(f'/proc/{pid}/environ', 'rb') as f:
            for item in f.read().split(b'\x00'):
                if b'=' in item:
                    k, v = item.split(b'=', 1)
                    vals[k.decode(errors='replace')] = v.decode(errors='surrogateescape')
        if vals.get('DISPLAY') or vals.get('WAYLAND_DISPLAY'):
            sources.append((0 if 'es-de' in name else 1, pid, name, vals))
    except (OSError, ValueError, UnicodeError):
        continue
sources.sort()
allowed = re.compile(r'^(DISPLAY|WAYLAND_DISPLAY|XDG_RUNTIME_DIR|XAUTHORITY|DBUS_SESSION_BUS_ADDRESS|XDG_SESSION_TYPE|XDG_DATA_HOME|PATH|LD_LIBRARY_PATH|PULSE_SERVER|SDL_.*|VK_.*|RADV_.*|MESA_.*|AMD_.*)$')
if sources:
    _, pid, name, values = sources[0]
    for k, v in values.items():
        if allowed.match(k):
            env[k] = v
    source = f'{name} PID={pid}'
else:
    source = 'SSH_SESSION'
    if not env.get('DISPLAY') and os.path.exists('/tmp/.X11-unix/X0'):
        env['DISPLAY'] = ':0'
    env.setdefault('XDG_RUNTIME_DIR', f'/run/user/{os.getuid()}')
with open(os.path.join(work, 'graphics-environment.txt'), 'w') as f:
    f.write(f'ENV_SOURCE={source}\n')
    for k in ('DISPLAY','WAYLAND_DISPLAY','XDG_RUNTIME_DIR','XAUTHORITY','VK_DRIVER_FILES','SDL_VIDEODRIVER'):
        f.write(f'{k}={env.get(k, "<unset>")}\n')
if not (env.get('DISPLAY') or env.get('WAYLAND_DISPLAY')):
    print('No suitable graphical display found; keep Moonlight/ES-DE open.', flush=True)
    raise SystemExit(2)
env['SHADPS4_CPU_ID_MODE'] = 'native'
cmd = ['timeout', '--signal=INT', '--kill-after=10s', '180s',
       'gdb', '-nx', '-q', '-batch', '-x', os.path.join(work, 'native.gdb'),
       '--args', binary, '--cpu-id-mode', 'native', '--game', 'CUSA34384',
       '--fullscreen', 'true']
print(f'GUI_ENV_SOURCE={source}; NATIVE_CPU_ID=ON', flush=True)
print('DEBUGGER_COMMAND=gdb <installed binary> --cpu-id-mode native --game CUSA34384', flush=True)
with open(os.path.join(work, 'gdb-native-session.log'), 'w') as f:
    rc = subprocess.call(cmd, env=env, stdout=f, stderr=subprocess.STDOUT)
print(f'GDB_EXIT_CODE={rc}', flush=True)
with open(os.path.join(work, 'debugger-exit.txt'), 'w') as f:
    f.write(f'GDB_EXIT_CODE={rc}\n')
PY

phase="collect native crash result and game log"
logdir="${XDG_DATA_HOME:-$HOME/.local/share}/shadPS4/log"
if [[ -f "$logdir/CUSA34384.log" ]]; then
  tail -n 600 "$logdir/CUSA34384.log" > "$work/gow-game-log.txt"
fi
if grep -q 'NATIVE_FATAL_UNREACHABLE_BREAK' "$work/gdb-native-session.log"; then
  status="NATIVE_FATAL_STACK_CAPTURED"
  sed -n '/NATIVE_FATAL_UNREACHABLE_BREAK/,+46p' "$work/gdb-native-session.log"
elif grep -q 'PROGRAM_EXITED_WITHOUT_UNREACHABLE_BREAK' "$work/gdb-native-session.log"; then
  status="NATIVE_ENDED_WITHOUT_FATAL_ASSERT"
  tail -n 30 "$work/gdb-native-session.log"
else
  status="NATIVE_DIAGNOSTIC_NO_BREAK"
  tail -n 45 "$work/gdb-native-session.log"
fi
