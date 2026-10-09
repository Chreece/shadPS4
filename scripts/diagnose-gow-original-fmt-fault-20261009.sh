#!/usr/bin/env bash
set -Eeuo pipefail
umask 077
ulimit -c 0 || true
stamp="$(date +%Y%m%d-%H%M%S)"
work="$(mktemp -d "$HOME/.gow-original-fault-XXXXXX")"
archive="$HOME/shadps4-gow-original-fault-$stamp.tar.gz"
bin="$HOME/Applications/shadps4/shadps4"
sha="faca7d7ec1b375afdfac3d35b396a33092d3d56fd917ced8a67c9dc6e8e99834"
phase="preflight"
status="NOT_CAPTURED"
finish() {
  rc=$?
  trap - EXIT
  set +e
  {
    printf 'STATUS=%s\nEXIT_CODE=%s\nPHASE=%s\n' "$status" "$rc" "$phase"
    printf 'INSTALLED_BINARY=UNCHANGED\nSSH_SESSION=UNCHANGED\n'
  } > "$work/summary.txt"
  tar -czf "$archive" -C "$work" . || true
  rm -rf -- "$work"
  printf '\nRESULT=%s\nREPORT=%s\nSSH_SESSION=UNCHANGED\n' "$status" "$archive"
}
trap finish EXIT
for cmd in gdb python3 sha256sum timeout pgrep; do
  command -v "$cmd" >/dev/null || { echo "Missing $cmd"; exit 1; }
done
[[ -x "$bin" ]] || { echo "Missing emulator: $bin"; exit 1; }
actual="$(sha256sum "$bin" | awk '{print $1}')"
printf 'EXPECTED_SHA=%s\nACTUAL_SHA=%s\n' "$sha" "$actual" > "$work/binary-identity.txt"
[[ "$sha" == "$actual" ]] || { echo "SAFE_STOP: installed binary changed"; exit 1; }
if pgrep -x shadps4 >/dev/null || pgrep -x drrun >/dev/null; then
  echo "SAFE_STOP: another emulator session is running. Close it normally before retrying."
  exit 1
fi

# The guest traps frequently. Only save the original host fmt::parse_format_string
# SIGSEGV, and silently let unrelated guest signals reach the existing handlers.
cat > "$work/commands.gdb" <<'GDB'
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
handle SIGSEGV stop noprint pass
handle SIGBUS nostop noprint pass
handle SIGILL nostop noprint pass
handle SIGUSR1 nostop noprint pass
handle SIGUSR2 nostop noprint pass
set $gow_fmt_captured = 0
break sceKernelLoadStartModule
commands
silent
python
import os, gdb
p = os.path.join(os.environ["GOW_FAULT_WORK"], "module-entry.txt")
try:
    rdi = int(gdb.parse_and_eval("$rdi"))
    rsi = int(gdb.parse_and_eval("$rsi"))
    rdx = int(gdb.parse_and_eval("$rdx"))
    rcx = int(gdb.parse_and_eval("$rcx"))
    msg = "ENTRY_PC=%#x filename_ptr=%#x args=%#x argp=%#x flags=%#x\n" % (
        int(gdb.selected_frame().pc()),rdi,rsi,rdx,rcx)
    try:
        # Read through GDB, not through the guest process; invalid guest pointers are safe here.
        data = bytes(gdb.selected_inferior().read_memory(rdi, 128))
        value = data.split(b'\0',1)[0]
        msg += "GUEST_FILENAME_BYTES=%s\nGUEST_FILENAME_PREFIX=%r\n" % (
            data[:48].hex(),value)
    except Exception as e:
        msg += "GUEST_FILENAME_READ_FAILED=%r\n" % e
    with open(p,"a",encoding="utf-8") as f:
        f.write(msg)
except Exception as e:
    with open(p,"a",encoding="utf-8") as f:
        f.write("ENTRY_CAPTURE_ERROR=%r\n" % e)
end
continue
end
catch signal SIGSEGV
commands
silent
python
import os, gdb, traceback
try:
    pc = int(gdb.parse_and_eval("$pc"))
    name = (gdb.selected_frame().name() or "")
    sym = gdb.execute("info symbol %#x" % pc,to_string=True).strip()
    interested = ("fmt::" in (name+sym) and "parse_format_string" in (name+sym))
    if interested:
        gdb.set_convenience_variable("gow_fmt_captured",1)
        out = os.path.join(os.environ["GOW_FAULT_WORK"],"original-fmt-sigsegv.txt")
        chunks = ["SIGNAL=SIGSEGV\nFAULT_PC=%#x\nFUNCTION=%s\nSYMBOL=%s\n" % (
            pc,name,sym)]
        for label,cmd in [
            ("FAULT_REGISTERS","info registers"),
            ("FAULT_STACK","bt 50"),
            ("SIGINFO","p $_siginfo"),
            ("DISASSEMBLY","x/24i $rip-32"),
            ("FAULT_MEMORY","x/16gx $rsp"),
            ("CALLER_FRAME","frame 3"),
            ("CALLER_REGISTERS","info registers rdi rsi rdx rcx r8 r9 rax rbx"),
            ("RETURN_TO_FAULT_FRAME","frame 0"),
            ("MAPPINGS","info proc mappings")]:
            try:
                chunks.append("\n===== %s =====\n%s\n" % (
                    label,gdb.execute(cmd,to_string=True)))
            except Exception as e:
                chunks.append("\n===== %s ERROR =====\n%r\n" % (label,e))
        with open(out,"w",encoding="utf-8") as f:
            f.write("".join(chunks))
        print("CAPTURED_ORIGINAL_FMT_SIGSEGV=%#x" % pc)
except Exception as e:
    p=os.path.join(os.environ["GOW_FAULT_WORK"],"capture-error.txt")
    with open(p,"a",encoding="utf-8") as f:
        f.write(repr(e)+"\n"+traceback.format_exc())
end
if $gow_fmt_captured
  quit
end
continue
end
break unreachable_impl
commands
silent
printf "\n===== FALLBACK_UNREACHABLE_STACK =====\n"
bt 48
info registers
printf "\n===== END_FALLBACK =====\n"
quit
end
run
printf "\n===== INFERIOR_EXITED_WITHOUT_CAPTURE =====\n"
info program
GDB

phase="run native GoW with original-fault tracing"
echo "Capturing module filename and the original SIGSEGV (native CPU-ID mode)."
python3 - "$bin" "$work" <<'PY'
import glob,os,re,subprocess,sys
from pathlib import Path
binary, work = sys.argv[1:]
env = os.environ.copy()
candidates=[]
for namefile in glob.glob('/proc/[0-9]*/comm'):
    try:
        pid=int(namefile.split('/')[2])
        if os.stat(namefile).st_uid!=os.getuid():
            continue
        name=Path(namefile).read_text().strip().lower()
        if 'sunshine' not in name and 'es-de' not in name:
            continue
        vals={}
        for item in Path(f'/proc/{pid}/environ').read_bytes().split(b'\0'):
            if b'=' in item:
                k,v=item.split(b'=',1)
                vals[k.decode(errors='replace')]=v.decode(errors='surrogateescape')
        if vals.get('DISPLAY') or vals.get('WAYLAND_DISPLAY'):
            candidates.append((0 if 'es-de' in name else 1,pid,name,vals))
    except (OSError,ValueError,UnicodeError):
        continue
candidates.sort()
allowed=re.compile(r'^(DISPLAY|WAYLAND_DISPLAY|XDG_RUNTIME_DIR|XAUTHORITY|DBUS_SESSION_BUS_ADDRESS|XDG_SESSION_TYPE|XDG_DATA_HOME|PATH|LD_LIBRARY_PATH|PULSE_SERVER|SDL_.*|VK_.*|RADV_.*|MESA_.*|AMD_.*)$')
if candidates:
    _,pid,name,vals=candidates[0]
    for k,v in vals.items():
        if allowed.match(k):
            env[k]=v
    source=f'{name} PID={pid}'
else:
    source='SSH_FALLBACK'
    if not env.get('DISPLAY') and Path('/tmp/.X11-unix/X0').exists():
        env['DISPLAY']=':0'
    env.setdefault('XDG_RUNTIME_DIR',f'/run/user/{os.getuid()}')
with open(Path(work)/'graphics-context.txt','w') as f:
    f.write(f'SOURCE={source}\nDISPLAY={env.get("DISPLAY","<unset>")}\n')
if not (env.get('DISPLAY') or env.get('WAYLAND_DISPLAY')):
    raise SystemExit("SAFE_STOP: no graphical display; keep Moonlight/ES-DE open.")
env["SHADPS4_CPU_ID_MODE"]="native"
env["GOW_FAULT_WORK"]=work
cmd=['timeout','--signal=INT','--kill-after=9s','115s','gdb','-nx','-q','-batch',
     '-x',str(Path(work)/'commands.gdb'),'--args',binary,'--cpu-id-mode','native',
     '--game','CUSA34384','--fullscreen','true']
print(f'GUI={source}; EXECUTION_MODE=native; GAME=CUSA34384')
with open(Path(work)/'debugger.log','w') as log:
    rc=subprocess.call(cmd,env=env,stdout=log,stderr=subprocess.STDOUT)
print(f'GDB_EXIT_CODE={rc}')
(Path(work)/'debugger-exit.txt').write_text(f'GDB_EXIT_CODE={rc}\n')
PY

phase="analyze capture and collect game logs"
logdir="${XDG_DATA_HOME:-$HOME/.local/share}/shadPS4/log"
if [[ -f "$logdir/CUSA34384.log" ]]; then
  tail -n 320 "$logdir/CUSA34384.log" > "$work/gow-game.log"
fi
if [[ -s "$work/original-fmt-sigsegv.txt" ]]; then
  status="ORIGINAL_FMT_FAULT_CAPTURED"
  echo 'HOST_FMT_FAULT=CAPTURED'
  sed -n '1,22p' "$work/original-fmt-sigsegv.txt"
elif grep -q 'FALLBACK_UNREACHABLE_STACK' "$work/debugger.log"; then
  status="FALLBACK_FATAL_CAPTURED"
  echo 'HOST_FMT_FAULT=NOT_CAPTURED; FATAL_STACK=CAPTURED'
  sed -n '/FALLBACK_UNREACHABLE_STACK/,+32p' "$work/debugger.log"
else
  status="NO_FATAL_CAPTURED"
  tail -n 45 "$work/debugger.log"
fi
