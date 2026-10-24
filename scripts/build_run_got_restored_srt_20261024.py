#!/usr/bin/env python3
"""Rebuild GoT from the verified no-DynamoRIO ABI candidate and restored SRT fixes.

Change exactly 3 shader/SRT files plus the cache-version file. The old native
candidate, source checkout, save profiles, and ES-DE launchers remain unchanged.
There are no game/shader selectors, forced guest values or dropped draws.
"""
from __future__ import annotations
import argparse,fcntl,hashlib,importlib.util,json,os,re,shutil,stat,subprocess,sys,tarfile,tempfile,time,traceback
from pathlib import Path

ROOT=Path.home()/'projects/shadps4'
SRC=ROOT/'GhostOfTsushima/source'
WORK=ROOT/'GhostOfTsushima/fresh-source/ca69b6509427-3967624'
BUILD=ROOT/'GhostOfTsushima/fresh-builds/ca69b6509427-3967624'
OLD_REV='b6a1c4fea7f91b54f4284d5555759335b6e88245'
OLD_TREE='215275aacdca5fa08f7a558929737a08f9c675e8'
OLD_SHA='27fe1c812cc86442432a8dccf821b5de100cfa7aa5d04d216f414c92a418d2d7'
OLD_CANDIDATE=ROOT/'GhostOfTsushima/candidates'/(OLD_REV+'-'+OLD_SHA)/'shadps4'
OLD_INFO=ROOT/'logs/got-native-hle-abi-20261024-20261024-143707-nch9eb0x/info'
COMPOSER=ROOT/'scripts/build_run_generic_large_indexed_resources_20261023.py'
COMPOSER_BLOB='4d4db01296af760928806720b57ffcf17a510537'
NATIVE=ROOT/'scripts/build_run_got_upstream_native_20261024.py'
NATIVE_BLOB='96c6b84bde950513c237a9daec6177999fb3ef92'
PORT=ROOT/'scripts/restore_proven_srt_resources_20261024.py'
PORT_BLOB='__PORT_BLOB__'
CACHE='src/video_core/renderer_vulkan/vk_pipeline_serialization.cpp'

def blob(path):
    b=path.read_bytes()
    return hashlib.sha1(b'blob '+str(len(b)).encode()+b'\0'+b).hexdigest()

def digest(path):
    if not path.is_file() or path.is_symlink():
        raise RuntimeError('REGULAR_CANDIDATE_REQUIRED='+str(path))
    with path.open('rb') as f:return hashlib.file_digest(f,'sha256').hexdigest()

def load(path,hash_,name):
    if not path.is_file() or blob(path)!=hash_:
        raise RuntimeError('PINNED_MODULE_HASH_MISMATCH='+str(path))
    spec=importlib.util.spec_from_file_location(name,path)
    if spec is None or spec.loader is None:raise RuntimeError('MODULE_LOAD_FAILED='+name)
    m=importlib.util.module_from_spec(spec)
    sys.modules[spec.name]=m;spec.loader.exec_module(m)
    return m

def load_verified():
    composer=load(COMPOSER,COMPOSER_BLOB,'got_srt_composer')
    native=load(NATIVE,NATIVE_BLOB,'got_srt_native_runtime')
    port=load(PORT,PORT_BLOB,'got_srt_port')
    if not SRC.is_dir() or not (SRC/'.git').exists():
        raise RuntimeError('ORIGINAL_GOT_SOURCE_MISSING')
    composer.BASE=composer.git('rev-parse','HEAD').decode().strip()
    composer.BASE_STATUS=composer.source_status()
    composer.validate_source_status(composer.BASE_STATUS)
    report=json.loads((OLD_INFO/'build-result.json').read_text())
    checks={'status':'NATIVE_ABI_GENERIC_BUILD_PASS','revision':OLD_REV,
            'tree':OLD_TREE,'candidate_sha256':OLD_SHA,
            'dynamorio':False,'generic_hle_alignment':True,
            'source_untouched':True,'gameplay_verified':False}
    for key,expected in checks.items():
        if report.get(key)!=expected:raise RuntimeError('OLD_BUILD_DRIFT='+key)
    if Path(report['candidate'])!=OLD_CANDIDATE or digest(OLD_CANDIDATE)!=OLD_SHA:
        raise RuntimeError('LAST_NATIVE_BINARY_CHANGED')
    if composer.git('rev-parse',OLD_REV+'^{tree}').decode().strip()!=OLD_TREE:
        raise RuntimeError('LAST_NATIVE_SOURCE_TREE_CHANGED')
    if any(composer.exists(OLD_REV+':'+p) for p in (
        'cmake/CpuIdTranslation.cmake','src/core/cpu_id_translation/launcher.cpp')):
        raise RuntimeError('DYNAMORIO_FOUND_IN_LAST_NATIVE_COMMIT')
    return composer,native,port,report

def bump_cache(text):
    output=text
    for key in ('ShaderBinaryVersion','ShaderMetaVersion'):
        pat=r'(?m)(static constexpr u32 '+key+r'\s*=\s*)(0x[0-9A-Fa-f]+|\d+)(u?;)'
        found=re.search(pat,output)
        if not found or len(re.findall(pat,output))!=1:
            raise RuntimeError('CACHE_VERSION_SOURCE_CHANGED='+key)
        value=int(found.group(2),0)
        output=re.sub(pat,lambda m:m.group(1)+hex(value+1)+m.group(3),
                      output,count=1)
        updated=re.search(pat,output)
        if not updated or int(updated.group(2),0)!=value+1:
            raise RuntimeError('CACHE_EPOCH_BUMP_FAILED='+key)
    return output

def compose(composer,port,run):
    index=run/'shader.index'
    e=composer.env({'GIT_INDEX_FILE':str(index)})
    composer.git('read-tree',OLD_REV,e=e)
    paths=[]
    for path in port.FILES:
        previous=composer.index_text(index,path)
        updated=port.transform(path,previous)
        if updated==previous:
            raise RuntimeError('RESTORED_SOURCE_WAS_ALREADY_PRESENT='+path)
        composer.update_index_text(index,path,updated)
        paths.append(path)
    port.verify(lambda p:composer.index_text(index,p))
    previous=composer.index_text(index,CACHE)
    updated=bump_cache(previous)
    composer.update_index_text(index,CACHE,updated)
    paths.append(CACHE)
    tree=composer.git('write-tree',e=e).decode().strip()
    rev=composer.commit_tree(tree,OLD_REV,
         b'Restore proven uniform SRT lane lowering and mandatory GPU guest-read fallback\n')
    changed=composer.git('diff','--name-only',OLD_REV,rev).decode().splitlines()
    if sorted(changed)!=sorted(paths):
        raise RuntimeError('UNREVIEWED_SRT_SOURCE_CHANGES='+str(changed))
    if composer.git('rev-parse',rev+'^').decode().strip()!=OLD_REV:
        raise RuntimeError('NATIVE_ABI_PARENT_LOST')
    if any(composer.exists(rev+':'+p) for p in (
            'cmake/CpuIdTranslation.cmake','src/core/cpu_id_translation/client.c')):
        raise RuntimeError('DYNAMORIO_REINTRODUCED')
    patch=composer.git('diff','--binary',OLD_REV,rev,'--',*paths)
    (run/'info/restored-srt.patch').write_bytes(patch)
    metadata={'old_revision':OLD_REV,'new_revision':rev,'new_tree':tree,
              'paths_changed':changed,'preexisting_generic_gpu_fixes_preserved':True,
              'native_abi_fix_preserved':True,'dynamorio':False,
              'source_restored':['host-uniform-lane-read',
                                 'required-unflattened-guest-address-read',
                                 'mandatory-DMA-binding',
                                 'shader-cache-epoch-bump'],
              'game_specific_overrides':False}
    (run/'info/source-recovery.json').write_text(json.dumps(metadata,indent=2)+'\n')
    return rev,tree

def checkout(composer,revision):
    if WORK.is_symlink():raise RuntimeError('WORKSPACE_IS_SYMLINK')
    if not WORK.exists():
        tmp='refs/shadps4-composer/restore-'+OLD_REV[:12]
        composer.git('update-ref',tmp,OLD_REV)
        try:
            composer.add_fresh_source(WORK,OLD_REV,tmp)
            composer.submodules_at(WORK)
        finally:composer.git('update-ref','-d',tmp,check=False)
    if not (WORK/'.git').exists():raise RuntimeError('WORKSPACE_NOT_GIT')
    get=lambda *args:subprocess.check_output(['git','-C',str(WORK),*args]).decode().strip()
    previous=get('rev-parse','HEAD')
    if previous not in (OLD_REV,revision):
        raise RuntimeError('WORKSPACE_HEAD_UNEXPECTED='+previous)
    if get('diff','--name-only','HEAD','--') or get('diff','--cached','--name-only'):
        raise RuntimeError('WORKSPACE_HAS_LOCAL_EDITS')
    if previous!=revision:
        ref='refs/shadps4-composer/restored-srt-'+revision[:12]
        composer.git('update-ref',ref,revision)
        try:
            proc=composer.run(['git','-C',WORK,'fetch','--no-tags',
              '--recurse-submodules=no',str(SRC),
              ref+':refs/remotes/composer/restored-srt'],check=False,timeout=300)
            if proc.returncode:
                raise RuntimeError('RESTORED_SRT_COMMIT_FETCH_FAILED='+
                    (proc.stderr or b'').decode(errors='replace')[-1400:])
            subprocess.run(['git','-C',str(WORK),'checkout','--detach',revision],
                           check=True,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
        finally:composer.git('update-ref','-d',ref,check=False)
    if get('rev-parse','HEAD')!=revision:
        raise RuntimeError('WORKSPACE_CHECKOUT_FAILED')
    if (WORK/'src/core/cpu_id_translation').exists():
        raise RuntimeError('DYNAMORIO_FOLDER_STILL_PRESENT')
    if not (WORK/'externals/sirit').is_dir():
        composer.submodules_at(WORK)

def build(composer,port,rev,tree,run):
    composer.stage('configure_restored_native_srt_without_dynamorio')
    composer.tee(['cmake','--fresh','-S',WORK,'-B',BUILD,'-G','Ninja',
          '-DCMAKE_BUILD_TYPE=Release','-DENABLE_TESTS=OFF',
          '-DCMAKE_INTERPROCEDURAL_OPTIMIZATION_RELEASE=ON'],
          run/'configure.log')
    ninja=(BUILD/'build.ninja').read_text(errors='replace')
    for marker in ('ENABLE_CPU_ID_TRANSLATION','shadps4_cpu_id_bundle',
                   'core/cpu_id_translation/launcher.cpp'):
        if marker in ninja:raise RuntimeError('DYNAMORIO_TARGET_RETURNED='+marker)
    composer.stage('compile_restored_srt_generic_native_candidate')
    composer.tee(['nice','-n','10','cmake','--build',BUILD,
                  '--target','shadps4','--parallel','2'],run/'full-build.log')
    exe=composer.exe(BUILD)
    if not exe:raise RuntimeError('RESTORED_SRT_EXECUTABLE_MISSING')
    sha=digest(exe)
    if sha==OLD_SHA:raise RuntimeError('RESTORED_EMULATOR_MATCHES_BROKEN_BINARY')
    cand=ROOT/'GhostOfTsushima/candidates'/(rev+'-'+sha)/'shadps4'
    cand.parent.mkdir(parents=True,exist_ok=True)
    shutil.copy2(exe,cand);cand.chmod(cand.stat().st_mode|stat.S_IXUSR)
    if digest(cand)!=sha:raise RuntimeError('CANDIDATE_COPY_HASH_FAILED')
    if (cand.parent/'cpu-id-runtime').exists():
        raise RuntimeError('DYNAMORIO_BUNDLE_IN_NATIVE_CANDIDATE')
    composer.assert_source_unchanged()
    result={'status':'RESTORED_PROVEN_GENERIC_SRT_BUILD_PASS','revision':rev,'tree':tree,
            'parent':OLD_REV,'candidate':str(cand),'candidate_sha256':sha,
            'full_build':'PASS','dynamorio':False,'es_de_changed':False,
            'source_unchanged':True,'rendering_verified':False,
            'shader_fallback_restored':True,'uniform_lane_restored':True}
    (run/'info/build-result.json').write_text(json.dumps(result,indent=2)+'\n')
    print('RESTORED_NATIVE_SRT_BUILD=PASS',flush=True)
    print('CANDIDATE='+str(cand),flush=True)
    print('CANDIDATE_SHA256='+sha,flush=True)
    return cand

def archive(run):
    for name in ('configure.log','full-build.log','game-console.log'):
        p=run/name
        if not p.is_file():continue
        with p.open('rb') as f:
            (run/(name+'.head')).write_bytes(f.read(512*1024))
            f.seek(max(0,p.stat().st_size-8*1024*1024))
            (run/(name+'.tail')).write_bytes(f.read())
    out=Path(str(run)+'.tar.gz')
    with tarfile.open(out,'w:gz') as t:
        for p in run.rglob('*'):
            if p.is_file() and not p.is_symlink() and p.name not in (
                  'configure.log','full-build.log','game-console.log') and p.stat().st_size<16*1024*1024:
                t.add(p,arcname=str(p.relative_to(run)))
    return out

def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--seconds',type=int,default=420)
    ap.add_argument('--build-only',action='store_true')
    args=ap.parse_args()
    if args.seconds<60 or args.seconds>1200:ap.error('seconds must be 60..1200')
    (ROOT/'logs').mkdir(parents=True,exist_ok=True)
    folder=Path(tempfile.mkdtemp(prefix='got-restored-srt-'+
             time.strftime('%Y%m%d-%H%M%S')+'-',dir=ROOT/'logs'))
    (folder/'info').mkdir()
    rc=2
    try:
        composer,native,port,report=load_verified()
        with (ROOT/'GhostOfTsushima/.build.lock').open('a') as lock:
            try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
            except BlockingIOError:raise RuntimeError('ANOTHER_BUILD_OWNS_LOCK')
            composer.stage('restore_verified_shader_and_guest_read_paths')
            rev,tree=compose(composer,port,folder)
            print('RESTORED_SOURCE_REVISION='+rev,flush=True)
            checkout(composer,rev)
            cand=build(composer,port,rev,tree,folder)
        if args.build_only:
            print('GAMEPLAY=NOT_RUN',flush=True);rc=0
        else:
            print('TESTING_GAME_WITH_OLDER_PROVEN_SRT_PATHS',flush=True)
            print('At GoT menu, press controller X (keyboard N) to enter gameplay.',flush=True)
            game_rc=native.run_game(cand,folder/'info',folder,args.seconds)
            rc=0 if game_rc==0 else 2
    except BaseException as exc:
        traceback.print_exc()
        (folder/'info/failure.txt').write_text(traceback.format_exc())
        print('RESTORED_SRT_BUILD_ERROR='+str(exc),flush=True)
    finally:
        print('UPLOAD_THIS='+str(archive(folder)),flush=True)
    return rc

if __name__=='__main__':
    raise SystemExit(main())
