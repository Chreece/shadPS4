#!/usr/bin/env python3
"""Manual GoT play on the exact v51 binary: full gamepad, no injected inputs.

No build, driver patch, debugger, UMR, input injector, automatic menu actions or
playtime cutoff. Copies the latest v51 private profile ONCE into a persistent
manual profile. Bounded screenshots/logs and PID-filtered audio-route metadata
are diagnostic only; reaching their budgets never stops the game. No microphone
or output audio is recorded, and no volume/channel/route setting is changed.
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
import threading
import time

LABEL = 'got-v52-manual-visual'
TREE = 'got-v51-stencil-reference-20261014'
BINARY_SHA = 'd2a4f5a3c9b25c595739bdc9ee2a879fe27881f3613c48cfa2f0ded843fb5312'
INPUT_SOURCE_SHA = '16ff2532f3dbb3a4fd7893c42638f6b14c3410965afbf1e6f7125acf562bdb63'
DRIVER_SHA = '87908e0c6833c0f6dc86cb079cb0f34de0dee7e970da89b5303f0e6b29e326f7'
SYSTEM_SHA = '7ad998b763b899649facdb4e53ac37d666dff372e84184accc0d19508e8c55e1'
SYSTEM_DRIVER = Path('/usr/lib/x86_64-linux-gnu/libvulkan_radeon.so')
GAME = Path('/mnt/roms-all/ps4-games/CUSA11456/eboot.bin')
SOURCE_HASHES = {
    'src/shader_recompiler/backend/spirv/emit_spirv_integer.cpp': '655e4bfa8c37a849c9fe7ddb11d2cfb7db2f3a20cea42b1343970524b2e6ae05',
    'src/video_core/amdgpu/liverpool.cpp': '80bb1db2414b52feb339196833d385b548056395a746a80ad59c85ba121713fb',
    'src/video_core/renderer_vulkan/vk_pipeline_cache.cpp': '4fe3da5ca6e77ccdbe9c25309cdb84cbcf11ad0d9b795829ef4ea83b21ba7f6f',
    'src/video_core/renderer_vulkan/vk_rasterizer.cpp': '814677f63524610ba23932fd4ebbe062be299d641ac4699c390d62fe1cc1b03b',
}
PROFILE_FILES = ('config.json', 'users.json', 'keys.json', 'custom_configs', 'home', 'inputs', 'data')
EXPECTED_BUTTONS = ('triangle','circle','cross','square','l1','l2','l3','r1','r2','r3',
                    'options','pad_up','pad_down','pad_left','pad_right',
                    'axis_left_x','axis_left_y','axis_right_x','axis_right_y')
ANSI = re.compile(r'\x1b\[[0-9;]*[A-Za-z]')
MAX_PROFILE = 512 * 1024**2
MAX_LOG = 48 * 1024**2
MAX_SHOTS = 160 * 1024**2
ENV_KEYS = ('DISPLAY','XAUTHORITY','XDG_RUNTIME_DIR','DBUS_SESSION_BUS_ADDRESS',
            'PULSE_SERVER','PULSE_COOKIE','PIPEWIRE_REMOTE','PIPEWIRE_RUNTIME_DIR',
            'SDL_AUDIO_DRIVER','SDL_AUDIODRIVER','SDL_GAMECONTROLLERCONFIG',
            'SDL_GAMECONTROLLERCONFIG_FILE','SDL_JOYSTICK_HIDAPI','LANG','LC_ALL')


def now():
    return dt.datetime.now().astimezone().isoformat()


def regular(p: Path, limit: int = 512 * 1024**2) -> bool:
    return p.is_file() and not p.is_symlink() and p.stat().st_size <= limit


def read_json(p: Path) -> dict:
    if not regular(p, 2 * 1024**2): raise RuntimeError('Missing/unsafe JSON: '+str(p))
    value = json.loads(p.read_text(encoding='utf-8-sig'))
    if not isinstance(value, dict): raise RuntimeError('Expected JSON object: '+str(p))
    return value


def store(p: Path, value):
    part = p.with_name(p.name+'.part')
    if p.is_symlink() or part.is_symlink(): raise RuntimeError('Unsafe output: '+str(p))
    part.write_text(json.dumps(value, indent=2)+'\n')
    os.replace(part,p)


def digest(p: Path) -> str:
    if not regular(p): raise RuntimeError('Missing/unsafe hash input: '+str(p))
    with p.open('rb') as f: return hashlib.file_digest(f,'sha256').hexdigest()


def command(argv, timeout=5, env=None, check=True):
    result = subprocess.run(argv,stdin=subprocess.DEVNULL,capture_output=True,text=True,
                            errors='replace',timeout=timeout,env=env)
    if check and result.returncode:
        raise RuntimeError(Path(argv[0]).name+': '+(result.stderr or result.stdout)[-700:])
    return result


def child_directory(p: Path, root: Path, prefix: str):
    if p.parent!=root or not p.name.startswith(prefix) or p.is_symlink() or not p.is_dir():
        raise RuntimeError('Unexpected project directory: '+str(p))
    if p.resolve().parent!=root.resolve(): raise RuntimeError('Path escaped project')


def running_games(proc: Path = Path('/proc')) -> list[int]:
    found=[]
    for p in proc.iterdir():
        if not p.name.isdigit(): continue
        try:
            if p.stat().st_uid!=os.getuid(): continue
        except OSError: continue
        names=[]; state='?'
        try: names.append((p/'comm').read_text().strip())
        except OSError: pass
        try: names.append(Path(os.fsdecode((p/'cmdline').read_bytes().split(b'\0')[0])).name)
        except (OSError,ValueError): pass
        try: names.append(Path(os.readlink(p/'exe').removesuffix(' (deleted)')).name)
        except OSError: pass
        try: state=(p/'stat').read_text().rsplit(') ',1)[1].split()[0]
        except (OSError,IndexError): pass
        if state not in ('Z','X') and any(x.lower().startswith('shadps4') for x in names):
            found.append(int(p.name))
    return sorted(found)


def active(unit: str) -> bool:
    if not re.fullmatch(re.escape(LABEL)+r'-[A-Za-z0-9_-]+\.service',unit):
        raise RuntimeError('Unexpected manual unit name')
    r=command(['systemctl','--user','show',unit,'--property=LoadState','--property=ActiveState'],check=False)
    values=dict(line.split('=',1) for line in r.stdout.splitlines() if '=' in line)
    if values.get('LoadState')=='not-found': return False
    if r.returncode or 'ActiveState' not in values: raise RuntimeError('Cannot determine manual session state')
    return values['ActiveState'] not in ('inactive','failed')


def verify(project: Path) -> dict:
    source=project/'tests'/TREE
    driver=project/'tools/radv-nuw-complete-25.0.7/libvulkan_radeon.so'
    for p in (project,project/'tests',source,source/'build',driver.parent):
        if not p.is_dir() or p.is_symlink(): raise RuntimeError('Missing/unsafe checkpoint: '+str(p))
    checks={rel:digest(source/rel)==value for rel,value in SOURCE_HASHES.items()}
    checks.update(binary=digest(source/'build/shadps4')==BINARY_SHA,
                  private_driver=digest(driver)==DRIVER_SHA,system_driver=digest(SYSTEM_DRIVER)==SYSTEM_SHA)
    proof=read_json(source/'build/proof.json')
    checks['proof']=proof.get('binary_sha256')==BINARY_SHA and proof.get('private_driver_sha256')==DRIVER_SHA
    checks['icd']=read_json(driver.with_name('radeon_icd.json')).get('ICD',{}).get('library_path')==str(driver)
    if not all(checks.values()): raise RuntimeError('Checkpoint mismatch: '+str(checks))
    return {'checks':checks,'binary':str(source/'build/shadps4'),'driver':str(driver),
            'icd':str(driver.with_name('radeon_icd.json'))}


def default_bindings(source: str) -> tuple[str,str]:
    def extract(function):
        m=re.search(r'constexpr std::string_view '+function+r'\(\)\s*\{\s*return R"\((.*?)\)";\s*\}',source,re.S)
        if not m: raise RuntimeError('Exact local input configuration declaration missing: '+function)
        return m.group(1)
    defaults=extract('GetDefaultInputConfig')
    for key in EXPECTED_BUTTONS:
        if not re.search(r'(?m)^'+re.escape(key)+r'\s*=\s*'+re.escape(key)+r'\s*$',defaults):
            raise RuntimeError('Full local gamepad map missing '+key)
    if 'touchpad_center = back' not in defaults: raise RuntimeError('Touchpad mapping missing')
    if '"input_config"' not in source or '(game_id + ".ini")' not in source:
        raise RuntimeError('Input-config path contract differs')
    return defaults,extract('GetDefaultGlobalConfig')


def check_profile(ready: Path):
    cfg=read_json(ready/'user/config.json')
    if cfg.get('General',{}).get('home_dir')!='': raise RuntimeError('Nonportable home_dir refused')
    read_json(ready/'user/users.json')
    custom=ready/'user/custom_configs'
    if custom.is_symlink(): raise RuntimeError('Symlinked custom config')
    if custom.is_dir():
        for p in custom.glob('*.json'):
            if read_json(p).get('General',{}).get('home_dir','')!='':
                raise RuntimeError('Nonportable per-game home_dir refused')


def previous_ready(project: Path) -> Path:
    pointer=project.parent/'logs/.got-v51-stencil-reference-visual-latest.json'
    report=Path(read_json(pointer)['report'])
    child_directory(report,project.parent/'logs','got-v51-stencil-reference-visual-')
    session=read_json(report/'info/session.json')
    p=Path(session['ready'])
    child_directory(p,project/'tests','got-v51-stencil-reference-ready-')
    if digest(p/'shadps4')!=BINARY_SHA: raise RuntimeError('v51 profile binary differs')
    check_profile(p)
    return p


def profile_inventory(ready: Path) -> dict:
    data={};total=0
    for name in PROFILE_FILES:
        p=ready/'user'/name
        if p.is_symlink(): raise RuntimeError('Profile symlink refused: '+str(p))
        if not p.exists(): continue
        entries=[p]+list(p.rglob('*')) if p.is_dir() else [p]
        for f in entries:
            if f.is_symlink() or not (f.is_file() or f.is_dir()):
                raise RuntimeError('Unsafe profile entry: '+str(f))
            if f.is_file():
                size=f.stat().st_size;total+=size
                data[str(f.relative_to(ready))]={'size':size,'sha256':digest(f)}
                if len(data)>20000 or total>MAX_PROFILE:
                    raise RuntimeError('Profile exceeds bounded copy limit')
    return data


def prepare_profile(project: Path, binary: Path) -> tuple[Path,dict]:
    root=project/'play'
    if root.is_symlink(): raise RuntimeError('Unsafe manual profile parent')
    root.mkdir(exist_ok=True)
    ready=root/'manual-v51'
    if ready.exists() or ready.is_symlink():
        child_directory(ready,root,'manual-v51')
        proof=read_json(ready/'manual-profile.json')
        if proof.get('kind')!='GOT52_MANUAL' or proof.get('binary_sha256')!=BINARY_SHA or digest(ready/'shadps4')!=BINARY_SHA:
            raise RuntimeError('Unknown existing manual profile; no files overwritten')
        check_profile(ready)
        return ready,{'reused_persistent_profile':True,'automatic_input':False}
    parent=previous_ready(project)
    before=profile_inventory(parent)
    src=project/'tests'/TREE/'src/input/input_handler.cpp'
    if not regular(src,1_000_000): raise RuntimeError('Local input mapping source missing')
    if digest(src)!=INPUT_SOURCE_SHA:raise RuntimeError('Local input source differs from the v51 archive')
    defaults,global_cfg=default_bindings(src.read_text())
    stage=Path(tempfile.mkdtemp(prefix='.manual-v51-stage-',dir=root))
    try:
        (stage/'user').mkdir()
        # Preserve empty savedata/user directories too, preventing migration prompts.
        for name in PROFILE_FILES:
            old_dir=parent/'user'/name
            if old_dir.is_dir():
                for folder in [old_dir]+list(old_dir.rglob('*')):
                    if folder.is_dir() and not folder.is_symlink():
                        (stage/folder.relative_to(parent)).mkdir(parents=True,exist_ok=True)
        for relative in before:
            p=stage/relative;p.parent.mkdir(parents=True,exist_ok=True)
            shutil.copyfile(parent/relative,p);p.chmod(0o600)
        folder=stage/'user/input_config';folder.mkdir()
        for name,content in (('default.ini',defaults),('CUSA11456.ini',defaults),('global.ini',global_cfg)):
            (folder/name).write_text(content);(folder/name).chmod(0o600)
        # Inputs from the automation profile are preserved separately for audit,
        # never loaded into this fresh manual input_config directory.
        old=parent/'user/input_config'
        old_hashes={}
        if old.is_dir() and not old.is_symlink():
            for f in old.glob('*.ini'):
                if not regular(f,100_000): raise RuntimeError('Unsafe old input file')
                dest=stage/'previous-automatic-inputs'/f.name;dest.parent.mkdir(exist_ok=True)
                shutil.copyfile(f,dest);old_hashes[f.name]=digest(f)
        for name in ('log','cache','shader/dumps'):(stage/'user'/name).mkdir(parents=True,exist_ok=True)
        check_profile(stage)
        if profile_inventory(stage)!=before or profile_inventory(parent)!=before:
            raise RuntimeError('Copy changed prior profile/audio settings; no launch')
        shutil.copyfile(binary,stage/'shadps4');(stage/'shadps4').chmod(0o700)
        if digest(stage/'shadps4')!=BINARY_SHA: raise RuntimeError('Runtime copy checksum mismatch')
        proof={'kind':'GOT52_MANUAL','binary_sha256':BINARY_SHA,'source_ready':str(parent),
               'source_profile_unchanged':True,'created':now(),'automatic_input':False,
               'full_controls_from':str(src),'input_source_sha256':digest(src),
               'replaced_test_inputs':old_hashes,'copied_files':len(before),
               'saves_persist_here':True,'audio_settings_unchanged':True}
        store(stage/'manual-profile.json',proof)
        if ready.exists(): raise RuntimeError('Manual profile appeared during setup')
        stage.rename(ready)
        return ready,proof
    except BaseException:
        # Keep partial setup for diagnosis. Do not delete saves/checkpoints.
        print('PARTIAL_PROFILE_PRESERVED='+str(stage),flush=True)
        raise


def environment(report: Path, proof: dict) -> dict:
    env={k:v for k,v in os.environ.items() if not k.startswith(('RADV_','ACO_','MESA_','VK_','SHADPS4_'))}
    env.update(HOME=str(report/'radv-home'),SDL_VIDEODRIVER='x11',
               VK_DRIVER_FILES=proof['icd'],VK_ICD_FILENAMES=proof['icd'],
               RADV_DEBUG='hang,noumr',MESA_SHADER_CACHE_DISABLE='true',
               MESA_SPIRV_FAIL_DUMP_PATH=str(report/'spirv-failures'),
               SHADPS4_GHOST_BINDLESS_EXPERIMENT='1',SDL_JOYSTICK_ALLOW_BACKGROUND_EVENTS='1')
    return env


def mapped_driver(pid: int, ready: Path, driver: Path) -> dict | None:
    p=Path('/proc')/str(pid)
    if (p/'exe').resolve()!=(ready/'shadps4').resolve(): raise RuntimeError('Child executable identity changed')
    rows=[]
    for line in (p/'maps').read_text().splitlines():
        fields=line.split(None,5)
        if len(fields)!=6 or 'libvulkan_radeon.so' not in fields[5]: continue
        if fields[5]!=str(driver): raise RuntimeError('Wrong RADV loaded: '+fields[5])
        rows.append(line)
    if not rows:return None
    if digest(driver)!=DRIVER_SHA: raise RuntimeError('Private driver hash changed')
    return {'result':'PRIVATE_RADV_MAPPED_FILE_VERIFIED','pid':pid,'path':str(driver),'sha256':DRIVER_SHA,'maps':rows}


class LogDrain(threading.Thread):
    """Continuously drain stdout independently of screenshots and audio queries."""
    def __init__(self,pipe,report: Path,began: float,max_size: int=MAX_LOG):
        super().__init__(daemon=True)
        self.pipe=pipe;self.report=report;self.began=began;self.max_size=max_size
        self.error=None;self.metrics={'gpu_hang':False,'native_error':False,'graphics_compiles':0,
            'compute_compiles':0,'ajm_create':0,'ajm_destroy':0,'last_guest_flips':None,'controller_messages':[]}
        self.rotations=0;self.lock=threading.Lock()
    def run(self):
        raw=None;events=None
        try:
            p=self.report/'logs/game-stdout.log';raw=p.open('wb')
            events=(self.report/'logs/audio-input-timeline.jsonl').open('w')
            size=0;pending=b'';event_count=0
            while True:
                block=self.pipe.read1(65536)
                if not block: break
                if size+len(block)>self.max_size:
                    raw.close()
                    for older,newer in (('game-stdout.1.log','game-stdout.2.log'),('game-stdout.log','game-stdout.1.log')):
                        q=self.report/'logs'/older
                        if q.exists():os.replace(q,self.report/'logs'/newer)
                    raw=p.open('wb');size=0;self.rotations+=1
                raw.write(block);raw.flush();size+=len(block)
                lines=(pending+block).split(b'\n');pending=lines.pop()[-65536:]
                for data in lines:
                    line=ANSI.sub('',data.decode(errors='replace'))
                    with self.lock:
                        m=self.metrics
                        m['gpu_hang']|='GPU hang detected' in line
                        m['native_error']|='Unhandled access violation' in line or 'Unreachable code!' in line
                        m['graphics_compiles']+='Compiling graphics pipeline' in line
                        m['compute_compiles']+='Compiling compute pipeline' in line
                        m['ajm_create']+='sceAjmInstanceCreate:' in line
                        m['ajm_destroy']+='sceAjmInstanceDestroy:' in line
                        match=re.search(r'guest_flips=(\d+)',line)
                        if match:m['last_guest_flips']=int(match.group(1))
                        if '[Input]' in line and len(m['controller_messages'])<100:m['controller_messages'].append(line[:1000])
                    if event_count<5000 and (line.startswith(('[Lib.Audio','[Lib.AvPlayer','[Input]')) or
                            ('/movies/' in line and ('open:' in line or 'close:' in line)) or
                            ('[Lib.Ajm]' in line and ('<Error>' in line or '<Critical>' in line))):
                        events.write(json.dumps({'elapsed':round(time.monotonic()-self.began,3),'text':line[:2000]})+'\n')
                        events.flush();event_count+=1
        except Exception as exc:self.error=str(exc)
        finally:
            if raw:raw.close()
            if events:events.close()
            self.pipe.close()
    def snapshot(self):
        with self.lock:return dict(self.metrics,controller_messages=list(self.metrics['controller_messages']),log_rotations=self.rotations,log_error=self.error)


def audio_routes(pid: int,env: dict) -> dict:
    """Read only stream/output metadata for this child; no samples or mixer writes."""
    tool=shutil.which('pactl')
    if not tool:return {'result':'PACTL_UNAVAILABLE','audio_loss_cause':'NOT_ESTABLISHED'}
    def query(kind):
        result=command([tool,'--format=json','list',kind],3,env,False)
        if result.returncode:raise RuntimeError(result.stderr[-300:])
        if len(result.stdout)>4*1024**2:raise RuntimeError('Audio metadata too large')
        data=json.loads(result.stdout)
        if not isinstance(data,list):raise RuntimeError('Unsupported pactl JSON structure')
        return data
    try:
        streams=query('sink-inputs')
        selected=[];sinks=set()
        for row in streams:
            prop=row.get('properties',{})
            if str(prop.get('application.process.id',''))!=str(pid):continue
            fields=('index','sink','sample_specification','channel_map','corked','mute','volume','buffer_latency_usec','sink_latency_usec')
            record={k:row[k] for k in fields if k in row}
            record['process_id']=str(pid);selected.append(record);sinks.add(str(row.get('sink')))
        outputs=[]
        if sinks:
            for row in query('sinks'):
                if str(row.get('index')) not in sinks:continue
                fields=('index','name','state','sample_specification','channel_map','mute','volume','active_port','latency')
                outputs.append({k:row[k] for k in fields if k in row})
        return {'result':'PID_FILTERED_METADATA' if selected else 'NO_PID_MATCHING_PULSE_STREAM',
                'streams':selected,'outputs':outputs,'audio_samples_recorded':False,'audio_loss_cause':'NOT_ESTABLISHED'}
    except Exception as exc:return {'result':'AUDIO_METADATA_UNAVAILABLE','error':str(exc)[:700],'audio_loss_cause':'NOT_ESTABLISHED'}


def screenshot(pid: int,report: Path,label: str,env: dict) -> dict:
    if not all(shutil.which(x) for x in ('xdotool','import')):return {'result':'SCREENSHOT_TOOL_UNAVAILABLE'}
    ids=command(['xdotool','search','--onlyvisible','--pid',str(pid)],3,env,False).stdout.splitlines()
    for wid in reversed(ids):
        if not wid.isdigit():continue
        owner=command(['xdotool','getwindowpid',wid],2,env,False).stdout.strip()
        if owner!=str(pid):continue
        path=report/'screenshots'/(label+'.png')
        result=command(['import','-window',wid,str(path)],5,env,False)
        if result.returncode or not regular(path,24*1024**2):return {'result':'SCREENSHOT_FAILED'}
        return {'result':'CAPTURED','file':path.name,'bytes':path.stat().st_size,'pid':pid}
    return {'result':'NO_PID_VERIFIED_WINDOW'}


def stop_child(child):
    # Only the child process group created by this worker; never killall/pkill.
    if child.poll() is not None:return
    try:os.killpg(child.pid,signal.SIGTERM)
    except ProcessLookupError:return
    try:child.wait(timeout=5)
    except subprocess.TimeoutExpired:
        try:os.killpg(child.pid,signal.SIGKILL)
        except ProcessLookupError:pass
        child.wait(timeout=3)


def collect_sources(project: Path,report: Path):
    source=project/'tests'/TREE;total=0;manifest=[]
    candidates=set()
    for part in (source/'src/core/libraries',source/'src/input'):
        if part.is_dir():
            for p in part.rglob('*'):
                if p.suffix in ('.cpp','.h') and any(x in str(p).lower() for x in ('audio','ajm','ngs2','controller','input_handler')):
                    candidates.add(p)
    for p in sorted(candidates):
        if not regular(p,1_000_000):continue
        if total+p.stat().st_size>6*1024**2:break
        rel=p.relative_to(source);dest=report/'info/audio-input-source'/rel
        dest.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(p,dest)
        total+=p.stat().st_size;manifest.append({'path':str(rel),'sha256':digest(p)})
    store(report/'info/audio-input-source-manifest.json',manifest)


def pack(report: Path) -> Path:
    archive=report.with_name(report.name+'.tar.gz')
    if archive.is_file():return archive
    part=archive.with_name(archive.name+'.part');total=0;excluded=[]
    with tarfile.open(part,'w:gz') as tar:
        for folder in ('info','logs','screenshots','spirv-failures'):
            for p in sorted((report/folder).rglob('*')):
                if p.is_dir():continue
                rel=str(p.relative_to(report))
                if not regular(p,64*1024**2) or total+p.stat().st_size>400*1024**2:
                    excluded.append(rel);continue
                total+=p.stat().st_size;tar.add(p,arcname=rel,recursive=False)
        # Existing driver dumps only; bounded independently, no raw process core.
        for folder in sorted((report/'radv-home').glob('radv_dumps_*')):
            if not folder.is_dir() or folder.is_symlink():continue
            for p in sorted(folder.iterdir()):
                if p.suffix not in ('.txt','.log','.spv') or not regular(p,48*1024**2):continue
                if total+p.stat().st_size>450*1024**2:excluded.append(p.name);continue
                total+=p.stat().st_size;tar.add(p,arcname='radv/'+folder.name+'/'+p.name,recursive=False)
        record=report/'info/packaging.json'
        store(record,{'bytes':total,'excluded':excluded,'private_profiles_keys_and_audio_samples_excluded':True})
        tar.add(record,arcname='info/packaging.json',recursive=False)
    os.replace(part,archive);return archive


def worker(project: Path,report: Path) -> int:
    child_directory(report,project.parent/'logs',LABEL+'-')
    spec=read_json(report/'info/session.json');ready=Path(spec['ready'])
    if ready!=project/'play/manual-v51':raise RuntimeError('Wrong manual profile')
    if digest(Path(__file__).resolve())!=spec['helper_sha256']:raise RuntimeError('Frozen worker changed')
    cancelled=threading.Event()
    old={sig:signal.signal(sig,lambda *_:cancelled.set()) for sig in (signal.SIGTERM,signal.SIGINT)}
    state={'result':'PREFLIGHT','started':now(),'automatic_input':False,'playtime_limit':None,
           'audio_routing_changed':False,'audio_samples_recorded':False,'gameplay_verified':False}
    child=None;drain=None;began=time.monotonic();shots=0;shot_bytes=0
    try:
        proof=verify(project);check_profile(ready)
        if digest(ready/'shadps4')!=BINARY_SHA:raise RuntimeError('Manual runtime binary changed')
        games=running_games()
        if games:state.update(result='BLOCKED_OTHER_SHADPS4',pids=games);return 0
        env=environment(report,proof);command(['xwininfo','-root'],env=env)
        args=[str(ready/'shadps4'),'--cpu-id-mode=native','--game',str(GAME),'--fullscreen','true']
        child=subprocess.Popen(args,cwd=ready,env=env,stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,
                               stderr=subprocess.STDOUT,start_new_session=True)
        began=time.monotonic();drain=LogDrain(child.stdout,report,began);drain.start()
        state.update(result='RUNNING_MANUAL',pid=child.pid,args=args)
        checked=False;next_info=0.;next_shot=6.;last_metrics=0.;audio_count=0;marked=0
        store(report/'info/result.json',state)
        with (report/'logs/audio-routes.jsonl').open('w') as routes:
            while child.poll() is None:
                elapsed=time.monotonic()-began
                if not checked:
                    try:loaded=mapped_driver(child.pid,ready,Path(proof['driver']))
                    except FileNotFoundError:
                        if child.poll() is not None:break
                        loaded=None
                    if loaded:checked=True;store(report/'info/driver-provenance.json',loaded)
                    elif elapsed>45:raise RuntimeError('Private driver load not verified')
                if cancelled.is_set() or (report/'stop-requested').exists():
                    state['result']='USER_STOP_REQUEST';break
                mark=report/'mark-audio-lost'
                snap=report/'snapshot-requested'
                requested=(mark.exists() or snap.exists()) and marked<16
                if requested:
                    why='AUDIO_LOST_USER_MARK' if mark.exists() else 'USER_SCREENSHOT'
                    for p in (mark,snap):
                        if p.is_file() and not p.is_symlink():p.unlink()
                    marked+=1
                    store(report/'info'/f'mark-{marked:02d}.json',{'elapsed':round(elapsed,3),'reason':why,'timestamp':now()})
                if requested or (elapsed>=next_info and audio_count<720):
                    row={'elapsed':round(elapsed,3),'time':now(),'pid':child.pid,**audio_routes(child.pid,env)}
                    routes.write(json.dumps(row)+'\n');routes.flush();audio_count+=1;next_info=elapsed+10
                if requested or (elapsed>=next_shot and shots<240 and shot_bytes<MAX_SHOTS):
                    try:
                        shot=screenshot(child.pid,report,f'{int(elapsed):05d}s-{shots:03d}',env)
                        shots+=1;shot_bytes+=shot.get('bytes',0)
                        store(report/'info'/f'shot-{shots:03d}.json',dict(shot,elapsed=round(elapsed,3)))
                    except Exception as exc:
                        store(report/'info/screenshot-warning.json',{'error':str(exc)})
                    next_shot=elapsed+(15 if elapsed<600 else 60)
                if elapsed-last_metrics>=10:
                    store(report/'info/metrics.json',dict(drain.snapshot(),elapsed=round(elapsed,3)))
                    last_metrics=elapsed
                time.sleep(.2)
        if state['result']=='RUNNING_MANUAL':state['result']='GAME_EXITED'
        state['driver_load_verified']=checked;state['exit_before_cleanup']=child.poll()
    except Exception as exc:state.update(result='SESSION_ERROR',error=str(exc))
    finally:
        if child:
            try:stop_child(child)
            except Exception as exc:state['cleanup_error']=str(exc)
            state['game_exit']=child.poll()
        if drain:
            drain.join(timeout=5)
            state['log_drain_complete']=not drain.is_alive()
            store(report/'info/metrics.json',drain.snapshot())
        state.update(finished=now(),elapsed=round(time.monotonic()-began,3),screenshots=shots,
                     screenshot_bytes=shot_bytes,private_ready=str(ready))
        try:state['checkpoints_after']=verify(project)['checks']
        except Exception as exc:state['preservation_error']=str(exc)
        store(report/'info/result.json',state)
        for sig,handler in old.items():signal.signal(sig,handler)
        print('UPLOAD='+str(pack(report)),flush=True)
    return 1 if state['result']=='SESSION_ERROR' else 0


def latest(project: Path) -> Path | None:
    p=project.parent/'logs'/('.'+LABEL+'-latest.json')
    if not p.exists():return None
    report=Path(read_json(p)['report']);child_directory(report,project.parent/'logs',LABEL+'-')
    return report


def status(project: Path,action='status') -> int:
    report=latest(project)
    if report is None:print('SESSION=NOT_STARTED');return 0
    spec=read_json(report/'info/session.json');live=active(spec['unit'])
    if action!='status' and live:
        name={'stop':'stop-requested','audio-lost':'mark-audio-lost','snapshot':'snapshot-requested'}[action]
        p=report/name
        if p.is_symlink():raise RuntimeError('Unsafe request marker')
        p.touch(exist_ok=True)
        print('REQUEST='+action+'; NO_CONTROLLER_INPUT_SENT')
    print('ACTIVE='+str(live));print('PROFILE='+spec['ready']);print('REPORT='+str(report))
    if regular(report/'info/result.json'):print('RESULT='+read_json(report/'info/result.json').get('result','UNKNOWN'))
    archive=report.with_name(report.name+'.tar.gz')
    if archive.is_file():print('UPLOAD='+str(archive))
    elif not live:
        store(report/'info/recovery.json',{'result':'INACTIVE_SESSION_PARTIAL_REPORT','gameplay_verified':False})
        print('UPLOAD='+str(pack(report)))
    else:print('CLOSE_GAME_NORMALLY_OR_USE_STOP_TO_FINISH_REPORT')
    return 0


def start(project: Path,script: Path) -> int:
    logroot=project.parent/'logs';logroot.mkdir(parents=True,exist_ok=True)
    fd=os.open(logroot/('.'+LABEL+'.lock'),os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW,0o600)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):raise RuntimeError('Unsafe setup lock')
        fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
        old=latest(project)
        if old and active(read_json(old/'info/session.json')['unit']):return status(project)
        games=running_games()
        if games:
            print('BLOCKED_OTHER_SHADPS4='+','.join(map(str,games))+'; CLOSE_CURRENT_GAME_NORMALLY; NO_PROCESS_CHANGED')
            return 0
        # Prevent a previously scheduled automatic test starting after this check.
        units=command(['systemctl','--user','list-units','--all','--plain','--no-legend','--no-pager','got-*.service','got-*.timer']).stdout
        for line in units.splitlines():
            parts=line.split()
            if len(parts)>=4 and parts[0].startswith('got-') and parts[2] not in ('inactive','failed'):
                raise RuntimeError('Another GoT capture is scheduled/running: '+parts[0]+'; finish it first')
        for name in ('systemd-run','systemctl','xwininfo'):
            if not shutil.which(name):raise RuntimeError('Missing '+name+'; nothing installed')
        proof=verify(project)
        if not regular(GAME):raise RuntimeError('Expected GoT game executable missing')
        env=dict(os.environ);env['DISPLAY']=env.get('DISPLAY') or ':0'
        if not env.get('XAUTHORITY') and (Path.home()/'.Xauthority').is_file():env['XAUTHORITY']=str(Path.home()/'.Xauthority')
        command(['xwininfo','-root'],env=env)
        if shutil.disk_usage(project).free<2*1024**3:raise RuntimeError('Need 2 GiB free for profile and bounded diagnostics')
        ready,profile=prepare_profile(project,Path(proof['binary']))
        stamp=dt.datetime.now().strftime('%Y%m%d-%H%M%S')
        report=Path(tempfile.mkdtemp(prefix=LABEL+'-'+stamp+'-',dir=logroot))
        for name in ('info','logs','screenshots','radv-home','spirv-failures'):(report/name).mkdir()
        unit=report.name+'.service'
        spec={'unit':unit,'ready':str(ready),'report':str(report),'helper_sha256':digest(script),'playtime_limit':None}
        store(report/'info/session.json',spec);store(logroot/('.'+LABEL+'-latest.json'),{'report':str(report)})
        try:
            cfg=read_json(ready/'user/config.json')
            audio={'Audio':cfg.get('Audio',{}),'volume_slider':cfg.get('General',{}).get('volume_slider'),
                   'changed':False,'channel_downmix_forced':False}
            store(report/'info/audio-settings.json',audio)
            store(report/'info/preparation.json',{'checkpoints':proof,'profile':profile,'automatic_input':False,
                  'emulator_rebuilt':False,'driver_patched':False,'playtime_limit':None,
                  'full_gamepad_bindings':True,'audio_settings_changed':False})
            for name in ('default.ini','CUSA11456.ini','global.ini'):
                p=ready/'user/input_config'/name
                if regular(p,100_000):shutil.copyfile(p,report/'info'/('input-'+name))
            collect_sources(project,report)
            frozen=report/'info/runner.py';shutil.copyfile(script,frozen)
            argv=['systemd-run','--user','--collect','--no-block','--service-type=exec','--unit='+unit,
                  '--description=GoT v51 manual gamepad; no automatic inputs or time limit',
                  '--property=TimeoutStopSec=30s','--property=KillMode=control-group',
                  '--working-directory='+str(project),'--setenv=HOME='+str(Path.home()),
                  '--setenv=PATH=/usr/local/bin:/usr/bin:/bin']
            for key in ENV_KEYS:
                if env.get(key):argv.append('--setenv='+key+'='+env[key])
            argv+=['/usr/bin/python3',str(frozen),'worker',str(project),str(report)]
            store(report/'info/result.json',{'result':'SCHEDULING','automatic_input':False})
            command(argv,20,env)
        except Exception as exc:
            store(report/'info/result.json',{'result':'SETUP_FAILED','error':str(exc),'automatic_input':False})
            print('UPLOAD_SETUP_FAILURE='+str(pack(report)));raise
        print('SCHEDULED='+unit)
        print('INPUT=FULL_GAMEPAD_AND_KEYBOARD; AUTOMATIC_PRESSES=NONE; NO_PLAYTIME_CUTOFF')
        print('PROFILE='+str(ready))
        print('GET_REPORT=python3 '+str(script)+' status')
        print('AUDIO_LOSS_MARK=python3 '+str(script)+' audio-lost')
        print('FINISH=python3 '+str(script)+' stop')
        return 0
    finally:os.close(fd)


def main() -> int:
    os.umask(0o077)
    if os.geteuid()==0:raise RuntimeError('Run as your normal user without sudo')
    args=sys.argv[1:]
    if len(args)==3 and args[0]=='worker':
        project=Path(args[1])
        if project!=Path.home()/'projects/shadps4/GhostOfTsushima':raise RuntimeError('Wrong worker project')
        return worker(project,Path(args[2]))
    script=Path(__file__).resolve();project=script.parent.parent
    if script.parent.name!='scripts' or project!=Path.home()/'projects/shadps4/GhostOfTsushima':
        raise RuntimeError('Place helper in ~/projects/shadps4/GhostOfTsushima/scripts')
    if not args or args==['start']:return start(project,script)
    if len(args)==1 and args[0] in ('status','stop','audio-lost','snapshot'):return status(project,args[0])
    raise RuntimeError('Usage: python3 '+str(script)+' [start|status|stop|audio-lost|snapshot]')


if __name__=='__main__':
    try:sys.exit(main())
    except BlockingIOError:print('SETUP_ALREADY_IN_PROGRESS');sys.exit(0)
    except KeyboardInterrupt:sys.exit(130)
    except Exception as exc:print('STOP: '+str(exc),file=sys.stderr);sys.exit(1)
