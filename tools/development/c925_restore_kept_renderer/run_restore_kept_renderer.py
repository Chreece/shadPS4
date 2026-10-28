#!/usr/bin/env python3
"""Repair missing accepted renderer semantics in the existing isolated C925 build.
No new main promotion, game-specific rendering workaround or upstream rebase.
"""
from __future__ import annotations
import datetime as dt
import fcntl
import importlib.util
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess as sp
import sys
import tarfile
import time
import traceback

EXPECTED_ELF='21a26822390451d4b21bde8feae4712b2bd66bf068096ad246055db914122fde'
PREVIOUS='got-c925-indexed-revalidation-20261028T133609110195Z'
EBOOT=Path('/mnt/roms-all/ps4-games/CUSA11456/eboot.bin')
PINNED_RESUME_SHA='df7617549006c8733049a684f22f6ac2bf6b96701bf2fe1da5ee9265df568250'


def module(name, path):
    spec=importlib.util.spec_from_file_location(name,path)
    if spec is None or spec.loader is None:
        raise RuntimeError('MODULE_UNAVAILABLE:'+name)
    value=importlib.util.module_from_spec(spec);spec.loader.exec_module(value)
    return value


def build_command(util, args, path, stage):
    util.emit('STAGE='+stage+' LOG='+str(path))
    p=None
    try:
        with path.open('wb') as out, path.open('r',errors='replace') as reader:
            p=sp.Popen(args,stdout=out,stderr=sp.STDOUT,start_new_session=True)
            last=0
            with util.heartbeat(stage):
                while p.poll() is None:
                    lines=reader.read().splitlines();now=time.monotonic()
                    if lines and now-last>=3:
                        util.emit(stage+' '+lines[-1][:260]);last=now
                    time.sleep(.2)
            rc=p.returncode
        if rc:
            for line in path.read_text(errors='replace').splitlines()[-35:]:print(line,flush=True)
            raise RuntimeError(stage+'_EXIT_'+str(rc))
        util.emit(stage+'=PASS')
    finally:
        util.stop_owned(p)


def private_profile(candidate, prior):
    profile=Path(prior['isolated_profile']).resolve()
    if profile.name!='user' or not profile.is_relative_to((candidate/'runs').resolve()):
        raise RuntimeError('PRIVATE_PROFILE_OUTSIDE_CANDIDATE')
    if not (profile/'config.json').is_file():
        raise RuntimeError('PRIVATE_PROFILE_MISSING')
    for p in profile.rglob('*'):
        if p.is_symlink():raise RuntimeError('PRIVATE_PROFILE_SYMLINK_REFUSED')
    for path in (profile/'config.json',profile/'custom_configs/CUSA11456.json'):
        if path.is_file():
            data=json.loads(path.read_text());home=data.get('General',{}).get('home_dir','')
            if home and not Path(home).expanduser().resolve().is_relative_to(profile):
                raise RuntimeError('PRIVATE_PROFILE_EXTERNAL_SAVE_ROOT')
    return profile


def main():
    here=Path(__file__).resolve().parent
    util=module('resume_runtime',here/'resume_c925_revalidation.py')
    if util.sha(here/'resume_c925_revalidation.py')!=PINNED_RESUME_SHA:
        raise RuntimeError('RUNTIME_HELPER_HASH_MISMATCH')
    fix=module('kept_renderer',here/'restore_kept_renderer.py')
    project=Path.home()/'projects/shadps4';logs=project/'logs'
    stamp=dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    evid=logs/('got-c925-accepted-renderer-'+stamp);evid.mkdir(parents=True)
    result={'schema_version':2,'record_id':evid.name,'context_games':['CUSA11456','CUSA34384'],
        'tested_games':[],'restored_existing_prs':[69,68],'build':'NOT_RUN','game':{'result':'NOT_RUN'},
        'new_graphics_fix_claimed':False,'main_promotion':False,'upstream_rebase':False,
        'upstream_checked_not_integrated':'caaa5419726c912013ffd5154dd4a9bac461cd94',
        'prior_runtime':'got-c925-revalidation-resume-20261028T135245496700Z',
        'decision':'RESTORE_ACCEPTED_BASELINE_NOT_FULL_SCENE_ACCEPTANCE'}
    util.emit('EVIDENCE_DIRECTORY='+str(evid))
    locks=[];unloaded=[];api=util.Ollama(evid);originals={};sources={};changed=False
    native_ok=False;binary=None;backup=None;rc=1;source=None;candidate=None
    try:
        for name in ('.got-pr95-scalar-bounds-build.lock','.got-indexed-revalidation.lock','.got-restore-accepted-renderer.lock'):
            lock=(logs/name).open('a+');fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB);locks.append(lock)
        previous=logs/PREVIOUS
        prior=json.loads((previous/'trial.json').read_text())
        if prior.get('candidate_sha256')!=EXPECTED_ELF or prior.get('build')!='PASS':
            raise RuntimeError('VERIFIED_PR96_BUILD_RECORD_MISMATCH')
        candidate=Path(prior['candidate']).resolve()
        if not candidate.is_relative_to((project/'GhostOfTsushima/experiments').resolve()):
            raise RuntimeError('CANDIDATE_PATH_REFUSED')
        source=candidate/'source';binary=candidate/'build/shadps4'
        if util.sha(binary)!=EXPECTED_ELF:raise RuntimeError('CANDIDATE_CHANGED_NO_PATCH')
        if util.emulator_pids():raise RuntimeError('EMULATOR_RUNNING_NO_PATCH')
        for p in Path('/proc').glob('[0-9]*'):
            try:
                cmd=(p/'cmdline').read_bytes().replace(b'\0',b' ')
                if b'ninja' in cmd and (p/'cwd').resolve()==candidate/'build':
                    raise RuntimeError('CANDIDATE_ALREADY_BUILDING')
            except OSError:pass
        manifest=json.loads((previous/'source-manifest.json').read_text())
        for rel,values in manifest.items():
            path=source/rel
            if not path.resolve().is_relative_to(source.resolve()) or util.sha(path)!=values['after']:
                raise RuntimeError('PR96_SOURCE_CHANGED:'+rel)
        result['retained_pr96_manifest']=manifest
        sources={p:(source/p).read_bytes() for p in fix.PATHS}
        fix.transform(sources)  # Validate both expected source bytes before any mutation.
        profile=private_profile(candidate,prior)
        result['private_profile']=str(profile)
        protection=json.loads((previous/'profile-protection.json').read_text())
        protect=[Path(p) for p in protection.get('protected_sha256',{})]
        protected=project/'GhostOfTsushima/experiments/c925-integrated-gpu-20261027-122701/build/shadps4'
        if not protected.is_file():raise RuntimeError('PROTECTED_ELF_MISSING')
        protect.append(protected)
        with util.heartbeat('VERIFYING_ORIGINALS'):
            originals={str(p):util.sha(p) if p.is_file() else None for p in protect}
        util.save(evid/'original-files-before.json',originals)
        if not shutil.which('xdpyinfo') or not (shutil.which('import') or shutil.which('maim')):
            raise RuntimeError('DISPLAY_CAPTURE_TOOL_UNAVAILABLE_NO_PATCH')
        screen=os.environ.get('DISPLAY') or ':0'
        (evid/'display-before.log').write_bytes(util.command(['env','DISPLAY='+screen,'xdpyinfo'],timeout=8))
        eboot=EBOOT
        if not eboot.is_file():raise RuntimeError('GAME_MISSING_NO_PATCH')
        checkpoint=candidate/'checkpoints'/('pr96-before-kept-renderer-'+stamp)
        checkpoint.mkdir(parents=True);backup=checkpoint/'shadps4'
        util.emit('STAGE=PRESERVING_PR96_ELF')
        with util.heartbeat('PRESERVING_PR96_ELF'):
            util.command(['cp','--reflink=auto','--preserve=mode,timestamps',str(binary),str(backup)],timeout=120)
            if util.sha(backup)!=EXPECTED_ELF:raise RuntimeError('CHECKPOINT_VERIFY_FAILED')
        result['pr96_checkpoint']=str(backup)
        changed=True
        result['restored_source_manifest']=fix.apply(source,evid)
        compilers=[]
        for cxx in ('g++','clang++'):
            if not shutil.which(cxx):continue
            tag=cxx.replace('+','p');exe=checkpoint/('fixture-'+tag)
            build_command(util,[cxx,'-std=c++20','-O2','-Wall','-Wextra','-Werror','-pedantic',
                str(evid/'restored_renderer_fixture.cpp'),'-o',str(exe)],evid/(tag+'-compile.log'),'UNIT_COMPILE_'+tag)
            (evid/(tag+'-run.log')).write_bytes(util.command([str(exe)],timeout=30))
            compilers.append(cxx)
        if not compilers:raise RuntimeError('NO_TEST_COMPILER')
        result['unit_compilers_passed']=compilers
        build_command(util,['cmake','--build',str(candidate/'build'),'--target','shadps4','--parallel','5','--','-k','0'],
                      evid/'build.log','INCREMENTAL_RENDERER_BUILD')
        if not binary.is_file():raise RuntimeError('NO_LINKED_CANDIDATE')
        for rel,values in manifest.items():
            if util.sha(source/rel)!=values['after']:raise RuntimeError('PR96_SOURCE_LOST_AFTER_BUILD:'+rel)
        for rel,values in result['restored_source_manifest'].items():
            if util.sha(source/rel)!=values['after']:raise RuntimeError('RESTORED_SOURCE_CHANGED_DURING_BUILD:'+rel)
        native_ok=True;result['build']='PASS';result['candidate_sha256']=util.sha(binary)
        if util.sha(protected)!=originals[str(protected)]:raise RuntimeError('PROTECTED_CHANGED_EXTERNALLY_NO_GAME')
        if util.emulator_pids():raise RuntimeError('OTHER_EMULATOR_RUNNING_NO_GAME')
        (evid/'display-prelaunch.log').write_bytes(util.command(['env','DISPLAY='+screen,'xdpyinfo'],timeout=8))
        util.ensure_headroom(api,evid,unloaded,result)
        if util.emulator_pids() or util.sha(binary)!=result['candidate_sha256']:
            raise RuntimeError('LAUNCH_PREFLIGHT_CHANGED')
        result['game']=util.run_game(binary,eboot,profile.parent,evid,screen)
        result['tested_games']=['CUSA11456']
        text=(evid/'game.log').read_text(errors='replace')
        markers=('StencilReferenceExactMerged','StencilReferenceUnrepresentable','Stencil test requires',
            'INDEXED_TABLE_REVALIDATE','INDEXED_MATERIAL_BIND_REJECT','Sharp source was not flatenned',
            'GuestIndexedImageDimensions','GpuDescriptorTableCpuCoherence','VK_ERROR_DEVICE_LOST','Device lost during submit')
        result['sample_limited_events']={key:text.count(key) for key in markers}
        result['status']='RESTORED_BASELINE_CAPTURED_VISUAL_PENDING' if result['game']['time_limit_reached'] else 'GAME_EXITED_EARLY'
        rc=0 if result['game']['time_limit_reached'] or result['game']['exit_code']==0 else 1
    except (Exception,KeyboardInterrupt) as exc:
        result['status']=str(exc) or type(exc).__name__;util.emit('STOP='+result['status'])
        (evid/'failure.txt').write_text(traceback.format_exc());rc=130 if isinstance(exc,KeyboardInterrupt) else 1
    finally:
        if unloaded:
            result['ollama_restore']=util.restore_models(api,unloaded,evid)
            if any(x['result'] in ('RESTORE_FAILED','RESTORE_NOT_VERIFIED') for x in result['ollama_restore']):rc=1
        if changed and not native_ok:
            try:
                for rel,data in sources.items():
                    path=source/rel;path.write_bytes(data);os.utime(path,None)
                if backup and backup.is_file() and util.sha(backup)==EXPECTED_ELF:
                    shutil.copy2(backup,binary)
                result['rollback']='SOURCE_AND_PR96_ELF_RESTORED' if util.sha(binary)==EXPECTED_ELF else 'BINARY_VERIFY_FAILED'
            except Exception as exc:result['rollback']='FAILED:'+str(exc)
        try:
            result['original_files_changed']=[p for p,d in originals.items() if (util.sha(p) if Path(p).is_file() else None)!=d]
            if result['original_files_changed']:rc=1
            if binary and binary.is_file():result['candidate_after_sha256']=util.sha(binary)
        except Exception as exc:result['verification_error']=str(exc);rc=1
        result['runner_exit_code']=rc
        util.save(evid/'trial.json',result)
        with tarfile.open(str(evid)+'.tar.gz','w:gz') as archive:archive.add(evid,arcname=evid.name)
        util.emit('RESULT='+result.get('status','UNKNOWN'));util.emit('EVIDENCE='+str(evid)+'.tar.gz')
        for lock in locks:lock.close()
    return rc

if __name__=='__main__':
    def terminate(signum,frame):raise KeyboardInterrupt('TERMINATED_BY_USER')
    signal.signal(signal.SIGTERM,terminate)
    sys.exit(main())
