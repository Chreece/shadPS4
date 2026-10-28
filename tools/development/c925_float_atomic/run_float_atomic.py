#!/usr/bin/env python3
"""Confirmed float-atomic correction on an isolated native C925 candidate.
Offline reference validation -> source contract tests -> incremental build ->
owned menu capture -> actual regenerated shader validation. No main promotion.
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

BASE='c925-pr95-scalar-bounds-20261028T112756671630889Z'
EXPECTED='9f0f6c9fe83f15aec6dea1dcf3f23226d944472daa0bdb6258cff13a95f219d3'
PROFILE_TRIAL='got-c925-remaining-shaders-20261028T155744943557Z'
PREVIOUS='got-c925-finite-raw-images-20261028T171004960983Z'
HELPERS={
 'resume_c925_revalidation.py':'df7617549006c8733049a684f22f6ac2bf6b96701bf2fe1da5ee9265df568250',
 'resume_finite_raw.py':'c6017d0d96e83341315e6a05dafb3fb5478239b384b41e510c475b6d89884a0b'}

def load(name,path):
    spec=importlib.util.spec_from_file_location(name,path)
    if spec is None or spec.loader is None:raise RuntimeError('IMPORT_FAILED:'+name)
    obj=importlib.util.module_from_spec(spec);spec.loader.exec_module(obj);return obj

def save(path,data):path.write_text(json.dumps(data,indent=2)+'\n')

def logged(u,args,path,label):
    u.emit('STAGE='+label+' LOG='+str(path));proc=None
    try:
        with path.open('wb') as out,path.open('r',errors='replace') as reader:
            proc=sp.Popen([str(x) for x in args],stdout=out,stderr=sp.STDOUT,start_new_session=True)
            last=0
            with u.heartbeat(label):
                while proc.poll() is None:
                    lines=reader.read().splitlines();now=time.monotonic()
                    if lines and now-last>=3:u.emit(label+' '+lines[-1][:260]);last=now
                    time.sleep(.2)
            code=proc.returncode
        if code:
            print('\n'.join(path.read_text(errors='replace').splitlines()[-45:]),flush=True)
            raise RuntimeError(label+'_EXIT_'+str(code))
        u.emit(label+'=PASS')
    finally:u.stop_owned(proc)

def fresh_validation(u,validator,profile,evid,started,project):
    """Validate newly emitted bytes, never replace a shader or original cache."""
    val,asm,dis=validator.find_tools(project)
    helptext=u.command([str(val),'--help'],timeout=10).decode(errors='replace')
    modes={'universal':['--target-env','spv1.6'], 'vulkan13-strict':['--target-env','vulkan1.3']}
    if '--allow-offset-texture-operand' in helptext:
        modes['vulkan13-maintenance8']=['--target-env','vulkan1.3','--allow-offset-texture-operand']
    unique={};budget=0;target_modules=[];inventory=[];skipped=[]
    for p in sorted((profile/'shader/dumps').glob('*.spv')):
        if p.is_symlink() or not p.is_file():continue
        st=p.stat()
        if st.st_mtime<started:continue
        if st.st_size>20*1024**2 or budget+st.st_size>96*1024**2 or len(unique)>=384:
            skipped.append(p.name);continue
        digest=u.sha(p);inventory.append({'name':p.name,'sha256':digest,'mtime':st.st_mtime,'bytes':st.st_size})
        unique.setdefault(digest,p);budget+=st.st_size
        if '79ab8f30' in p.name.lower():target_modules.append(digest)
    directory=evid/'shader-validation';directory.mkdir()
    save(directory/'fresh-inventory.json',inventory)
    results=[];deadline=time.monotonic()+180
    for digest,p in unique.items():
        if time.monotonic()>deadline:
            skipped.append('TIME_BUDGET_REACHED');break
        outcomes={}
        for mode,flags in modes.items():
            log=directory/(digest+'-'+mode+'.log')
            code=validator.call([val,*flags,p],log)
            message=log.read_text(errors='replace')
            results.append({'sha256':digest,'name':p.name,'mode':mode,'exit_code':code,'diagnostic':message})
            outcomes[mode]=code
        # Preserve failed or affected actual modules, not any saved game data.
        if any(outcomes.values()) or digest in target_modules:
            target=directory/(digest+'.spv');shutil.copy2(p,target)
            validator.call([dis,target,'-o',directory/(digest+'.spvasm')],directory/(digest+'-dis.log'))
        if len(results)%30==0:u.emit('FRESH_SHADER_VALIDATIONS='+str(len(results)))
    save(directory/'results.json',results)
    for p in (profile/'shader/dumps').glob('*79ab8f30*'):
        if p.is_file() and not p.is_symlink() and p.suffix=='.txt' and p.stat().st_mtime>=started and p.stat().st_size<8*1024**2:
            shutil.copy2(p,directory/p.name)
    target_results=[r for r in results if r['sha256'] in target_modules]
    return {'fresh_unique_modules':len(unique),'validation_count':len(results),'skipped':skipped,
        'failures_by_mode':{m:sum(x['mode']==m and x['exit_code']!=0 for x in results) for m in modes},
        'atomic_target_modules':target_modules,
        'atomic_target_all_modes_pass':bool(target_results) and all(x['exit_code']==0 for x in target_results),
        'maintenance8_mode_qualification':'Conditional on actual device feature; source captured separately, strict errors retained',
        'actual_gpu_execution':'NOT_PROVEN_BY_VALIDATION'}

def main(u=None,mem=None,project=None):
    here=Path(__file__).resolve().parent
    if u is None:
        for name,sha in HELPERS.items():
            if hashlib.sha256((here/name).read_bytes()).hexdigest()!=sha:raise RuntimeError('HELPER_CHANGED:'+name)
        u=load('runtime_helper',here/'resume_c925_revalidation.py')
        mem=load('memory_helper',here/'resume_finite_raw.py')
    fix=load('atomic_fix',here/'apply_float_atomic.py')
    validator=load('atomic_validation',here/'validate_atomic_fixture.py')
    project=project or Path.home()/'projects/shadps4';logs=project/'logs'
    stamp=dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    evid=logs/('got-c925-float-atomic-'+stamp);evid.mkdir(parents=True)
    cand=project/'GhostOfTsushima/experiments'/BASE;source=cand/'source';binary=cand/'build/shadps4'
    result={'schema_version':2,'record_id':evid.name,'recorded_on':dt.date.today().isoformat(),
        'journal_scope':'UNIVERSAL_SHADPS4','context_games':['CUSA11456','CUSA34384'],'tested_games':[],
        'change':'Separate uint atomic image view and exactly one sign-selected atomic side effect',
        'source_base':'c925523b49ef39434024b16b2b014d120471ee1a','build':'NOT_RUN','game':{'result':'NOT_RUN'},
        'graphics_fixed':'UNVERIFIED','main_promotion':False,'upstream_rebase':False,
        'upstream_checked_not_integrated':'b6f0c58dba9ee75b93477703032ae4f1e0de8f1c'}
    locks=[];pending=[];originals={};old={};changed=False;built=False;checkpoint=None
    api=u.Ollama(evid);rc=1
    u.emit('EVIDENCE_DIRECTORY='+str(evid))
    try:
        for name in ('.got-pr95-scalar-bounds-build.lock','.got-indexed-revalidation.lock','.got-finite-raw-image.lock','.got-float-atomic.lock'):
            f=(logs/name).open('a+')
            try:fcntl.flock(f,fcntl.LOCK_EX|fcntl.LOCK_NB)
            except Exception:f.close();raise
            locks.append(f)
        if u.sha(binary)!=EXPECTED:raise RuntimeError('CANDIDATE_CHANGED_NO_PATCH')
        if u.emulator_pids():raise RuntimeError('OTHER_EMULATOR_RUNNING_NO_PATCH')
        for proc in Path('/proc').glob('[0-9]*'):
            try:
                if b'ninja' in (proc/'cmdline').read_bytes() and (proc/'cwd').resolve()==cand/'build':
                    raise RuntimeError('BUILD_ALREADY_ACTIVE_NO_PATCH')
            except OSError:pass
        mem.verify_sources(u,source)
        old={p:(source/p).read_bytes() for p in fix.FILES};fix.transform(old)
        profile=mem.verified_profile(cand,json.loads((logs/PROFILE_TRIAL/'trial.json').read_text()))
        result['private_profile']=str(profile)
        for rel in ('src/video_core/renderer_vulkan/vk_instance.cpp','src/video_core/renderer_vulkan/vk_instance.h'):
            p=source/rel
            if p.is_file() and p.stat().st_size<2*1024**2:
                dest=evid/'device-feature-source'/p.name;dest.parent.mkdir(exist_ok=True);shutil.copy2(p,dest)
        # Actual tools must approve the reference type/branch transformation
        # before any C++ source mutation or compilation. Original modules stay intact.
        u.emit('STAGE=OFFLINE_REAL_VALIDATOR_PREFLIGHT')
        with u.heartbeat('OFFLINE_VALIDATION'):
            result['offline_reference']=validator.run(project,evid/'offline-validation')
        protection=json.loads((logs/PREVIOUS/'original-files-before.json').read_text())
        protected=project/'GhostOfTsushima/experiments/c925-integrated-gpu-20261027-122701/build/shadps4'
        if not protected.is_file():raise RuntimeError('PROTECTED_BINARY_MISSING')
        originals={p:u.sha(p) if Path(p).is_file() else None for p in protection}
        originals[str(protected)]=u.sha(protected);save(evid/'original-files-before.json',originals)
        checkpoint=cand/'checkpoints'/('pr100-before-float-atomic-'+stamp);checkpoint.mkdir(parents=True)
        with u.heartbeat('CHECKPOINT'):
            u.command(['cp','--reflink=auto','--preserve=mode,timestamps',str(binary),str(checkpoint/'shadps4')],timeout=120)
            if u.sha(checkpoint/'shadps4')!=EXPECTED:raise RuntimeError('CHECKPOINT_VERIFY_FAILED')
        result['previous_candidate_checkpoint']=str(checkpoint/'shadps4')
        changed=True;manifest=fix.apply(source,evid);result['source_manifest']=manifest
        logged(u,[sys.executable,'-u',here/'test_float_atomic.py',source,evid/'contract-tests'],evid/'contract-tests.log','SOURCE_CONTRACT_TESTS')
        logged(u,['cmake','--build',cand/'build','--target','shadps4','--parallel','5','--','-k','0'],evid/'build.log','INCREMENTAL_BUILD')
        if not binary.is_file():raise RuntimeError('LINKED_BINARY_MISSING')
        built=True;result['build']='PASS';result['candidate_sha256']=u.sha(binary)
        expected=dict(mem.SOURCES,**mem.KEPT);expected.update({p:x['after'] for p,x in manifest.items()})
        for p,sha in expected.items():
            if u.sha(source/p)!=sha:raise RuntimeError('SOURCE_OR_ACCEPTED_FIX_CHANGED:'+p)
        screen=os.environ.get('DISPLAY') or ':0'
        if not shutil.which('xdpyinfo') or not (shutil.which('import') or shutil.which('maim')):
            raise RuntimeError('DISPLAY_CAPTURE_TOOLS_MISSING_NO_GAME')
        (evid/'display.log').write_bytes(u.command(['env','DISPLAY='+screen,'xdpyinfo'],timeout=8))
        eboot=Path('/mnt/roms-all/ps4-games/CUSA11456/eboot.bin')
        if not eboot.is_file():raise RuntimeError('GAME_FILE_MISSING_NO_GAME')
        mem.verified_headroom(u,api,evid,pending,result)
        if u.emulator_pids() or u.sha(binary)!=result['candidate_sha256']:raise RuntimeError('LAUNCH_PREFLIGHT_CHANGED')
        if u.free_vram()<mem.MIN_FREE:raise RuntimeError('VRAM_DROPPED_BEFORE_LAUNCH')
        for p,sha in expected.items():
            if u.sha(source/p)!=sha:raise RuntimeError('SOURCE_CHANGED_BEFORE_LAUNCH:'+p)
        started=time.time();result['game']=u.run_game(binary,eboot,profile.parent,evid,screen)
        result['tested_games']=['CUSA11456']
        text=(evid/'game.log').read_text(errors='replace')
        result['sample_limited_events']={key:text.count(key) for key in (
            'EmulatedImageFloatAtomicU32View:','FiniteRawImageRead:','IndexedExplicitLodBound',
            'INDEXED_TABLE_REVALIDATE','INDEXED_MATERIAL_BIND_REJECT','StencilReferenceExactMerged',
            'Stencil test requires','VK_ERROR_DEVICE_LOST','Device lost during submit')}
        signatures={}
        if shutil.which('identify'):
            for p in sorted(evid.glob('menu-*.png')):
                try:signatures[p.name]=u.command(['identify','-format','%[signature]',str(p)],timeout=10).decode().strip()
                except Exception as exc:signatures[p.name]={'error':str(exc)}
        result['decoded_pixel_signatures']=signatures
        u.emit('STAGE=VALIDATE_ACTUAL_REGENERATED_SHADERS')
        with u.heartbeat('ACTUAL_SHADER_VALIDATION'):
            result['actual_shader_validation']=fresh_validation(u,validator,profile,evid,started,project)
        v=result['actual_shader_validation']
        result['status']='CHANGED_SOURCE_CAPTURE_AND_VALIDATION_COMPLETE_VISUAL_PENDING'
        rc=0 if (result['game']['time_limit_reached'] or result['game']['exit_code']==0) else 1
        if v['failures_by_mode']['universal'] or not v['atomic_target_all_modes_pass']:
            result['status']='ACTUAL_SHADER_ACCEPTANCE_FAILED_OR_TARGET_NOT_CAPTURED';rc=1
        if v['skipped']:result['validation_incomplete']=True
    except (Exception,KeyboardInterrupt) as exc:
        result['status']=str(exc) or type(exc).__name__;u.emit('STOP='+result['status'])
        (evid/'failure.txt').write_text(traceback.format_exc());rc=130 if isinstance(exc,KeyboardInterrupt) else 1
    finally:
        if pending:
            try:
                result['ollama_restore']=u.restore_models(api,pending,evid)
                if any(x['result'] in ('RESTORE_FAILED','RESTORE_NOT_VERIFIED') for x in result['ollama_restore']):rc=1
            except Exception as exc:result['restoration_error']=str(exc);rc=1
        if changed and not built:
            try:
                for p,data in old.items():(source/p).write_bytes(data)
                if checkpoint and (checkpoint/'shadps4').is_file() and u.sha(checkpoint/'shadps4')==EXPECTED:
                    shutil.copy2(checkpoint/'shadps4',binary)
                result['rollback']='SOURCE_AND_PR100_ELF_RESTORED' if u.sha(binary)==EXPECTED else 'VERIFY_FAILED'
            except Exception as exc:result['rollback']='FAILED:'+str(exc)
        try:
            result['original_files_changed']=[p for p,sha in originals.items() if (u.sha(p) if Path(p).is_file() else None)!=sha]
            if result['original_files_changed']:rc=1
            if binary.is_file():result['candidate_after_sha256']=u.sha(binary)
        except Exception as exc:result['verification_error']=str(exc);rc=1
        result['runner_exit_code']=rc;save(evid/'trial.json',result)
        # Test executables are local scaffolding, not necessary evidence.
        def filtered(info):
            if Path(info.name).name in ('fixture-gpp','fixture-clangpp','fixture-sanitizers'):return None
            return info
        with tarfile.open(str(evid)+'.tar.gz','w:gz') as tf:tf.add(evid,arcname=evid.name,filter=filtered)
        u.emit('RESULT='+result.get('status','UNKNOWN'));u.emit('EVIDENCE='+str(evid)+'.tar.gz')
        for f in locks:f.close()
    return rc

if __name__=='__main__':
    def stop(signum,frame):raise KeyboardInterrupt('TERMINATED_BY_USER')
    signal.signal(signal.SIGTERM,stop);sys.exit(main())
