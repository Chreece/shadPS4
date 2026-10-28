#!/usr/bin/env python3
"""Apply bounded raw-image lowering to the existing isolated PR99 candidate.
No main promotion, upstream rebase, invented descriptors or automated input.
"""
from __future__ import annotations
import datetime as dt
import fcntl
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
PREVIOUS='got-c925-remaining-shaders-20261028T155744943557Z'
BASE='c925-pr95-scalar-bounds-20261028T112756671630889Z'
EBOOT=Path('/mnt/roms-all/ps4-games/CUSA11456/eboot.bin')
RUNTIME_SHA='df7617549006c8733049a684f22f6ac2bf6b96701bf2fe1da5ee9265df568250'
KEPT={
 'src/video_core/renderer_vulkan/vk_rasterizer.cpp':'ebed2441ceda3d6b3e6a6ebe1afe7469ec35f9d8a99c3b7f0feb1a3fb533b4a0',
 'src/video_core/texture_cache/texture_cache.cpp':'3273c68d3c8a9932867603bb6cb12e6b546b228cfa32c7c4267a4d0de4207ed0'}

def load(name,path):
    spec=importlib.util.spec_from_file_location(name,path)
    if spec is None or spec.loader is None:raise RuntimeError('IMPORT_FAILED:'+name)
    m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m

def logged(util,args,path,label):
    util.emit('STAGE='+label+' LOG='+str(path));p=None
    try:
        with path.open('wb') as out,path.open('r',errors='replace') as reader:
            p=sp.Popen(args,stdout=out,stderr=sp.STDOUT,start_new_session=True)
            last=0
            with util.heartbeat(label):
                while p.poll() is None:
                    lines=reader.read().splitlines();now=time.monotonic()
                    if lines and now-last>=3:util.emit(label+' '+lines[-1][:250]);last=now
                    time.sleep(.2)
            rc=p.returncode
        if rc:
            print('\n'.join(path.read_text(errors='replace').splitlines()[-45:]),flush=True)
            raise RuntimeError(label+'_EXIT_'+str(rc))
        util.emit(label+'=PASS')
    finally:util.stop_owned(p)

def main():
    here=Path(__file__).resolve().parent
    u=load('runtime',here/'resume_c925_revalidation.py')
    if u.sha(here/'resume_c925_revalidation.py')!=RUNTIME_SHA:raise RuntimeError('RUNTIME_HASH_MISMATCH')
    fix=load('finite_fix',here/'apply_finite_raw.py')
    project=Path.home()/'projects/shadps4';logs=project/'logs'
    stamp=dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    evid=logs/('got-c925-finite-raw-images-'+stamp);evid.mkdir(parents=True)
    cand=project/'GhostOfTsushima/experiments'/BASE;src=cand/'source';binary=cand/'build/shadps4'
    protected=project/'GhostOfTsushima/experiments/c925-integrated-gpu-20261027-122701/build/shadps4'
    result={'schema_version':2,'record_id':evid.name,'recorded_on':dt.date.today().isoformat(),
        'game_id':'CUSA11456','context_games':['CUSA11456','CUSA34384'],'tested_games':[],
        'change':'CFG-proven finite raw image descriptors with GPU-guarded reads',
        'build':'NOT_RUN','game':{'result':'NOT_RUN'},'source_base':'c925523b49ef39434024b16b2b014d120471ee1a',
        'upstream_checked_not_integrated':'ca36c7e31e9edb958db8d2b94a0721e58eca9daf',
        'main_promotion':False,'decision':'DRAFT_NATIVE_AND_GRAPHICS_ACCEPTANCE_PENDING'}
    u.emit('EVIDENCE_DIRECTORY='+str(evid));locks=[];old={};changed=False;built=False
    backup=None;originals={};unloaded=[];api=u.Ollama(evid);rc=1
    try:
        for name in ('.got-pr95-scalar-bounds-build.lock','.got-indexed-revalidation.lock','.got-finite-raw-image.lock'):
            lock=(logs/name).open('a+');fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB);locks.append(lock)
        if u.sha(binary)!=EXPECTED:raise RuntimeError('CANDIDATE_CHANGED_NO_PATCH')
        if u.emulator_pids():raise RuntimeError('EMULATOR_ALREADY_RUNNING_NO_PATCH')
        for proc in Path('/proc').glob('[0-9]*'):
            try:
                if b'ninja' in (proc/'cmdline').read_bytes() and (proc/'cwd').resolve()==cand/'build':raise RuntimeError('BUILD_ALREADY_ACTIVE')
            except OSError:pass
        for path,sha in KEPT.items():
            if u.sha(src/path)!=sha:raise RuntimeError('ACCEPTED_RENDERER_CHANGED:'+path)
        old={p:(src/p).read_bytes() for p in fix.FILES}
        if (src/fix.NEW).exists():raise RuntimeError('FINITE_HEADER_ALREADY_EXISTS')
        fix.transform(old)  # All old hashes and edits must match before mutation.
        prior=json.loads((logs/PREVIOUS/'trial.json').read_text())
        if prior.get('expected_binary')!=EXPECTED:raise RuntimeError('PREVIOUS_EVIDENCE_MISMATCH')
        profile=Path(prior['diagnostic_profile']).resolve()
        if profile.name!='user' or not profile.is_relative_to((cand/'runs').resolve()):raise RuntimeError('PRIVATE_PROFILE_PATH_REFUSED')
        for p in profile.rglob('*'):
            if p.is_symlink():raise RuntimeError('PRIVATE_PROFILE_SYMLINK_REFUSED')
        if not (profile/'config.json').is_file():raise RuntimeError('PRIVATE_PROFILE_MISSING')
        for p in (profile/'config.json',profile/'custom_configs/CUSA11456.json'):
            if p.is_file():
                home=json.loads(p.read_text()).get('General',{}).get('home_dir','')
                if home and not Path(home).expanduser().resolve().is_relative_to(profile):raise RuntimeError('EXTERNAL_SAVE_ROOT_REFUSED')
        protection=json.loads((logs/'got-c925-indexed-revalidation-20261028T133609110195Z/profile-protection.json').read_text())
        originals={p:u.sha(p) if Path(p).is_file() else None for p in protection['protected_sha256']}
        originals[str(protected)]=u.sha(protected)
        u.save(evid/'original-files-before.json',originals)
        screen=os.environ.get('DISPLAY') or ':0'
        if not shutil.which('xdpyinfo') or not(shutil.which('import') or shutil.which('maim')):raise RuntimeError('DISPLAY_CAPTURE_TOOLS_MISSING')
        (evid/'display.log').write_bytes(u.command(['env','DISPLAY='+screen,'xdpyinfo'],timeout=8))
        eboot=EBOOT
        if not eboot.is_file():raise RuntimeError('GAME_FILE_MISSING')
        checkpoint=cand/'checkpoints'/('pr99-before-finite-raw-'+stamp);checkpoint.mkdir(parents=True)
        backup=checkpoint/'shadps4'
        with u.heartbeat('CHECKPOINT'):
            u.command(['cp','--reflink=auto','--preserve=mode,timestamps',str(binary),str(backup)],timeout=120)
            if u.sha(backup)!=EXPECTED:raise RuntimeError('CHECKPOINT_FAILED')
        result['previous_candidate_checkpoint']=str(backup)
        changed=True;manifest=fix.apply(src,evid);result['source_manifest']=manifest
        logged(u,[sys.executable,'-u',str(here/'test_finite_raw.py'),str(src),str(evid)],evid/'contract-tests.log','CONTRACT_TESTS')
        logged(u,['cmake','--build',str(cand/'build'),'--target','shadps4','--parallel','5','--','-k','0'],evid/'build.log','INCREMENTAL_BUILD')
        built=True;result['build']='PASS';result['candidate_sha256']=u.sha(binary)
        for p,sha in KEPT.items():
            if u.sha(src/p)!=sha:raise RuntimeError('ACCEPTED_RENDERER_CHANGED_DURING_BUILD')
        for p,entry in manifest.items():
            if u.sha(src/p)!=entry['after']:raise RuntimeError('SOURCE_CHANGED_DURING_BUILD:'+p)
        if u.emulator_pids():raise RuntimeError('OTHER_EMULATOR_RUNNING_NO_LAUNCH')
        u.ensure_headroom(api,evid,unloaded,result)
        if u.emulator_pids():raise RuntimeError('OTHER_EMULATOR_STARTED_NO_LAUNCH')
        started=time.time();result['game']=u.run_game(binary,eboot,profile.parent,evid,screen)
        result['tested_games']=['CUSA11456']
        text=(evid/'game.log').read_text(errors='replace')
        keys=('FiniteRawImageRead:','FiniteRawImageSharp:','IndexedExplicitLodBound','INDEXED_TABLE_REVALIDATE','INDEXED_MATERIAL_BIND_REJECT','Sharp source was not flatenned','StencilReferenceExactMerged','Stencil test requires','VK_ERROR_DEVICE_LOST','Device lost during submit')
        result['sample_limited_events']={k:text.count(k) for k in keys}
        hashes=set(re.findall(r'FiniteRawImageRead: shader=0x([0-9a-fA-F]+)',text));dumped=[];total=0
        for h in hashes:
            for p in (profile/'shader/dumps').glob('*'+h+'*'):
                if not p.is_file() or p.is_symlink() or p.stat().st_mtime<started:continue
                if p.suffix not in ('.txt','.spv') or p.stat().st_size>20*1024**2:continue
                total+=p.stat().st_size
                if total>60*1024**2:continue
                dest=evid/'fresh-shaders'/p.name;dest.parent.mkdir(exist_ok=True);shutil.copy2(p,dest)
                dumped.append({'file':p.name,'sha256':u.sha(p),'mtime':p.stat().st_mtime})
        u.save(evid/'fresh-shader-inventory.json',dumped)
        result['status']='CHANGED_SOURCE_CAPTURE_COMPLETE_VISUAL_PENDING' if result['game']['time_limit_reached'] else 'GAME_EXITED_EARLY'
        rc=0 if result['game']['time_limit_reached'] or result['game']['exit_code']==0 else 1
    except (Exception,KeyboardInterrupt) as exc:
        result['status']=str(exc) or type(exc).__name__;u.emit('STOP='+result['status'])
        (evid/'failure.txt').write_text(traceback.format_exc());rc=130 if isinstance(exc,KeyboardInterrupt) else 1
    finally:
        if unloaded:
            result['ollama_restore']=u.restore_models(api,unloaded,evid)
            if any(x['result'] in ('RESTORE_FAILED','RESTORE_NOT_VERIFIED') for x in result['ollama_restore']):rc=1
        if changed and not built:
            try:
                for p,data in old.items():(src/p).write_bytes(data)
                (src/fix.NEW).unlink(missing_ok=True)
                if backup and backup.is_file() and u.sha(backup)==EXPECTED:shutil.copy2(backup,binary)
                result['rollback']='SOURCE_AND_BINARY_RESTORED' if u.sha(binary)==EXPECTED else 'VERIFY_FAILED'
            except Exception as exc:result['rollback']='FAILED:'+str(exc)
        try:
            result['original_files_changed']=[p for p,h in originals.items() if (u.sha(p) if Path(p).is_file() else None)!=h]
            if result['original_files_changed']:rc=1
            if binary.is_file():result['candidate_after_sha256']=u.sha(binary)
        except Exception as exc:result['verification_error']=str(exc);rc=1
        result['runner_exit_code']=rc;u.save(evid/'trial.json',result)
        with tarfile.open(str(evid)+'.tar.gz','w:gz') as t:t.add(evid,arcname=evid.name)
        u.emit('RESULT='+result.get('status','UNKNOWN'));u.emit('EVIDENCE='+str(evid)+'.tar.gz')
        for lock in locks:lock.close()
    return rc

if __name__=='__main__':
    def terminated(signum,frame):raise KeyboardInterrupt('TERMINATED_BY_USER')
    signal.signal(signal.SIGTERM,terminated)
    sys.exit(main())
