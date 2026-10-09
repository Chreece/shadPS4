#!/usr/bin/env bash
set -Eeuo pipefail
umask 077
ulimit -c 0 || true

stamp="$(date +%Y%m%d-%H%M%S)"
src="$HOME/shadps4-esde-verified-builds/20261009-173836/source"
build="$HOME/shadps4-esde-verified-builds/20261009-174801/build"
file="$src/src/core/libraries/kernel/process.cpp"
trial="$HOME/Applications/shadps4-gow-nullguard-trial-$stamp"
work="$HOME/shadps4-gow-nullguard-evidence-$stamp"
report="$HOME/shadps4-gow-nullguard-$stamp.tar.gz"
original_sha="05acbce16dc315c5abaccb5190011ceed6ad848a"
source_head="4eb9fc5f92188abbb30eb2cc55980e922b2a0ec0"
installed_sha="faca7d7ec1b375afdfac3d35b396a33092d3d56fd917ced8a67c9dc6e8e99834"
installed="$HOME/Applications/shadps4/shadps4"
modified=0
status="FAILED"
step="preflight"
mkdir -p "$work"
finish() {
  local rc=$?
  trap - EXIT
  set +e
  if (( modified == 1 )); then
    cp -p -- "$work/process.cpp.original" "$file" &&
      touch "$file" &&
      echo 'ORIGINAL_SOURCE=RESTORED' ||
      echo "WARNING: couldn't restore source; backup at $work/process.cpp.original"
  fi
  {
    echo "STATUS=$status"
    echo "LAST_STEP=$step"
    echo "EXIT_CODE=$rc"
    echo "STAGED_BINARY=$trial/shadps4"
    echo "PRODUCTION_BINARY=$installed"
    echo "PRODUCTION_BINARY=UNMODIFIED"
    echo "SSH_SESSION=UNCHANGED"
  } > "$work/summary.txt"
  tar -czf "$report" -C "$work" . || true
  if ((modified == 0)) || [[ "$(git hash-object "$file" 2>/dev/null)" == "$original_sha" ]]; then
    rm -rf -- "$work" 
  fi
  echo
  echo "RESULT=$status"
  echo "REPORT=$report"
  echo "TRIAL_BINARY=$trial/shadps4"
  echo "SSH_SESSION=UNCHANGED"
}
trap finish EXIT
exec > >(tee "$work/console.log") 2>&1

echo '=== God of War: build the minimal null-filename guard and test separately ==='
for cmd in cmake git python3 gdb timeout sha256sum cmp; do
  command -v "$cmd" >/dev/null || { echo "MISSING_TOOL=$cmd"; exit 1; }
done
[[ -f "$file" && -x "$build/shadps4" && -d "$src/.git" ]] || {
  echo 'The verified source/build layout is missing'; exit 1;
}
[[ "$(git -C "$src" rev-parse HEAD)" == "$source_head" ]] || {
  echo 'Integrated source revision changed; refusing to apply an old patch'; exit 1;
}
[[ "$(git hash-object "$file")" == "$original_sha" ]] || {
  echo 'process.cpp differs from the source reviewed for this fix'; exit 1;
}
[[ -z "$(git -C "$src" status --porcelain --untracked-files=no)" ]] || {
  echo 'Source has local changes; preserving them'; exit 1;
}
[[ "$(sha256sum "$installed" | awk '{print $1}')" == "$installed_sha" ]] || {
  echo 'The working ES-DE executable differs from the recorded baseline'; exit 1;
}
[[ -x "$build/cpu-id-runtime/bin64/drrun" &&
   -f "$build/cpu-id-runtime/libshadps4_cpu_id.so" ]] || {
  echo 'Bundled CPU-ID runtime missing'; exit 1;
}
if pgrep -x shadps4 >/dev/null || pgrep -x drrun >/dev/null; then
  echo 'An emulator session is already running. Close it normally, then retry.'
  exit 1
fi
sha256sum "$installed" > "$work/production-before.sha256"
cp -p "$file" "$work/process.cpp.original"

step="apply exact reviewed null guard to local build source"
modified=1
python3 - "$file" <<'PATCH_SOURCE'
from pathlib import Path
import sys
path = Path(sys.argv[1])
source = path.read_text()
old = '''s32 PS4_SYSV_ABI sceKernelLoadStartModule(const char* moduleFileName, u64 args, const void* argp,
                                          u32 flags, const void* pOpt, s32* pRes) {
    LOG_INFO(Lib_Kernel, "called filename = {}, args = {}", moduleFileName, args);'''
new = '''s32 PS4_SYSV_ABI sceKernelLoadStartModule(const char* moduleFileName, u64 args, const void* argp,
                                          u32 flags, const void* pOpt, s32* pRes) {
    // Reject a null filename before the formatter or std::string can read it.
    if (moduleFileName == nullptr) {
        LOG_ERROR(Lib_Kernel, "sceKernelLoadStartModule called with null module filename");
        return ORBIS_KERNEL_ERROR_EFAULT;
    }
    LOG_INFO(Lib_Kernel, "called filename = {}, args = {}", moduleFileName, args);'''
if source.count(old) != 1:
    raise SystemExit('SAFE_STOP: the reviewed function text has changed')
path.write_text(source.replace(old, new))
patched = path.read_text()
assert patched.index("if (moduleFileName == nullptr)") < patched.index("LOG_INFO(Lib_Kernel, \"called filename")
assert patched.index("return ORBIS_KERNEL_ERROR_EFAULT;") < patched.index("LOG_INFO(Lib_Kernel, \"called filename")
print('NULL_GUARD_STATIC_CHECK=PASS')
PATCH_SOURCE
git -C "$src" diff -- src/core/libraries/kernel/process.cpp > "$work/nullguard.patch"
git -C "$src" diff --check -- src/core/libraries/kernel/process.cpp

step="incrementally build production shadps4 executable (no test suite)"
if ! cmake --build "$build" --target shadps4 --parallel 5 > "$work/build.log" 2>&1; then
  tail -n 100 "$work/build.log"
  echo 'BUILD=FAILED'
  exit 1
fi
echo 'BUILD=PASS'

step="stage isolated trial executable and CPU-ID translation bundle"
mkdir -p "$trial"
cp -a "$build/shadps4" "$trial/shadps4"
cp -a "$build/cpu-id-runtime" "$trial/cpu-id-runtime"
cmp "$build/shadps4" "$trial/shadps4"
timeout 20s "$trial/shadps4" --help > "$work/cli.txt" 2>&1
grep -q -- '--cpu-id-mode' "$work/cli.txt"
sha256sum "$trial/shadps4" > "$work/trial.sha256"
echo "STAGED=$trial/shadps4"

# Native mode first: compare against the crash reproduced without DynamoRIO.
cat > "$work/capture.gdb" <<'GDB'
set pagination off
set confirm off
set print thread-events off
set breakpoint pending on
set debuginfod enabled off
set follow-exec-mode same
handle SIGSEGV nostop noprint pass
handle SIGBUS nostop noprint pass
handle SIGILL nostop noprint pass
handle SIGUSR1 nostop noprint pass
# Confirm whether the guest passed a null filename at the function boundary.
break sceKernelLoadStartModule if $rdi == 0
commands
silent
printf "\n===== NULL_MODULE_FILENAME_ARGUMENT =====\n"
bt 14
info registers rdi rsi rdx rcx r8 r9
continue
end
break unreachable_impl
commands
silent
printf "\n===== GOW_FATAL_BREAK =====\n"
bt 50
info registers
printf "\n===== END_FATAL_BREAK =====\n"
quit
end
run
printf "\n===== GOW_EXITED_WITHOUT_FATAL_BREAK =====\n"
info program
GDB

step="run isolated God of War trials under Sunshine display"
python3 - "$trial/shadps4" "$work" <<'RUN_GAME'
import glob, os, re, signal, subprocess, sys
from pathlib import Path
binary, work = sys.argv[1:]
env = os.environ.copy()
candidates = []
for proc in glob.glob('/proc/[0-9]*/comm'):
    try:
        pid = int(proc.split('/')[2])
        if os.stat(proc).st_uid != os.getuid():
            continue
        name = Path(proc).read_text().strip().lower()
        if 'sunshine' not in name and 'es-de' not in name:
            continue
        values = {}
        for item in Path(f'/proc/{pid}/environ').read_bytes().split(b'\0'):
            if b'=' in item:
                k, v = item.split(b'=', 1)
                values[k.decode(errors='replace')] = v.decode(errors='surrogateescape')
        if values.get('DISPLAY') or values.get('WAYLAND_DISPLAY'):
            candidates.append((0 if 'es-de' in name else 1, pid, name, values))
    except (OSError, ValueError):
        pass
candidates.sort()
if candidates:
    _, pid, name, values = candidates[0]
    allowed = re.compile(r'^(DISPLAY|WAYLAND_DISPLAY|XDG_RUNTIME_DIR|XAUTHORITY|DBUS_SESSION_BUS_ADDRESS|XDG_DATA_HOME|PATH|LD_LIBRARY_PATH|PULSE_SERVER|SDL_.*|VK_.*|RADV_.*|MESA_.*|AMD_.*)$')
    for k, v in values.items():
        if allowed.match(k):
            env[k] = v
    print(f'GRAPHICS_ENV={name} PID={pid}', flush=True)
else:
    if not env.get('DISPLAY') and Path('/tmp/.X11-unix/X0').exists():
        env['DISPLAY'] = ':0'
    env.setdefault('XDG_RUNTIME_DIR', f'/run/user/{os.getuid()}')
    print('GRAPHICS_ENV=SSH_FALLBACK', flush=True)
if not (env.get('DISPLAY') or env.get('WAYLAND_DISPLAY')):
    raise SystemExit('SAFE_STOP: no graphical display; staged binary remains available')

logdir = Path(env.get('XDG_DATA_HOME', str(Path.home()/'.local/share'))) / 'shadPS4' / 'log'
before = logdir/'CUSA34384.log'
if before.is_file():
    (Path(work)/'prior-gow-game-log.txt').write_bytes(before.read_bytes()[-120000:])

def test(mode, seconds=70):
    print(f'RUNNING_GOW_MODE={mode}', flush=True)
    env['SHADPS4_CPU_ID_MODE'] = mode
    command = ['gdb','-nx','-q','-batch','-x', str(Path(work)/'capture.gdb'),
               '--args',binary,'--cpu-id-mode',mode,'--game','CUSA34384','--fullscreen','true']
    with open(Path(work)/f'gdb-{mode}.txt','w') as log:
        process = subprocess.Popen(command, env=env, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
        timed_out = False
        try:
            process.wait(timeout=seconds)
        except subprocess.TimeoutExpired:
            timed_out = True
            try:
                os.killpg(process.pid, signal.SIGTERM)
                process.wait(timeout=7)
            except (ProcessLookupError, subprocess.TimeoutExpired):
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                process.wait()
    text = (Path(work)/f'gdb-{mode}.txt').read_text(errors='replace')
    fatal = '===== GOW_FATAL_BREAK =====' in text
    game_log = logdir/'CUSA34384.log'
    if game_log.is_file():
        (Path(work)/f'gow-{mode}.log').write_bytes(game_log.read_bytes()[-180000:])
    (Path(work)/f'result-{mode}.txt').write_text(
        f'MODE={mode}\nGDB_RETURN_CODE={process.returncode}\nTIMED_OUT={timed_out}\n'
        f'FATAL_BREAK={fatal}\nNULL_FILENAME_GUARD_HIT={"null module filename" in text}\n')
    print(f'{mode.upper()}_FATAL_BREAK={fatal}; TIME_LIMIT_REACHED={timed_out}', flush=True)
    return fatal

native_fatal = test('native')
if not native_fatal:
    test('auto')
else:
    print('AUTO_TEST_SKIPPED: native mode still hit a fatal assertion', flush=True)
RUN_GAME

step="verify production binary untouched"
after="$(sha256sum "$installed" | awk '{print $1}')"
[[ "$after" == "$installed_sha" ]] || {
  echo 'ALERT: installed binary checksum unexpectedly changed'; exit 1;
}
status="TRIAL_COMPLETED"
step="done"
echo 'Trial logs captured. The ES-DE installation was not replaced.'
