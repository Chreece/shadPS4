#!/usr/bin/env python3
"""Capture remaining shader chains and presentation activity. No renderer edit/build.
Shader hashes select private evidence files ONLY; they never affect emulation.
"""
from __future__ import annotations
import datetime as dt
import fcntl
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess as sp
import sys
import tarfile
import time
import traceback

EXPECTED='a293a496f42f7e85ef5e76bf0e25e9f3dccd269adae332e646d2234b378c5216'
HELPER_SHA='df7617549006c8733049a684f22f6ac2bf6b96701bf2fe1da5ee9265df568250'
PRIOR='got-c925-indexed-explicit-lod-20261028T153318468127Z'
PROFILE_TRIAL='got-c925-indexed-revalidation-20261028T133609110195Z'
# Evidence selectors, not emulator source/runtime exceptions.
TARGETS={'5065ed7b':'cs','58f22286':'cs','c7542465':'cs','ee963aae':'cs',
         'c0cac3b6':'cs','33a001dc':'cs','b5c898c7':'fs','7eaf40ad':'fs'}
SKIP={'cache','shader','log','temp','screenshots','captures','download'}
MAX_BYTES=96*1024**2


def write(path:Path,value):
    path.write_text(json.dumps(value,indent=2,ensure_ascii=False)+'\n')


def sha(path:Path):
    h=hashlib.sha256()
    with path.open('rb') as f:
        for b in iter(lambda:f.read(4*1024**2),b''):h.update(b)
    return h.hexdigest()


def relocate(value,old:str,new:str):
    if isinstance(value,str):return new+value[len(old):] if value==old or value.startswith(old+'/') else value
    if isinstance(value,list):return [relocate(x,old,new) for x in value]
    if isinstance(value,dict):return {k:relocate(v,old,new) for k,v in value.items()}
    return value


def copy_private_profile(original:Path,destination:Path):
    files=[];total=0;before={}
    for base,dirs,names in os.walk(original,followlinks=False):
        base=Path(base);rel=base.relative_to(original)
        if not rel.parts:dirs[:]=[x for x in dirs if x not in SKIP]
        for name in dirs+names:
            if (base/name).is_symlink():raise RuntimeError('PRIVATE_PROFILE_SYMLINK_REFUSED')
        for name in names:
            p=base/name
            if not p.is_file():raise RuntimeError('PROFILE_SPECIAL_FILE_REFUSED')
            total+=p.stat().st_size;files.append(p)
            if total>2*1024**3 or len(files)>100000:raise RuntimeError('PROFILE_COPY_LIMIT')
    for p in files:
        dest=destination/p.relative_to(original);dest.parent.mkdir(parents=True,exist_ok=True)
        before[str(p)]=sha(p);shutil.copy2(p,dest)
        if p.suffix=='.json':
            try:data=json.loads(p.read_text())
            except (ValueError,UnicodeError):continue
            moved=relocate(data,str(original),str(destination))
            if data!=moved:write(dest,moved)
    configs=[destination/'config.json',destination/'custom_configs/CUSA11456.json']
    if not configs[0].is_file():raise RuntimeError('PRIVATE_GLOBAL_CONFIG_MISSING')
    for config in configs:
        if not config.is_file():continue
        data=json.loads(config.read_text())
        home=data.get('General',{}).get('home_dir','')
        if home and not Path(home).expanduser().resolve().is_relative_to(destination.resolve()):
            raise RuntimeError('PRIVATE_EXTERNAL_SAVE_ROOT_REFUSED')
        # Diagnostic output only; no GPU correctness/compatibility options changed.
        gpu=data.setdefault('GPU',{})
        if not isinstance(gpu,dict):raise RuntimeError('INVALID_GPU_CONFIG')
        gpu['dump_shaders']=True
        write(config,data)
    return before


def thread_snapshot(pid:int,proc_root:Path=Path('/proc')):
    result=[]
    for task in sorted((proc_root/str(pid)/'task').glob('[0-9]*'))[:128]:
        row={'tid':int(task.name)}
        for name in ('comm','wchan','stat'):
            try:row[name]=(task/name).read_text()[:2048].strip()
            except OSError as exc:row[name]={'error':type(exc).__name__}
        result.append(row)
    return result


def gpu_snapshot(drm:Path=Path('/sys/class/drm')):
    out={}
    for card in sorted(drm.glob('card[0-9]*')):
        for name in ('gpu_busy_percent','mem_info_vram_used','mem_info_vram_total'):
            p=card/'device'/name
            try:out[str(p)]=p.read_text().strip()[:100]
            except OSError:pass
    return out


def capture_game(util,binary:Path,work:Path,evid:Path,screen:str):
    proc=None;captures=[];frames=[];start=time.monotonic();last=0;next_telemetry=0;timed=False
    marks=iter((40,55,85,112));mark=next(marks,None);env=dict(os.environ,DISPLAY=screen)
    util.emit('STAGE=DIAGNOSTIC_MENU NO_REBUILD=YES NO_INPUT=YES LIMIT=118+2s')
    try:
        with (evid/'game.log').open('wb') as log,(evid/'capture.log').open('ab') as caplog, (evid/'threads-and-gpu.jsonl').open('w') as telemetry:
            proc=sp.Popen([str(binary),'--game','/mnt/roms-all/ps4-games/CUSA11456/eboot.bin','--fullscreen','true','--show-fps'],cwd=work,env=env,stdout=log,stderr=sp.STDOUT,start_new_session=True)
            while proc.poll() is None:
                now=time.monotonic();elapsed=now-start
                if elapsed>=118:timed=True;util.stop_owned(proc);break
                if elapsed>=next_telemetry:
                    row={'seconds':elapsed,'pid':proc.pid,'threads':thread_snapshot(proc.pid),'gpu':gpu_snapshot()}
                    telemetry.write(json.dumps(row)+'\n');telemetry.flush();next_telemetry=elapsed+5
                if mark is not None and elapsed>=mark:
                    dest=evid/f'menu-{mark}.png'
                    cmd=['import','-window','root',str(dest)] if shutil.which('import') else ['maim',str(dest)]
                    cap=sp.Popen(cmd,env=env,stdout=caplog,stderr=sp.STDOUT,start_new_session=True)
                    captures.append((cap,now,{'file':dest.name,'requested_seconds':mark,'started_seconds':elapsed}))
                    mark=next(marks,None)
                for cap,then,row in captures[:]:
                    if cap.poll() is not None or now-then>6:
                        util.stop_owned(cap);row.update(exit_code=cap.returncode,finished_seconds=time.monotonic()-start,game_alive_at_finish=proc.poll() is None)
                        frames.append(row);captures.remove((cap,then,row))
                if elapsed-last>=15:util.emit(f'CAPTURE_ACTIVE elapsed={elapsed:.0f}s pid={proc.pid}');last=elapsed
                time.sleep(.05)
            return {'exit_code':proc.wait(timeout=3),'seconds':round(time.monotonic()-start,3),'time_limit_reached':timed,'visual':'NOT_AUTOMATICALLY_PASS'}
    finally:
        util.stop_owned(proc)
        for cap,then,row in captures:
            util.stop_owned(cap);row.update(exit_code=cap.returncode,game_alive_at_finish=False);frames.append(row)
        write(evid/'capture-times.json',frames)


def pixel_signatures(evid:Path,util):
    rows=[]
    for p in sorted(evid.glob('menu-*.png')):
        row={'file':p.name,'png_sha256':sha(p)}
        try:
            if shutil.which('identify'):
                # ImageMagick's pixel signature excludes PNG timestamps/metadata.
                row['pixel_signature']=util.command(['identify','-format','%#',str(p)],timeout=10).decode().strip()
            else:row['pixel_signature']='UNAVAILABLE_NOT_INFERRED_FROM_PNG_HASH'
        except Exception as exc:row['signature_error']=str(exc)
        rows.append(row)
    write(evid/'pixel-signatures.json',rows)
    return rows


def collect_dumps(roots:list[Path],evid:Path,fresh_root:Path|None=None):
    records=[];total=0;seen=set()
    for root in roots:
        if not root.is_dir():continue
        for h,stage in TARGETS.items():
            for p in sorted(root.glob(f'{stage}_0x{int(h,16):016x}*')):
                if p.is_symlink() or not p.is_file():continue
                if not (p.name.endswith(('.irprogram.txt','.asl.txt','.srtprogram.txt','.bin','.spv'))):continue
                size=p.stat().st_size
                if size>12*1024**2 or total+size>MAX_BYTES:continue
                key=(str(p.resolve()),sha(p))
                if key in seen:continue
                seen.add(key);label='fresh' if fresh_root is not None and root==fresh_root else 'existing'
                dest=evid/'shaders'/label/h/(key[1][:12]+'-'+p.name)
                dest.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(p,dest);total+=size
                records.append({'shader':'0x'+h,'stage':stage,'origin':str(p),'relative':str(dest.relative_to(evid)),'sha256':key[1],'mtime_ns':p.stat().st_mtime_ns,'size':size,'provenance':label})
    write(evid/'shader-inventory.json',records)
    return records


def validate_spirv(records,evid:Path,util):
    exe=shutil.which('spirv-val');results=[]
    if not exe:return {'tool':'NOT_INSTALLED_NO_INSTALL_ATTEMPT','scope':'NOT_VALIDATED'}
    for i,r in enumerate(records):
        if not r['relative'].endswith('.spv') or r['provenance']!='fresh':continue
        log=evid/f'spirv-validation-{i:03d}.txt'
        proc=None
        try:
            with log.open('wb') as output:
                proc=sp.Popen([exe,str(evid/r['relative'])],stdout=output,stderr=sp.STDOUT,start_new_session=True)
                code=proc.wait(timeout=10)
            results.append({'file':r['relative'],'exit_code':code,'log':log.name})
        except sp.TimeoutExpired:results.append({'file':r['relative'],'result':'TIMEOUT'})
        finally:util.stop_owned(proc)
    return {'tool':exe,'scope':'universal SPIR-V structural validation, not full Vulkan enabled-feature acceptance','results':results}


def main():
    project=Path.home()/'projects/shadps4';logs=project/'logs'
    stamp=dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    evid=logs/('got-c925-remaining-shaders-'+stamp);evid.mkdir(parents=True)
    helper=Path(__file__).with_name('resume_c925_revalidation.py')
    result={'record_id':evid.name,'recorded_on':dt.date.today().isoformat(),'context_games':['CUSA11456','CUSA34384'],'tested_games':[],'build':'NO_REBUILD','source_edits':False,'game':{'result':'NOT_RUN'},'decision':'DIAGNOSTIC_ONLY_NO_RENDERER_PROMOTION','expected_binary':EXPECTED}
    locks=[];unloaded=[];before={};util=None;api=None;rc=1
    try:
        if sha(helper)!=HELPER_SHA:raise RuntimeError('HELPER_HASH_MISMATCH')
        spec=importlib.util.spec_from_file_location('runtime',helper)
        if spec is None or spec.loader is None:raise RuntimeError('HELPER_IMPORT_FAILED')
        util=importlib.util.module_from_spec(spec);spec.loader.exec_module(util);api=util.Ollama(evid)
        util.emit('EVIDENCE_DIRECTORY='+str(evid))
        for name in ('.got-pr95-scalar-bounds-build.lock','.got-indexed-revalidation.lock','.got-restore-accepted-renderer.lock','.got-indexed-lod.lock','.got-remaining-shaders.lock'):
            f=(logs/name).open('a+');fcntl.flock(f,fcntl.LOCK_EX|fcntl.LOCK_NB);locks.append(f)
        latest=json.loads((logs/PRIOR/'trial.json').read_text());prior=json.loads((logs/PROFILE_TRIAL/'trial.json').read_text())
        candidate=Path(prior['candidate']).resolve();source=candidate/'source';binary=candidate/'build/shadps4'
        if not candidate.is_relative_to((project/'GhostOfTsushima/experiments').resolve()):raise RuntimeError('CANDIDATE_PATH_REFUSED')
        if latest.get('build')!='PASS' or latest.get('candidate_sha256')!=EXPECTED or sha(binary)!=EXPECTED:raise RuntimeError('CANDIDATE_CHANGED_NO_GAME')
        for rel,values in latest['source_manifest'].items():
            if not (source/rel).resolve().is_relative_to(source) or sha(source/rel)!=values['after']:raise RuntimeError('SOURCE_CHANGED:'+rel)
        before[str(binary)]=sha(binary)
        for rel in latest['source_manifest']:before[str(source/rel)]=sha(source/rel)
        profile=Path(prior['isolated_profile']).resolve()
        if profile.name!='user' or not profile.is_relative_to(candidate/'runs'):raise RuntimeError('PRIVATE_PROFILE_PATH_REFUSED')
        protection=json.loads((logs/PROFILE_TRIAL/'profile-protection.json').read_text())
        for p in protection.get('protected_sha256',{}):
            path=Path(p);before[p]=sha(path) if path.is_file() else None
        protected=project/'GhostOfTsushima/experiments/c925-integrated-gpu-20261027-122701/build/shadps4'
        if not protected.is_file():raise RuntimeError('PROTECTED_ELF_MISSING')
        before[str(protected)]=sha(protected)
        roots=[profile/'shader/dumps',Path.home()/'.local/share/shadPS4/shader/dumps']
        collect_dumps(roots,evid)
        if util.emulator_pids():raise RuntimeError('OTHER_EMULATOR_RUNNING_NO_GAME')
        screen=os.environ.get('DISPLAY') or ':0'
        if not shutil.which('xdpyinfo') or not (shutil.which('import') or shutil.which('maim')):raise RuntimeError('DISPLAY_CAPTURE_TOOL_MISSING')
        (evid/'display.log').write_bytes(util.command(['env','DISPLAY='+screen,'xdpyinfo'],timeout=8))
        if not Path('/mnt/roms-all/ps4-games/CUSA11456/eboot.bin').is_file():raise RuntimeError('EBOOT_MISSING')
        work=candidate/'runs'/('remaining-shaders-'+stamp)
        with util.heartbeat('COPYING_DIAGNOSTIC_PROFILE'):
            before.update(copy_private_profile(profile,work/'user'))
        result['diagnostic_profile']=str(work/'user')
        result['configuration_change']='dump_shaders=true only in new disposable private profile; original profiles unchanged'
        util.ensure_headroom(api,evid,unloaded,result)
        if util.emulator_pids() or sha(binary)!=EXPECTED:raise RuntimeError('LAUNCH_PREFLIGHT_CHANGED')
        result['game']=capture_game(util,binary,work,evid,screen);result['tested_games']=['CUSA11456']
        fresh=work/'user/shader/dumps'
        records=collect_dumps(roots+[fresh],evid,fresh)
        result['fresh_shader_files']=sum(r['provenance']=='fresh' for r in records)
        result['fresh_shader_hashes']=sorted({r['shader'] for r in records if r['provenance']=='fresh'})
        result['frame_signatures']=pixel_signatures(evid,util)
        result['spirv_validation']=validate_spirv(records,evid,util)
        text=(evid/'game.log').read_text(errors='replace')
        result['dump_setting_confirmed']='GPU shouldDumpShaders: true' in text
        result['events']={k:text.count(k) for k in ('IndexedExplicitLodBound','INDEXED_TABLE_REVALIDATE','INDEXED_MATERIAL_BIND_REJECT','VK_ERROR_DEVICE_LOST','Device lost during submit')}
        result['status']='DIAGNOSTIC_CAPTURE_COMPLETE' if result['fresh_shader_files'] else 'NO_FRESH_TARGET_DUMPS'
        rc=0 if result['fresh_shader_files'] and (result['game']['time_limit_reached'] or result['game']['exit_code']==0) else 1
    except (Exception,KeyboardInterrupt) as exc:
        result['status']=str(exc) or type(exc).__name__; (evid/'failure.txt').write_text(traceback.format_exc());rc=130 if isinstance(exc,KeyboardInterrupt) else 1
        print('STOP='+result['status'],flush=True)
    finally:
        if unloaded and util is not None:
            result['ollama_restore']=util.restore_models(api,unloaded,evid)
            if any(x['result'] in ('RESTORE_FAILED','RESTORE_NOT_VERIFIED') for x in result['ollama_restore']):rc=1
        changed=[]
        for p,d in before.items():
            try:after=sha(Path(p)) if Path(p).is_file() else None
            except OSError:after='UNREADABLE'
            if after!=d:changed.append(p)
        result['original_candidate_source_profile_files_changed']=changed
        if changed:rc=1
        result['exit_code']=rc;write(evid/'trial.json',result)
        with tarfile.open(str(evid)+'.tar.gz','w:gz') as t:t.add(evid,arcname=evid.name)
        print('RESULT='+result.get('status','UNKNOWN'),flush=True);print('EVIDENCE='+str(evid)+'.tar.gz',flush=True)
        for f in locks:f.close()
    return rc

if __name__=='__main__':
    def stop(signum,frame):raise KeyboardInterrupt('TERMINATED_BY_USER')
    signal.signal(signal.SIGTERM,stop)
    sys.exit(main())
