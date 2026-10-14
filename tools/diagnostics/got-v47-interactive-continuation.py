#!/usr/bin/env python3
"""Ten-minute, user-controlled continuation of the verified GoT v46 executable.

No build, patch, GDB, UMR, watchpoint, input injection or privileged command.
Clones only the last v46 private profile, verifies the mapped private driver,
keeps screenshots/error dumps, and preserves all existing builds and saves.
The test's settings/saves remain in its private ready directory, not the report.
"""
from __future__ import annotations
import datetime as dt
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import signal
import stat
import subprocess
import sys
import tarfile
import tempfile
import time

LABEL = 'got-v47-interactive-visual'
TREE = 'got-v46-bitfield64-lowering-20261014'
BINARY_SHA = 'a22d65b9b958f743154bc1e9d2b3019498336ee5530ab480e52fe3c66a575e76'
DRIVER_SHA = '87908e0c6833c0f6dc86cb079cb0f34de0dee7e970da89b5303f0e6b29e326f7'
SYSTEM_SHA = '7ad998b763b899649facdb4e53ac37d666dff372e84184accc0d19508e8c55e1'
SYSTEM_DRIVER = Path('/usr/lib/x86_64-linux-gnu/libvulkan_radeon.so')
GAME = Path('/mnt/roms-all/ps4-games/CUSA11456/eboot.bin')
DURATION = 600
SOURCE_HASHES = {
 'src/shader_recompiler/backend/spirv/emit_spirv_integer.cpp': '655e4bfa8c37a849c9fe7ddb11d2cfb7db2f3a20cea42b1343970524b2e6ae05',
 'src/video_core/amdgpu/liverpool.cpp': '80bb1db2414b52feb339196833d385b548056395a746a80ad59c85ba121713fb',
 'src/video_core/renderer_vulkan/vk_pipeline_cache.cpp': '4fe3da5ca6e77ccdbe9c25309cdb84cbcf11ad0d9b795829ef4ea83b21ba7f6f',
}
ANSI = re.compile(r'\x1b\[[0-9;]*[A-Za-z]')


def now() -> str:
    return dt.datetime.now().astimezone().isoformat()


def regular(p: Path, limit: int = 512*1024*1024) -> bool:
    return p.is_file() and not p.is_symlink() and p.stat().st_size <= limit


def digest(p: Path) -> str:
    if not regular(p): raise RuntimeError('Missing/unsafe hash input: '+str(p))
    with p.open('rb') as f: return hashlib.file_digest(f, 'sha256').hexdigest()


def read_json(p: Path) -> dict:
    if not regular(p, 1_000_000): raise RuntimeError('Missing/unsafe JSON input: '+str(p))
    value = json.loads(p.read_text(encoding='utf-8-sig'))
    if not isinstance(value, dict): raise RuntimeError('Expected JSON object: '+str(p))
    return value


def store(p: Path, data: object) -> None:
    part = p.with_name(p.name+'.part')
    if p.is_symlink() or part.is_symlink(): raise RuntimeError('Unsafe output link: '+str(p))
    part.write_text(json.dumps(data, indent=2)+'\n')
    os.replace(part, p)


def child_directory(p: Path, root: Path, prefix: str) -> None:
    if p.parent != root or not p.name.startswith(prefix) or p.is_symlink() or not p.is_dir():
        raise RuntimeError('Unexpected private directory: '+str(p))
    if p.resolve().parent != root.resolve(): raise RuntimeError('Private directory escaped project')


def run(argv: list[str], timeout: int = 10, env=None, check: bool = True):
    result = subprocess.run(argv, stdin=subprocess.DEVNULL, capture_output=True, text=True,
                            errors='replace', timeout=timeout, env=env)
    if check and result.returncode:
        raise RuntimeError(Path(argv[0]).name+' failed: '+(result.stderr or result.stdout)[-1000:])
    return result


def verify(project: Path) -> dict:
    source = project/'tests'/TREE
    lib = project/'tools/radv-nuw-complete-25.0.7/libvulkan_radeon.so'
    icd = lib.with_name('radeon_icd.json')
    for p in (project, project/'tests', project/'tools', source, source/'build', lib.parent):
        if not p.is_dir() or p.is_symlink(): raise RuntimeError('Missing/unsafe directory: '+str(p))
    checks = {'v46_binary': digest(source/'build/shadps4') == BINARY_SHA,
              'private_driver': digest(lib) == DRIVER_SHA,
              'system_driver': digest(SYSTEM_DRIVER) == SYSTEM_SHA}
    checks.update({rel: digest(source/rel) == sha for rel,sha in SOURCE_HASHES.items()})
    proof = read_json(source/'build/proof.json')
    checks['build_proof'] = proof.get('binary_sha256') == BINARY_SHA and proof.get('private_driver_sha256') == DRIVER_SHA
    checks['icd'] = read_json(icd).get('ICD', {}).get('library_path') == str(lib)
    if not all(checks.values()): raise RuntimeError('Checkpoint mismatch: '+str(checks))
    return {'checks':checks, 'binary':str(source/'build/shadps4'), 'driver':str(lib),
            'icd':str(icd), 'binary_sha256':BINARY_SHA, 'driver_sha256':DRIVER_SHA}


def running_games(proc: Path = Path('/proc')) -> list[int]:
    found=[]
    for p in proc.iterdir():
        if not p.name.isdigit(): continue
        try:
            if p.stat().st_uid != os.getuid(): continue
        except (FileNotFoundError, ProcessLookupError, PermissionError): continue
        names=[]; state='?'
        try: names.append((p/'comm').read_text().strip())
        except (OSError, ValueError): pass
        try: names.append(Path(os.fsdecode((p/'cmdline').read_bytes().split(b'\0')[0])).name)
        except (OSError, ValueError): pass
        try: names.append(Path(os.readlink(p/'exe').removesuffix(' (deleted)')).name)
        except OSError: pass
        try: state=(p/'stat').read_text().rsplit(') ',1)[1].split()[0]
        except (OSError, IndexError): pass
        if state not in ('Z','X') and any(n.lower().startswith('shadps4') for n in names):
            found.append(int(p.name))
    return sorted(found)


def previous_ready(project: Path) -> Path:
    pointer=project.parent/'logs/.got-v46-bitfield64-visual-latest'
    if not regular(pointer, 8000): raise RuntimeError('The last v46 capture pointer is missing; no guessed profile used')
    fields=dict(line.split('=',1) for line in pointer.read_text().splitlines() if '=' in line)
    p=Path(fields.get('prepared',''))
    child_directory(p, project/'tests', 'got-')
    if digest(p/'shadps4') != BINARY_SHA: raise RuntimeError('Last capture is not the verified v46 executable')
    check_profile(p)
    return p


def check_profile(ready: Path) -> None:
    config=read_json(ready/'user/config.json')
    if config.get('General',{}).get('home_dir') != '':
        raise RuntimeError('Nonportable home_dir; original saves will not be used')
    read_json(ready/'user/users.json')
    custom=ready/'user/custom_configs'
    if custom.is_symlink(): raise RuntimeError('Symlinked custom config directory')
    if custom.is_dir():
        for p in custom.glob('*.json'):
            if read_json(p).get('General',{}).get('home_dir','') != '':
                raise RuntimeError('Nonportable per-game home_dir')


def copy_profile(source: Path, ready: Path) -> dict:
    """Copy a bounded allow-list, never shader caches/logs or links to real saves."""
    check_profile(source)
    selected=[]; size=0
    for name in ('config.json','users.json','keys.json','custom_configs','home','inputs'):
        p=source/'user'/name
        if p.is_symlink(): raise RuntimeError('Profile symlink refused: '+str(p))
        if not p.exists(): continue
        entries=[p]+list(p.rglob('*')) if p.is_dir() else [p]
        for f in entries:
            if f.is_symlink() or not (f.is_file() or f.is_dir()): raise RuntimeError('Unsafe profile entry: '+str(f))
            if f.is_file():
                size+=f.stat().st_size
                if size>256*1024*1024 or len(selected)>=10000: raise RuntimeError('Private profile exceeds bounded copy limit')
                selected.append(f)
    (ready/'user').mkdir()
    for p in selected:
        dest=ready/'user'/p.relative_to(source/'user')
        dest.parent.mkdir(parents=True,exist_ok=True)
        shutil.copyfile(p,dest); dest.chmod(0o600)
    for uid in (1000,1001,1002,1003):
        for name in ('savedata','trophy','inputs'):
            (ready/'user/home'/str(uid)/name).mkdir(parents=True,exist_ok=True)
    for name in ('log','shaders','shader/dumps','cache'):
        (ready/'user'/name).mkdir(parents=True,exist_ok=True)
    check_profile(ready)
    return {'source_ready':str(source), 'files_copied':len(selected), 'bytes_copied':size,
            'saves_are_private_copies':True, 'profile_in_report':False}


def game_environment(environment: dict, home: Path, report: Path, icd: Path) -> dict:
    env={k:v for k,v in environment.items() if not k.startswith(('RADV_','ACO_','MESA_','VK_','SHADPS4_'))}
    env.update(HOME=str(report/'radv-home'), SDL_VIDEODRIVER='x11',
       VK_DRIVER_FILES=str(icd), VK_ICD_FILENAMES=str(icd), RADV_DEBUG='hang,noumr',
       MESA_SHADER_CACHE_DISABLE='true', MESA_SPIRV_FAIL_DUMP_PATH=str(report/'spirv-failures'),
       SHADPS4_GHOST_BINDLESS_EXPERIMENT='1')
    # Do not change audio/SDL controller configuration or desktop connection data.
    return env


def mapped_driver(pid: int, binary: Path, lib: Path, proc: Path = Path('/proc')) -> dict | None:
    p=proc/str(pid)
    if (p/'exe').resolve() != binary.resolve(): raise RuntimeError('Game process identity changed')
    rows=[]
    for line in (p/'maps').read_text().splitlines():
        a=line.split(None,5)
        if len(a)!=6 or 'libvulkan_radeon.so' not in a[5]: continue
        if a[5] != str(lib): raise RuntimeError('Wrong RADV mapped into the test: '+a[5])
        rows.append(line)
    if not rows: return None
    if digest(lib)!=DRIVER_SHA: raise RuntimeError('Mapped driver file checksum changed')
    return {'result':'PRIVATE_RADV_MAPPED_FILE_VERIFIED', 'pid':pid, 'path':str(lib),
            'sha256':DRIVER_SHA, 'maps':rows, 'gpu_or_inferior_memory_written':False}


def screenshot(pid: int, report: Path, elapsed: float, env: dict) -> dict:
    label=f'{int(elapsed):03d}s'
    result={'elapsed':round(elapsed,3)}
    # PID-filtered windows only: never capture the user's desktop or another game.
    out=run(['xdotool','search','--onlyvisible','--pid',str(pid)],4,env,False)
    candidates=[x for x in out.stdout.splitlines() if x.isdigit()]
    if not candidates: return dict(result,result='NO_TEST_WINDOW')
    for wid in reversed(candidates):
        owner=run(['xdotool','getwindowpid',wid],3,env,False).stdout.strip()
        if owner!=str(pid): continue
        image=report/'screenshots'/(label+'.png')
        done=run(['import','-window',wid,str(image)],5,env,False)
        if done.returncode or not regular(image,24*1024*1024):
            return dict(result,result='SCREENSHOT_FAILED',error=done.stderr[-500:])
        return dict(result,result='CAPTURED',file=image.name,sha256=digest(image))
    return dict(result,result='NO_PID_VERIFIED_WINDOW')


def stop_child(child) -> None:
    # child is the Popen object created here with start_new_session=True.
    if child.poll() is not None: return
    try: os.killpg(child.pid,signal.SIGTERM)
    except ProcessLookupError: return
    try: child.wait(timeout=5)
    except subprocess.TimeoutExpired:
        try: os.killpg(child.pid,signal.SIGKILL)
        except ProcessLookupError: pass
        child.wait(timeout=3)


def collect(report: Path, ready: Path) -> None:
    manifest=[]; total=0
    for dirname,pattern in (('radv-home','radv_dumps_*'),):
        for folder in sorted((report/dirname).glob(pattern)):
            if not folder.is_dir() or folder.is_symlink(): continue
            dest=report/'radv'/folder.name; dest.mkdir(parents=True,exist_ok=True)
            for p in sorted(folder.iterdir()):
                if p.suffix not in ('.log','.spv','.txt'): continue
                if not regular(p,32*1024*1024) or total+p.stat().st_size>160*1024*1024:
                    manifest.append({'name':p.name,'copied':False}); continue
                total+=p.stat().st_size; shutil.copyfile(p,dest/p.name)
                manifest.append({'name':p.name,'copied':True})
    metrics={'gpu_hang':False,'native_error':False,'graphics_compiles':0,'compute_compiles':0,
             'last_guest_flips':None,'controller_messages':[], 'gameplay_verified':False}
    log=report/'logs/game-stdout.log'
    if regular(log,260*1024*1024):
        with log.open(errors='replace') as f:
            for raw in f:
                line=ANSI.sub('',raw)
                metrics['gpu_hang'] |= 'GPU hang detected' in line
                metrics['native_error'] |= 'Unhandled access violation' in line or 'Unreachable code!' in line
                metrics['graphics_compiles'] += 'Compiling graphics pipeline' in line
                metrics['compute_compiles'] += 'Compiling compute pipeline' in line
                flips=re.search(r'guest_flips=(\d+)',line)
                if flips: metrics['last_guest_flips']=int(flips.group(1))
                if 'controller' in line.lower() and len(metrics['controller_messages'])<40:
                    metrics['controller_messages'].append(line.strip()[:500])
    store(report/'info/metrics.json',metrics)
    store(report/'info/dump-collection.json',manifest)


def pack(report: Path) -> Path:
    archive=report.with_name(report.name+'.tar.gz'); part=archive.with_name(archive.name+'.part')
    if archive.exists(): return archive
    total=0; included=[]; excluded=[]
    with tarfile.open(part,'w:gz') as tar:
        for name in ('info','logs','screenshots','radv','spirv-failures'):
            for p in sorted((report/name).rglob('*')):
                if p.is_dir(): continue
                rel=str(p.relative_to(report))
                if not regular(p,260*1024*1024) or total+p.stat().st_size>512*1024*1024:
                    excluded.append(rel); continue
                total+=p.stat().st_size; included.append(rel)
                tar.add(p,arcname=rel,recursive=False)
        record=report/'info/packaging.json'
        store(record,{'bytes':total,'included_count':len(included),'excluded':excluded,
                      'private_profiles_and_keys_excluded':True})
        tar.add(record,arcname='info/packaging.json',recursive=False)
    os.replace(part,archive)
    return archive


def worker(project: Path, report: Path) -> int:
    child_directory(report,project.parent/'logs',LABEL+'-')
    spec=read_json(report/'info/session.json'); ready=Path(spec['ready'])
    child_directory(ready,project/'tests','got-v47-interactive-ready-')
    if not 60<=spec['duration']<=DURATION: raise RuntimeError('Unexpected capture duration')
    stop_requested=False
    def cancel(*_):
        nonlocal stop_requested
        stop_requested=True
    old={sig:signal.signal(sig,cancel) for sig in (signal.SIGTERM,signal.SIGINT)}
    state={'started':now(),'result':'PREFLIGHT','gameplay_verified':False,
           'debugger':False,'watchpoints':False,'automatic_input':False,'screenshots':[],
           'driver_load_verified':False}
    child=None; output=None; began=time.monotonic()
    try:
        checkpoints=verify(project)
        if digest(ready/'shadps4')!=BINARY_SHA: raise RuntimeError('Private runtime binary differs from v46')
        check_profile(ready)
        busy=running_games()
        if busy:
            state.update(result='BLOCKED_OTHER_SHADPS4',pids=busy)
            return 0
        env=game_environment(os.environ,project.parent.parent.parent,report,Path(checkpoints['icd']))
        run(['xwininfo','-root'],5,env)
        output=(report/'logs/game-stdout.log').open('wb')
        args=[str(ready/'shadps4'),'--cpu-id-mode=native','--game',str(GAME),'--fullscreen','true']
        child=subprocess.Popen(args,cwd=ready,env=env,stdin=subprocess.DEVNULL,
                    stdout=output,stderr=subprocess.STDOUT,start_new_session=True)
        began=time.monotonic(); next_shot=6.; checked=False
        state.update(result='RUNNING',pid=child.pid,args=args)
        store(report/'info/result.json',state)
        while child.poll() is None:
            elapsed=time.monotonic()-began
            if not checked:
                try: proof=mapped_driver(child.pid,ready/'shadps4',Path(checkpoints['driver']))
                except FileNotFoundError:
                    if child.poll() is not None: break
                    proof=None
                if proof:
                    checked=True;store(report/'info/driver-provenance.json',proof)
                elif elapsed>45: raise RuntimeError('Private driver load could not be verified within 45 seconds')
            if elapsed>=next_shot:
                try: state['screenshots'].append(screenshot(child.pid,report,elapsed,env))
                except Exception as exc: state['screenshots'].append({'elapsed':elapsed,'error':str(exc)})
                next_shot=elapsed+(6 if elapsed<90 else 15)
                store(report/'info/screenshots.json',state['screenshots'])
            if stop_requested or (report/'stop-requested').exists():
                state['result']='USER_STOP_REQUEST'; break
            if elapsed>=spec['duration']:
                state['result']='CAPTURE_TIME_LIMIT_NOT_A_CRASH'; break
            if (report/'logs/game-stdout.log').stat().st_size>256*1024*1024:
                state['result']='CAPTURE_LOG_LIMIT_NOT_A_CRASH'; break
            time.sleep(0.2)
        if state['result']=='RUNNING': state['result']='GAME_EXITED'
        state['exit_before_cleanup']=child.poll()
        state['driver_load_verified']=checked
    except Exception as exc:
        state.update(result='CAPTURE_ERROR',error=str(exc))
    finally:
        if child:
            try: stop_child(child)
            except Exception as exc: state['cleanup_error']=str(exc)
            state['game_exit']=child.poll()
        if output: output.close()
        state.update(finished=now(),elapsed=round(time.monotonic()-began,3),private_ready=str(ready))
        try: state['checkpoints_after']=verify(project)['checks']
        except Exception as exc: state['preservation_error']=str(exc)
        store(report/'info/result.json',state)
        try: collect(report,ready)
        except Exception as exc: store(report/'info/collection-error.json',{'error':str(exc)})
        for sig,handler in old.items(): signal.signal(sig,handler)
        print('UPLOAD='+str(pack(report)),flush=True)
    return 1 if state['result']=='CAPTURE_ERROR' else 0


def latest(project: Path) -> Path | None:
    p=project.parent/'logs'/('.'+LABEL+'-latest.json')
    if not p.exists(): return None
    report=Path(read_json(p)['report']); child_directory(report,project.parent/'logs',LABEL+'-')
    return report


def active(unit: str) -> bool:
    p=run(['systemctl','--user','show',unit,'--property=LoadState','--property=ActiveState'],10,check=False)
    values=dict(line.split('=',1) for line in p.stdout.splitlines() if '=' in line)
    if values.get('LoadState') == 'not-found': return False
    if p.returncode or 'ActiveState' not in values: raise RuntimeError('Cannot determine capture unit state')
    return values['ActiveState'] not in ('inactive','failed')


def status(project: Path, stop: bool = False) -> int:
    report=latest(project)
    if report is None:
        print('CAPTURE=NOT_SCHEDULED'); return 0
    session=read_json(report/'info/session.json')
    is_active=active(session['unit'])
    if stop and is_active:
        (report/'stop-requested').touch(exist_ok=True)
        print('STOP_REQUEST=QUEUED_FOR_OWN_CAPTURE_ONLY')
    print('UNIT='+session['unit']); print('ACTIVE='+str(is_active)); print('REPORT='+str(report))
    result=report/'info/result.json'
    if regular(result,1_000_000): print('RESULT='+read_json(result).get('result','UNKNOWN'))
    archive=report.with_name(report.name+'.tar.gz')
    if archive.is_file(): print('UPLOAD='+str(archive))
    elif not is_active:
        store(report/'info/recovery.json',{'result':'INACTIVE_UNIT_PARTIAL_REPORT','gameplay_verified':False})
        print('UPLOAD='+str(pack(report)))
    else: print('REPORT_NOT_FINISHED=YES')
    return 0


def start(project: Path, script: Path) -> int:
    logroot=project.parent/'logs';logroot.mkdir(parents=True,exist_ok=True)
    lockpath=logroot/('.'+LABEL+'.lock')
    fd=os.open(lockpath,os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW,0o600)
    try:
        fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
        previous=latest(project)
        if previous and active(read_json(previous/'info/session.json')['unit']):
            print('EXISTING_CAPTURE=STATUS_ONLY');return status(project)
        busy=running_games()
        if busy:
            print('BLOCKED_OTHER_SHADPS4='+','.join(map(str,busy))+'; NO_NEW_GAME_OR_GPU_PROBE');return 0
        for tool in ('systemd-run','systemctl','xdotool','xwininfo','import'):
            if not shutil.which(tool): raise RuntimeError('Missing '+tool+'; nothing installed or launched')
        checkpoints=verify(project)
        if not regular(GAME): raise RuntimeError('Expected CUSA11456 eboot.bin missing')
        parent=previous_ready(project)
        env=dict(os.environ)
        env['DISPLAY']=env.get('DISPLAY') or ':0'
        if not env.get('XAUTHORITY') and (Path.home()/'.Xauthority').is_file():
            env['XAUTHORITY']=str(Path.home()/'.Xauthority')
        run(['systemctl','--user','show-environment'])
        run(['xwininfo','-root'],5,env)
        if shutil.disk_usage(project).free<2*1024**3: raise RuntimeError('Less than 2 GiB free for private run and diagnostics')
        stamp=dt.datetime.now().strftime('%Y%m%d-%H%M%S')
        report=Path(tempfile.mkdtemp(prefix=LABEL+'-'+stamp+'-',dir=logroot))
        for name in ('info','logs','screenshots','radv','radv-home','spirv-failures'): (report/name).mkdir()
        ready=Path(tempfile.mkdtemp(prefix='got-v47-interactive-ready-'+stamp+'-',dir=project/'tests'))
        unit=report.name+'.service'
        spec={'unit':unit,'ready':str(ready),'duration':DURATION,'report':str(report),'helper_sha256':digest(script)}
        store(report/'info/session.json',spec)
        store(logroot/('.'+LABEL+'-latest.json'),{'report':str(report)})
        try:
            profile=copy_profile(parent,ready)
            shutil.copyfile(checkpoints['binary'],ready/'shadps4');(ready/'shadps4').chmod(0o700)
            if digest(ready/'shadps4')!=BINARY_SHA: raise RuntimeError('Private runtime copy checksum failed')
            store(report/'info/preparation.json',{'checkpoints':checkpoints,'profile':profile,
                'debugger':False,'automatic_input':False,'limit_seconds':DURATION,'emulator_rebuilt':False,
                'driver_patched':False,'settings_saved_only_under':str(ready/'user/home')})
            # Freeze the exact helper for the running unit; future downloads cannot alter it.
            frozen=report/'info/runner.py';shutil.copyfile(script,frozen)
            command=['systemd-run','--user','--collect','--no-block','--service-type=exec',
                '--unit='+unit,'--description=GoT v46 interactive continuation',
                '--property=RuntimeMaxSec=720s','--property=TimeoutStopSec=30s',
                '--property=KillMode=control-group','--working-directory='+str(project),
                '--setenv=HOME='+str(Path.home()),'--setenv=PATH=/usr/local/bin:/usr/bin:/bin']
            for key in ('DISPLAY','XAUTHORITY','XDG_RUNTIME_DIR','DBUS_SESSION_BUS_ADDRESS'):
                if env.get(key): command.append('--setenv='+key+'='+env[key])
            command+=['/usr/bin/python3',str(frozen),'worker',str(project),str(report)]
            store(report/'info/result.json',{'result':'SCHEDULING','gameplay_verified':False})
            run(command,20,env)
        except Exception as exc:
            store(report/'info/result.json',{'result':'SETUP_FAILED','error':str(exc),'gameplay_verified':False})
            print('UPLOAD_SETUP_FAILURE='+str(pack(report)));raise
        print('SCHEDULED='+unit)
        print('BINARY=VERIFIED_V46; NO_REBUILD; NO_DEBUGGER_PAUSES; WINDOW=10_MINUTES')
        print('CONNECT_CONTROLLER; CONFIRM_BRIGHTNESS_WITH_CROSS_X_ON_THE_GAME_SCREEN')
        print('PRIVATE_SETTINGS='+str(ready/'user/home'))
        print('GET_REPORT=python3 '+str(script)+' status')
        print('FINISH_EARLY=python3 '+str(script)+' stop')
        return 0
    finally: os.close(fd)


def main() -> int:
    os.umask(0o077)
    if os.geteuid()==0: raise RuntimeError('Run as your normal user, without sudo')
    args=sys.argv[1:]
    if args and args[0]=='worker' and len(args)==3:
        project=Path(args[1]);report=Path(args[2])
        if project != Path.home()/'projects/shadps4/GhostOfTsushima': raise RuntimeError('Wrong worker project')
        return worker(project,report)
    script=Path(__file__).resolve();project=script.parent.parent
    if script.parent.name!='scripts' or project!=Path.home()/'projects/shadps4/GhostOfTsushima':
        raise RuntimeError('Place helper in ~/projects/shadps4/GhostOfTsushima/scripts')
    if not args or args==['start']: return start(project,script)
    if args==['status']: return status(project)
    if args==['stop']: return status(project,True)
    raise RuntimeError('Usage: python3 '+str(script)+' [start|status|stop]')


if __name__=='__main__':
    try: sys.exit(main())
    except BlockingIOError:
        print('CAPTURE_SETUP_ALREADY_IN_PROGRESS');sys.exit(0)
    except KeyboardInterrupt: sys.exit(130)
    except Exception as exc:
        print('STOP: '+str(exc),file=sys.stderr);sys.exit(1)
