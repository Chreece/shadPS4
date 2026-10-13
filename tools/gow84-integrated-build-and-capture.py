#!/usr/bin/env python3
"""GoW84 DS ordered-count + verified Phase80 GPU fixes. Never edits older builds."""
from __future__ import annotations
import argparse, datetime, fcntl, hashlib, json, os, re, shutil, signal, subprocess, sys, tarfile, time, traceback
from pathlib import Path

HOME=Path.home()
ROOT=HOME/'projects/shadps4/GodOfWarRagnarok'
LOGS=HOME/'projects/shadps4/logs'
BASE=ROOT/'builds/gow80-sparse-queue-20261013-145953-3525134'
BASE_BINARY='03d9cfad127eb0d08d40fdb7f8dd7494ea0f1e2ade87363afd6b0e929e6960f3'
BASE_RASTER='e20d7f2705e087d7feae54bc9a1a449b6554101c3174f3bb7774e194baf2f212'
PIN='966fa45bc36ce2e46b8c14f77acb5fdd95d96fbe'
URL='https://codeload.github.com/Chreece/shadPS4/tar.gz/'+PIN
PATHS=["src/shader_recompiler/backend/spirv/emit_spirv_atomic.cpp","src/shader_recompiler/backend/spirv/emit_spirv_barriers.cpp","src/shader_recompiler/backend/spirv/emit_spirv_instructions.h","src/shader_recompiler/backend/spirv/emit_spirv_special.cpp","src/shader_recompiler/backend/spirv/emit_spirv_warp.cpp","src/shader_recompiler/backend/spirv/spirv_emit_context.cpp","src/shader_recompiler/backend/spirv/spirv_emit_context.h","src/shader_recompiler/frontend/control_flow_graph.cpp","src/shader_recompiler/frontend/translate/data_share.cpp","src/shader_recompiler/frontend/translate/scalar_flow.cpp","src/shader_recompiler/frontend/translate/translate.cpp","src/shader_recompiler/frontend/translate/translate.h","src/shader_recompiler/info.h","src/shader_recompiler/ir/ir_emitter.cpp","src/shader_recompiler/ir/ir_emitter.h","src/shader_recompiler/ir/microinstruction.cpp","src/shader_recompiler/ir/opcodes.inc","src/shader_recompiler/ir/passes/lower_wave64_pass.cpp","src/shader_recompiler/ir/passes/resource_pass.h","src/shader_recompiler/ir/passes/resource_patching_pass.cpp","src/shader_recompiler/ir/passes/shader_info_collection_pass.cpp","src/shader_recompiler/recompiler.cpp","src/shader_recompiler/runtime_info.h","src/video_core/amdgpu/regs_shader.h","src/video_core/buffer_cache/buffer_cache.cpp","src/video_core/buffer_cache/buffer_cache.h","src/video_core/renderer_vulkan/vk_instance.cpp","src/video_core/renderer_vulkan/vk_instance.h","src/video_core/renderer_vulkan/vk_pipeline_cache.cpp","src/video_core/renderer_vulkan/vk_pipeline_serialization.cpp","src/video_core/renderer_vulkan/vk_rasterizer.cpp","src/video_core/renderer_vulkan/vk_rasterizer.h","tests/gcn/gcn_test_runner.cpp","tests/gcn/gcn_test_runner.hpp","tests/gcn/instructions.hpp","tests/gcn/test_gcn_instructions.cpp","tests/gcn/translator.cpp","tests/gcn/translator.hpp","tests/stubs/resource_tracking_pass_stub.cpp"]

def digest(path):
    hasher=hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda:stream.read(1048576),b''):
            hasher.update(chunk)
    return hasher.hexdigest()

def note(out,msg):
    print('GOW84 '+msg,flush=True)
    with (out/'steps.log').open('a') as f:f.write(msg+'\n')

def kill_child(proc):
    if proc.poll() is not None:return
    pg=os.getpgid(proc.pid)
    if pg!=proc.pid or pg==os.getpgrp():raise RuntimeError('Refusing to kill SSH/shared process group')
    os.killpg(pg,signal.SIGTERM)
    try:proc.wait(timeout=7)
    except subprocess.TimeoutExpired:os.killpg(pg,signal.SIGKILL);proc.wait(timeout=7)

def run(cmd,path,timeout=5000):
    with path.open('wb') as f:
        child=subprocess.Popen(list(map(str,cmd)),stdin=subprocess.DEVNULL,
                               stdout=f,stderr=subprocess.STDOUT,start_new_session=True)
        try:code=child.wait(timeout=timeout)
        except subprocess.TimeoutExpired:kill_child(child);raise RuntimeError('Timeout in '+str(path))
    if code:
        tail='\n'.join(path.read_text(errors='replace').splitlines()[-18:])
        raise RuntimeError('Command failed code '+str(code)+'; '+str(path)+'\n'+tail)

def running_emulator():
    for p in Path('/proc').iterdir():
        if not p.name.isdecimal():continue
        try:
            if p.stat().st_uid==os.getuid() and Path(os.readlink(p/'exe').removesuffix(' (deleted)')).name.lower()=='shadps4':return True
        except OSError:pass
    return False

def preflight():
    if running_emulator():raise RuntimeError('Another shadps4 instance is running')
    if digest(BASE/'build/shadps4')!=BASE_BINARY:raise RuntimeError('Protected Phase80 binary checksum mismatch')
    if digest(BASE/'source/src/video_core/renderer_vulkan/vk_rasterizer.cpp')!=BASE_RASTER:raise RuntimeError('Protected Phase80 source checksum mismatch')
    if not Path('/mnt/roms-all/ps4-games/CUSA34384').is_dir():raise RuntimeError('GoW CUSA34384 missing')
    for source in (BASE/'source',BASE/'build/_deps',BASE/'build/cpu-id-runtime-source'):
        if not source.is_dir():raise RuntimeError('Required offline source missing: '+str(source))
    for exe in ('cmake','ninja','cp','curl'):
        if not shutil.which(exe):raise RuntimeError('Missing command '+exe)

def prepare(out,work):
    (work/'build').mkdir(parents=True)
    for src,dst in ((BASE/'source',work/'source'),(BASE/'build/_deps',work/'build/_deps'),(BASE/'build/cpu-id-runtime-source',work/'build/cpu-id-runtime-source')):
        run(['cp','-a','--reflink=auto','--',src,dst],out/('copy-'+dst.name+'.log'),1800)
    for cache in (work/'build').rglob('CMakeCache.txt'):
        if cache.is_symlink():raise RuntimeError('Unexpected symlinked CMake cache')
        cache.unlink()
    archive=out/'integrated-source.tar.gz'
    run(['curl','-4','-fLSs','--retry','2','--connect-timeout','20','--max-time','420',URL,'-o',archive],out/'fetch.log',480)
    records=[]
    with tarfile.open(archive,'r:gz') as tar:
        selected={}
        for item in tar.getmembers():
            if item.isfile() and '/' in item.name:
                path=item.name.split('/',1)[1]
                if path in PATHS:
                    if path in selected:raise RuntimeError('Duplicate tar source '+path)
                    selected[path]=item
        if set(selected)!=set(PATHS):raise RuntimeError('Incomplete pinned source: '+str(set(PATHS)-set(selected)))
        for name in PATHS:
            target=work/'source'/name
            if target.is_symlink() or not target.is_file() or selected[name].size>12000000:raise RuntimeError('Unsafe source file '+name)
            before=digest(target)
            data=tar.extractfile(selected[name]).read()
            if len(data)!=selected[name].size:raise RuntimeError('Truncated source '+name)
            target.write_bytes(data)
            records.append({'path':name,'before_sha256':before,'after_sha256':digest(target)})
    (out/'overlaid-files.json').write_text(json.dumps(records,indent=2)+'\n')
    (out/'source-sha.json').write_text(json.dumps({'pinned_commit':PIN,'original_archive_sha256':digest(archive)},indent=2)+'\n')
    archive.unlink()
    checks={
      'src/shader_recompiler/frontend/translate/data_share.cpp':'DS_ORDERED_COUNT',
      'src/shader_recompiler/backend/spirv/emit_spirv_atomic.cpp':'EmitBufferOrderedCount',
      'src/video_core/buffer_cache/buffer_cache.cpp':'GetSparseQueue().bindSparse',
      'src/video_core/buffer_cache/buffer_cache.h':'STREAM_THRESHOLD = 64_KB',
      'src/video_core/renderer_vulkan/vk_pipeline_serialization.cpp':'ShaderBinaryVersion = 8u'}
    for path,token in checks.items():
        if token not in (work/'source'/path).read_text():raise RuntimeError('Integrated code missing '+token)
    note(out,'SOURCE=PASS 39 source files; Phase80 fixes and CPU-ID preserved')

def game_environment():
    e=os.environ.copy()
    for key in list(e):
        if key.startswith(('SHADPS4_GOW','ACO_DEBUG')) or key.startswith('SHADPS4_') and 'SKIP' in key:e.pop(key,None)
    e['RADV_DEBUG']='hang';e.setdefault('DISPLAY',':0')
    if (HOME/'.Xauthority').is_file():e.setdefault('XAUTHORITY',str(HOME/'.Xauthority'))
    if (Path('/run/user')/str(os.getuid())).is_dir():e.setdefault('XDG_RUNTIME_DIR','/run/user/'+str(os.getuid()))
    return e

def photo(folder,pid,env,seconds):
    data={'seconds':round(seconds,2),'status':'UNAVAILABLE'}
    if not all(shutil.which(x) for x in ('xdotool','xwininfo','ffmpeg')):return data
    try:
        windows=subprocess.run(['xdotool','search','--pid',str(pid)],env=env,capture_output=True,text=True,timeout=3)
        if windows.returncode:return data
        for wid in windows.stdout.splitlines():
            if not wid.isdecimal():continue
            title=subprocess.run(['xdotool','getwindowname',wid],env=env,capture_output=True,text=True,timeout=3).stdout
            if 'CUSA34384' not in title and 'God of War Ragnar' not in title:continue
            details=subprocess.run(['xwininfo','-id',wid],env=env,capture_output=True,text=True,timeout=3)
            if details.returncode:continue
            def f(label):
                m=re.search(re.escape(label)+r':\s*(-?\d+)',details.stdout)
                return int(m.group(1)) if m else 0
            x,y=f('Absolute upper-left X'),f('Absolute upper-left Y')
            w,h=f('Width'),f('Height')
            if w<129 or h<129:continue
            filename=f'{seconds:05.1f}s.jpg';dest=folder/filename
            result=subprocess.run(['ffmpeg','-hide_banner','-loglevel','error','-y','-f','x11grab',
                     '-video_size',f'{w}x{h}','-i',env['DISPLAY']+f'+{x},{y}',
                     '-frames:v','1','-q:v','3',str(dest)],env=env,capture_output=True,timeout=10)
            if result.returncode==0 and dest.is_file() and dest.stat().st_size>500:
                data.update({'status':'CAPTURED','filename':filename,'sha256':digest(dest),'scope':'verified_game_window'})
            return data
    except (OSError,ValueError,subprocess.TimeoutExpired):return data
    return data

def gameplay(binary,out):
    normal=out/'game';normal.mkdir();shots=normal/'screenshots';shots.mkdir()
    env=game_environment()
    with (normal/'console.log').open('wb') as f:
        p=subprocess.Popen([str(binary),'--cpu-id-mode','auto','--game','CUSA34384','--fullscreen','true'],
             cwd=normal,env=env,stdin=subprocess.DEVNULL,stdout=f,stderr=subprocess.STDOUT,start_new_session=True)
        start=time.monotonic();screens=[];times=[2,5,10,16,24,32,45];i=0
        try:
            while time.monotonic()-start<55 and p.poll() is None:
                now=time.monotonic()-start
                if i<len(times) and now>=times[i]:
                    screens.append(photo(shots,p.pid,env,now));i+=1
                time.sleep(.25)
        finally:
            returncode=p.poll();kill_child(p)
    console=(normal/'console.log').read_text(errors='replace')
    return {'pid':p.pid,'returncode':returncode,'seconds':round(time.monotonic()-start,2),
            'screenshots':screens,'radv_hang':'GPU hang detected' in console,
            'sparse_queue_ok':'Graphics queue family: 0, sparse queue family: 4' in console,
            'radv_dumps':[d.name for d in HOME.glob(f'radv_dumps_{p.pid}_*') if d.is_dir()]}

def package(out,result):
    (out/'result.json').write_text(json.dumps(result,indent=2,sort_keys=True)+'\n')
    path=LOGS/(out.name+'.tar.gz');used=0
    with tarfile.open(path,'w:gz',compresslevel=3) as tar:
        for p in sorted(out.rglob('*')):
            if p.is_file() and p.stat().st_size<50000000 and used+p.stat().st_size<250000000:
                tar.add(p,arcname=str(Path(out.name)/p.relative_to(out)));used+=p.stat().st_size
        for label in result.get('gameplay',{}).get('radv_dumps',[]):
            dump=HOME/label
            if not label.startswith('radv_dumps_') or not dump.is_dir():continue
            for p in dump.rglob('*'):
                if p.is_file() and p.stat().st_size<50000000 and used+p.stat().st_size<250000000:
                    tar.add(p,arcname=str(Path(out.name)/'radv'/label/p.relative_to(dump)));used+=p.stat().st_size
    print('GOW84_REPORT='+str(path),flush=True)

def main():
    arg=argparse.ArgumentParser()
    arg.add_argument('--self-test',action='store_true')
    arg.add_argument('--build-only',action='store_true')
    opts=arg.parse_args()
    if opts.self_test:
        assert len(PATHS)==39 and len(set(PATHS))==39 and all(p.startswith(('src/','tests/')) for p in PATHS)
        assert len(PIN)==40 and len(BASE_BINARY)==64 and URL.endswith(PIN)
        print('GOW84_SELF_TEST=PASS pinned source manifest and isolation contract')
        return 0
    LOGS.mkdir(parents=True,exist_ok=True)
    with (LOGS/'gow84-exclusive.lock').open('a+') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        stamp=datetime.datetime.now().strftime('%Y%m%d-%H%M%S')
        name='gow84-ds-ordered-'+stamp+'-'+str(os.getpid())
        out=LOGS/name;work=ROOT/'builds'/name;out.mkdir(exist_ok=False)
        result={'status':'STARTED','phase':'PREFLIGHT','pinned_source':PIN}
        try:
            preflight();note(out,'PREFLIGHT=PASS protected Phase80 baseline')
            work.mkdir(parents=True,exist_ok=False)
            result['phase']='SOURCE';prepare(out,work)
            cache=[]
            if Path('/usr/bin/ccache').is_file():cache=['-DCMAKE_C_COMPILER_LAUNCHER=/usr/bin/ccache','-DCMAKE_CXX_COMPILER_LAUNCHER=/usr/bin/ccache']
            result['phase']='CONFIGURE'
            run(['cmake','-S',work/'source','-B',work/'build','-G','Ninja','-DCMAKE_BUILD_TYPE=Release',
                 '-DENABLE_TESTS=OFF','-DENABLE_UPDATER=OFF','-DENABLE_CPU_ID_TRANSLATION=ON',
                 '-DFETCHCONTENT_FULLY_DISCONNECTED=ON',*cache],out/'configure.log',1400)
            result['phase']='BUILD'
            run(['cmake','--build',work/'build','--target','shadps4','--parallel','4'],out/'build.log',6000)
            binary=work/'build/shadps4'
            if not binary.is_file() or binary.is_symlink():raise RuntimeError('No newly linked emulator')
            result['binary_sha256']=digest(binary);note(out,'BUILD=PASS integrated source')
            if running_emulator():raise RuntimeError('Another emulator started; stopping before gameplay')
            if opts.build_only:result['status']='BUILD_PASS'
            else:
                result['phase']='RUNTIME';result['gameplay']=gameplay(binary,out)
                result['status']='RUNTIME_CAPTURED'
            return 0
        except BaseException as e:
            result['status']='FAILED';result['error']=repr(e)
            (out/'traceback.txt').write_text(traceback.format_exc())
            note(out,'FAILED '+result['phase']+' '+repr(e))
            return 1
        finally:
            try:
                result['phase80_preserved']=(
                    digest(BASE/'build/shadps4')==BASE_BINARY and
                    digest(BASE/'source/src/video_core/renderer_vulkan/vk_rasterizer.cpp')==BASE_RASTER)
                if not result['phase80_preserved']:result['status']='BASELINE_INTEGRITY_FAILURE'
                package(out,result)
            except BaseException as e:print('GOW84_REPORT_ERROR='+repr(e),flush=True)

if __name__=='__main__':sys.exit(main())
