#!/usr/bin/env python3
"""Capture the first null-pointer faults in a verified shadPS4 runtime.

No emulator rebuild, source changes, ES-DE changes, or game-specific emulation flags.
Uses ptrace on its own child and existing isolated GoT test profile.
"""
from __future__ import annotations
import argparse, ctypes, hashlib, importlib.util, json, os, pathlib, re
import shutil, signal, subprocess, sys, tarfile, tempfile, time, traceback

ROOT=pathlib.Path.home()/'projects/shadps4'
DIRECT=ROOT/'scripts/run_got_verified_generic_20261024.py'
DIRECT_BLOB='ded2e7a7cb1436db17636439116103d88104b047'
PTRACE_TRACEME=0;PTRACE_CONT=7;PTRACE_GETREGS=12;PTRACE_SETOPTIONS=0x4200
PTRACE_GETEVENTMSG=0x4201;PTRACE_GETSIGINFO=0x4202
TRACE_EVENTS=(1,2,3)
TRACE_OPTS=0x100000 | 0x2 | 0x4 | 0x8 | 0x10
WALL=0x40000000
libc=ctypes.CDLL(None,use_errno=True)
libc.ptrace.restype=ctypes.c_long
class Regs(ctypes.Structure):
    _fields_=[(name,ctypes.c_ulonglong) for name in (
        'r15 r14 r13 r12 rbp rbx r11 r10 r9 r8 rax rcx rdx rsi rdi orig_rax '
        'rip cs eflags rsp ss fs_base gs_base ds es fs gs').split()]

def ptrace(cmd,pid,addr=0,data=0):
    ctypes.set_errno(0)
    result=libc.ptrace(cmd,pid,addr,data)
    if result==-1 and ctypes.get_errno():
        raise OSError(ctypes.get_errno(),f'ptrace({cmd},{pid})')
    return result

def sha1blob(path):
    buf=path.read_bytes()
    return hashlib.sha1(b'blob '+str(len(buf)).encode()+b'\0'+buf).hexdigest()

def load_verified_runtime():
    if not DIRECT.is_file() or sha1blob(DIRECT)!=DIRECT_BLOB:
        raise RuntimeError('PREVIOUS_DIRECT_RUNNER_MISSING_OR_HASH_CHANGED')
    spec=importlib.util.spec_from_file_location('got_generic_direct',DIRECT)
    module=importlib.util.module_from_spec(spec);sys.modules[spec.name]=module
    spec.loader.exec_module(module)
    module.verify();module.ensure_cpu_id_bundle();module.prepare_profile()
    return module

def extract_memory(pid,addr,length):
    with open(f'/proc/{pid}/mem','rb',buffering=0) as stream:
        stream.seek(addr);return stream.read(length)

def capture(tid,reg,run,index):
    slot=run/f'null-fault-{index:02d}';slot.mkdir()
    mapping=pathlib.Path(f'/proc/{tid}/maps').read_text(errors='replace')
    (slot/'maps.txt').write_text(mapping)
    matched=[]
    for line in mapping.splitlines():
        m=re.match(r'([0-9a-f]+)-([0-9a-f]+)\s+',line)
        if m and int(m[1],16)<=reg.rip<int(m[2],16):matched.append(line)
    name=pathlib.Path(f'/proc/{tid}/comm').read_text(errors='replace').strip()
    registers={x:hex(getattr(reg,x)) for x,_ in Regs._fields_}
    result={'tid':tid,'thread':name,'rip':hex(reg.rip),'rsp':hex(reg.rsp),
            'mapping':matched,'registers':registers}
    (slot/'registers.json').write_text(json.dumps(result,indent=2)+'\n')
    try:
        start=max(0,reg.rip-48)
        buf=extract_memory(tid,start,160)
        (slot/'instruction-bytes.bin').write_bytes(buf)
        if shutil.which('objdump'):
            p=subprocess.run(['objdump','-D','-b','binary','-m','i386:x86-64',
                              '--adjust-vma='+hex(start),str(slot/'instruction-bytes.bin')],
                             capture_output=True,text=True,timeout=5)
            (slot/'disassembly.txt').write_text(p.stdout+p.stderr)
    except Exception as e:(slot/'instruction-read-error.txt').write_text(str(e))
    try:(slot/'stack-first-512.bin').write_bytes(extract_memory(tid,reg.rsp,512))
    except Exception as e:(slot/'stack-read-error.txt').write_text(str(e))
    print('NULL_FAULT='+str(index)+' THREAD='+name+' RIP='+hex(reg.rip),flush=True)
    print('FAULT_CODE_MAPPING='+(' | '.join(matched) if matched else 'UNMAPPED'),flush=True)
    return result

def run_trace(runtime,run,seconds):
    game=runtime.GAME
    command=[str(runtime.CANDIDATE),'--game',str(game),'--fullscreen','true','--show-fps']
    env=runtime.display_environment()
    log=run/'emulator-console.log'
    print('TRACE_MODE=OWN_CHILD_PTRACE NO_REBUILD=YES ES_DE_CHANGED=NO',flush=True)
    print('CANDIDATE_SHA256='+runtime.BIN_SHA,flush=True)
    print('GAME='+str(game),flush=True)
    print('DISPLAY='+env.get('DISPLAY','UNSET'),flush=True)
    sys.stdout.flush();sys.stderr.flush()
    pid=os.fork()
    if pid==0:
        try:
            os.setsid();os.chdir(runtime.SESSION)
            fd=os.open(log,os.O_WRONLY|os.O_CREAT|os.O_TRUNC,0o600)
            os.dup2(fd,1);os.dup2(fd,2);os.close(fd)
            ptrace(PTRACE_TRACEME,0)
            os.execvpe(command[0],command,env)
        except BaseException as e:
            os.write(2,('TRACE_CHILD_START_ERROR='+str(e)+'\n').encode())
        os._exit(127)
    alive={pid};events=[];outcome={};started=time.monotonic()
    first,st=os.waitpid(pid,0)
    if first!=pid or not os.WIFSTOPPED(st):raise RuntimeError('TRACE_INITIAL_STOP_MISSING')
    ptrace(PTRACE_SETOPTIONS,pid,0,TRACE_OPTS)
    ptrace(PTRACE_CONT,pid)
    reason='exited'
    try:
        while alive:
            if time.monotonic()-started>seconds:
                reason='timeout';break
            try:tid,st=os.waitpid(-1,os.WNOHANG|WALL)
            except (ChildProcessError,InterruptedError):
                if not alive:break
                time.sleep(.02);continue
            if tid==0:time.sleep(.02);continue
            if os.WIFEXITED(st):
                outcome[str(tid)]={'exit':os.WEXITSTATUS(st)};alive.discard(tid);continue
            if os.WIFSIGNALED(st):
                outcome[str(tid)]={'signal':os.WTERMSIG(st)};alive.discard(tid);continue
            if not os.WIFSTOPPED(st):continue
            sig=os.WSTOPSIG(st);event=st>>16
            if event:
                if event in TRACE_EVENTS:
                    new=ctypes.c_ulonglong(0)
                    ptrace(PTRACE_GETEVENTMSG,tid,0,ctypes.byref(new))
                    alive.add(new.value)
                ptrace(PTRACE_CONT,tid);continue
            if sig==signal.SIGSEGV:
                try:
                    si=ctypes.create_string_buffer(128)
                    ptrace(PTRACE_GETSIGINFO,tid,0,ctypes.byref(si))
                    addr=int.from_bytes(si.raw[16:24],'little')
                    if addr==0 and len(events)<8:
                        reg=Regs();ptrace(PTRACE_GETREGS,tid,0,ctypes.byref(reg))
                        events.append(capture(tid,reg,run,len(events)+1))
                except Exception as e:
                    (run/'trace-capture-error.log').open('a').write(str(e)+'\n')
            # Pass genuine faults back to DR/shadPS4; suppress ptrace events.
            try:ptrace(PTRACE_CONT,tid,0,0 if sig in (signal.SIGTRAP,signal.SIGSTOP) else sig)
            except OSError as e:
                if e.errno not in (3,):raise
    finally:
        if alive:
            try:os.killpg(pid,signal.SIGTERM)
            except ProcessLookupError:pass
            time.sleep(.3)
            try:os.killpg(pid,signal.SIGKILL)
            except ProcessLookupError:pass
        for _ in range(120):
            if not alive:break
            try:
                tid,st=os.waitpid(-1,os.WNOHANG|WALL)
                if tid>0:
                    if os.WIFEXITED(st) or os.WIFSIGNALED(st):alive.discard(tid)
                    elif os.WIFSTOPPED(st):
                        try:ptrace(PTRACE_CONT,tid,0,signal.SIGKILL)
                        except OSError:alive.discard(tid)
            except ChildProcessError:break
            time.sleep(.02)
    result={'reason':reason,'duration_seconds':round(time.monotonic()-started,2),
            'null_faults_captured':len(events),'exit_status':outcome,
            'faults':[{'tid':e['tid'],'thread':e['thread'],'rip':e['rip'],'mapping':e['mapping']} for e in events]}
    (run/'trace-result.json').write_text(json.dumps(result,indent=2)+'\n')
    return result

def make_archive(run):
    src=run/'emulator-console.log'
    if src.is_file():
        with src.open('rb') as f:
            (run/'console-head.log').write_bytes(f.read(1024*1024))
            if src.stat().st_size>5*1024*1024:f.seek(-5*1024*1024,2)
            else:f.seek(0)
            (run/'console-tail.log').write_bytes(f.read(5*1024*1024))
    out=pathlib.Path(str(run)+'.tar.gz')
    with tarfile.open(out,'w:gz') as tar:
        for p in run.rglob('*'):
            if p.is_file() and not p.is_symlink() and p.name!='emulator-console.log':
                tar.add(p,arcname=str(p.relative_to(run)))
    return out

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--seconds',type=int,default=120)
    ap.add_argument('--self-test',action='store_true',help=argparse.SUPPRESS)
    args=ap.parse_args()
    if not 30<=args.seconds<=600:ap.error('seconds must be 30..600')
    (ROOT/'logs').mkdir(parents=True,exist_ok=True)
    run=pathlib.Path(tempfile.mkdtemp(prefix='got-null-crash-trace-',dir=ROOT/'logs'))
    unloaded=[]
    try:
        if args.self_test:
            class Mock:pass
            r=Mock();r.GAME=pathlib.Path('/tmp/test_segv');r.CANDIDATE=r.GAME;r.BIN_SHA='FIXTURE'
            r.SESSION=pathlib.Path('/tmp');r.display_environment=lambda:os.environ.copy()
        else:
            r=load_verified_runtime();unloaded=r.prepare_vram()
        run_trace(r,run,args.seconds)
    except BaseException:
        (run/'trace-failure.txt').write_text(traceback.format_exc())
        print('TRACE_SETUP_FAILED=YES',flush=True)
    finally:
        if unloaded:
            try:r.restore_ollama(unloaded)
            except Exception as e:(run/'ollama-restore-warning.txt').write_text(str(e))
        print('UPLOAD_THIS='+str(make_archive(run)),flush=True)
    return 0
if __name__=='__main__':raise SystemExit(main())
