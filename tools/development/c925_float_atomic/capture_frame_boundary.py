#!/usr/bin/env python3
"""Read-only frame-stall capture of the verified PR102 binary.
Two brief debugger attaches inspect call stacks, not memory values or expressions.
No emulator/source/config build or write; never attach to an existing process.
"""
from __future__ import annotations
import ctypes
import datetime as dt
import fcntl
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import select
import shutil
import signal
import subprocess as sp
import sys
import tarfile
import threading
import time
import traceback

EXPECTED='da4a37e5f60069afbdcc7ab5f24a1e3495f92deae8b21c851dd1157a5d1dcbb3'
PREVIOUS='got-c925-float-atomic-20261028T182458657462Z'
BASE='c925-pr95-scalar-bounds-20261028T112756671630889Z'
RUNTIME_SHA='df7617549006c8733049a684f22f6ac2bf6b96701bf2fe1da5ee9265df568250'
HEADROOM_SHA='c6017d0d96e83341315e6a05dafb3fb5478239b384b41e510c475b6d89884a0b'
LATEST={
 'src/shader_recompiler/resource.h':'f5f92b13e06dbc9324c66624bcdb9f5620567a038d326f7861654b8462b51a66',
 'src/shader_recompiler/ir/passes/resource_patching_pass.cpp':'57026fa0e8f9e7bf9c7be2c7bd9aa0a953c33b9b432694b065f3d0192af20801',
 'src/shader_recompiler/backend/spirv/emit_spirv_atomic.cpp':'5415a923f012dc593a2fb6f23a0a4f3e7e27c20a26b4bfabbf38a9bc518cc693',
 'src/video_core/renderer_vulkan/vk_pipeline_serialization.cpp':'c96a019cbe554712b8e5f1fbb38d7f1d3ec37718063b602c992ceeabe41eafa9'}
SOURCE_PATHS=[
 'src/sdl_window.cpp','src/sdl_window.h','src/emulator.cpp',
 'src/core/libraries/videoout/driver.cpp','src/core/libraries/videoout/driver.h',
 'src/core/libraries/videoout/video_out.cpp','src/core/libraries/videoout/video_out.h',
 'src/video_core/amdgpu/liverpool.cpp','src/video_core/amdgpu/liverpool.h',
 'src/video_core/renderer_vulkan/vk_scheduler.cpp','src/video_core/renderer_vulkan/vk_scheduler.h',
 'src/video_core/renderer_vulkan/vk_presenter.cpp','src/video_core/renderer_vulkan/vk_presenter.h',
 'src/video_core/renderer_vulkan/vk_swapchain.cpp','src/video_core/renderer_vulkan/vk_swapchain.h',
 'src/video_core/renderer_vulkan/vk_master_semaphore.cpp','src/video_core/renderer_vulkan/vk_master_semaphore.h',
 'src/video_core/renderer_vulkan/vk_command_pool.cpp','src/video_core/renderer_vulkan/vk_command_pool.h',
 'src/video_core/renderer_vulkan/vk_pipeline_cache.cpp','src/video_core/renderer_vulkan/vk_pipeline_cache.h',
 'src/video_core/renderer_vulkan/vk_rasterizer.cpp','src/video_core/renderer_vulkan/vk_rasterizer.h',
 'src/video_core/buffer_cache/buffer_cache.cpp','src/video_core/buffer_cache/buffer_cache.h',
 'src/video_core/texture_cache/texture_cache.cpp','src/video_core/texture_cache/texture_cache.h']


def sha(p):
    h=hashlib.sha256()
    with Path(p).open('rb') as f:
        for b in iter(lambda:f.read(4*1024**2),b''):h.update(b)
    return h.hexdigest()


def save(p,obj):Path(p).write_text(json.dumps(obj,indent=2,ensure_ascii=False)+'\n')
def emit(s):print(time.strftime('%H:%M:%S')+' '+s,flush=True)


def stop_owned(p):
    if p is None or p.poll() is not None:return
    try:
        os.killpg(p.pid,signal.SIGTERM)
        try:p.wait(timeout=2)
        except sp.TimeoutExpired:
            os.killpg(p.pid,signal.SIGKILL);p.wait(timeout=2)
    except ProcessLookupError:pass


def load(name,path,expected):
    if sha(path)!=expected:raise RuntimeError('HELPER_HASH_CHANGED:'+name)
    spec=importlib.util.spec_from_file_location(name,path)
    if spec is None or spec.loader is None:raise RuntimeError('HELPER_IMPORT_FAILED')
    m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m


class MI:
    """Tokenized GDB/MI, output capped; no shell, breakpoints, writes or inferior calls."""
    def __init__(self,args,evid):
        self.log=(evid/'gdb-mi.log').open('wb');self.text=(evid/'gdb-stacks.txt').open('w')
        self.total=0;self.token=0;self.buffer=b'';self.lock=threading.Lock();self.closed=False
        self.p=sp.Popen(args,stdin=sp.PIPE,stdout=sp.PIPE,stderr=sp.STDOUT,
            env=dict(os.environ,DEBUGINFOD_URLS=''),start_new_session=True,bufsize=0)
    def command(self,cmd,timeout=4):
        self.token+=1;token=str(self.token).encode();deadline=time.monotonic()+timeout
        self.log.write(b'>>> '+token+cmd.encode()+b'\n');self.log.flush()
        self.p.stdin.write(token+cmd.encode()+b'\n');self.p.stdin.flush()
        while time.monotonic()<deadline:
            while b'\n' in self.buffer:
                raw,self.buffer=self.buffer.split(b'\n',1)
                if self.total<8*1024**2:
                    self.log.write(raw+b'\n');self.log.flush();self.total+=len(raw)+1
                    if raw.startswith((b'~',b'&',b'@')):
                        try:self.text.write(json.loads(raw[1:].decode()) );self.text.flush()
                        except (ValueError,UnicodeError):pass
                if raw.startswith(token+b'^'):
                    if raw.startswith(token+b'^done') or raw.startswith(token+b'^exit'):return raw.decode(errors='replace')
                    raise RuntimeError('GDB_RESULT:'+raw.decode(errors='replace')[:600])
            if self.p.poll() is not None:raise RuntimeError('GDB_EXITED')
            ready,_,_=select.select([self.p.stdout],[],[],min(.1,max(0,deadline-time.monotonic())))
            if ready:
                chunk=os.read(self.p.stdout.fileno(),65536)
                if not chunk:raise RuntimeError('GDB_EOF')
                self.buffer+=chunk
                if len(self.buffer)>2*1024**2:raise RuntimeError('GDB_LINE_TOO_LARGE')
        raise TimeoutError('GDB_COMMAND_TIMEOUT:'+cmd)
    def setup(self):
        for command in ('set auto-load off','set pagination off','set confirm off',
                        'set print frame-arguments none','set may-call-functions off'):
            self.command('-interpreter-exec console '+json.dumps(command))
        # debuginfod disabled by both environment and command; no network symbol fetch.
        try:self.command('-interpreter-exec console "set debuginfod enabled off"')
        except RuntimeError:pass
    def snapshot(self,game,evid,mark,started):
        item={'requested_seconds':mark,'started_seconds':time.monotonic()-started,
              'game_pid':game.pid,'debugger_pid':self.p.pid,'read_only':True}
        end=time.monotonic()+8
        with self.lock:
            try:
                if game.poll() is not None:raise RuntimeError('OWNED_GAME_EXITED')
                if Path(f'/proc/{game.pid}/exe').resolve()!=Path(game.expected_exe).resolve():
                    raise RuntimeError('OWNED_PID_EXECUTABLE_CHANGED')
                self.text.write(f'\n=== SNAPSHOT {mark}s PID {game.pid} ===\n');self.text.flush()
                self.command('-target-attach '+str(game.pid),timeout=max(.1,end-time.monotonic()))
                self.command('-interpreter-exec console "thread apply all bt 18"',timeout=max(.1,end-time.monotonic()))
                self.command('-target-detach',timeout=max(.1,end-time.monotonic()))
                item['result']='STACKS_CAPTURED_AND_DETACHED'
            except Exception as exc:
                item['result']='STACK_CAPTURE_FAILED';item['error']=str(exc)
                # Killing only our tracer releases its attach if a command wedged.
                stop_owned(self.p)
                if game.poll() is None:
                    try:os.kill(game.pid,signal.SIGCONT)
                    except ProcessLookupError:pass
            finally:
                item['finished_seconds']=time.monotonic()-started
                save(evid/f'stack-{mark}-status.json',item)
        return item
    def close(self):
        if self.closed:return
        stop_owned(self.p)
        for stream in (self.p.stdin,self.p.stdout):
            if stream:stream.close()
        self.log.close();self.text.close();self.closed=True


def child_exec(args):
    """Process-local debugger authorization before exec; never alter Yama sysctls."""
    pid=int(args[0]);target=Path(args[1]).resolve()
    if pid<=1 or not target.is_file():raise RuntimeError('INVALID_CHILD_TARGET_OR_DEBUGGER')
    policy=Path('/proc/sys/kernel/yama/ptrace_scope')
    scope=int(policy.read_text()) if policy.is_file() else 0
    if scope>1:raise RuntimeError('PTRACE_POLICY_DISALLOWS_CHILD')
    if scope==1:
        libc=ctypes.CDLL(None,use_errno=True)
        prctl=libc.prctl;prctl.restype=ctypes.c_int
        if prctl(ctypes.c_int(0x59616d61),ctypes.c_ulong(pid),ctypes.c_ulong(0),ctypes.c_ulong(0),ctypes.c_ulong(0))!=0:
            raise OSError(ctypes.get_errno(),'PR_SET_PTRACER failed; no game started')
    # Replaces this owned child only, never the interactive SSH shell.
    os.execv(str(target),[str(target),*args[2:]])


def activity(pid,root=Path('/proc')):
    p=root/str(pid);out={'monotonic':time.monotonic(),'pid':pid,'threads':[],'drm_clients':[]}
    for t in sorted((p/'task').glob('[0-9]*'))[:256]:
        try:
            raw=(t/'stat').read_text();v=raw.rsplit(')',1)[1].split()
            entry={'tid':int(t.name),'name':(t/'comm').read_text().strip(),'state':v[0],
                   'utime_ticks':int(v[11]),'stime_ticks':int(v[12])}
            for name in ('wchan','syscall'):
                try:entry[name]=(t/name).read_text()[:1024].strip()
                except OSError as exc:entry[name+'_error']=str(exc)
            out['threads'].append(entry)
        except (OSError,ValueError,IndexError) as exc:out['threads'].append({'tid':t.name,'error':str(exc)})
    seen=set()
    for f in sorted((p/'fdinfo').glob('[0-9]*'))[:1024]:
        try:
            fields=dict(line.split(':',1) for line in f.read_text()[:16384].splitlines() if line.startswith('drm-') and ':' in line)
            fields={k:v.strip() for k,v in fields.items()}
            if not fields:continue
            identity=(fields.get('drm-pdev'),fields.get('drm-client-id'))
            fields.update(fd=f.name,duplicate_client_identity=identity in seen)
            out['drm_clients'].append(fields);seen.add(identity)
        except OSError:pass
    for name in ('gpu_busy_percent','mem_info_vram_used','mem_info_vram_total'):
        values={}
        for f in Path('/sys/class/drm').glob('card*/device/'+name):
            try:values[str(f)]=int(f.read_text())
            except (OSError,ValueError):pass
        out[name]=values
    return out


def collect_source(source,evid):
    inventory={};total=0
    for rel in SOURCE_PATHS:
        p=source/rel
        if not p.is_file():inventory[rel]={'status':'MISSING'};continue
        if p.is_symlink() or not p.resolve().is_relative_to(source.resolve()):raise RuntimeError('SOURCE_LINK_REFUSED:'+rel)
        size=p.stat().st_size;total+=size
        if size>4*1024**2 or total>24*1024**2:raise RuntimeError('SOURCE_CAPTURE_LIMIT')
        dest=evid/'exact-source'/rel;dest.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(p,dest)
        inventory[rel]={'sha256':sha(p),'bytes':size}
    save(evid/'source-inventory.json',inventory);return inventory


def pixel_digest(path):
    # Prefer a raster decoder, never an optional/blank ImageMagick signature property.
    try:
        from PIL import Image
        with Image.open(path) as im:
            if im.width*im.height>32*1024**2:raise ValueError('FRAME_SIZE_LIMIT')
            data=im.convert('RGB').tobytes()
            return {'rgb_sha256':hashlib.sha256(data).hexdigest(),'size':[im.width,im.height],'decoder':'Pillow'}
    except ImportError:pass
    exe=shutil.which('magick') or shutil.which('convert')
    if not exe:return {'error':'NO_PIXEL_DECODER','file_sha256':sha(path)}
    p=None
    try:
        p=sp.Popen([exe,str(path),'-alpha','off','-depth','8','RGB:-'],stdout=sp.PIPE,stderr=sp.PIPE,start_new_session=True)
        data,error=p.communicate(timeout=8)
        if p.returncode or not data or len(data)>96*1024**2:return {'error':'PIXEL_DECODE_FAILED','detail':error.decode(errors='replace')[:300]}
        return {'rgb_sha256':hashlib.sha256(data).hexdigest(),'decoder':'ImageMagick RGB8','bytes':len(data)}
    except Exception as exc:return {'error':str(exc)}
    finally:
        stop_owned(p)
        if p:
            for f in (p.stdout,p.stderr):
                if f:f.close()


def run_capture(u,binary,eboot,work,evid,screen,debugger,*,duration=118,marks=(40,55,85,112),stack_marks=(65,95)):
    start=time.monotonic();process=None;caps=[];frames=[];workers=[];stacks=[];timed=False
    marklist=list(marks);stacklist=list(stack_marks);next_sample=0;next_beat=15
    try:
        with (evid/'game.log').open('wb') as log,(evid/'capture.log').open('ab') as clog,(evid/'activity.jsonl').open('w') as alog:
            args=[sys.executable,str(Path(__file__).resolve()),'--exec-child',str(debugger.p.pid),str(binary),
                  '--game',str(eboot),'--fullscreen','true','--show-fps']
            process=sp.Popen(args,cwd=work,env=dict(os.environ,DISPLAY=screen),stdin=sp.DEVNULL,
                             stdout=log,stderr=sp.STDOUT,start_new_session=True)
            process.expected_exe=str(binary)
            emit(f'GAME_PID={process.pid} READ_ONLY_STACKS={stack_marks} MAX_SECONDS={duration}+2')
            while process.poll() is None:
                now=time.monotonic();elapsed=now-start
                if elapsed>=duration:timed=True;break
                if elapsed>=next_sample:
                    record=activity(process.pid);record['elapsed_seconds']=elapsed
                    alog.write(json.dumps(record)+'\n');alog.flush();next_sample=elapsed+5
                if marklist and elapsed>=marklist[0]:
                    mark=marklist.pop(0);target=evid/f'menu-{mark}.png'
                    cmd=['import','-window','root',str(target)] if shutil.which('import') else ['maim',str(target)]
                    cap=sp.Popen(cmd,env=dict(os.environ,DISPLAY=screen),stdout=clog,stderr=sp.STDOUT,start_new_session=True)
                    caps.append((cap,now,{'requested_seconds':mark,'started_seconds':elapsed,'file':target.name}))
                if stacklist and elapsed>=stacklist[0]:
                    mark=stacklist.pop(0)
                    if debugger.p.poll() is None:
                        worker=threading.Thread(target=lambda m=mark:stacks.append(debugger.snapshot(process,evid,m,start)),daemon=True)
                        worker.start();workers.append(worker);emit('READ_ONLY_STACK_SNAPSHOT='+str(mark))
                for cap,then,item in caps[:]:
                    if cap.poll() is not None or now-then>6:
                        stop_owned(cap);item.update(exit_code=cap.returncode,finished_seconds=time.monotonic()-start,game_alive_at_finish=process.poll() is None)
                        frames.append(item);caps.remove((cap,then,item))
                if elapsed>=next_beat:emit(f'CAPTURE_ACTIVE elapsed={elapsed:.0f}s');next_beat=elapsed+15
                time.sleep(.05)
    finally:
        # Release any diagnostic stop before applying the game's fixed deadline.
        if any(w.is_alive() for w in workers):
            stop_owned(debugger.p)
            if process and process.poll() is None:
                try:os.kill(process.pid,signal.SIGCONT)
                except ProcessLookupError:pass
        stop_owned(process)
        for w in workers:w.join(timeout=1)
        for cap,then,item in caps:
            stop_owned(cap);item.update(exit_code=cap.returncode,game_alive_at_finish=False);frames.append(item)
        save(evid/'capture-times.json',frames);save(evid/'stack-snapshots.json',stacks)
    return {'seconds':round(time.monotonic()-start,3),'time_limit_reached':timed,
            'exit_code':process.returncode if process else None,'input_injected':False,
            'debugger_pauses':'two bounded read-only stack snapshots; not a normalized FPS test'}


def main():
    here=Path(__file__).resolve().parent
    u=load('frame_runtime',here/'resume_c925_revalidation.py',RUNTIME_SHA)
    h=load('frame_headroom',here/'resume_finite_raw.py',HEADROOM_SHA)
    project=Path.home()/'projects/shadps4';logs=project/'logs'
    stamp=dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    evid=logs/('got-c925-frame-boundary-'+stamp);evid.mkdir(parents=True)
    candidate=project/'GhostOfTsushima/experiments'/BASE;source=candidate/'source';binary=candidate/'build/shadps4'
    result={'record_id':evid.name,'build':'NOT_RUN','source_changes':False,'config_changes':False,
            'expected_elf':EXPECTED,'game':{'result':'NOT_RUN'},'diagnostic_only':True,'graphics_fixed':'UNVERIFIED'}
    emit('EVIDENCE_DIRECTORY='+str(evid));emit('NO_REBUILD=YES NO_RENDERER_EDIT=YES')
    locks=[];pending=[];originals={};source_inventory={};api=u.Ollama(evid);debugger=None;rc=1
    expected={**h.SOURCES,**h.KEPT,**LATEST}
    try:
        for name in ('.got-pr95-scalar-bounds-build.lock','.got-indexed-revalidation.lock','.got-finite-raw-image.lock','.got-float-atomic.lock'):
            lock=(logs/name).open('a+')
            try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
            except Exception:lock.close();raise
            locks.append(lock)
        prior=json.loads((logs/PREVIOUS/'trial.json').read_text())
        if prior.get('build')!='PASS' or prior.get('candidate_sha256')!=EXPECTED or sha(binary)!=EXPECTED:
            raise RuntimeError('CANDIDATE_IDENTITY_CHANGED_NO_GAME')
        for rel,d in expected.items():
            p=source/rel
            if p.is_symlink() or not p.resolve().is_relative_to(source.resolve()) or sha(p)!=d:raise RuntimeError('KEPT_SOURCE_CHANGED:'+rel)
        source_inventory=collect_source(source,evid)
        if u.emulator_pids():raise RuntimeError('OTHER_EMULATOR_RUNNING_NO_GAME')
        for p in Path('/proc').glob('[0-9]*'):
            try:
                if b'ninja' in (p/'cmdline').read_bytes() and (p/'cwd').resolve()==candidate/'build':raise RuntimeError('CANDIDATE_BUILD_RUNNING')
            except OSError:pass
        profile=h.verified_profile(candidate,{'diagnostic_profile':prior['private_profile']})
        result['private_profile']=str(profile)
        old=json.loads((logs/PREVIOUS/'original-files-before.json').read_text())
        originals={p:sha(p) if Path(p).is_file() else None for p in old}
        save(evid/'original-files-before.json',originals)
        debugger_exe=shutil.which('gdb') or shutil.which('gdb-multiarch')
        if not debugger_exe:raise RuntimeError('GDB_MISSING_SOURCE_CAPTURE_ONLY_NO_GAME')
        policy=Path('/proc/sys/kernel/yama/ptrace_scope')
        if policy.is_file():
            result['ptrace_scope']=int(policy.read_text())
            if result['ptrace_scope']>1:raise RuntimeError('PTRACE_POLICY_DISALLOWS_CAPTURE_NO_GAME')
        (evid/'gdb-version.txt').write_bytes(u.command([debugger_exe,'--version'],timeout=5))
        screen=os.environ.get('DISPLAY') or ':0'
        if not shutil.which('xdpyinfo') or not (shutil.which('import') or shutil.which('maim')):raise RuntimeError('DISPLAY_OR_CAPTURE_TOOL_MISSING')
        (evid/'display.log').write_bytes(u.command(['env','DISPLAY='+screen,'xdpyinfo'],timeout=8))
        eboot=Path('/mnt/roms-all/ps4-games/CUSA11456/eboot.bin')
        if not eboot.is_file():raise RuntimeError('GAME_PATH_MISSING')
        debugger=MI([debugger_exe,'--nx','--nh','--quiet','--interpreter=mi2','-iex','set auto-load off'],evid)
        debugger.setup()
        # Prove this host debugger can attach/detach a disposable owned helper
        # before any game launch or Ollama unload. No permission/sysctl changes.
        probe=None
        try:
            sleep_exe=Path(shutil.which('sleep') or '/bin/sleep').resolve()
            probe=sp.Popen([sys.executable,str(Path(__file__).resolve()),'--exec-child',str(debugger.p.pid),str(sleep_exe),'15'],
                           stdout=sp.DEVNULL,stderr=sp.DEVNULL,start_new_session=True)
            probe.expected_exe=str(sleep_exe)
            deadline=time.monotonic()+3
            while probe.poll() is None and time.monotonic()<deadline:
                if Path(f'/proc/{probe.pid}/exe').resolve()==sleep_exe:break
                time.sleep(.05)
            proof=debugger.snapshot(probe,evid,'preflight',time.monotonic())
            if proof['result']!='STACKS_CAPTURED_AND_DETACHED':raise RuntimeError('DEBUGGER_PREFLIGHT_FAILED_NO_GAME')
        finally:stop_owned(probe)
        h.verified_headroom(u,api,evid,pending,result)
        if u.emulator_pids() or sha(binary)!=EXPECTED or u.free_vram()<h.MIN_FREE:raise RuntimeError('LAUNCH_PREFLIGHT_CHANGED')
        result['game']=run_capture(u,binary,eboot,profile.parent,evid,screen,debugger)
        result['tested_games']=['CUSA11456']
        result['status']='FRAME_BOUNDARY_CAPTURED_REVIEW_REQUIRED' if result['game']['time_limit_reached'] else 'OWNED_GAME_EXITED_EARLY'
        rc=0 if result['game']['time_limit_reached'] else 1
    except (Exception,KeyboardInterrupt) as exc:
        result['status']=str(exc) or type(exc).__name__
        (evid/'failure.txt').write_text(traceback.format_exc());emit('STOP='+result['status'])
        rc=130 if isinstance(exc,KeyboardInterrupt) else 1
    finally:
        if debugger:debugger.close()
        if pending:
            result['ollama_restore']=u.restore_models(api,pending,evid)
            if any(x['result'] in ('RESTORE_FAILED','RESTORE_NOT_VERIFIED') for x in result['ollama_restore']):rc=1
        try:
            result['pixel_signatures']={p.name:pixel_digest(p) for p in sorted(evid.glob('menu-*.png'))}
            result['original_files_changed']=[p for p,d in originals.items() if (sha(p) if Path(p).is_file() else None)!=d]
            result['captured_source_changes']=[p for p,v in source_inventory.items() if 'sha256' in v and (not (source/p).is_file() or sha(source/p)!=v['sha256'])]
            result['candidate_after_sha256']=sha(binary) if binary.is_file() else None
            if result['original_files_changed'] or result['captured_source_changes'] or result['candidate_after_sha256']!=EXPECTED:rc=1
        except Exception as exc:result['verification_error']=str(exc);rc=1
        result['runner_exit_code']=rc;save(evid/'trial.json',result)
        with tarfile.open(str(evid)+'.tar.gz','w:gz') as t:t.add(evid,arcname=evid.name)
        emit('RESULT='+result.get('status','UNKNOWN'));emit('EVIDENCE='+str(evid)+'.tar.gz')
        for lock in locks:lock.close()
    return rc

if __name__=='__main__':
    if len(sys.argv)>1 and sys.argv[1]=='--exec-child':child_exec(sys.argv[2:])
    else:
        def terminate(signum,frame):raise KeyboardInterrupt('TERMINATED_BY_USER')
        signal.signal(signal.SIGTERM,terminate)
        sys.exit(main())
