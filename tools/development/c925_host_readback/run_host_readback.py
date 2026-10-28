#!/usr/bin/env python3
"""Conditional two-file readback visibility correction in the isolated PR102 build.
Never remove waits, force descriptor values, change saved graphics options or rebuild a baseline.
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
EXPECTED='da4a37e5f60069afbdcc7ab5f24a1e3495f92deae8b21c851dd1157a5d1dcbb3'
PREVIOUS='got-c925-frame-boundary-20261028T184819836147Z'
ATOMIC='got-c925-float-atomic-20261028T182458657462Z'
RUNTIME_SHA='df7617549006c8733049a684f22f6ac2bf6b96701bf2fe1da5ee9265df568250'
HEADROOM_SHA='c6017d0d96e83341315e6a05dafb3fb5478239b384b41e510c475b6d89884a0b'
RENDERER='src/video_core/renderer_vulkan/vk_rasterizer.cpp'
RENDERER_SHA='ebed2441ceda3d6b3e6a6ebe1afe7469ec35f9d8a99c3b7f0feb1a3fb533b4a0'
EBOOT=Path('/mnt/roms-all/ps4-games/CUSA11456/eboot.bin')
REQUIRED_HELPERS=(
 'src/video_core/renderer_vulkan/vk_runtime.cpp',
 'src/video_core/renderer_vulkan/vk_runtime.h',
 'src/video_core/renderer_vulkan/vk_staging_buffer_pool.h',
 'src/video_core/renderer_vulkan/vk_semaphore.h',
 'src/video_core/texture_cache/tile_manager.cpp',
 'src/video_core/texture_cache/tile_manager.h',
 'src/video_core/buffer_cache/buffer.h',
)
OPTIONAL_HELPERS=(
 'src/video_core/renderer_vulkan/vk_staging_buffer_pool.cpp',
 'src/video_core/renderer_vulkan/vk_semaphore.cpp',
 'src/video_core/buffer_cache/buffer.cpp',
 'src/video_core/renderer_vulkan/vk_scheduler.cpp',
 'src/video_core/renderer_vulkan/vk_scheduler.h',
)

class SourceReviewOnly(Exception): pass

def load(name,path):
    spec=importlib.util.spec_from_file_location(name,path)
    if spec is None or spec.loader is None:raise RuntimeError('MODULE_MISSING:'+name)
    mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod);return mod

def save(p,data):p.write_text(json.dumps(data,indent=2,ensure_ascii=False)+'\n')

def code_only(text):
    # Ignore comments/string contents in the conservative existing-barrier screen.
    return re.sub(r'//[^\n]*|/\*.*?\*/|"(?:\\.|[^"\\])*"', '', text, flags=re.S)

def helper_gate(source,evid):
    inventory={};text={};missing=[]
    for rel in REQUIRED_HELPERS+OPTIONAL_HELPERS:
        p=source/rel
        if not p.is_file():
            inventory[rel]={'status':'MISSING'}
            if rel in REQUIRED_HELPERS:missing.append(rel)
            continue
        if p.is_symlink() or not p.resolve().is_relative_to(source.resolve()) or p.stat().st_size>2*1024**2:
            raise RuntimeError('HELPER_SOURCE_PATH_OR_SIZE_REFUSED:'+rel)
        data=p.read_bytes();text[rel]=data.decode();dest=evid/'readback-helpers'/rel
        dest.parent.mkdir(parents=True,exist_ok=True);dest.write_bytes(data)
        inventory[rel]={'sha256':hashlib.sha256(data).hexdigest(),'bytes':len(data)}
    save(evid/'readback-helper-inventory.json',inventory)
    if missing:raise SourceReviewOnly('MISSING_HELPER_SOURCE_NO_PATCH:'+','.join(missing))
    matches=[]
    pattern=r'\beHostRead\b|\bVK_ACCESS(?:_2)?_HOST_READ_BIT(?:_KHR)?\b|\bVK_ACCESS(?:_2)?_MEMORY_READ_BIT(?:_KHR)?\b[^;]{0,200}\bVK_PIPELINE_STAGE(?:_2)?_HOST_BIT|PipelineStageFlagBits2?::eHost\b'
    for rel,s in text.items():
        if re.search(pattern,code_only(s)):
            matches.append(rel)
    if matches:
        save(evid/'helper-gate.json',{'result':'EXISTING_HOST_DEPENDENCY_REQUIRES_REVIEW','files':matches})
        raise SourceReviewOnly('EXISTING_HOST_DEPENDENCY_REQUIRES_REVIEW_NO_PATCH')
    runtime=code_only(text[REQUIRED_HELPERS[0]])
    copy=re.search(r'void\s+Runtime::CopyBuffer\s*\(',runtime)
    # Only the familiar same-scheduler copy/access-tracking implementation is admitted.
    next_method=runtime.find('\nvoid Runtime::',copy.end()) if copy else -1
    body=runtime[copy.start():next_method if next_method>=0 else len(runtime)] if copy else ''
    required=('scheduler.EndRendering()', 'scheduler.CommandBuffer()', '.copyBuffer(', 'AccessBuffer(dst',
              'vk::PipelineStageFlagBits2::eCopy','vk::AccessFlagBits2::eTransferWrite')
    compact=re.sub(r'\s+','',body)
    if not all(re.sub(r'\s+','',x) in compact for x in required):
        raise SourceReviewOnly('UNREVIEWED_COPY_HELPER_SHAPE_NO_PATCH')
    if not all(x in runtime for x in ('memory_barrier.dstStageMask','eAllCommands','memory_barrier.dstAccessMask','eMemoryRead','eMemoryWrite')):
        raise SourceReviewOnly('UNREVIEWED_RUNTIME_DEPENDENCY_SHAPE_NO_PATCH')
    if 'TileManager::TileImage' not in text['src/video_core/texture_cache/tile_manager.cpp']:
        raise SourceReviewOnly('UNREVIEWED_IMAGE_DOWNLOAD_HELPER_NO_PATCH')
    result={'result':'KNOWN_COPY_TRACKING_NO_EXPLICIT_HOST_READ_IN_CAPTURED_HELPERS',
            'scope':'Source screen, not Vulkan execution or proof of frozen-frame causality',
            'files':{p:v['sha256'] for p,v in inventory.items() if 'sha256' in v}}
    save(evid/'helper-gate.json',result);return result

def logged(u,args,path,label,timeout=None):
    u.emit('STAGE='+label+' LOG='+str(path));p=None;start=time.monotonic()
    try:
        with path.open('wb') as out,path.open('r',errors='replace') as reader:
            p=sp.Popen(args,stdout=out,stderr=sp.STDOUT,start_new_session=True)
            last=0
            with u.heartbeat(label):
                while p.poll() is None:
                    now=time.monotonic();lines=reader.read().splitlines()
                    if lines and now-last>=3:u.emit(label+' '+lines[-1][:250]);last=now
                    if timeout and now-start>timeout:raise RuntimeError(label+'_TIME_LIMIT')
                    time.sleep(.2)
            rc=p.returncode
        if rc:
            print('\n'.join(path.read_text(errors='replace').splitlines()[-45:]),flush=True)
            raise RuntimeError(label+'_EXIT_'+str(rc))
        u.emit(label+'=PASS')
    finally:u.stop_owned(p)

def manifest_from_history(logs,prior):
    expected={}
    # Overlay only later SHA-pinned after-images; old trials remain immutable.
    names=('got-c925-indexed-revalidation-20261028T133609110195Z',
           'got-c925-indexed-explicit-lod-20261028T153318468127Z',
           'got-c925-finite-raw-images-20261028T171004960983Z', ATOMIC)
    for name in names:
        folder=logs/name;entry=json.loads((folder/'trial.json').read_text())
        m=entry.get('source_manifest')
        if m is None:m=json.loads((folder/'source-manifest.json').read_text())
        if not isinstance(m,dict) or not m:raise RuntimeError('MISSING_RETAINED_SOURCE_MANIFEST:'+name)
        for rel,x in m.items():
            if not isinstance(x,dict) or not re.fullmatch('[0-9a-f]{64}',str(x.get('after',''))):
                raise RuntimeError('BAD_SOURCE_MANIFEST:'+name)
            expected[rel]=x['after']
    inventory=json.loads((logs/PREVIOUS/'source-inventory.json').read_text())
    for rel,entry in inventory.items():
        if 'sha256' in entry:expected[rel]=entry['sha256']
    if expected.get(RENDERER)!=RENDERER_SHA:raise RuntimeError('RESTORED_STENCIL_NOT_PROVEN')
    return expected

def verify(u,source,expected):
    for rel,sha in expected.items():
        p=source/rel
        if p.is_symlink() or not p.resolve().is_relative_to(source.resolve()) or u.sha(p)!=sha:
            raise RuntimeError('SOURCE_CHANGED:'+rel)

def private_profile(candidate,prior):
    profile=Path(prior['private_profile']).resolve()
    if profile.name!='user' or not profile.is_relative_to((candidate/'runs').resolve()) or not (profile/'config.json').is_file():
        raise RuntimeError('ISOLATED_PROFILE_MISSING_OR_OUTSIDE_CANDIDATE')
    if any(p.is_symlink() for p in profile.rglob('*')):raise RuntimeError('PROFILE_SYMLINK_REFUSED')
    for p in (profile/'config.json',profile/'custom_configs/CUSA11456.json'):
        if p.is_file():
            home=json.loads(p.read_text()).get('General',{}).get('home_dir','')
            if home and not Path(home).expanduser().resolve().is_relative_to(profile):
                raise RuntimeError('EXTERNAL_SAVE_ROOT_REFUSED')
    return profile

def pixel_signatures(u,evid):
    result={}
    for p in sorted(evid.glob('menu-*.png')):
        try:
            from PIL import Image
            with Image.open(p) as img:
                rgb=img.convert('RGB');result[p.name]={'rgb_sha256':hashlib.sha256(rgb.tobytes()).hexdigest(),'size':list(rgb.size)}
        except ImportError:
            try:result[p.name]={'imagemagick_pixel_signature':u.command(['identify','-format','%#',str(p)],timeout=8).decode().strip()}
            except Exception as exc:result[p.name]={'unverified':str(exc)}
        except Exception as exc:result[p.name]={'unverified':str(exc)}
    return result

def main(u=None,h=None,fix=None,project=None):
    here=Path(__file__).resolve().parent
    if u is None:
        for name,sha in (('resume_c925_revalidation.py',RUNTIME_SHA),('resume_finite_raw.py',HEADROOM_SHA)):
            if hashlib.sha256((here/name).read_bytes()).hexdigest()!=sha:raise RuntimeError('HELPER_HASH_MISMATCH:'+name)
        u=load('runtime',here/'resume_c925_revalidation.py');h=load('headroom',here/'resume_finite_raw.py')
    fix=fix or load('host_readback_patch',here/'apply_host_readback.py')
    project=project or Path.home()/'projects/shadps4';logs=project/'logs'
    stamp=dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    evid=logs/('got-c925-host-readback-'+stamp);evid.mkdir(parents=True)
    cand=project/'GhostOfTsushima/experiments'/BASE;source=cand/'source';binary=cand/'build/shadps4'
    result={'schema_version':2,'record_id':evid.name,'recorded_on':dt.date.today().isoformat(),
      'journal_scope':'UNIVERSAL_SHADPS4','context_games':['CUSA11456','CUSA34384'],'tested_games':[],
      'change':'Explicit staging device-to-host visibility without removing completion or invalidation',
      'build':'NOT_RUN','game':{'result':'NOT_RUN'},'graphics_fixed':'UNVERIFIED',
      'source_base':'c925523b49ef39434024b16b2b014d120471ee1a',
      'upstream_checked_not_integrated':'b6f0c58dba9ee75b93477703032ae4f1e0de8f1c',
      'main_promotion':False,'upstream_rebase':False,'decision':'ISOLATED_CORRECTNESS_CANDIDATE_NATIVE_PENDING'}
    u.emit('EVIDENCE_DIRECTORY='+str(evid));u.emit('STAGE=VERIFYING_NATIVE_BASELINE_AND_READBACK_HELPERS')
    locks=[];pending=[];originals={};old={};changed=False;built=False;backup=None;rc=1;api=u.Ollama(evid)
    try:
        for name in ('.got-pr95-scalar-bounds-build.lock','.got-indexed-revalidation.lock','.got-host-readback.lock'):
            lock=(logs/name).open('a+')
            try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
            except Exception:lock.close();raise
            locks.append(lock)
        if u.sha(binary)!=EXPECTED:raise RuntimeError('PR102_CANDIDATE_CHANGED_NO_PATCH')
        prior=json.loads((logs/PREVIOUS/'trial.json').read_text())
        if prior.get('candidate_after_sha256')!=EXPECTED:raise RuntimeError('NATIVE_BASELINE_EVIDENCE_CHANGED')
        expected=manifest_from_history(logs,prior);verify(u,source,expected)
        result['preserved_source_manifest']=expected
        if u.emulator_pids():raise RuntimeError('EMULATOR_ALREADY_RUNNING_NO_PATCH')
        for p in Path('/proc').glob('[0-9]*'):
            try:
                if b'ninja' in (p/'cmdline').read_bytes() and (p/'cwd').resolve()==cand/'build':raise RuntimeError('BUILD_ACTIVE_NO_PATCH')
            except OSError:pass
        old={p:(source/p).read_bytes() for p in fix.EXPECTED};fix.transform(old)
        result['helper_audit']=helper_gate(source,evid)
        profile=private_profile(cand,prior);result['private_profile']=str(profile)
        protect=json.loads((logs/PREVIOUS/'original-files-before.json').read_text())
        if not isinstance(protect,dict):raise RuntimeError('PROTECTED_FILE_MANIFEST_INVALID')
        protected=project/'GhostOfTsushima/experiments/c925-integrated-gpu-20261027-122701/build/shadps4'
        if not protected.is_file():raise RuntimeError('PROTECTED_BINARY_MISSING')
        originals={p:u.sha(p) if Path(p).is_file() else None for p in protect};originals[str(protected)]=u.sha(protected)
        save(evid/'original-files-before.json',originals)
        # Real Vulkan-Hpp declarations are checked BEFORE touching emulator sources.
        headers=source/'externals/vulkan-headers/include'
        if not (headers/'vulkan/vulkan.hpp').is_file():raise SourceReviewOnly('NATIVE_VULKAN_HEADERS_MISSING_NO_PATCH')
        fixture=evid/'barrier-real-header-fixture.cpp';fixture.write_text(fix.api_fixture(True))
        compilers=[]
        for cxx in ('g++','clang++'):
            if not shutil.which(cxx):continue
            tag=cxx.replace('+','p');exe=evid/('fixture-'+tag)
            logged(u,[cxx,'-std=c++20','-O2','-Wall','-Wextra','-Werror','-pedantic','-isystem',str(headers),str(fixture),'-o',str(exe)],evid/(tag+'-compile.log'),'REAL_VULKAN_HEADER_'+tag,120)
            (evid/(tag+'-run.log')).write_bytes(u.command([str(exe)],timeout=20));compilers.append(cxx)
            exe.unlink() # Only own disposable test executable, not emulator/caches.
        if not compilers:raise RuntimeError('NO_CXX_COMPILER_NO_PATCH')
        result['native_header_fixture_compilers']=compilers
        checkpoint=cand/'checkpoints'/('pr102-before-host-readback-'+stamp);checkpoint.mkdir(parents=True);backup=checkpoint/'shadps4'
        with u.heartbeat('CHECKPOINT'):
            u.command(['cp','--reflink=auto','--preserve=mode,timestamps',str(binary),str(backup)],timeout=120)
            if u.sha(backup)!=EXPECTED:raise RuntimeError('CHECKPOINT_VERIFY_FAILED')
        result['previous_candidate_checkpoint']=str(backup)
        verify(u,source,expected);changed=True;manifest=fix.apply(source,evid);result['source_manifest']=manifest
        logged(u,['cmake','--build',str(cand/'build'),'--target','shadps4','--parallel','5','--','-k','0'],evid/'build.log','INCREMENTAL_BUILD')
        expected_after=dict(expected);expected_after.update({p:v['after'] for p,v in manifest.items()})
        verify(u,source,expected_after)
        if not binary.is_file():raise RuntimeError('NATIVE_LINK_NO_EXECUTABLE')
        built=True;result['build']='PASS';result['candidate_sha256']=u.sha(binary)
        screen=os.environ.get('DISPLAY') or ':0'
        if not shutil.which('xdpyinfo') or not(shutil.which('import') or shutil.which('maim')):raise RuntimeError('DISPLAY_CAPTURE_TOOL_MISSING_NO_GAME')
        (evid/'display.log').write_bytes(u.command(['env','DISPLAY='+screen,'xdpyinfo'],timeout=8))
        if not EBOOT.is_file():raise RuntimeError('GAME_MISSING_NO_LAUNCH')
        if u.emulator_pids():raise RuntimeError('OTHER_EMULATOR_STARTED_NO_GAME')
        h.verified_headroom(u,api,evid,pending,result)
        if u.emulator_pids() or u.sha(binary)!=result['candidate_sha256']:raise RuntimeError('LAUNCH_PREFLIGHT_CHANGED')
        verify(u,source,expected_after)
        result['vram_immediately_before_launch_bytes']=u.free_vram()
        if result['vram_immediately_before_launch_bytes']<8*1024**3:raise RuntimeError('VRAM_DROPPED_BEFORE_LAUNCH')
        result['game']=u.run_game(binary,EBOOT,profile.parent,evid,screen);result['tested_games']=['CUSA11456']
        text=(evid/'game.log').read_text(errors='replace')
        keys=('HostReadbackBarrier: kind=buffer','HostReadbackBarrier: kind=image','StencilReferenceExactMerged',
              'Stencil test requires','IndexedExplicitLodBound','FiniteRawImageRead:','INDEXED_TABLE_REVALIDATE',
              'INDEXED_MATERIAL_BIND_REJECT','Sharp source was not flatenned','VK_ERROR_DEVICE_LOST','Device lost during submit')
        result['sample_limited_events']={s:text.count(s) for s in keys}
        result['pixel_signatures']=pixel_signatures(u,evid)
        result['status']='CHANGED_SOURCE_CAPTURE_COMPLETED_VISUAL_PENDING' if result['game']['time_limit_reached'] else 'EARLY_GAME_EXIT'
        rc=0 if result['game']['time_limit_reached'] or result['game']['exit_code']==0 else 1
    except SourceReviewOnly as exc:
        result['status']=str(exc);result['decision']='SOURCE_REVIEW_ONLY_NO_PATCH_NO_BUILD_NO_GAME';u.emit(result['status']);rc=0
    except (Exception,KeyboardInterrupt) as exc:
        result['status']=str(exc) or type(exc).__name__;u.emit('STOP='+result['status'])
        (evid/'failure.txt').write_text(traceback.format_exc());rc=130 if isinstance(exc,KeyboardInterrupt) else 1
    finally:
        if pending:
            try:
                result['ollama_restore']=u.restore_models(api,pending,evid)
                if any(x['result'] in ('RESTORE_FAILED','RESTORE_NOT_VERIFIED') for x in result['ollama_restore']):rc=1
            except Exception as exc:result['model_restore_error']=str(exc);rc=1
        if changed and not built:
            try:
                for p,data in old.items():(source/p).write_bytes(data)
                if backup and backup.is_file() and u.sha(backup)==EXPECTED:shutil.copy2(backup,binary)
                result['rollback']='SOURCE_AND_PR102_ELF_RESTORED' if u.sha(binary)==EXPECTED else 'BINARY_RESTORE_UNVERIFIED'
            except Exception as exc:result['rollback']='FAILED:'+str(exc);rc=1
        try:
            result['original_files_changed']=[p for p,sha in originals.items() if (u.sha(p) if Path(p).is_file() else None)!=sha]
            if result['original_files_changed']:rc=1
            if binary.is_file():result['candidate_after_sha256']=u.sha(binary)
        except Exception as exc:result['final_verification_error']=str(exc);rc=1
        result['runner_exit_code']=rc;save(evid/'trial.json',result)
        with tarfile.open(str(evid)+'.tar.gz','w:gz') as tf:
            for p in sorted(evid.rglob('*')):
                if p.is_file() and not p.name.startswith('fixture-'):tf.add(p,arcname=str(Path(evid.name)/p.relative_to(evid)))
        u.emit('RESULT='+result.get('status','UNKNOWN'));u.emit('EVIDENCE='+str(evid)+'.tar.gz')
        for lock in locks:lock.close()
    return rc

if __name__=='__main__':
    def interrupted(signum,frame):raise KeyboardInterrupt('TERMINATED_BY_USER')
    signal.signal(signal.SIGTERM,interrupted)
    sys.exit(main())
