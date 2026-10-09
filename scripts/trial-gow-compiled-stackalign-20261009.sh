#!/usr/bin/env bash
set -Eeuo pipefail
umask 077
ulimit -c 0 || true

stamp="$(date +%Y%m%d-%H%M%S)"
src="$HOME/shadps4-esde-verified-builds/20261009-173836/source"
build="$HOME/shadps4-esde-verified-builds/20261009-174801/build"
live="$HOME/Applications/shadps4/shadps4"
candidate="$build/shadps4"
trial="$HOME/Applications/shadps4-gow-stackalign-trial-$stamp"
work="$HOME/shadps4-gow-stackalign-trial-evidence-$stamp"
report="$HOME/shadps4-gow-stackalign-trial-$stamp.tar.gz"
live_sha="faca7d7ec1b375afdfac3d35b396a33092d3d56fd917ced8a67c9dc6e8e99834"
source_sha="4eb9fc5f92188abbb30eb2cc55980e922b2a0ec0"
source_file_sha="05acbce16dc315c5abaccb5190011ceed6ad848a"
status="FAILED"
stage="preflight"
mkdir -p "$work"
finish() {
  rc=$?
  trap - EXIT
  set +e
  {
    echo "RESULT=$status"
    echo "EXIT_CODE=$rc"
    echo "STAGE=$stage"
    echo "TRIAL=$trial/shadps4"
    echo "INSTALLED_SHA256=$(sha256sum "$live" 2>/dev/null | awk '{print $1}')"
    echo "PRODUCTION_ESDE=UNMODIFIED"
    echo "SSH_SESSION=UNCHANGED"
  } > "$work/summary.txt"
  tar -czf "$report" -C "$work" . >/dev/null 2>&1 || true
  echo
  echo "RESULT=$status"
  echo "REPORT=$report"
  if [[ -x "$trial/shadps4" ]]; then echo "TRIAL_BINARY=$trial/shadps4"; fi
  echo 'PRODUCTION_ESDE_AND_SSH=UNCHANGED'
}
trap finish EXIT
exec > >(tee "$work/console.log") 2>&1

echo '=== GOD OF WAR: REUSE COMPILED STACK-ALIGNMENT FIX / DO NOT REBUILD ==='
for cmd in git nm objdump gdb python3 sha256sum timeout cmp; do
  command -v "$cmd" >/dev/null || { echo "Missing executable: $cmd"; exit 1; }
done
[[ -d "$src/.git" && -x "$candidate" && -x "$live" ]] || {
  echo 'Missing the compiled candidate or production baseline'; exit 1;
}
[[ "$(git -C "$src" rev-parse HEAD)" == "$source_sha" ]] || {
  echo 'Source revision differs from the proven integration'; exit 1;
}
[[ "$(git hash-object "$src/src/core/libraries/kernel/process.cpp")" == "$source_file_sha" ]] || {
  echo 'Original source was not restored or was edited since the last test'; exit 1;
}
[[ -z "$(git -C "$src" status --porcelain --untracked-files=no)" ]] || {
  echo 'Unexpected source modifications. No trial will be started'; exit 1;
}
[[ "$(sha256sum "$live" | awk '{print $1}')" == "$live_sha" ]] || {
  echo 'Installed emulator changed. No trial will be started'; exit 1;
}
[[ -x "$build/cpu-id-runtime/bin64/drrun" &&
   -f "$build/cpu-id-runtime/libshadps4_cpu_id.so" ]] || {
  echo 'CPU-ID translation runtime missing from completed build'; exit 1;
}
if pgrep -x shadps4 >/dev/null || pgrep -x drrun >/dev/null; then
  echo 'Another emulator game is running. Close it normally before retrying.'
  exit 1
fi

stage="prove old breakpoint was real entry and compare assembly"
python3 - "$live" "$candidate" "$work" <<'SYMBOLS'
import pathlib, re, subprocess, sys
live,candidate,work=sys.argv[1:]
def find_symbol(binary):
    output=subprocess.run(['nm','-anC','--defined-only',binary],
                          text=True,stdout=subprocess.PIPE,stderr=subprocess.PIPE,check=True).stdout
    # Compiler-generated cold-code and lambda clones are not the actual
    # function entry. Match only the complete demangled function signature.
    expected = ('Libraries::Kernel::sceKernelLoadStartModule(char const*, '
                'unsigned long, void const*, unsigned int, void const*, int*)')
    matches=[]
    for line in output.splitlines():
        m=re.match(r'^\s*([0-9a-fA-F]+)\s+([TtWw])\s+(.*)$',line)
        if m and m.group(3).strip() == expected:
            matches.append((int(m.group(1),16),m.group(3)))
    if len(matches)!=1:
        raise RuntimeError(f'{binary}: exact function entry count={len(matches)}')
    return matches[0]
def disassemble(binary, addr):
    return subprocess.run(['objdump','-d','-C',f'--start-address={addr}',
                           f'--stop-address={addr+160}',binary],
                          text=True,stdout=subprocess.PIPE,stderr=subprocess.PIPE,check=True).stdout
old_addr,old_symbol=find_symbol(live)
new_addr,new_symbol=find_symbol(candidate)
old_code=disassemble(live,old_addr)
new_code=disassemble(candidate,new_addr)
pathlib.Path(work,'original-prologue.txt').write_text(
    f'SYMBOL={old_symbol}\nADDRESS={old_addr:#x}\n'+old_code)
pathlib.Path(work,'candidate-prologue.txt').write_text(
    f'SYMBOL={new_symbol}\nADDRESS={new_addr:#x}\n'+new_code)
if old_addr != 0x82a750:
    raise RuntimeError(f'Previous debugger breakpoint was 0x82a750; original function symbol is {old_addr:#x}, so prior alignment is not proven.')
def has_align(code):
    return any(re.search(r'\band[a-z]*\b',line) and '%rsp' in line and
               any(x in line.lower() for x in ('$0xfffffffffffffff0','$0xfffffff0','$-0x10','$-16'))
               for line in code.splitlines()[:18])
old_align=has_align(old_code)
new_align=has_align(new_code)
pathlib.Path(work,'assembly-verdict.txt').write_text(
    f'ORIGINAL_FUNCTION_ENTRY={old_addr:#x}\n'
    f'PATCHED_FUNCTION_ENTRY={new_addr:#x}\n'
    f'ORIGINAL_HAS_STACK_REALIGN={old_align}\n'
    f'PATCHED_HAS_STACK_REALIGN={new_align}\n'
    f'ORIGINAL_BREAKPOINT_RSP_MOD16=0\nEXPECTED_SYSV_ENTRY_RSP_MOD16=8\n')
print(f'ORIGINAL_FUNCTION_ENTRY={old_addr:#x}; GDB_ENTRY_BREAK=0x82a750')
print(f'ORIGINAL_HAS_STACK_REALIGN={old_align}')
print(f'PATCHED_HAS_STACK_REALIGN={new_align}')
if old_align or not new_align:
    raise RuntimeError('Unable to isolate the compiled alignment change: do not run trial')
SYMBOLS

stage="stage already compiled candidate without touching ES-DE"
cand_sha="$(sha256sum "$candidate" | awk '{print $1}')"
[[ "$cand_sha" != "$live_sha" ]] || {
  echo 'Built executable matches original; patched candidate is not present'; exit 1
}
mkdir -p "$trial"
cp -a "$candidate" "$trial/shadps4"
cp -a "$build/cpu-id-runtime" "$trial/cpu-id-runtime"
cmp "$candidate" "$trial/shadps4"
timeout 15s "$trial/shadps4" --help > "$work/cli-smoke.txt" 2>&1
grep -q -- '--cpu-id-mode' "$work/cli-smoke.txt"
sha256sum "$live" "$trial/shadps4" > "$work/binary-checksums.txt"
echo "STAGED_TRIAL=$trial/shadps4"

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
handle SIGUSR2 nostop noprint pass
set $gow_fmt_seen = 0
catch signal SIGSEGV
commands
silent
python
import gdb,os
pc=int(gdb.parse_and_eval('$pc'))
sym=gdb.execute('info symbol %#x' % pc,to_string=True).strip()
name=gdb.selected_frame().name() or ''
if 'parse_format_string' in sym+name and 'fmt::' in sym+name:
    gdb.set_convenience_variable('gow_fmt_seen',1)
    file=os.path.join(os.environ['GOW_TRIAL_WORK'],'original-fmt-crash.txt')
    with open(file,'w') as output:
        output.write('ORIGINAL_FMT_SIGSEGV_PC=%#x\nSYMBOL=%s\nFUNCTION=%s\n' % (pc,sym,name))
        for cmd in ('info registers','bt 45','info proc mappings'):
            try: output.write('\n'+cmd+'\n'+gdb.execute(cmd,to_string=True))
            except Exception as e: output.write(repr(e))
end
if $gow_fmt_seen
    quit
end
continue
end
break unreachable_impl
commands
silent
printf "\n===== OTHER_FATAL_ASSERT =====\n"
bt 55
info registers
quit
end
run
printf "\n===== PROGRAM_RETURNED_NO_CAPTURE =====\n"
info program
GDB

stage="test God of War with compiled realignment fix in native mode"
python3 - "$trial/shadps4" "$work" <<'RUN'
import glob,os,re,signal,subprocess,sys
from pathlib import Path
binary,work=sys.argv[1:]
env=os.environ.copy()
sources=[]
for fp in glob.glob('/proc/[0-9]*/comm'):
    try:
        pid=int(fp.split('/')[2])
        if os.stat(fp).st_uid!=os.getuid(): continue
        label=Path(fp).read_text().strip().lower()
        if not ('es-de' in label or 'sunshine' in label): continue
        values={}
        for item in Path(f'/proc/{pid}/environ').read_bytes().split(b'\x00'):
            if b'=' in item:
                k,v=item.split(b'=',1)
                values[k.decode(errors='replace')]=v.decode(errors='surrogateescape')
        if values.get('DISPLAY') or values.get('WAYLAND_DISPLAY'):
            sources.append((0 if 'es-de' in label else 1,pid,label,values))
    except (OSError,ValueError,UnicodeError):
        pass
sources.sort()
if sources:
    _,pid,label,values=sources[0]
    allow=re.compile(r'^(DISPLAY|WAYLAND_DISPLAY|XDG_RUNTIME_DIR|XAUTHORITY|XDG_DATA_HOME|XDG_SESSION_TYPE|DBUS_SESSION_BUS_ADDRESS|PATH|LD_LIBRARY_PATH|PULSE_SERVER|SDL_.*|VK_.*|RADV_.*|MESA_.*|AMD_.*)$')
    for k,v in values.items():
        if allow.fullmatch(k): env[k]=v
    origin=f'{label},pid={pid}'
else:
    if not env.get('DISPLAY') and Path('/tmp/.X11-unix/X0').exists():env['DISPLAY']=':0'
    env.setdefault('XDG_RUNTIME_DIR',f'/run/user/{os.getuid()}')
    origin='SSH_FALLBACK'
if not (env.get('DISPLAY') or env.get('WAYLAND_DISPLAY')):
    raise SystemExit('No GUI environment; keep Moonlight/ES-DE connected')
env['GOW_TRIAL_WORK']=work
env['SHADPS4_CPU_ID_MODE']='native'
print('GUI='+origin+'; CPU_ID=native; GAME=CUSA34384',flush=True)
cmd=['gdb','-nx','-q','-batch','-x',str(Path(work)/'trial.gdb'),
     '--args',binary,'--cpu-id-mode','native','--game','CUSA34384','--fullscreen','true']
with open(Path(work)/'gdb-trial.log','w') as out:
    proc=subprocess.Popen(cmd,env=env,stdout=out,stderr=subprocess.STDOUT,start_new_session=True)
    timed_out=False
    try:proc.wait(timeout=95)
    except subprocess.TimeoutExpired:
        timed_out=True
        try:os.killpg(proc.pid,signal.SIGTERM);proc.wait(timeout=8)
        except (ProcessLookupError,subprocess.TimeoutExpired):
            try:os.killpg(proc.pid,signal.SIGKILL)
            except ProcessLookupError:pass
            proc.wait()
Path(work,'trial-exit.txt').write_text(
    f'EXIT_CODE={proc.returncode}\nTIME_LIMIT_REACHED={timed_out}\n')
print(f'TRIAL_GDB_EXIT={proc.returncode}; TIME_LIMIT_REACHED={timed_out}',flush=True)
RUN

stage="classify test and collect logs"
logdir="${XDG_DATA_HOME:-$HOME/.local/share}/shadPS4/log"
if [[ -f "$logdir/CUSA34384.log" ]]; then
  tail -n 400 "$logdir/CUSA34384.log" > "$work/gow-game-log.txt"
fi
if [[ -s "$work/original-fmt-crash.txt" ]]; then
  status="ORIGINAL_FMT_CRASH_STILL_PRESENT"
  echo 'ORIGINAL_FMT_CRASH=REPRODUCED'
  sed -n '1,28p' "$work/original-fmt-crash.txt"
elif grep -q 'OTHER_FATAL_ASSERT' "$work/gdb-trial.log"; then
  status="ORIGINAL_FMT_CRASH_NOT_SEEN_OTHER_FATAL"
  echo 'ORIGINAL_FMT_CRASH=NOT_SEEN; OTHER_FATAL=YES'
  sed -n '/OTHER_FATAL_ASSERT/,+35p' "$work/gdb-trial.log"
elif grep -q 'GAME=never' "$work/gdb-trial.log"; then
  status="GAME_DID_NOT_LAUNCH"
else
  status="NO_FMT_CRASH_CAPTURED_IN_TRIAL_WINDOW"
  echo 'ORIGINAL_FMT_CRASH=NOT_CAPTURED; REAL_GAMEPLAY_STILL_UNVERIFIED'
  tail -n 30 "$work/gdb-trial.log"
fi
[[ "$(sha256sum "$live" | awk '{print $1}')" == "$live_sha" ]] || {
  echo 'ALERT: installed ES-DE binary unexpectedly changed'; exit 1
}
echo 'The production ES-DE executable and SSH session remain unchanged.'
