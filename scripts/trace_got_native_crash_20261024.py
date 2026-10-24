#!/usr/bin/env python3
"""Trace native shadPS4 GoT startup faults and resolve their ELF symbols.

No DynamoRIO, emulator rebuild, ES-DE changes or game-specific source changes.
Uses ptrace only on its own launched child and the existing isolated profile.
"""
from __future__ import annotations
import argparse, ctypes, hashlib, importlib.util, json, os, pathlib, re, types
import shutil, signal, subprocess, sys, tarfile, tempfile, time, traceback

ROOT=pathlib.Path.home()/'projects/shadps4'
NATIVE=ROOT/'scripts/build_run_got_upstream_native_20261024.py'
NATIVE_BLOB='96c6b84bde950513c237a9daec6177999fb3ef92'
NATIVE_REV='806bf5093d6829f97db501aaba51d767a34e822c'
NATIVE_SHA='2b617a7148be38e1e653093e7f83ce1ab725a050135ee14f85c8aa53353106aa'
NATIVE_REPORT=ROOT/'logs/got-upstream-native-20261024-135431-xieq3ph7/info/build-result.json'
NATIVE_CANDIDATE=ROOT/'GhostOfTsushima/candidates'/(NATIVE_REV+'-'+NATIVE_SHA)/'shadps4'
GAME=pathlib.Path('/mnt/roms-all/ps4-games/CUSA11456/eboot.bin')
SESSION=ROOT/'test-profiles/got-generic-world-20261024'
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
    if not NATIVE.is_file() or sha1blob(NATIVE)!=NATIVE_BLOB:
        raise RuntimeError('PINNED_NATIVE_BUILD_RUNNER_MISSING_OR_CHANGED')
    if not NATIVE_REPORT.is_file():
        raise RuntimeError('NATIVE_SUCCESS_REPORT_MISSING')
    report=json.loads(NATIVE_REPORT.read_text())
    expected={'status':'UPSTREAM_NATIVE_GENERIC_BUILD_PASS','revision':NATIVE_REV,
              'candidate_sha256':NATIVE_SHA,'dynamorio_in_build':False,
              'dynamorio_in_source':False,'dynamorio_required':False,
              'native_guest_cpu_handling':True,'es_de_modified':False}
    for key,value in expected.items():
        if report.get(key)!=value:
            raise RuntimeError('NATIVE_BUILD_REPORT_MISMATCH='+key)
    if (not NATIVE_CANDIDATE.is_file() or NATIVE_CANDIDATE.is_symlink() or
        pathlib.Path(report['candidate'])!=NATIVE_CANDIDATE or
        hashlib.sha256(NATIVE_CANDIDATE.read_bytes()).hexdigest()!=NATIVE_SHA):
        raise RuntimeError('PINNED_NATIVE_EXECUTABLE_HASH_MISMATCH')
    if (not GAME.is_file() or not (SESSION/'user/config.json').is_file()):
        raise RuntimeError('GOT_GAME_OR_ISOLATED_TEST_PROFILE_MISSING')
    if (NATIVE_CANDIDATE.parent/'cpu-id-runtime').exists():
        raise RuntimeError('REJECTED_DYNAMORIO_RUNTIME_PRESENT')
    spec=importlib.util.spec_from_file_location('got_native_builder',NATIVE)
    module=importlib.util.module_from_spec(spec)
    sys.modules[spec.name]=module;spec.loader.exec_module(module)
    proxy=types.SimpleNamespace()
    proxy.GAME=GAME;proxy.CANDIDATE=NATIVE_CANDIDATE
    proxy.BIN_SHA=NATIVE_SHA;proxy.SESSION=SESSION
    proxy.display_environment=module.prepare_display
    proxy.prepare_vram=module.vram_prepare
    proxy.restore_ollama=module.vram_restore
    print('NATIVE_BUILD_VERIFIED=PASS DYNAMORIO=REJECTED',flush=True)
    return proxy

def extract_memory(pid,addr,length):
    with open(f'/proc/{pid}/mem','rb',buffering=0) as stream:
        stream.seek(addr);return stream.read(length)

def capture(tid,reg,run,index,sig,si_code,si_addr):
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
            'rsp_mod16':reg.rsp % 16, 'signal':sig,'signal_name':signal.Signals(sig).name,
            'si_code':si_code,'si_addr':hex(si_addr),
            'mapping':matched,'registers':registers}
    if matched:
        parts=matched[0].split(maxsplit=5)
        if len(parts)>=6 and parts[5].startswith('/'):
            code_file=pathlib.Path(parts[5])
            map_start=int(parts[0].split('-')[0],16)
            map_offset=int(parts[2],16)
            elf_addr=reg.rip-(map_start-map_offset)
            result['elf_offset']=hex(elf_addr)
            result['elf_file']=str(code_file)
            if code_file.is_file():
                for tool,args,filename in (
                    ('addr2line',['-f','-i','-C','-e',str(code_file),hex(elf_addr)],'symbols.txt'),
                    ('objdump',['-C','-d','--start-address='+hex(max(0,elf_addr-64)),
                                '--stop-address='+hex(elf_addr+80),str(code_file)],'elf-disassembly.txt'),
                ):
                    if shutil.which(tool):
                        try:
                            ret=subprocess.run([tool,*args],capture_output=True,
                                               text=True,timeout=12)
                            (slot/filename).write_text(ret.stdout+ret.stderr)
                        except Exception as e:
                            (slot/(filename+'.error')).write_text(str(e))
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
    print('NATIVE_FAULT='+str(index)+' SIGNAL='+str(sig)+' THREAD='+name+' RIP='+hex(reg.rip)+' RSP_MOD16='+str(reg.rsp%16)+' SI_ADDR='+hex(si_addr),flush=True)
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
            if sig in (signal.SIGSEGV,signal.SIGBUS,signal.SIGILL,signal.SIGTRAP):
                try:
                    si=ctypes.create_string_buffer(128)
                    ptrace(PTRACE_GETSIGINFO,tid,0,ctypes.byref(si))
                    addr=int.from_bytes(si.raw[16:24],'little')
                    si_code=int.from_bytes(si.raw[8:12],'little',signed=True)
                    if (len(events)<12 and
                        ((sig in (signal.SIGSEGV,signal.SIGBUS) and addr==0) or
                         sig in (signal.SIGTRAP,signal.SIGILL))):
                        reg=Regs();ptrace(PTRACE_GETREGS,tid,0,ctypes.byref(reg))
                        events.append(capture(tid,reg,run,len(events)+1,sig,si_code,addr))
                except Exception as e:
                    (run/'trace-capture-error.log').open('a').write(str(e)+'\n')
            # Deliver native fault/trap signals faithfully, including SIGTRAP
            # generated by the emulator's fatal-assert instruction. Only the
            # synthetic thread-start SIGSTOP is suppressed.
            try:ptrace(PTRACE_CONT,tid,0,0 if sig==signal.SIGSTOP else sig)
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
            'native_faults_captured':len(events),'exit_status':outcome,
            'faults':[{'tid':e['tid'],'thread':e['thread'],'rip':e['rip'],
                       'signal':e['signal_name'],'si_code':e['si_code'],
                       'si_addr':e['si_addr'],'rsp_mod16':e['rsp_mod16'],
                       'elf_offset':e.get('elf_offset'),'mapping':e['mapping']} for e in events]}
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
    run=pathlib.Path(tempfile.mkdtemp(prefix='got-native-crash-trace-',dir=ROOT/'logs'))
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
