#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Pinned private Ghost-only build with observable intro/post-intro scheduler comparison.

Never installs to Applications/shadps4, edits shadps4-esde, changes other
emulator binaries, touches other games' saves, or terminates unrelated PIDs.
Private executable: ~/Applications/shadps4-ghost/shadps4
Private portable profile: ~/Applications/shadps4-ghost/user/
Separate CMake/Ninja build: ~/.cache/shadps4-ghost-isolated/build/
"""
from __future__ import annotations
import ctypes
import ctypes.util
import hashlib
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tarfile
import time
import traceback
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory

HOME=Path.home()
SRC=HOME/'.cache/shadps4-ghost-fullstack-20261008-131621/source'
PIN='89af13f6d306ebc24396b4e8e207688537cdc28b'
GHOST=HOME/'Applications/shadps4-ghost'
BIN=GHOST/'shadps4'
BUILD=HOME/'.cache/shadps4-ghost-isolated/build'
PRIVATE=GHOST/'user'
SHARED=HOME/'Applications/shadps4/shadps4'
STAMP=datetime.now().strftime('%Y%m%d-%H%M%S')
WORK=HOME/'.cache'/('ghost-isolated-diagnostic-'+STAMP)
OUT=HOME/('ghost-isolated-scheduler-'+STAMP+'.tar.gz')
HELPER_NAME='ghost-guest-timing-queue-snapshot-20261009.py'
HELPER_REF='ea52533bff6eabf47507336d73a8df37dc8f7f09'
HELPER_BLOB='6b760dc94b951395bb31914d4c574ea2d9ba7bb8'
FRAMES_RE=re.compile(rb'GHOST_TRACE vblank=(\d+) guest_flips=(\d+) pending=(\d+) queued=(\d+)')
MAX_SECONDS=145
EVENTS=[]
FRAME_SAMPLES=[]
SNAPSHOTS=[]
STATUS='not_started'
GAME=None
GAME_STARTED=None
DISPLAY_ENV=None
BEFORE_SHARED=None
ERROR=None
BUILD_COMPLETED=False
GAME_LAUNCHED=False

def info(message):
    line=f'[{datetime.now().isoformat(timespec="seconds")}] {message}'
    print(line,flush=True)
    EVENTS.append(line)

def sha(path:Path)->str|None:
    if not path.is_file() or path.is_symlink(): return None
    h=hashlib.sha256()
    with path.open('rb') as f:
        for data in iter(lambda:f.read(1<<20),b''):h.update(data)
    return h.hexdigest()

def git(*args:str)->str:
    return subprocess.check_output(['git','-C',str(SRC),*args],text=True,timeout=25).strip()

def run(args:list[str],log:Path,timeout:int|None=None)->None:
    info('RUN '+ ' '.join(map(str,args))[:280])
    log.parent.mkdir(parents=True,exist_ok=True)
    with log.open('a') as out:
        p=subprocess.Popen(args,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,
                           text=True,errors='replace',bufsize=1)
        last=None
        started=time.monotonic()
        try:
            for line in p.stdout:
                out.write(line)
                if line.startswith('-- ') or ('[' in line and '/' in line and ']' in line):
                    if time.monotonic()-started > 15 and (last is None or time.monotonic()-last>20):
                        info('BUILD_PROGRESS '+line.strip()[:150]);last=time.monotonic()
            rc=p.wait(timeout=timeout)
        finally:
            if p.poll() is None:
                p.terminate()
                try:p.wait(timeout=4)
                except subprocess.TimeoutExpired:p.kill();p.wait()
    if rc!=0:raise RuntimeError(f'Command failed rc={rc}; see {log}')
    info('COMMAND_OK '+Path(args[0]).name)

def source_preflight():
    if not SRC.is_dir():raise RuntimeError('Pinned Ghost source checkout missing: '+str(SRC))
    head=git('rev-parse','--verify','HEAD')
    if head!=PIN:raise RuntimeError(f'Pinned Ghost source HEAD mismatch: {head} != {PIN}')
    diff=git('status','--porcelain','--untracked-files=no','--ignore-submodules=dirty')
    if diff:raise RuntimeError('Pinned Ghost source has tracked edits; refusing build:\n'+diff[:1100])
    for name in ('CMakeLists.txt','src/main.cpp','src/core/signals.cpp',
                 'src/shader_recompiler/ir/passes/flatten_extended_userdata_pass.cpp'):
        if not (SRC/name).is_file():raise RuntimeError('Pinned source incomplete: '+name)
    info('GHOST_SOURCE_PIN=PASS '+head)

def select_compilers()->tuple[str,str]:
    old=HOME/'.cache/ghost-fullstack-resume-20261008-132842/build/CMakeCache.txt'
    cc=cxx=None
    if old.is_file():
        t=old.read_text(errors='replace')
        def cached(label):
            m=re.search(r'(?m)^'+re.escape(label)+r':FILEPATH=(.+)$',t)
            return m.group(1) if m and Path(m.group(1)).is_file() else None
        cc,cxx=cached('CMAKE_C_COMPILER'),cached('CMAKE_CXX_COMPILER')
    if cc and cxx:return cc,cxx
    for c,cpp in [('clang-19','clang++-19'),('gcc-14','g++-14')]:
        if shutil.which(c) and shutil.which(cpp):
            return shutil.which(c),shutil.which(cpp)
    raise RuntimeError('Neither GCC 14 nor Clang 19 compiler pair exists')

def emulator_pids():
    hits=[]
    for p in Path('/proc').iterdir():
        if not p.name.isdigit():continue
        try:
            exe=os.readlink(p/'exe').removesuffix(' (deleted)')
            if Path(exe).name.lower()!='shadps4':continue
            stat=(p/'stat').read_text().rsplit(') ',1)[1].split()
            if stat[0] not in ('Z','X'):hits.append((int(p.name),exe))
        except (OSError,ValueError,PermissionError):pass
    return hits

def build_private(force=False):
    global BUILD_COMPLETED
    source_preflight()
    for d in (GHOST,BUILD,PRIVATE):
        if d.is_symlink():raise RuntimeError('Private Ghost directory is a symlink: '+str(d))
    if BIN.is_symlink():raise RuntimeError('Ghost executable must not be a symlink')
    GHOST.mkdir(parents=True,exist_ok=True)
    BUILD.mkdir(parents=True,exist_ok=True)
    manifest=GHOST/'ghost-build-manifest.json'
    if BIN.is_file() and manifest.is_file() and not force:
        try:
            m=json.loads(manifest.read_text())
            if m.get('source_head')==PIN and m.get('sha256')==sha(BIN) and sha(BIN):
                info('REUSING_ALREADY_VERIFIED_PRIVATE_GHOST='+str(BIN))
                BUILD_COMPLETED=True
                return
        except (OSError,ValueError,TypeError):pass
    active=[pid for pid,exe in emulator_pids() if Path(exe)==BIN]
    if active:raise RuntimeError('Refusing replacement of running private Ghost PIDs: '+str(active))
    cc,cxx=select_compilers()
    info('COMPILERS='+cc+' '+cxx)
    flags=['cmake','-S',str(SRC),'-B',str(BUILD),'-G','Ninja',
           '-DCMAKE_BUILD_TYPE=Release','-DCMAKE_C_COMPILER='+cc,
           '-DCMAKE_CXX_COMPILER='+cxx,'-DCMAKE_INTERPROCEDURAL_OPTIMIZATION=OFF',
           '-DENABLE_TESTS=OFF']
    if shutil.which('ccache'):
        flags+=['-DCMAKE_C_COMPILER_LAUNCHER=ccache','-DCMAKE_CXX_COMPILER_LAUNCHER=ccache']
    run(flags,WORK/'build-full.log')
    jobs=int(os.environ.get('GHOST_BUILD_JOBS','4'))
    if not 1<=jobs<=16:raise RuntimeError('GHOST_BUILD_JOBS must be 1..16')
    run(['cmake','--build',str(BUILD),'--target','shadps4','--parallel',str(jobs)],
        WORK/'build-full.log')
    candidates=[p for p in BUILD.rglob('shadps4') if p.is_file() and os.access(p,os.X_OK)]
    if len(candidates)!=1:raise RuntimeError('Expected one compiled shadps4; found '+str(candidates))
    candidate=candidates[0]
    kind=subprocess.check_output(['file','-b',str(candidate)],text=True,timeout=10)
    if 'ELF 64-bit' not in kind:raise RuntimeError('Built Ghost executable is not ELF 64-bit')
    ldd=subprocess.run(['ldd',str(candidate)],capture_output=True,text=True,timeout=25)
    (WORK/'ghost-ldd.txt').write_text(ldd.stdout+ldd.stderr)
    if 'not found' in ldd.stdout or ldd.returncode!=0:
        raise RuntimeError('Isolated Ghost has unresolved runtime dependencies')
    source_preflight()  # Build must not change tracked source.
    if any(exe==str(BIN) for _,exe in emulator_pids()):
        raise RuntimeError('Private Ghost started while building; refusing replacement')
    staged=GHOST/('.shadps4-staged-'+STAMP)
    shutil.copy2(candidate,staged)
    staged.chmod(0o755)
    if sha(staged)!=sha(candidate):
        staged.unlink(missing_ok=True)
        raise RuntimeError('Isolated binary copy verification failed')
    if BIN.is_file():
        backup=GHOST/('shadps4.before-'+STAMP+'-'+sha(BIN)[:12])
        if backup.exists():raise RuntimeError('Backup already exists; refusing clobber')
        shutil.copy2(BIN,backup)
        if sha(BIN)!=sha(backup):raise RuntimeError('Prior Ghost binary backup failed')
    os.replace(staged,BIN)
    if sha(BIN)!=sha(candidate):raise RuntimeError('Private Ghost installation verification failed')
    manifest.write_text(json.dumps({'source_head':PIN,'sha256':sha(BIN),
                                   'compiler_c':cc,'compiler_cxx':cxx,
                                   'build_dir':str(BUILD),'built_at':STAMP},indent=2)+'\n')
    BUILD_COMPLETED=True
    info('GHOST_BUILD_OK=1 private_bin='+str(BIN)+' sha256='+sha(BIN))

def copy_portable_profile():
    PRIVATE.mkdir(parents=True,exist_ok=True)
    if PRIVATE.is_symlink():raise RuntimeError('Private user path is symlinked')
    candidates=[HOME/'Applications/shadps4/user',
                HOME/'.local/share/shadPS4',
                SRC/'user']
    source=next((p for p in candidates if p.is_dir() and not p.is_symlink()
                 and (p/'config.json').is_file()),None)
    if not (PRIVATE/'config.json').is_file():
        if source is None:raise RuntimeError('Cannot initialize Ghost-only config: no existing config.json')
        shutil.copy2(source/'config.json',PRIVATE/'config.json')
        info('GHOST_PRIVATE_CONFIG_INITIALIZED_FROM='+str(source))
    if source:
        cp=source/'custom_configs/CUSA11456.json'
        dest=PRIVATE/'custom_configs/CUSA11456.json'
        dest.parent.mkdir(parents=True,exist_ok=True)
        if cp.is_file() and not cp.is_symlink() and not dest.exists():
            shutil.copy2(cp,dest)
        for static in ('sys_modules','fonts','custom_modules','licenses'):
            a,b=source/static,PRIVATE/static
            if a.is_dir() and not a.is_symlink() and not b.exists():
                shutil.copytree(a,b,symlinks=False)
        parent=source/'home'
        if parent.is_dir() and not parent.is_symlink():
            for p in parent.glob('**/savedata/CUSA11456'):
                if not p.is_dir() or p.is_symlink():continue
                rel=p.relative_to(parent)
                target=PRIVATE/'home'/rel
                if not target.exists():
                    target.parent.mkdir(parents=True,exist_ok=True)
                    shutil.copytree(p,target,symlinks=False)
    for name in ('log','shader','cache','custom_configs'):
        (PRIVATE/name).mkdir(parents=True,exist_ok=True)
    info('PRIVATE_USER_PATH='+str(PRIVATE)+' (separate logs, caches, saves)')

def display_env():
    xlib=ctypes.util.find_library('X11')
    if not xlib:raise RuntimeError('X11 library missing')
    dll=ctypes.CDLL(xlib)
    dll.XOpenDisplay.argtypes=[ctypes.c_char_p]
    dll.XOpenDisplay.restype=ctypes.c_void_p
    dll.XCloseDisplay.argtypes=[ctypes.c_void_p]
    options=[os.environ.get('XAUTHORITY',''),str(HOME/'.Xauthority'),'']
    for p in Path('/proc').iterdir():
        if not p.name.isdigit():continue
        try:
            if p.stat().st_uid!=os.getuid():continue
            comm=(p/'comm').read_text(errors='replace').lower()
            if not any(k in comm for k in ('sunshine','es-de','xorg','xwayland')):continue
            data=(p/'environ').read_bytes().split(b'\0')
            options += [x.split(b'=',1)[1].decode(errors='replace') for x in data if x.startswith(b'XAUTHORITY=')][:1]
        except (OSError,PermissionError,ValueError):pass
    original_display,original_auth=os.environ.get('DISPLAY'),os.environ.get('XAUTHORITY')
    try:
        for auth in dict.fromkeys(options):
            if auth and not Path(auth).is_file():continue
            os.environ['DISPLAY']=':0'
            if auth:os.environ['XAUTHORITY']=auth
            else:os.environ.pop('XAUTHORITY',None)
            pointer=dll.XOpenDisplay(b':0')
            if pointer:
                dll.XCloseDisplay(pointer)
                env=dict(os.environ)
                info('X11_VERIFIED=:0')
                return env
    finally:
        for name,value in [('DISPLAY',original_display),('XAUTHORITY',original_auth)]:
            if value is None:os.environ.pop(name,None)
            else:os.environ[name]=value
    raise RuntimeError('Cannot open existing X11 :0 display without changing it')

def runtime_log():
    root=PRIVATE/'log'
    options=[p for p in root.glob('*') if p.is_file() and p.suffix in ('.txt','.log')]
    return max(options,key=lambda p:p.stat().st_mtime) if options else None

def log_tail(p:Path,limit=3000000)->bytes:
    try:
        with p.open('rb') as f:
            f.seek(0,2);n=f.tell();f.seek(max(0,n-limit));return f.read()
    except OSError:return b''

def latest_frame():
    logfile=runtime_log()
    if not logfile:return None
    records=FRAMES_RE.findall(log_tail(logfile,250000))
    if not records:return None
    return tuple(map(int,records[-1]))

def test_game_pid(pid:int)->bool:
    try:
        if pid<2:return False
        exe=os.readlink(Path('/proc')/str(pid)/'exe').removesuffix(' (deleted)')
        argv=(Path('/proc')/str(pid)/'cmdline').read_bytes()
        return Path(exe)==BIN and b'CUSA11456' in argv
    except (OSError,ValueError,PermissionError):return False

def screenshot(env:dict[str,str],label:str):
    tool=shutil.which('import')
    if not tool:return
    file=WORK/('desktop-'+label+'.png')
    try:
        result=subprocess.run([tool,'-display',':0','-window','root','-silent',str(file)],
                              env=env,capture_output=True,timeout=10)
        if result.returncode==0 and file.is_file():
            info(f'SCREENSHOT={file.name} bytes={file.stat().st_size}')
        else:file.unlink(missing_ok=True)
    except (OSError,subprocess.TimeoutExpired) as exc:info('SCREENSHOT_UNAVAILABLE='+repr(exc))

def get_helper():
    target=WORK/HELPER_NAME
    url='https://raw.githubusercontent.com/Chreece/shadPS4/'+HELPER_REF+'/scripts/'+HELPER_NAME
    subprocess.run(['curl','-fsSL','--retry','2','--max-time','35',url,'-o',str(target)],
                   check=True,timeout=50)
    blob=subprocess.check_output(['git','hash-object',str(target)],text=True,timeout=20).strip()
    if blob!=HELPER_BLOB:raise RuntimeError('Read-only GDB helper blob mismatch')
    proc=subprocess.run([sys.executable,'-I',str(target),'--self-test'],
                        capture_output=True,text=True,timeout=20)
    (WORK/'gdb-selftest.txt').write_text(proc.stdout+proc.stderr)
    if proc.returncode!=0:raise RuntimeError('Read-only GDB helper self-test failed')
    info('PINNED_GDB_CLOCK_QUEUE_SNAPSHOT_READY')
    return target

def snapshot(helper:Path,pid:int,label:str,frame:tuple[int,int,int,int]|None):
    if not test_game_pid(pid):
        info('REFUSED_UNKNOWN_GDB_TARGET='+str(pid));return False
    command=[sys.executable,'-I',str(helper),'--pid',str(pid),'--outdir',str(WORK),
             '--label',label]
    try:
        result=subprocess.run(command,capture_output=True,text=True,timeout=29)
        (WORK/('gdb-runner-'+label+'.txt')).write_text(result.stdout+result.stderr)
        result_file=WORK/('gdb-guest-stall-'+label+'.status')
        status=result_file.read_text(errors='replace') if result_file.is_file() else ''
        passed=result.returncode==0 and 'begin_marker=True' in status and 'end_marker=True' in status
        SNAPSHOTS.append({'label':label,'frame':frame,'returncode':result.returncode,
                          'complete':passed,'gdb_status':status[:1000]})
        info(f'SNAPSHOT_{label}_COMPLETE={passed} '+('guest_flips='+str(frame[1]) if frame is not None else 'frame_counters=not_instrumented'))
        return passed
    except (OSError,subprocess.TimeoutExpired) as exc:
        SNAPSHOTS.append({'label':label,'frame':frame,'error':repr(exc)})
        info('GDB_SNAPSHOT_FAILED='+repr(exc))
        return False

def stop_owned_game():
    global GAME
    if GAME is None or GAME.poll() is not None:return
    pid=GAME.pid
    if not test_game_pid(pid):
        info('REFUSING_SIGNAL_TO_UNKNOWN_PID='+str(pid));return
    if os.getsid(pid)!=pid:
        info('REFUSING_SIGNAL_TO_NONOWN_SESSION='+str(pid));return
    info('STOPPING_ONLY_TEST_CREATED_GHOST='+str(pid))
    os.killpg(pid,signal.SIGTERM)
    try:GAME.wait(timeout=5)
    except subprocess.TimeoutExpired:
        if test_game_pid(pid) and os.getsid(pid)==pid:
            os.killpg(pid,signal.SIGKILL)
            GAME.wait(timeout=5)


def process_game_events(state:dict, lines:list[str],elapsed:float)->None:
    """Use actual Ghost event lines, not absent instrumentation counters."""
    for line in lines:
        if "open: path = /app0/movies/cutscene/splash_america.bsf" in line:
            if state['movie_open_at'] is None: state['movie_open_at']=elapsed
        elif "Closing /app0/movies/cutscene/splash_america.bsf" in line:
            if state['movie_close_at'] is None: state['movie_close_at']=elapsed
        if ("Compiling graphics pipeline" in line or
            "Compiling compute pipeline" in line or
            ("CompileModule" in line and "Compiling " in line and "shader " in line)):
            state['gpu_compile_count']+=1
            state['last_gpu_compile_at']=elapsed
        if "sceAjmInstanceCreate" in line and state['movie_close_at'] is not None:
            state['audio_after_close']+=1
        if "Unexpected instruction for offset computation, Phi" in line:
            state['phi_errors']+=1

def fresh_phase_state()->dict:
    return {'movie_open_at':None,'movie_close_at':None,
            'gpu_compile_count':0,'last_gpu_compile_at':None,
            'audio_after_close':0,'phi_errors':0}

def due_intro_snapshot(state:dict, elapsed:float)->bool:
    start=state['movie_open_at']
    return bool(start is not None and elapsed-start>=3 and
                state['movie_close_at'] is None and state['gpu_compile_count']>=3)

def due_postintro_snapshot(state:dict, elapsed:float)->bool:
    closed=state['movie_close_at']
    last=state['last_gpu_compile_at']
    return bool(closed is not None and last is not None and
                state['audio_after_close']>=2 and
                elapsed-max(closed,last)>=15)

def game_trial():
    global GAME,GAME_LAUNCHED,GAME_STARTED
    others=emulator_pids()
    if others:
        info('OTHER_EMULATOR_RUNNING='+'; '.join(f'{pid}:{exe}' for pid,exe in others))
        return 'other_emulator_active_skipped_game_test'
    env=display_env()
    for variable in ('RADV_DEBUG','SHADPS4_CPU_ID_MODE','GHOST_CPU_RIP_LOG'):
        env.pop(variable,None)
    env['SHADPS4_GRAPHICS_DIAGNOSTICS']='1'
    env['SHADPS4_STARTUP_DIAGNOSTICS']='1'
    runtime=Path('/run/user')/str(os.getuid())
    if runtime.is_dir():env.setdefault('XDG_RUNTIME_DIR',str(runtime))
    helper=get_helper()
    launch=[str(BIN),'--game','CUSA11456','--fullscreen','true']
    (WORK/'launch-command.txt').write_text(' '.join(launch)+'\ncwd='+str(GHOST)+'\n')
    output=WORK/'ghost-stdout.log'
    with output.open('w') as out:
        GAME_STARTED=time.time()
        GAME=subprocess.Popen(launch,cwd=GHOST,env=env,stdin=subprocess.DEVNULL,
                              stdout=out,stderr=subprocess.STDOUT,start_new_session=True)
    GAME_LAUNCHED=True
    info('ISOLATED_GHOST_STARTED_PID='+str(GAME.pid)+' bin='+str(BIN))
    start=time.monotonic()
    phase_state=fresh_phase_state()
    cursor=0;partial=b''
    early=False;late=False;early_complete=False
    last_monitor=-100
    while time.monotonic()-start<MAX_SECONDS:
        elapsed=time.monotonic()-start
        if GAME.poll() is not None:
            info('GAME_EXITED rc='+str(GAME.returncode)+' elapsed='+str(round(elapsed,1)))
            return 'game_exited'
        if not test_game_pid(GAME.pid):
            if elapsed>8:return 'launched_pid_not_ghost'
            time.sleep(.4);continue
        # Read only this trial's newly created stdout, never a prior session.
        try:
            with output.open('rb') as source:
                source.seek(cursor)
                chunk=source.read()
                cursor=source.tell()
        except OSError:
            chunk=b''
        if chunk:
            combined=partial+chunk
            pieces=combined.split(b'\n')
            partial=pieces.pop()
            lines=[line.decode('utf-8','replace') for line in pieces]
            process_game_events(phase_state,lines,elapsed)
        if not early and due_intro_snapshot(phase_state,elapsed):
            early=True
            info('PREINTRO_SCHEDULER_SNAPSHOT_A elapsed='+str(round(elapsed,1))+
                 ' gpu_compiles='+str(phase_state['gpu_compile_count']))
            screenshot(env,'movie-active')
            early_complete=snapshot(helper,GAME.pid,'A',None)
        if not late and due_postintro_snapshot(phase_state,elapsed):
            late=True
            info('POSTINTRO_GPU_COMPILATION_QUIET elapsed='+str(round(elapsed,1))+
                 ' audio_events='+str(phase_state['audio_after_close'])+
                 ' no_new_shader_compilation_for=15s')
            screenshot(env,'postintro-quiet')
            late_complete=snapshot(helper,GAME.pid,'B',None)
            (WORK/'host-event-phase-status.json').write_text(json.dumps({
                'interpretation':'post-intro compile inactivity, NOT confirmed frame-submission stall',
                'event_state':phase_state,'early_snapshot_complete':early_complete,
                'late_snapshot_complete':late_complete},indent=2)+'\n')
            return ('event_phase_snapshots_complete' if early_complete and late_complete
                    else 'event_phase_snapshots_incomplete')
        if elapsed-last_monitor>=20:
            last_monitor=elapsed
            info('HOST_EVENT_MONITOR elapsed='+str(round(elapsed,1))+
                 ' movie_open='+str(phase_state['movie_open_at'] is not None)+
                 ' movie_closed='+str(phase_state['movie_close_at'] is not None)+
                 ' gpu_compiles='+str(phase_state['gpu_compile_count'])+
                 ' postclose_audio='+str(phase_state['audio_after_close']))
        time.sleep(.5)
    screenshot(env,'event-timeout')
    (WORK/'host-event-phase-status.json').write_text(json.dumps({
        'event_state':phase_state,'early_snapshot_taken':early,
        'late_snapshot_taken':late,'error':'event_phase_window_not_confirmed'},indent=2)+'\n')
    return 'timeout_without_two_event_phases'

def test():
    assert len(PIN)==40 and len(HELPER_BLOB)==40
    assert (HOME/'Applications/shadps4-ghost/shadps4')==BIN
    assert (HOME/'Applications/shadps4/shadps4')==SHARED and BIN!=SHARED
    assert FRAMES_RE.findall(b'GHOST_TRACE vblank=1200 guest_flips=530 pending=2 queued=0')==[(b'1200',b'530',b'2',b'0')]
    with TemporaryDirectory() as d:
        p=Path(d)/'x.bin';p.write_bytes(b'diagnostic')
        assert sha(p)==hashlib.sha256(b'diagnostic').hexdigest()
        p.unlink()
        assert sha(p) is None
    assert MAX_SECONDS<200
    sample=fresh_phase_state()
    process_game_events(sample,[
        'open: path = /app0/movies/cutscene/splash_america.bsf',
        'CompileModule: Compiling cs shader 0xabcd',
        'GetComputePipeline: Compiling compute pipeline 0xabcd',
        'GetGraphicsPipeline: Compiling graphics pipeline 0xabcd'],10)
    assert sample['movie_open_at']==10 and due_intro_snapshot(sample,13)
    assert not due_postintro_snapshot(sample,70)
    process_game_events(sample,[
        'Closing /app0/movies/cutscene/splash_america.bsf',
        'CompileModule: Compiling cs shader 0x8ced785b',
        'ComputeOffset: Unexpected instruction for offset computation, Phi',
        'sceAjmInstanceCreate: called',
        'sceAjmInstanceCreate: called'],45)
    assert sample['phi_errors']==1
    assert not due_postintro_snapshot(sample,58)
    assert due_postintro_snapshot(sample,60)
    process_game_events(sample,['Compiling graphics pipeline 0x123'],65)
    assert not due_postintro_snapshot(sample,70)
    assert due_postintro_snapshot(sample,80)
    info('SELFTEST_PASS isolated_paths,sha,frame_parser,event_phase_detection,no_process_signals')

def main():
    global STATUS,BEFORE_SHARED,ERROR
    if sys.argv[1:]==['--self-test']:
        test();return 0
    if sys.argv[1:] not in ([],['--build-only'],['--test-only'],['--rebuild']):
        raise SystemExit('Usage: script.py [--self-test|--build-only|--test-only|--rebuild]')
    only_build=sys.argv[1:]==['--build-only']
    test_only=sys.argv[1:]==['--test-only']
    force=sys.argv[1:]==['--rebuild']
    WORK.mkdir(parents=True,exist_ok=False)
    BEFORE_SHARED=sha(SHARED)
    try:
        if test_only:
            if not BIN.is_file() or BIN.is_symlink():raise RuntimeError('No isolated Ghost binary; run default once')
            manifest=json.loads((GHOST/'ghost-build-manifest.json').read_text())
            if manifest.get('source_head')!=PIN or manifest.get('sha256')!=sha(BIN):
                raise RuntimeError('Isolated binary does not match its pinned build manifest')
            info('ISOLATED_GHOST_MANIFEST_VERIFIED')
        else:
            build_private(force=force)
            copy_portable_profile()
        if only_build:
            STATUS='isolated_ghost_build_ready'
        else:
            if test_only:copy_portable_profile()
            STATUS=game_trial()
    except BaseException as exc:
        ERROR=traceback.format_exc()
        info('TRIAL_FAILED='+type(exc).__name__+': '+str(exc))
        (WORK/'error.txt').write_text(ERROR)
        STATUS='failed_before_or_during_game'
    finally:
        try:stop_owned_game()
        except Exception as exc:info('CLEANUP_WARNING='+repr(exc))
        live=runtime_log()
        if live:
            (WORK/'runtime-tail.log').write_bytes(log_tail(live,2500000))
        built_log=WORK/'build-full.log'
        if built_log.exists():
            (WORK/'build-last-150k.txt').write_bytes(log_tail(built_log,150000))
        after_shared=sha(SHARED)
        (WORK/'status.json').write_text(json.dumps({
            'status':STATUS,'error':ERROR,'source_pin':PIN,
            'private_binary':str(BIN),'private_binary_sha256':sha(BIN),
            'private_user_root':str(PRIVATE),'build_completed':BUILD_COMPLETED,
            'game_launched':GAME_LAUNCHED,'frames':FRAME_SAMPLES,'snapshots':SNAPSHOTS,
            'shared_sha256_before':BEFORE_SHARED,'shared_sha256_after':after_shared,
            'shared_binary_was_not_targeted':True,
            'active_emulators_at_finish':emulator_pids()
        },indent=2)+'\n')
        (WORK/'events.txt').write_text('\n'.join(EVENTS)+'\n')
        with tarfile.open(OUT,'w:gz') as archive:
            for item in sorted(WORK.iterdir()):
                if item.name in (HELPER_NAME,'build-full.log','__pycache__'):
                    continue
                archive.add(item,arcname=item.name)
        info('GHOST_ISOLATED_BINARY='+str(BIN))
        info('UPLOAD_THIS_FILE='+str(OUT))
        info('SHARED_ESDE_BINARY=UNCHANGED_BY_THIS_SCRIPT')
        info('SSH_SESSION=REMAINS_OPEN')
    return 0 if ERROR is None and STATUS in ('event_phase_snapshots_complete','isolated_ghost_build_ready','other_emulator_active_skipped_game_test') else 1

if __name__=='__main__':
    raise SystemExit(main())
