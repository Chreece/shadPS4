#!/usr/bin/env python3
"""One isolated no-rebuild run of verified Oct-27 performance-positive GoT executable."""
from __future__ import annotations
import contextlib, datetime, fcntl, hashlib, importlib.util, json, os
from pathlib import Path
import shutil, subprocess, sys, tarfile, time, traceback, urllib.request
AUDIO_RESTORE_WARNINGS=[]

ROOT = Path.home() / 'projects/shadps4'
BASE = ROOT / 'GhostOfTsushima/experiments/c925-pr95-scalar-bounds-20261028T112756671630889Z'
FAST = ROOT / 'GhostOfTsushima/candidates/c925-20261027-195934-performance-positive/shadps4'
CURRENT = BASE / 'build/shadps4'
LOGS = ROOT / 'logs'
HELPER = ROOT / 'scripts/c925-runtime-helper-87d3ee.py'
EXPECTED_FAST = '395682a478f04e4f4a5a2da89488e90fe54ccf502dc23cbecf716a7adeac4e8b'
EXPECTED_CURRENT = '89ffa07ab49d0df36b3a4c4295e0cff8f42bb7e167cbced3d5879b1e5c2f5aec'
EXPECTED_HELPER = 'acc2251ea847e513a462187ead2613990e014c78'
HELPER_URL = ('https://raw.githubusercontent.com/Chreece/shadPS4/'
              '87d3eeacf828b0f990b6aeb11b3b5da46f1b0792/'
              'tools/development/c925_indexed_revalidation/resume_c925_revalidation.py')

def sha256(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()

def gitblob(data):
    return hashlib.sha1(b'blob '+str(len(data)).encode()+b'\0'+data).hexdigest()

def require(ok, description):
    if not ok: raise RuntimeError(description)

def pactl(*args):
    p=subprocess.run(['pactl',*args],capture_output=True,text=True,timeout=12)
    require(p.returncode==0,'PACTL_ERROR:'+p.stderr[-180:])
    return p.stdout.strip()

@contextlib.contextmanager
def physical_audio_muted():
    saved=[]
    try:
        for row in pactl('list','short','sinks').splitlines():
            cols=row.split()
            if len(cols)<2: continue
            sink=cols[1]
            if any(x in sink.lower() for x in ('sunshine','snapcast')):continue
            old=pactl('get-sink-mute',sink)
            require(old in ('Mute: yes','Mute: no'),'UNKNOWN_SINK_STATE')
            saved.append((sink,old=='Mute: yes'))
            pactl('set-sink-mute',sink,'1')
            require(pactl('get-sink-mute',sink)=='Mute: yes','MUTE_NOT_VERIFIED')
        yield
    finally:
        issues=[]
        for sink,old in reversed(saved):
            try:
                pactl('set-sink-mute',sink,'1' if old else '0')
                require(pactl('get-sink-mute',sink)==('Mute: yes' if old else 'Mute: no'),
                        'AUDIO_RESTORE_NOT_VERIFIED')
            except Exception as err:issues.append(str(err))
        if issues:
            AUDIO_RESTORE_WARNINGS.extend(issues)
            print('WARNING=PHYSICAL_AUDIO_RESTORE_PARTIAL:'+str(issues),flush=True)

def get_helper():
    if HELPER.exists():
        require(HELPER.is_file() and not HELPER.is_symlink() and
                gitblob(HELPER.read_bytes())==EXPECTED_HELPER,'EXISTING_HELPER_CHANGED')
    else:
        HELPER.parent.mkdir(parents=True,exist_ok=True)
        pending=HELPER.with_name(HELPER.name+'.pending')
        require(not pending.exists() and not pending.is_symlink(),'HELPER_DOWNLOAD_PENDING_EXISTS')
        try:
            with urllib.request.urlopen(HELPER_URL,timeout=20) as reply:
                data=reply.read(100_000)
            require(gitblob(data)==EXPECTED_HELPER,'PINNED_HELPER_DOWNLOAD_HASH_MISMATCH')
            pending.write_bytes(data); pending.replace(HELPER)
        finally:
            if pending.exists(): pending.unlink()
    return HELPER.read_text()

def main():
    LOGS.mkdir(parents=True,exist_ok=True)
    stamp=datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    out=LOGS/f'got-last-fast-95s-{stamp}-{os.getpid()}'
    out.mkdir()
    result={'game':'CUSA11456','checkpoint':'2026-10-27T19:59:54+01:00',
            'purpose':'verify archived performance-positive executable, not source modification',
            'game_launched':False,'graphics_recovered':'NOT_VERIFIED'}
    locks=[]; unloaded=[]; work=None; before=None; helper=None; rc=1
    print('STAGE=VALIDATE_LAST_FAST_CHECKPOINT',flush=True)
    try:
        for name in ('.got-pr95-scalar-bounds-build.lock','.got-indexed-revalidation.lock',
                     '.got-finite-raw-image.lock','.got-uniform-samples.lock',
                     '.got-indirect-materials.lock','.got-gbuffer-build.lock',
                     '.got-srt-invariant-repair.lock','.got-gpu-alias-correctness.lock'):
            lock=(LOGS/name).open('a+'); fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB); locks.append(lock)
        require(FAST.is_file() and not FAST.is_symlink() and os.access(FAST,os.X_OK),
                'FAST_EXECUTABLE_MISSING')
        require(sha256(FAST)==EXPECTED_FAST,'FAST_EXECUTABLE_HASH_CHANGED')
        require(CURRENT.is_file() and sha256(CURRENT)==EXPECTED_CURRENT,
                'RESTORED_BASELINE_CHANGED')
        before=sha256(CURRENT)
        text=get_helper()
        for a,b in (('marks = iter((40,55,85,112))','marks = iter((20,40,55,85))'),
                    ('if elapsed>=118:','if elapsed>=95:'),
                    ('STAGE=MENU_TEST MAX_SECONDS=118+2 INPUT=NONE',
                     'STAGE=LAST_FAST_CHECKPOINT MAX_SECONDS=95+2 INPUT=NONE')):
            require(text.count(a)==1,'HELPER_TEST_TIMER_SOURCE_CHANGED:'+a)
            text=text.replace(a,b,1)
        require(text.count('def run_game(')==1,'HELPER_GAME_FUNCTION_CHANGED')
        text=text.replace('def run_game(',"\ndef owned_x11_window(pid, display):\n    import re\n    env=dict(os.environ,DISPLAY=display)\n    ids=[]\n    for field in ('_NET_CLIENT_LIST_STACKING','_NET_CLIENT_LIST'):\n        try:\n            p=sp.run(['xprop','-root',field],env=env,capture_output=True,text=True,timeout=4)\n            if p.returncode==0:ids.extend(re.findall(r'0x[0-9a-fA-F]+',p.stdout))\n        except (OSError,sp.TimeoutExpired):\n            pass\n    if not ids:\n        try:\n            p=sp.run(['xwininfo','-root','-tree'],env=env,capture_output=True,text=True,timeout=4)\n            if p.returncode==0:\n                ids.extend(re.findall(r'(?m)^\\s*(0x[0-9a-fA-F]+)\\s+\"[^\\n]*?[sS]had[pP][sS]4',p.stdout))\n        except (OSError,sp.TimeoutExpired):\n            pass\n    for wid in dict.fromkeys(ids):\n        try:\n            owner=sp.run(['xprop','-id',wid,'_NET_WM_PID'],env=env,capture_output=True,text=True,timeout=3)\n            match=re.search(r'\\b(\\d+)\\s*$',owner.stdout)\n            if owner.returncode!=0 or not match or int(match.group(1))!=pid:continue\n            mapped=sp.run(['xwininfo','-id',wid],env=env,capture_output=True,text=True,timeout=3)\n            if mapped.returncode==0 and 'Map State: IsViewable' in mapped.stdout:\n                return wid\n        except (OSError,sp.TimeoutExpired):\n            continue\n    return None\n\n\n"+'def run_game(',1)
        source_args="                    args=['import','-window','root',str(target)] if shutil.which('import') else ['maim',str(target)]"
        source_new_args="                    wid=owned_x11_window(process.pid,screen)\n                    if wid is None:\n                        emit('WARNING=OWNED_GAME_X11_WINDOW_NOT_FOUND; ROOT_CAPTURE_NOT_VALID_FPS_EVIDENCE')\n                    args=(['import','-window',wid or 'root',str(target)]\n                          if shutil.which('import') else\n                          (['maim','-i',wid,str(target)] if wid else ['maim',str(target)]))"
        require(text.count(source_args)==1,'HELPER_ROOT_CAPTURE_SOURCE_CHANGED')
        text=text.replace(source_args,source_new_args,1)
        source_meta="                    captures.append((cap,now,{'requested_seconds':mark,'started_seconds':elapsed,'file':target.name}))"
        require(text.count(source_meta)==1,'HELPER_CAPTURE_METADATA_CHANGED')
        text=text.replace(source_meta,"                    captures.append((cap,now,{'requested_seconds':mark,'started_seconds':elapsed,'file':target.name,'owned_game_window':wid,'capture_verified':wid is not None}))",1)
        compile(text,str(out/'bounded_helper.py'),'exec')
        generated=out/'bounded_helper.py'; generated.write_text(text)
        spec=importlib.util.spec_from_file_location('got_lastfast_helper',generated)
        helper=importlib.util.module_from_spec(spec); spec.loader.exec_module(helper)
        require(not helper.emulator_pids(),'ANOTHER_EMULATOR_RUNNING')
        eboot=Path('/mnt/roms-all/ps4-games/CUSA11456/eboot.bin')
        require(eboot.is_file(),'GOT_EBOOT_MISSING')
        display=os.getenv('DISPLAY') or ':0'
        require(shutil.which('xprop') and shutil.which('xwininfo'),'X11_WINDOW_PROBE_TOOLS_MISSING')
        require(shutil.which('xdpyinfo') and shutil.which('pactl') and
                (shutil.which('import') or shutil.which('maim')),'DISPLAY_OR_AUDIO_TOOL_MISSING')
        helper.command(['env','DISPLAY='+display,'xdpyinfo'],timeout=10)
        origin=BASE/'runs/got-presrt-cache-89ffa07a/user'
        require((origin/'config.json').is_file() and not origin.is_symlink(),
                'PRIVATE_ORIGINAL_PROFILE_MISSING')
        require(not any(p.is_symlink() for p in origin.rglob('*')),
                'ORIGIN_PROFILE_CONTAINS_SYMLINKS')
        require(sum(p.stat().st_size for p in origin.rglob('*') if p.is_file())<2*1024**3,
                'ORIGIN_PROFILE_TOO_LARGE')
        work=BASE/'runs'/out.name
        require(not work.exists(),'PRIVATE_PROFILE_ALREADY_EXISTS')
        work.mkdir()
        shutil.copytree(origin,work/'user',ignore=lambda d,ns:[n for n in ns if n in
                  ('shader','cache','log','temp','captures','screenshots','download')])
        for name in ('config.json','custom_configs/CUSA11456.json'):
            file=work/'user'/name
            if not file.is_file():continue
            config=json.loads(file.read_text())
            general=config.get('General') or {}
            raw=general.get('home_dir','')
            if raw:
                home=Path(raw).expanduser().resolve()
                require(home.is_relative_to(origin.resolve()),'EXTERNAL_SAVE_ROOT')
                general['home_dir']=str(work/'user'/home.relative_to(origin.resolve()))
                config['General']=general
            if name=='config.json':
                gpu=config.get('GPU')
                require(isinstance(gpu,dict),'GPU_CONFIG_SECTION_MISSING')
                gpu['dump_shaders']=False
                vulkan=config.get('Vulkan')
                require(isinstance(vulkan,dict),'VULKAN_CONFIG_SECTION_MISSING')
                vulkan['pipeline_cache_enabled']=False  # no incompatible shader cache
            file.write_text(json.dumps(config,indent=2)+'\n')
        helper.MIN_VRAM=7*1024**3  # diagnostic safety floor, NOT a PS4 requirement
        api=helper.Ollama(out)
        helper.ensure_headroom(api,out,unloaded,result)
        require(not helper.emulator_pids() and sha256(CURRENT)==before,
                'PRELAUNCH_STATE_CHANGED')
        result['fast_executable_sha256']=EXPECTED_FAST
        print('STAGE=RUN_VERIFIED_20261027_FAST_BUILD_95_SECONDS',flush=True)
        since=time.time()
        with physical_audio_muted():
            result['game_launched']=True
            result['game_result']=helper.run_game(FAST,eboot,work,out,display)
        run=result['game_result']
        result['status']='FAST_CHECKPOINT_TRIAL_COMPLETE'
        rc=0 if run.get('time_limit_reached') or run.get('exit_code')==0 else 1
    except BaseException as exc:
        result['status']='FAILED_'+type(exc).__name__
        result['error']=str(exc)
        (out/'error.txt').write_text(traceback.format_exc())
        print('ERROR='+str(exc),flush=True)
    finally:
        if unloaded:
            restored=helper.restore_models(api,unloaded,out)
            result['ollama_restoration']=restored
            if any(x.get('result') in ('RESTORE_FAILED','RESTORE_NOT_VERIFIED') for x in restored):rc=1
        result['baseline_preserved']=CURRENT.is_file() and sha256(CURRENT)==EXPECTED_CURRENT
        result['fast_executable_preserved']=FAST.is_file() and sha256(FAST)==EXPECTED_FAST
        if not result['baseline_preserved'] or not result['fast_executable_preserved']:rc=1
        if work and work.is_dir():
            for directory in (work/'user/log',Path.home()/'.local/share/shadPS4/log'):
                if directory.is_dir():
                    for p in directory.glob('*.log'):
                        if 'since' in locals() and p.is_file() and not p.is_symlink() and p.stat().st_mtime>=since-3 and p.name in ('CUSA11456.log','shadps4.log'):
                            with p.open('rb') as fd:
                                fd.seek(max(0,p.stat().st_size-16*1024**2))
                                (out/('runtime-'+p.name)).write_bytes(fd.read())
            shutil.rmtree(work)
        frames_file=out/'capture-times.json'
        if frames_file.is_file():
            frames=json.loads(frames_file.read_text())
            result['captures_from_owned_game_window']=sum(bool(x.get('capture_verified')) and x.get('exit_code')==0 and (out/x['file']).is_file() for x in frames)
            result['captures_from_unverified_root']=sum(not x.get('capture_verified') for x in frames)
            images=[sha256(out/x['file']) for x in frames if (out/x['file']).is_file()]
            result['captured_frames_all_identical']=(len(images)>1 and len(set(images))==1)
        if AUDIO_RESTORE_WARNINGS:
            result['audio_restoration_unverified']=AUDIO_RESTORE_WARNINGS[:]
            rc=2 if rc==0 else rc
        result['exit_code']=rc
        (out/'trial.json').write_text(json.dumps(result,indent=2)+'\n')
        journal=ROOT/'docs/emulator/TRIALS.jsonl';journal.parent.mkdir(parents=True,exist_ok=True)
        with journal.open('a') as j:
            fcntl.flock(j,fcntl.LOCK_EX);j.write(json.dumps(result)+'\n')
        with tarfile.open(str(out)+'.tar.gz','w:gz') as a:a.add(out,arcname=out.name)
        for f in locks:f.close()
        print(f'RESULT={result["status"]}\nEVIDENCE={out}.tar.gz\nBASELINE_PRESERVED={result["baseline_preserved"]}\nSSH_SESSION=OPEN',flush=True)
    return rc

if __name__=='__main__':
    sys.exit(main())
