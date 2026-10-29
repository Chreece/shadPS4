#!/usr/bin/env python3
"""Recheck a real WSI diagnostic with upstream-fixed validation, not GPU workarounds.
Builds only an isolated Vulkan validation layer. Never edits emulator source or drivers.
"""
from __future__ import annotations
import contextlib
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

VVL_REPO = 'https://github.com/KhronosGroup/Vulkan-ValidationLayers.git'
VVL_COMMIT = 'da7669ad94110c7e7e89ab36defabff4baa507c0'
KNOWN_GOOD_BLOB = '51edbe55656c3fe285da9c85279bfbef4450ee91'
EXPECTED_ELF = '89ffa07ab49d0df36b3a4c4295e0cff8f42bb7e167cbced3d5879b1e5c2f5aec'
PRIOR = 'got-c925-sparse-host-limit-20261029T075648223982Z'
BASE = 'c925-pr95-scalar-bounds-20261028T112756671630889Z'
LAYER_NAME = 'VK_LAYER_KHRONOS_validation'
PINS = {
 'menu_validation_runtime.py': '7467dbc881babc9d36d53ff6f169bec112e7f97918a827cc17a0bfde47740f50',
 'resume_c925_revalidation.py': 'df7617549006c8733049a684f22f6ac2bf6b96701bf2fe1da5ee9265df568250',
 'resume_finite_raw.py': 'c6017d0d96e83341315e6a05dafb3fb5478239b384b41e510c475b6d89884a0b',
}
PROBE_BLOB = '48f79c95894718d23cb90becc45d5e3dc50d6387'
# Previously captured complete source. Capture the host's current bytes and refuse
# changed semantics rather than assume this static audit covers a different tree.
WSI_SOURCE = {
 'src/video_core/renderer_vulkan/vk_presenter.cpp': 'bb5a5e2942430f2350c4851a1dddbc2884037b0037e77004703a30c40982fc4b',
 'src/video_core/renderer_vulkan/vk_swapchain.cpp': '53533c8304f570bdb6a85611cee550baec036f6494e2381775906d28d63010ff',
 'src/video_core/renderer_vulkan/vk_swapchain.h': 'cabb0538a9c618daa3b9317976cd5b88f1a657514d0b3b5e3c21f775a35b7980',
 'src/video_core/renderer_vulkan/vk_scheduler.cpp': 'c3658281c244774e253d23661dba143db97af53051c97ea7a8912fb7c8ca7e3b',
 'src/video_core/renderer_vulkan/vk_scheduler.h': '9cd4966e4ddf110c33c0ef6bf67e1f2c416bd4ae85840ac761adf91a4ed49279',
}


def require(ok, text):
    if not ok: raise RuntimeError(text)


def sha(p):
    with Path(p).open('rb') as f: return hashlib.file_digest(f, 'sha256').hexdigest()


def blob(data):
    return hashlib.sha1(b'blob '+str(len(data)).encode()+b'\0'+data).hexdigest()


def save(path, data):
    Path(path).write_text(json.dumps(data, indent=2, ensure_ascii=False)+'\n')


def emit(text):
    print(time.strftime('%H:%M:%S')+' '+text, flush=True)


def stop(proc):
    if proc is None or proc.poll() is not None: return
    try:
        os.killpg(proc.pid, signal.SIGTERM)
        try: proc.wait(timeout=2)
        except sp.TimeoutExpired:
            os.killpg(proc.pid, signal.SIGKILL); proc.wait(timeout=5)
    except ProcessLookupError: pass


def command(args, log, *, env=None, cwd=None, timeout=1800, beat=15):
    """The parent stays attached; terminate only this command's new process group."""
    emit('STAGE='+log.stem+' LOG='+str(log)); proc=None; begin=time.monotonic(); last=begin; shown=begin
    try:
        with log.open('wb') as out, log.open('r',errors='replace') as rd:
            proc=sp.Popen([str(x) for x in args],stdout=out,stderr=sp.STDOUT,
                          stdin=sp.DEVNULL,cwd=cwd,env=env,start_new_session=True)
            while proc.poll() is None:
                now=time.monotonic(); lines=rd.read().splitlines()
                if lines and now-shown>=3:
                    emit(lines[-1][:300]); shown=now
                if now-last>=beat:
                    emit(f'{log.stem}_ACTIVE elapsed={now-begin:.0f}s pid={proc.pid} bytes={log.stat().st_size}'); last=now
                if now-begin>timeout: raise RuntimeError(log.stem+'_TIMEOUT')
                time.sleep(.1)
            rc=proc.returncode
        if rc:
            print('\n'.join(log.read_text(errors='replace').splitlines()[-30:]),flush=True)
            raise RuntimeError(log.stem+'_EXIT_'+str(rc))
        emit(log.stem+'=PASS')
        return log.read_text(errors='replace')
    finally: stop(proc)


def git_read(root, *args):
    return sp.check_output(['git','-c','core.hooksPath=/dev/null','-C',str(root),*args],
                           stderr=sp.STDOUT,text=True,timeout=30).strip()


def fixed_layer_environment(folder, base=None):
    env=dict(os.environ if base is None else base)
    # Local to the checking process and its game child. Never change ICD/driver paths.
    for key in list(env):
        if key.startswith('VK_LAYER_') or key.startswith('VK_ADD_LAYER_') or key.startswith('VK_KHRONOS_VALIDATION_'):
            env.pop(key,None)
    env.pop('VK_INSTANCE_LAYERS',None)
    env['VK_LAYER_PATH']=str(folder)
    env['VK_LOADER_DEBUG']='error,warn,layer'
    return env


def tool_manifest(raw, library):
    obj=json.loads(raw)
    require(obj.get('layer',{}).get('name')==LAYER_NAME,'WRONG_LAYER_MANIFEST')
    obj['layer']['library_path']=str(library.resolve())
    return obj


def verify_source(source, expected, evidence=None):
    changed=[]
    for rel,want in expected.items():
        path=source/rel
        require(not path.is_symlink() and path.is_file() and path.resolve().is_relative_to(source.resolve()),
                'SOURCE_PATH_UNSAFE:'+rel)
        if evidence is not None:
            copy=evidence/rel;copy.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(path,copy)
        if sha(path)!=want: changed.append(rel)
    require(not changed,'SOURCE_CHANGED_NO_GAME:'+','.join(changed))


def build_layer(project, evid):
    tool=project/'scripts'/'validation-tools'/('vvl-'+VVL_COMMIT)
    require(not tool.is_symlink(),'PRIVATE_TOOL_PATH_SYMLINK')
    tool.mkdir(parents=True,exist_ok=True)
    src=tool/'source';build=tool/'build';deps=tool/'dependencies';manifest_dir=tool/'layer'
    env=dict(os.environ,CMAKE_BUILD_PARALLEL_LEVEL='3',GIT_TERMINAL_PROMPT='0')
    if not src.exists():
        command(['git','-c','core.hooksPath=/dev/null','init',src],evid/'vvl-git-init.log',env=env,timeout=30)
        command(['git','-C',src,'remote','add','origin',VVL_REPO],evid/'vvl-git-origin.log',env=env,timeout=30)
    require(not src.is_symlink() and (src/'.git').is_dir() and not (src/'.git').is_symlink(),'VVL_SOURCE_SYMLINK_OR_NOT_GIT')
    require(git_read(src,'remote','get-url','origin')==VVL_REPO,'VVL_ORIGIN_CHANGED')
    try: head=git_read(src,'rev-parse','--verify','HEAD')
    except sp.CalledProcessError:
        require(not git_read(src,'status','--porcelain'),'INCOMPLETE_VVL_CHECKOUT_HAS_USER_FILES')
        command(['git','-c','core.hooksPath=/dev/null','-C',src,'fetch','--depth','1','origin',VVL_COMMIT],
                evid/'vvl-git-fetch.log',env=env,timeout=240)
        command(['git','-c','core.hooksPath=/dev/null','-C',src,'checkout','--detach',VVL_COMMIT],
                evid/'vvl-git-checkout.log',env=env,timeout=60)
    require(git_read(src,'rev-parse','HEAD')==VVL_COMMIT,'VVL_PIN_CHANGED')
    require(not git_read(src,'status','--porcelain','--untracked-files=no'),'VVL_TRACKED_SOURCE_CHANGED')
    kg=src/'scripts/known_good.json'
    require(blob(kg.read_bytes())==KNOWN_GOOD_BLOB,'VVL_DEPENDENCY_LIST_CHANGED')
    sync=src/'layers/sync/sync_submit.cpp'
    require('last_synchronized_present.Merge(signal_info.batch->last_synchronized_present);' in sync.read_text(),
            'UPSTREAM_PRESENT_TRACKING_CORRECTION_MISSING')
    record=tool/'built-layer.json'
    if record.exists():
        meta=json.loads(record.read_text());library=Path(meta['library'])
        require(meta.get('commit')==VVL_COMMIT and library.resolve().is_relative_to(build.resolve()) and
                library.is_file() and not library.is_symlink() and sha(library)==meta['library_sha256'],
                'PRIVATE_LAYER_CHANGED_REFUSED')
        original_manifest=Path(meta['build_manifest'])
        require(original_manifest.resolve().is_relative_to(build.resolve()) and original_manifest.is_file() and
                sha(original_manifest)==meta['build_manifest_sha256'],'LAYER_MANIFEST_CHANGED')
        emit('PRIVATE_CORRECTED_LAYER=REUSED_HASH_VERIFIED')
    else:
        require(shutil.disk_usage(tool).free>=6*1024**3,'NEED_6GIB_PRIVATE_TOOL_BUILD_SPACE')
        # Official CMake dependency handling installs only beneath dependencies/.
        # No sudo, apt, system install target, loader replacement or driver change.
        command(['cmake','-S',src,'-B',build,'-G','Ninja','-DCMAKE_BUILD_TYPE=Release',
                 '-DBUILD_TESTS=OFF','-DBUILD_WERROR=OFF','-DUPDATE_DEPS=ON',
                 '-DUPDATE_DEPS_DIR='+str(deps),'-DCMAKE_INSTALL_PREFIX='+str(tool/'install'),
                 '-DBUILD_WSI_XLIB_SUPPORT=ON','-DBUILD_WSI_XLIB_XRANDR_SUPPORT=OFF',
                 '-DBUILD_WSI_XCB_SUPPORT=OFF','-DBUILD_WSI_WAYLAND_SUPPORT=OFF'],
                evid/'vvl-configure-and-dependencies.log',env=env,timeout=2700)
        command(['cmake','--build',build,'--target','vvl','--parallel','3'],
                evid/'vvl-build.log',env=env,timeout=2700)
        libraries=list((build/'layers').glob('libVkLayer_khronos_validation.so'))
        require(len(libraries)==1 and not libraries[0].is_symlink(),'BUILT_LAYER_NOT_UNIQUE')
        library=libraries[0]
        manifests=list((build/'layers').glob('VkLayer_khronos_validation.json'))
        require(len(manifests)==1 and not manifests[0].is_symlink(),'BUILT_LAYER_MANIFEST_MISSING')
        original_manifest=manifests[0]
        dependencies={}
        for item in json.loads(kg.read_text())['repos']:
            d=deps/item['sub_dir']
            if (d/'.git').exists():
                require(not git_read(d,'status','--porcelain','--untracked-files=no'),
                        'DEPENDENCY_TRACKED_FILES_CHANGED:'+item['name'])
                declared=item.get('commit')
                head=git_read(d,'rev-parse','HEAD')
                require(declared and git_read(d,'rev-parse',declared+'^{commit}')==head,
                        'DEPENDENCY_REVISION_CHANGED:'+item['name'])
                dependencies[item['name']]={'declared':declared,'resolved_commit':head}
        meta={'commit':VVL_COMMIT,'library':str(library),'library_sha256':sha(library),
              'build_manifest':str(original_manifest),'build_manifest_sha256':sha(original_manifest),
              'known_good_git_blob':KNOWN_GOOD_BLOB,'dependencies':dependencies,
              'synchronization_fix_source_sha256':sha(sync),'system_install':False}
        save(record,meta)
    require(not git_read(src,'status','--porcelain','--untracked-files=no'),'VVL_CHANGED_AFTER_BUILD')
    manifest_dir.mkdir(exist_ok=True)
    save(manifest_dir/'VkLayer_khronos_validation.json',tool_manifest(original_manifest.read_text(),library))
    save(evid/'corrected-layer-provenance.json',meta)
    return library,manifest_dir,meta


@contextlib.contextmanager
def child_environment(env):
    before=os.environ.copy()
    os.environ.clear();os.environ.update(env)
    try:yield
    finally:os.environ.clear();os.environ.update(before)


def parse_probe(text):
    # Loader diagnostics may be written after the JSON on stderr. Locate the
    # probe's complete JSON object rather than assuming the final line is JSON.
    result=None
    for line in reversed(text.splitlines()):
        try: value=json.loads(line)
        except (ValueError,TypeError): continue
        if isinstance(value,dict) and 'layer' in value:
            result=value;break
    require(result is not None,'LAYER_PROBE_JSON_MISSING')
    require(result.get('layer')==LAYER_NAME and result.get('instance_create')=='PASS', 'LAYER_PROBE_FAILED')
    require(result.get('spec_version',0)>=(1<<22)+(4<<12)+363,'OLD_LAYER_STILL_LOADED')
    return result


def load(name,path):
    spec=importlib.util.spec_from_file_location(name,path)
    require(spec is not None and spec.loader is not None,'HELPER_LOAD_FAILED')
    mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod);return mod


def main(project=None, here=None):
    os.umask(0o077);here=Path(here) if here is not None else Path(__file__).resolve().parent
    for name,want in PINS.items():require(sha(here/name)==want,'HELPER_HASH_MISMATCH:'+name)
    probe=here/'validation_layer_probe.py';require(blob(probe.read_bytes())==PROBE_BLOB,'PROBE_BLOB_MISMATCH')
    u=load('runtime',here/'resume_c925_revalidation.py')
    h=load('headroom',here/'resume_finite_raw.py')
    menu=load('menu',here/'menu_validation_runtime.py')
    project=Path(project) if project is not None else Path.home()/'projects/shadps4';logs=project/'logs'
    stamp=dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    evid=logs/('got-c925-corrected-layer-'+stamp);evid.mkdir(parents=True)
    cand=project/'GhostOfTsushima/experiments'/BASE;source=cand/'source';binary=cand/'build/shadps4'
    result={'schema_version':2,'record_id':evid.name,'journal_scope':'UNIVERSAL_SHADPS4',
            'context_games':['CUSA11456','CUSA34384'],'tested_games':[],
            'emulator_build':'NOT_RUN','emulator_source_changed':False,
            'prior_trial':PRIOR,'expected_elf':EXPECTED_ELF,'game':{'result':'NOT_RUN'},
            'new_graphics_fix_claimed':False,'diagnostic_only':True,
            'upstream_layer_fix':VVL_COMMIT,'no_identical_menu_freeze_inference':True}
    emit('EVIDENCE_DIRECTORY='+str(evid));emit('EMULATOR_REBUILD=NO; PRIVATE_VALIDATION_LAYER_BUILD_ONLY')
    locks=[];pending=[];originals={};retained={};api=u.Ollama(evid);rc=1
    try:
        for name in ('.got-pr95-scalar-bounds-build.lock','.got-indexed-revalidation.lock',
                     '.got-sparse-host-limit.lock','.got-corrected-vvl.lock','vulkan-runtime-validation.lock'):
            lock=(logs/name).open('a+')
            try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
            except BaseException:lock.close();raise
            locks.append(lock)
        previous=logs/PRIOR
        prior=json.loads((previous/'trial.json').read_text())
        require(prior.get('build')=='PASS' and prior.get('candidate_sha256')==EXPECTED_ELF,'NATIVE_RECORD_CHANGED')
        require(not binary.is_symlink() and sha(binary)==EXPECTED_ELF,'NATIVE_BINARY_CHANGED_NO_GAME')
        retained=json.loads((previous/'retained-source-sha256.json').read_text())
        retained.update({p:d['after'] for p,d in prior['source_manifest'].items()})
        verify_source(source,retained)
        verify_source(source,WSI_SOURCE,evid/'audited-presentation-source')
        require(not u.emulator_pids(),'ANOTHER_EMULATOR_RUNNING')
        for p in Path('/proc').glob('[0-9]*'):
            try:
                if b'ninja' in (p/'cmdline').read_bytes() and (p/'cwd').resolve()==cand/'build':
                    raise RuntimeError('EMULATOR_BUILD_ACTIVE')
            except OSError:pass
        protection=json.loads((previous/'original-files-before.json').read_text())
        originals={p:sha(p) if Path(p).is_file() else None for p in protection}
        save(evid/'original-files-before.json',originals)
        for name in ('git','cmake','ninja','g++','pkg-config','xdpyinfo'):
            require(shutil.which(name),'REQUIRED_TOOL_MISSING_NO_INSTALL:'+name)
        require(shutil.which('import') or shutil.which('maim'),'SCREEN_CAPTURE_TOOL_MISSING')
        command(['pkg-config','--exists','x11'],evid/'xlib-development-check.log',timeout=10)
        # A pinned fresh tool avoids guessing a game barrier or filtering the error.
        library,folder,meta=build_layer(project,evid)
        env=fixed_layer_environment(folder)
        probe_text=command([sys.executable,probe,'--probe'],evid/'corrected-layer-probe.log',env=env,timeout=30)
        result['layer_probe']=parse_probe(probe_text)
        result['layer_library_sha256']=meta['library_sha256']
        # Capture loader's actual selected library path; a version alone cannot
        # distinguish the fixed commit from the earlier SDK with the same version.
        require(str(library.resolve()) in probe_text,'LOADER_DID_NOT_CONFIRM_PINNED_LIBRARY_PATH')
        old=Path(prior['private_profile']).resolve()
        require(old.is_relative_to((cand/'runs').resolve()) and old.name=='user','PROFILE_OUTSIDE_CANDIDATE')
        private=cand/'runs'/('corrected-layer-'+stamp)/'user'
        with u.heartbeat('COPYING_PRIVATE_PROFILE'):
            result['profile_copy']=menu.copy_profile(old,private)
        result['private_profile']=str(private)
        screen=os.environ.get('DISPLAY') or ':0'
        command(['env','DISPLAY='+screen,'xdpyinfo'],evid/'display.log',timeout=8)
        eboot=Path('/mnt/roms-all/ps4-games/CUSA11456/eboot.bin');require(eboot.is_file(),'GAME_FILE_MISSING')
        require(not u.emulator_pids(),'OTHER_EMULATOR_RUNNING_BEFORE_LAUNCH')
        h.verified_headroom(u,api,evid,pending,result)
        require(sha(binary)==EXPECTED_ELF,'NATIVE_BINARY_CHANGED_BEFORE_LAUNCH')
        verify_source(source,retained);verify_source(source,WSI_SOURCE)
        require(not u.emulator_pids() and u.free_vram()>=h.MIN_FREE,'LAUNCH_PREFLIGHT_CHANGED')
        require(sha(library)==meta['library_sha256'],'VALIDATION_LAYER_CHANGED_BEFORE_LAUNCH')
        with child_environment(env):
            result['game']=menu.game(u,binary,eboot,private,evid,screen)
        result['tested_games']=['CUSA11456']
        log=(evid/'game.log').read_text(errors='replace')
        result['fixed_layer_path_in_game_log']=str(library.resolve()) in log
        result['core_sync_confirmed']=all(s in log for s in (
            'Enabled instance layers: VK_LAYER_KHRONOS_validation','Vulkan vkValidationCore: true','Vulkan vkValidationSync: true'))
        layer=(evid/'validation-layer.log').read_text(errors='replace') if (evid/'validation-layer.log').is_file() else ''
        result['validation_ids']=sorted(set(re.findall(r'VUID-[A-Za-z0-9_-]+|SYNC-HAZARD-[A-Z_-]+',log+'\n'+layer)))
        result['no_suppressed_error_ids']=True
        result['sample_limited_events']={s:log.count(s) for s in ('SparseArenaLimits:','SparseArenaWindow:',
             'UniformRawSampleBound','IndirectRawSample:','INDEXED_MATERIAL_BIND_REJECT','StencilReferenceExactMerged',
             'VK_ERROR_DEVICE_LOST','Device lost during submit')}
        result['status']='FIRST_ERROR_CAPTURED_WITH_CORRECTED_LAYER' if result['game']['first_validation_error'] else 'CAPTURE_COMPLETE_REVIEW_MENU_MATERIALS'
        require(result['fixed_layer_path_in_game_log'] and result['core_sync_confirmed'],'RUNTIME_LAYER_OR_CORE_SYNC_NOT_CONFIRMED')
        rc=0 if result['game']['first_validation_error'] or result['game']['time_limit_reached'] or result['game']['exit_code']==0 else 1
    except (Exception,KeyboardInterrupt) as exc:
        result['status']=str(exc) or type(exc).__name__;emit('STOP='+result['status'])
        (evid/'failure.txt').write_text(traceback.format_exc());rc=130 if isinstance(exc,KeyboardInterrupt) else 1
    finally:
        if pending:
            try:
                result['ollama_restore']=u.restore_models(api,pending,evid)
                if any(x['result'] in ('RESTORE_FAILED','RESTORE_NOT_VERIFIED') for x in result['ollama_restore']):rc=1
            except Exception as exc:result['model_restore_error']=str(exc);rc=1
        try:
            result['original_files_changed']=[p for p,s in originals.items() if (sha(p) if Path(p).is_file() else None)!=s]
            result['candidate_after_sha256']=sha(binary)
            result['retained_source_changed']=[p for p,s in retained.items() if not (source/p).is_file() or sha(source/p)!=s]
            if result['original_files_changed'] or result['retained_source_changed'] or result['candidate_after_sha256']!=EXPECTED_ELF:rc=1
        except Exception as exc:result['preservation_check_error']=str(exc);rc=1
        result['runner_exit_code']=rc;save(evid/'trial.json',result)
        with tarfile.open(str(evid)+'.tar.gz','w:gz') as t:t.add(evid,arcname=evid.name)
        emit('RESULT='+result.get('status','UNKNOWN'));emit('EVIDENCE='+str(evid)+'.tar.gz')
        for lock in locks:lock.close()
    return rc


if __name__=='__main__':
    def terminated(signum,frame):raise KeyboardInterrupt('TERMINATED_BY_USER')
    signal.signal(signal.SIGTERM,terminated)
    sys.exit(main())
