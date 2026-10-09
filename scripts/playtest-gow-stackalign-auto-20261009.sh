#!/usr/bin/env bash
set -Eeuo pipefail
umask 077
ulimit -c 0 || true

stamp="$(date +%Y%m%d-%H%M%S)"
trial="$HOME/Applications/shadps4-gow-stackalign-trial-20261009-203151/shadps4"
installed="$HOME/Applications/shadps4/shadps4"
trial_expected="2e49283a3ef69d300c3a89ccc51e24f255ed777036125b1fc5e4b13440513839"
installed_expected="faca7d7ec1b375afdfac3d35b396a33092d3d56fd917ced8a67c9dc6e8e99834"
report="$HOME/shadps4-gow-auto-playtest-$stamp.tar.gz"
work="$(mktemp -d "$HOME/.gow-auto-playtest-XXXXXX")"
result="NOT_RUN"
phase="preflight"
finish() {
    rc=$?
    trap - EXIT
    set +e
    {
        printf 'RESULT=%s\nEXIT_CODE=%s\nPHASE=%s\n' "$result" "$rc" "$phase"
        printf 'TRIAL_BINARY=%s\n' "$trial"
        printf 'TRIAL_SHA256=%s\n' "$(sha256sum "$trial" 2>/dev/null | awk '{print $1}')"
        printf 'PRODUCTION_SHA256=%s\n' "$(sha256sum "$installed" 2>/dev/null | awk '{print $1}')"
        printf 'ESDE_INSTALLATION=UNMODIFIED\nSSH_SESSION=UNCHANGED\n'
    } > "$work/summary.txt"
    tar -czf "$report" -C "$work" . >/dev/null 2>&1 || true
    rm -rf -- "$work"
    printf '\nRESULT=%s\nREPORT=%s\nSSH_SESSION=UNCHANGED\n' "$result" "$report"
}
trap finish EXIT

echo '=== GOD OF WAR RAGNAROK: AUTOMATIC CPU-ID REAL GAMEPLAY TRIAL ==='
echo 'This launches ONLY the isolated patched executable, not ES-DE production.'
for cmd in python3 sha256sum readlink tar pgrep; do
    command -v "$cmd" >/dev/null || { echo "Missing $cmd"; exit 1; }
done
[[ -x "$trial" && -x "$installed" ]] || { echo 'Trial or installed binary missing'; exit 1; }
[[ "$(sha256sum "$trial" | awk '{print $1}')" == "$trial_expected" ]] || {
    echo 'Trial candidate has changed since its successful native-mode crash test'; exit 1;
}
[[ "$(sha256sum "$installed" | awk '{print $1}')" == "$installed_expected" ]] || {
    echo 'Working ES-DE production binary has changed. Refusing trial'; exit 1;
}
[[ "$(readlink -f "$trial")" != "$(readlink -f "$installed")" ]] || {
    echo 'Trial and installed executable resolve to the same path'; exit 1;
}
runtime="$(dirname "$trial")/cpu-id-runtime"
[[ -x "$runtime/bin64/drrun" &&
   -f "$runtime/libshadps4_cpu_id.so" &&
   -f "$runtime/lib64/release/libdynamorio.so" ]] || {
    echo 'Trial CPU-ID translation bundle is missing'; exit 1;
}
if pgrep -x shadps4 >/dev/null || pgrep -x drrun >/dev/null; then
    echo 'An emulator/game is running; close it normally before this trial.'
    exit 1
fi

phase="run patched trial with real Sunshine/Moonlight GPU and controller"
cat > "$work/run.py" <<'PY'
import glob, os, re, signal, subprocess, sys, time
from pathlib import Path

trial, outdir = sys.argv[1:]
out = Path(outdir)
env = os.environ.copy()
sources = []
for path in glob.glob('/proc/[0-9]*/comm'):
    try:
        pid = int(path.split('/')[2])
        if os.stat(path).st_uid != os.getuid():
            continue
        name = Path(path).read_text().strip().lower()
        if 'sunshine' not in name and 'es-de' not in name:
            continue
        values = {}
        for entry in Path(f'/proc/{pid}/environ').read_bytes().split(b'\0'):
            if b'=' in entry:
                key, value = entry.split(b'=', 1)
                values[key.decode(errors='replace')] = value.decode(errors='surrogateescape')
        if values.get('DISPLAY') or values.get('WAYLAND_DISPLAY'):
            sources.append((0 if 'es-de' in name else 1, pid, name, values))
    except (OSError, ValueError, UnicodeError):
        pass
sources.sort()
if sources:
    _, pid, name, values = sources[0]
    allowed = re.compile(
        r'^(DISPLAY|WAYLAND_DISPLAY|XAUTHORITY|XDG_RUNTIME_DIR|XDG_DATA_HOME|'
        r'XDG_SESSION_TYPE|DBUS_SESSION_BUS_ADDRESS|PATH|LD_LIBRARY_PATH|'
        r'PULSE_SERVER|SDL_.*|VK_.*|RADV_.*|MESA_.*|AMD_.*)$')
    for key, value in values.items():
        if allowed.fullmatch(key):
            env[key] = value
    gui_source = f'{name} PID={pid}'
else:
    gui_source = 'SSH_FALLBACK'
    if not env.get('DISPLAY') and Path('/tmp/.X11-unix/X0').exists():
        env['DISPLAY'] = ':0'
    env.setdefault('XDG_RUNTIME_DIR', f'/run/user/{os.getuid()}')

(out/'gui-context.txt').write_text(
    'SOURCE=' + gui_source + '\n' +
    'DISPLAY=' + str(env.get('DISPLAY')) + '\n' +
    'WAYLAND_DISPLAY=' + str(env.get('WAYLAND_DISPLAY')) + '\n' +
    'XDG_RUNTIME_DIR=' + str(env.get('XDG_RUNTIME_DIR')) + '\n')
if not (env.get('DISPLAY') or env.get('WAYLAND_DISPLAY')):
    raise SystemExit('SAFE_STOP: graphical Sunshine/ES-DE session unavailable.')

env['SHADPS4_CPU_ID_MODE'] = 'auto'
paths = []
for path in [
    Path(env.get('XDG_DATA_HOME') or str(Path.home()/'.local/share'))/'shadPS4'/'log',
    Path.home()/'.local/share/shadPS4/log',
    Path.cwd()/'user'/'log',
    Path(trial).parent/'user'/'log',
]:
    if path not in paths:
        paths.append(path)
snapshots = {}
for folder in paths:
    for filename in ('CUSA34384.log', 'shadps4.log'):
        file = folder / filename
        try:
            st = file.stat()
            snapshots[str(file)] = (st.st_ino, st.st_size)
        except OSError:
            pass

print('GUI_SOURCE='+gui_source, flush=True)
print('CPU_ID_MODE=auto; GAME=CUSA34384', flush=True)
print('PLAYTEST: use Moonlight to watch the game and try your controller.', flush=True)
print('PLAYTEST: exit the game normally when ready.', flush=True)
print('PLAYTEST: isolated trial ends automatically after a 10-minute safety limit.', flush=True)

start = time.monotonic()
cmd = [trial, '--cpu-id-mode', 'auto', '--game', 'CUSA34384', '--fullscreen', 'true']
timed_out = False
interrupted = False
with open(out/'emulator-stdout.log','wb') as log:
    p = subprocess.Popen(cmd, env=env, stdout=log, stderr=subprocess.STDOUT,
                         start_new_session=True)
    (out/'launched-process.txt').write_text(
        f'PID={p.pid}\nCOMMAND={cmd!r}\n')
    try:
        p.wait(timeout=600)
    except subprocess.TimeoutExpired:
        timed_out = True
    except KeyboardInterrupt:
        interrupted = True
    if timed_out or interrupted:
        # Only the process group started by this script, never ES-DE, Sunshine or SSH.
        for sig, grace in ((signal.SIGINT, 8), (signal.SIGTERM, 6), (signal.SIGKILL, 3)):
            if p.poll() is not None:
                break
            try:
                os.killpg(p.pid, sig)
                p.wait(timeout=grace)
            except ProcessLookupError:
                break
            except subprocess.TimeoutExpired:
                pass
    if p.poll() is None:
        try:
            os.killpg(p.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        p.wait()

# Bound archived console output even when the game logs heavily.
stdout_log = out/'emulator-stdout.log'
if stdout_log.stat().st_size > 2_000_000:
    with stdout_log.open('rb') as f:
        f.seek(-2_000_000, os.SEEK_END)
        last = f.read(2_000_000)
    stdout_log.write_bytes(last)

elapsed = round(time.monotonic() - start, 1)
(out/'playtest-result.txt').write_text(
    f'GAME=CUSA34384\nCPU_ID_MODE=auto\n'
    f'PROCESS_RETURN_CODE={p.returncode}\n'
    f'RUN_DURATION_SECONDS={elapsed}\n'
    f'SAFETY_LIMIT_REACHED={timed_out}\nINTERRUPTED={interrupted}\n'
    'GRAPHICS_AND_GAMEPLAY_MUST_BE_CONFIRMED_BY_USER=YES\n'
)
print(f'PROCESS_RETURN_CODE={p.returncode}; DURATION_SECONDS={elapsed}; LIMIT_REACHED={timed_out}', flush=True)

for folder in paths:
    for filename in ('CUSA34384.log', 'shadps4.log'):
        path = folder/filename
        if not path.is_file():
            continue
        try:
            st = path.stat()
            old = snapshots.get(str(path))
            with open(path,'rb') as f:
                if old and old[0] == st.st_ino and old[1] <= st.st_size:
                    start = old[1]
                    if st.st_size - start > 2_000_000:
                        start = st.st_size - 2_000_000
                else:
                    start = max(0,st.st_size-2_000_000)
                f.seek(start)
                data = f.read(2_000_000)
            label = str(path).replace('/', '_').strip('_')
            (out / (label+'.tail.txt')).write_bytes(data)
        except OSError as err:
            (out/'log-errors.txt').open('a').write(f'{path}: {err}\n')

try:
    result = subprocess.run(
        ['journalctl','-k','--no-pager','--since','-12 minutes'],
        text=True, capture_output=True, timeout=15, check=False)
    lines = [line for line in result.stdout.splitlines()
             if re.search('amdgpu|radv|drm|gpu.reset|gpu.hang|segfault|oom',line,re.I)]
    (out/'kernel-gpu.txt').write_text('\n'.join(lines[-500:])+'\n')
except (OSError, subprocess.TimeoutExpired):
    pass
PY
python3 "$work/run.py" "$trial" "$work"
phase="verify installed executable still identical"
[[ "$(sha256sum "$installed" | awk '{print $1}')" == "$installed_expected" ]] || {
    echo 'ALERT: production binary unexpectedly changed'; exit 1;
}
result="TRIAL_RAN_REPORT_READY"
echo 'The trial is complete. The installed ES-DE executable was not replaced.'
