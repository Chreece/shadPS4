#!/usr/bin/env bash
set -Eeuo pipefail
umask 077
ulimit -c 0 || true

stamp="$(date +%Y%m%d-%H%M%S)"
src="$HOME/shadps4-esde-verified-builds/20261009-173836/source"
build="$HOME/shadps4-esde-verified-builds/20261009-174801/build"
file="$src/src/core/libraries/kernel/process.cpp"
live="$HOME/Applications/shadps4/shadps4"
expected_src="4eb9fc5f92188abbb30eb2cc55980e922b2a0ec0"
expected_file="05acbce16dc315c5abaccb5190011ceed6ad848a"
expected_live="faca7d7ec1b375afdfac3d35b396a33092d3d56fd917ced8a67c9dc6e8e99834"
trial="$HOME/Applications/shadps4-gow-stackalign-trial-$stamp"
work="$HOME/shadps4-gow-stackalign-evidence-$stamp"
report="$HOME/shadps4-gow-stackalign-$stamp.tar.gz"
stage="preflight"
status="FAILED"
modified=0
mkdir -p "$work"
finish() {
  rc=$?
  trap - EXIT
  set +e
  restored=no
  if (( modified )); then
    if cp -p -- "$work/process.cpp.original" "$file" && touch "$file" &&
       [[ "$(git hash-object "$file")" == "$expected_file" ]]; then
      restored=yes
    fi
  else
    restored=not_modified
  fi
  {
    echo "STATUS=$status"
    echo "EXIT_CODE=$rc"
    echo "LAST_STAGE=$stage"
    echo "SOURCE_RESTORED=$restored"
    echo "TRIAL_BINARY=$trial/shadps4"
    echo "LIVE_BINARY=$live"
    echo "LIVE_BINARY_CHANGED=$(sha256sum "$live" 2>/dev/null | awk '{print $1}')"
    echo "SSH_SESSION=UNCHANGED"
  } > "$work/summary.txt"
  tar -czf "$report" -C "$work" . 2>/dev/null || true
  if [[ "$restored" != no ]]; then
    rm -rf -- "$work"
  else
    echo "SOURCE_RESTORE_ERROR: backup preserved at $work/process.cpp.original"
  fi
  echo
  echo "RESULT=$status"
  echo "STEP=$stage"
  echo "REPORT=$report"
  if [[ -x "$trial/shadps4" ]]; then echo "TRIAL_BINARY=$trial/shadps4"; fi
  echo 'ESDE_BINARY=NOT_REPLACED'
  echo 'SSH_SESSION=UNCHANGED'
}
trap finish EXIT
exec > >(tee "$work/console.log") 2>&1
echo '=== GOD OF WAR: PROVE GUEST ABI STACK ALIGNMENT, THEN CONDITIONALLY TRIAL FIX ==='
for tool in git gdb python3 cmake objdump timeout sha256sum cmp tar; do
  command -v "$tool" >/dev/null || { echo "Missing tool: $tool"; exit 1; }
done
[[ -d "$src/.git" && -d "$build" && -f "$file" && -x "$live" ]] || {
  echo 'Expected verified source/build or live binary missing'; exit 1;
}
[[ "$(git -C "$src" rev-parse HEAD)" == "$expected_src" ]] || {
  echo 'Source revision changed; not applying an old patch'; exit 1;
}
[[ "$(git hash-object "$file")" == "$expected_file" ]] || {
  echo 'Kernel function differs from the reviewed source'; exit 1;
}
[[ -z "$(git -C "$src" status --porcelain --untracked-files=no)" ]] || {
  echo 'Source has uncommitted modifications; leaving untouched'; exit 1;
}
[[ "$(sha256sum "$live" | awk '{print $1}')" == "$expected_live" ]] || {
  echo 'Installed binary differs from crash baseline; leaving untouched'; exit 1;
}
[[ -x "$build/cpu-id-runtime/bin64/drrun" && -f "$build/cpu-id-runtime/libshadps4_cpu_id.so" ]] || {
  echo 'Bundled CPU-ID runtime missing'; exit 1;
}
if pgrep -x shadps4 >/dev/null || pgrep -x drrun >/dev/null; then
  echo 'Another game is running. Close it normally before retrying.'; exit 1;
fi
cp -p "$file" "$work/process.cpp.original"

# Reuse the running Sunshine / ES-DE graphical environment without touching it.
cat > "$work/run.py" <<'PY'
import glob, os, re, signal, subprocess, sys
from pathlib import Path
binary, script, work, name, limit = sys.argv[1:]
env = os.environ.copy()
sources = []
for proc in glob.glob('/proc/[0-9]*/comm'):
    try:
        pid = int(proc.split('/')[2])
        if os.stat(proc).st_uid != os.getuid():
            continue
        label = Path(proc).read_text().strip().lower()
        if not ('sunshine' in label or 'es-de' in label):
            continue
        e = {}
        for item in Path(f'/proc/{pid}/environ').read_bytes().split(b'\0'):
            if b'=' in item:
                k, v = item.split(b'=', 1)
                e[k.decode(errors='replace')] = v.decode(errors='surrogateescape')
        if e.get('DISPLAY') or e.get('WAYLAND_DISPLAY'):
            sources.append((0 if 'es-de' in label else 1, pid, label, e))
    except (OSError, UnicodeError, ValueError):
        pass
sources.sort()
if sources:
    _, pid, label, source = sources[0]
    allow = re.compile(r'^(DISPLAY|WAYLAND_DISPLAY|XAUTHORITY|XDG_RUNTIME_DIR|XDG_DATA_HOME|XDG_SESSION_TYPE|DBUS_SESSION_BUS_ADDRESS|PATH|LD_LIBRARY_PATH|PULSE_SERVER|SDL_.*|VK_.*|RADV_.*|MESA_.*|AMD_.*)$')
    for k, v in source.items():
        if allow.fullmatch(k):
            env[k] = v
    print(f'ENV_SOURCE={label}, PID={pid}',flush=True)
else:
    if not env.get('DISPLAY') and Path('/tmp/.X11-unix/X0').exists():
        env['DISPLAY'] = ':0'
    env.setdefault('XDG_RUNTIME_DIR', f'/run/user/{os.getuid()}')
    print('ENV_SOURCE=SSH_FALLBACK',flush=True)
if not (env.get('DISPLAY') or env.get('WAYLAND_DISPLAY')):
    raise SystemExit('SAFE_STOP: graphical session not found; connect Moonlight before running')
env['SHADPS4_CPU_ID_MODE'] = 'native'
env['GOW_ALIGN_DIR'] = work
cmd = ['gdb', '-nx', '-q', '-batch', '-x', script,
       '--args', binary, '--cpu-id-mode', 'native',
       '--game', 'CUSA34384', '--fullscreen', 'true']
print('RUN='+name+' CPU_ID=native',flush=True)
timed_out = False
with open(Path(work)/f'{name}.log','w') as output:
    child = subprocess.Popen(cmd, env=env, stdout=output,
                             stderr=subprocess.STDOUT, start_new_session=True)
    try:
        child.wait(timeout=int(limit))
    except subprocess.TimeoutExpired:
        timed_out = True
        try:
            os.killpg(child.pid, signal.SIGTERM)
            child.wait(timeout=7)
        except (ProcessLookupError, subprocess.TimeoutExpired):
            try: os.killpg(child.pid,signal.SIGKILL)
            except ProcessLookupError: pass
            child.wait()
(Path(work)/f'{name}-exit.txt').write_text(
    f'GDB_EXIT_CODE={child.returncode}\nTIMED_OUT={timed_out}\n')
print(f'{name.upper()}_GDB_EXIT={child.returncode},TIMED_OUT={timed_out}',flush=True)
PY

stage="measure stack alignment at unmodified syscall entry"
cat > "$work/entry.gdb" <<'GDB'
set pagination off
set confirm off
set print thread-events off
set debuginfod enabled off
set breakpoint pending on
handle SIGSEGV nostop noprint pass
handle SIGILL nostop noprint pass
handle SIGUSR1 nostop noprint pass
break sceKernelLoadStartModule
commands
silent
python
import gdb,os
rsp=int(gdb.parse_and_eval('$rsp'))
rdi=int(gdb.parse_and_eval('$rdi'))
path=os.path.join(os.environ['GOW_ALIGN_DIR'],'entry.txt')
text='ENTRY_RSP=%#x\nENTRY_RSP_MOD16=%d\nFILENAME_PTR=%#x\n' % (rsp,rsp&15,rdi)
try:
    data=bytes(gdb.selected_inferior().read_memory(rdi,100))
    text+='FILENAME_PREFIX=%r\n' % data.split(b'\0',1)[0]
except Exception as e:
    text+='FILENAME_READ_ERROR=%r\n' % e
with open(path,'w') as f:
    f.write(text)
print('ENTRY_RSP_MOD16=%d' % (rsp&15))
end
quit
end
run
GDB
python3 "$work/run.py" "$live" "$work/entry.gdb" "$work" entry 55
if [[ ! -s "$work/entry.txt" ]]; then
  tail -n 60 "$work/entry.log"
  echo 'No syscall-entry alignment evidence; refusing speculative modification.'
  status="NO_ENTRY_CAPTURE"
  exit 0
fi
cat "$work/entry.txt"
mod16="$(sed -n 's/^ENTRY_RSP_MOD16=//p' "$work/entry.txt" | head -1)"
if [[ "$mod16" == 8 ]]; then
  echo 'SysV guest-to-host entry alignment is normal (RSP mod 16 = 8).'
  echo 'The formatter misalignment has a different origin; do not apply this fix.'
  status="ENTRY_ALIGNMENT_NORMAL"
  exit 0
elif [[ "$mod16" != 0 ]]; then
  echo "Unexpected guest RSP mod16=$mod16; no patch applied."
  status="ENTRY_ALIGNMENT_UNEXPECTED"
  exit 0
fi
echo 'ENTRY_ALIGNMENT_MISMATCH_CONFIRMED: SysV function expects 8; guest supplied 0.'

stage="apply isolated stack-realignment candidate to local source"
modified=1
python3 - "$file" <<'PATCH'
from pathlib import Path
import sys
path=Path(sys.argv[1])
content=path.read_text()
old='''s32 PS4_SYSV_ABI sceKernelLoadStartModule(const char* moduleFileName, u64 args, const void* argp,
                                          u32 flags, const void* pOpt, s32* pRes) {'''
new='''// Realign the C++ stack for guest calls that violate x86-64 SysV stack alignment.
__attribute__((force_align_arg_pointer))
s32 PS4_SYSV_ABI sceKernelLoadStartModule(const char* moduleFileName, u64 args, const void* argp,
                                          u32 flags, const void* pOpt, s32* pRes) {'''
if content.count(old)!=1:
    raise SystemExit('SAFE_STOP: expected source signature changed')
path.write_text(content.replace(old,new))
assert path.read_text().count('force_align_arg_pointer')==1
print('CANDIDATE_SOURCE_PATCH=APPLIED')
PATCH
git -C "$src" diff --check -- src/core/libraries/kernel/process.cpp
git -C "$src" diff -- src/core/libraries/kernel/process.cpp > "$work/proposed-change.diff"

stage="compile only modified production target"
if ! cmake --build "$build" --target shadps4 --parallel 5 > "$work/build.log" 2>&1; then
  tail -n 120 "$work/build.log"
  echo 'TRIAL_BUILD=FAIL'; exit 1
fi
echo 'TRIAL_BUILD=PASS'

stage="verify actual compiler-generated realignment"
gdb -nx -q -batch -ex 'disassemble sceKernelLoadStartModule' \
   "$build/shadps4" > "$work/prologue-disassembly.txt" 2>&1
if ! sed -n '1,42p' "$work/prologue-disassembly.txt" | grep -E 'and[a-z]*.*%rsp' >/dev/null; then
  echo 'Compiler did not emit expected stack realignment; refusing trial'
  sed -n '1,48p' "$work/prologue-disassembly.txt"
  exit 1
fi
echo 'REALIGNMENT_PROLOGUE=VERIFIED'

stage="stage independent trial executable"
mkdir -p "$trial"
cp -a "$build/shadps4" "$trial/shadps4"
cp -a "$build/cpu-id-runtime" "$trial/cpu-id-runtime"
cmp "$build/shadps4" "$trial/shadps4"
timeout 18s "$trial/shadps4" --help > "$work/trial-help.txt" 2>&1
grep -q -- '--cpu-id-mode' "$work/trial-help.txt"
sha256sum "$trial/shadps4" > "$work/trial-sha256.txt"

stage="test God of War against original fmt crash (native CPU-ID)"
cat > "$work/trial.gdb" <<'GDB'
set pagination off
set confirm off
set print asm-demangle on
set print thread-events off
set debuginfod enabled off
set breakpoint pending on
handle SIGSEGV stop noprint pass
handle SIGILL nostop noprint pass
handle SIGBUS nostop noprint pass
handle SIGUSR1 nostop noprint pass
catch signal SIGSEGV
commands
silent
python
import gdb,os
name=gdb.selected_frame().name() or ''
if 'parse_format_string' in name and 'fmt::' in name:
    path=os.path.join(os.environ['GOW_ALIGN_DIR'],'trial-fmt-crash.txt')
    with open(path,'w') as f:
        f.write('FMT_CRASH_PC=%#x\n' % int(gdb.parse_and_eval('$pc')))
        f.write(gdb.execute('info registers',to_string=True))
        f.write(gdb.execute('bt 30',to_string=True))
    gdb.set_convenience_variable('gow_fmt_failed',1)
end
if $gow_fmt_failed
  quit
end
continue
end
break unreachable_impl
commands
silent
printf "\n===== TRIAL_FATAL_ASSERT =====\n"
bt 45
info registers
quit
end
set $gow_fmt_failed=0
run
printf "\n===== TRIAL_PROGRAM_RETURNED =====\n"
info program
GDB
python3 "$work/run.py" "$trial/shadps4" "$work/trial.gdb" "$work" trial 90
if [[ -s "$work/trial-fmt-crash.txt" ]]; then
  status="REALIGNMENT_TRIAL_FMT_CRASH_REMAINS"
  echo 'SAME_FMT_CRASH=YES'
elif grep -q 'TRIAL_FATAL_ASSERT' "$work/trial.log"; then
  status="REALIGNMENT_TRIAL_DIFFERENT_FATAL"
  echo 'ORIGINAL_FMT_CRASH=NOT_CAPTURED; DIFFERENT_FATAL=YES'
else
  status="REALIGNMENT_TRIAL_NO_FMT_CRASH_SEEN"
  echo 'ORIGINAL_FMT_CRASH=NOT_SEEN_DURING_TEST'
fi
tail -n 45 "$work/trial.log"
logdir="${XDG_DATA_HOME:-$HOME/.local/share}/shadPS4/log"
if [[ -f "$logdir/CUSA34384.log" ]]; then
  tail -n 350 "$logdir/CUSA34384.log" > "$work/gow-game-log.txt"
fi
echo 'The live ES-DE binary and its CPU runtime remain unchanged.'
