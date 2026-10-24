#!/usr/bin/env python3
"""Rebuild and test the verified no-DynamoRIO generic GoT candidate with native HLE stack realignment.

Changes exactly src/common/types.h from pinned upstream to the reviewed,
portable x86-64 Linux guest/HLE ABI boundary fix. Does not change guest game
instructions, CPU translation, graphics code, saves, ES-DE or the original
GhostOfTsushima/source checkout. Preserves the earlier native candidate.
"""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tarfile
import tempfile
import time
import traceback

ROOT=Path.home()/'projects/shadps4'
SRC=ROOT/'GhostOfTsushima/source'
WORK=ROOT/'GhostOfTsushima/fresh-source/ca69b6509427-3967624'
BUILD=ROOT/'GhostOfTsushima/fresh-builds/ca69b6509427-3967624'
NATIVE_REV='806bf5093d6829f97db501aaba51d767a34e822c'
NATIVE_SHA='2b617a7148be38e1e653093e7f83ce1ab725a050135ee14f85c8aa53353106aa'
NATIVE_REPORT=ROOT/'logs/got-upstream-native-20261024-135431-xieq3ph7/info/build-result.json'
NATIVE_CANDIDATE=ROOT/'GhostOfTsushima/candidates'/(NATIVE_REV+'-'+NATIVE_SHA)/'shadps4'
ABI_SOURCE='274616ad38992c250e3e21c5bbf468d8a71472c4'
ABI_TEST_SOURCE='a98d65baddccf9a8668bf3ca56d8874ef5d56b19'
ABI_TEST_PATH='tests/cpu_id/test_native_hle_stack_alignment_20261024.cpp'
ABI_TEST_BLOB='ce42ede291c12c7582a511457735e50bfb23895f'
ABI_PATH='src/common/types.h'
BEFORE_BLOB='c2b7bd35ea2767287a80b84dc756394c72a7af03'
AFTER_BLOB='7ad311777652b21609f64283c1e216af5ffb6ec2'
SOURCE_REMOTE='https://github.com/Chreece/shadPS4.git'
NATIVE_RUNNER=ROOT/'scripts/build_run_got_upstream_native_20261024.py'
NATIVE_RUNNER_BLOB='96c6b84bde950513c237a9daec6177999fb3ef92'
COMPOSER=ROOT/'scripts/build_run_generic_large_indexed_resources_20261023.py'
COMPOSER_BLOB='4d4db01296af760928806720b57ffcf17a510537'
GAME=Path('/mnt/roms-all/ps4-games/CUSA11456/eboot.bin')
SESSION=ROOT/'test-profiles/got-generic-world-20261024'

def git_blob(path):
    data=path.read_bytes()
    return hashlib.sha1(b'blob '+str(len(data)).encode()+b'\0'+data).hexdigest()

def sha256(path):
    if path.is_symlink() or not path.is_file():
        raise RuntimeError('VERIFIED_REGULAR_FILE_REQUIRED='+str(path))
    with path.open('rb') as f:return hashlib.file_digest(f,'sha256').hexdigest()

def checked(cmd,timeout=None):
    return subprocess.run([str(x) for x in cmd],check=True,
                          stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=timeout)

def ensure_inputs():
    for file,expected in ((NATIVE_RUNNER,NATIVE_RUNNER_BLOB),
                          (COMPOSER,COMPOSER_BLOB)):
        if not file.is_file() or git_blob(file)!=expected:
            raise RuntimeError('PINNED_LOCAL_BUILD_MODULE_CHANGED='+str(file))
    import importlib.util
    spec=importlib.util.spec_from_file_location('native_host_abi_builder',NATIVE_RUNNER)
    module=importlib.util.module_from_spec(spec)
    sys.modules[spec.name]=module
    spec.loader.exec_module(module)
    r=module.import_composer()
    r.BASE=r.git('rev-parse','HEAD').decode().strip()
    r.BASE_STATUS=r.source_status()
    r.validate_source_status(r.BASE_STATUS)
    if not r.exists(NATIVE_REV+'^{commit}'):
        raise RuntimeError('PROVEN_NATIVE_SOURCE_COMMIT_NOT_FOUND')
    if sha256(NATIVE_CANDIDATE)!=NATIVE_SHA:
        raise RuntimeError('PRIOR_SUCCESSFUL_NATIVE_BINARY_CHANGED')
    report=json.loads(NATIVE_REPORT.read_text())
    expected={'revision':NATIVE_REV,'candidate_sha256':NATIVE_SHA,
              'status':'UPSTREAM_NATIVE_GENERIC_BUILD_PASS',
              'native_guest_cpu_handling':True,'dynamorio_in_build':False,
              'dynamorio_required':False,'es_de_modified':False}
    for k,v in expected.items():
        if report.get(k)!=v:
            raise RuntimeError('PRIOR_NATIVE_BUILD_REPORT_MISMATCH='+k)
    if Path(report['candidate'])!=NATIVE_CANDIDATE:
        raise RuntimeError('PRIOR_NATIVE_CANDIDATE_PATH_CHANGED')
    if r.git('rev-parse',NATIVE_REV+'^{tree}').decode().strip()!=report['tree']:
        raise RuntimeError('PRIOR_NATIVE_SOURCE_TREE_CHANGED')
    return module,r,report

def fetch_abi_fix(r):
    for revision in (ABI_SOURCE,ABI_TEST_SOURCE):
        if r.exists(revision+'^{commit}'):
            continue
        q=r.run(['git','-C',SRC,'fetch','--no-tags','--recurse-submodules=no',
                 SOURCE_REMOTE,revision],check=False,timeout=600)
        if q.returncode or not r.exists(revision+'^{commit}'):
            raise RuntimeError('VERIFIED_GENERIC_ABI_SOURCE_FETCH_FAILED='+revision+
                               (q.stderr or b'').decode(errors='replace')[-1700:])

def compose_abi_candidate(r,info,run):
    if r.git('rev-parse',NATIVE_REV+':'+ABI_PATH).decode().strip()!=BEFORE_BLOB:
        raise RuntimeError('NATIVE_SOURCE_ABI_HEADER_UNEXPECTED')
    if r.git('rev-parse',ABI_SOURCE+':'+ABI_PATH).decode().strip()!=AFTER_BLOB:
        raise RuntimeError('PINNED_GENERIC_ABI_HEADER_CHANGED')
    fixed=r.show(ABI_SOURCE,ABI_PATH).decode()
    if ('#define PS4_SYSV_ABI __attribute__((sysv_abi, force_align_arg_pointer))'
            not in fixed or
        '#if defined(__linux__) && defined(__x86_64__)' not in fixed or
        '#define PS4_SYSV_ABI __attribute__((sysv_abi))' not in fixed):
        raise RuntimeError('GENERIC_ABI_FIX_CONTRACT_CHANGED')
    with tempfile.TemporaryDirectory(prefix='got-native-abi-index-',dir=ROOT/'logs') as temp:
        index=Path(temp)/'index'
        env=r.env({'GIT_INDEX_FILE':str(index)})
        r.git('read-tree',NATIVE_REV,e=env)
        r.update_index_text(index,ABI_PATH,fixed)
        tree=r.git('write-tree',e=env).decode().strip()
    if r.git('rev-parse',tree+':'+ABI_PATH).decode().strip()!=AFTER_BLOB:
        raise RuntimeError('COMPOSED_ABI_FIX_BLOB_MISMATCH')
    revision=r.commit_tree(tree,NATIVE_REV,
        b'Native general PS4 HLE guest/host ABI stack realignment (Linux x86-64)\n')
    paths=r.git('diff','--name-only',NATIVE_REV,revision).decode().splitlines()
    if paths!=[ABI_PATH]:
        raise RuntimeError('GENERIC_ABI_CANDIDATE_CHANGED_UNRELATED_PATHS='+repr(paths))
    if r.git('rev-parse',revision+'^').decode().strip()!=NATIVE_REV:
        raise RuntimeError('GOT_NATIVE_PARENTAGE_CHANGED')
    for removed in ('cmake/CpuIdTranslation.cmake',
                    'src/core/cpu_id_translation/launcher.cpp',
                    'src/core/cpu_id_translation/client.c'):
        if r.exists(revision+':'+removed):
            raise RuntimeError('REJECTED_DYNAMORIO_SOURCE_REAPPEARED='+removed)
    r.git('update-ref','refs/shadps4-composer/native-hle-abi-'+revision[:12],revision)
    (info/'abi-fix.json').write_text(json.dumps({
        'source_parent':NATIVE_REV,'revision':revision,'tree':tree,
        'old_header_blob':BEFORE_BLOB,'new_header_blob':AFTER_BLOB,
        'modified_paths':paths,'stack_policy':'linux-x86_64_guest_hle_entry',
        'fix':'force_align_arg_pointer_plus_sysv_abi',
        'universal':True,'dynamorio':False,'es_de_modified':False,
    },indent=2)+'\n')
    (info/'generic-hle-abi.patch').write_bytes(r.git('diff',NATIVE_REV,revision,'--',ABI_PATH))
    return revision,tree

def checkout_verified_candidate(r,revision):
    if WORK.is_symlink():raise RuntimeError('DISPOSABLE_WORKSPACE_SYMLINK_UNSAFE')
    if not WORK.exists():
        name='refs/shadps4-composer/restore-native-'+NATIVE_REV[:12]
        r.git('update-ref',name,NATIVE_REV)
        try:
            r.add_fresh_source(WORK,NATIVE_REV,name)
        finally:
            r.git('update-ref','-d',name,check=False)
    if not (WORK/'.git').exists():
        raise RuntimeError('DISPOSABLE_WORKSPACE_NOT_A_GIT_REPO')
    head=checked(['git','-C',WORK,'rev-parse','HEAD']).stdout.decode().strip()
    if head not in (NATIVE_REV,revision):
        raise RuntimeError('DISPOSABLE_CHECKOUT_HAS_UNEXPECTED_REVISION='+head)
    staged=checked(['git','-C',WORK,'diff','--cached','--name-only']).stdout.strip()
    dirty=checked(['git','-C',WORK,'diff','--name-only','HEAD','--']).stdout.strip()
    if staged or dirty:
        raise RuntimeError('DISPOSABLE_WORKSPACE_HAS_UNSAVED_TRACKED_EDITS')
    if head!=revision:
        ref='refs/shadps4-composer/native-hle-abi-'+revision[:12]
        q=r.run(['git','-C',WORK,'fetch','--no-tags','--recurse-submodules=no',
                 str(SRC),ref+':refs/remotes/composer/native-hle-abi'],
                check=False,timeout=300)
        if q.returncode:
            raise RuntimeError('NATIVE_ABI_REVISION_FETCH_FAILED='+
                               (q.stderr or b'').decode(errors='replace')[-1200:])
        checked(['git','-C',WORK,'checkout','--detach',revision])
    if checked(['git','-C',WORK,'rev-parse','HEAD']).stdout.decode().strip()!=revision:
        raise RuntimeError('DISPOSABLE_CHECKOUT_FAILED')
    if not (WORK/'src/common/types.h').is_file():
        raise RuntimeError('UPDATED_GUEST_ABI_HEADER_MISSING')
    if git_blob(WORK/ABI_PATH)!=AFTER_BLOB:
        raise RuntimeError('WORKTREE_GUEST_ABI_HEADER_HASH_MISMATCH')
    for item in ('cmake/CpuIdTranslation.cmake','src/core/cpu_id_translation'):
        if (WORK/item).exists():
            raise RuntimeError('DYNAMORIO_SOURCE_PRESENT_IN_NATIVE_WORKTREE='+item)
    # Keep submodule state from the previous successful native build. If
    # required, initialise it in the disposable source directory only.
    missing=not (WORK/'externals/sirit').is_dir()
    if missing:r.submodules_at(WORK)

def preflight_guest_hle_abi(r,run,info):
    """Execute the same native misaligned guest -> host SIMD regression."""
    if r.git('rev-parse',ABI_TEST_SOURCE+':'+ABI_TEST_PATH).decode().strip()!=ABI_TEST_BLOB:
        raise RuntimeError('PINNED_GENERIC_HLE_STACK_REGRESSION_CHANGED')
    test=run/'guest-hle-abi-test.cpp'
    test.write_bytes(r.show(ABI_TEST_SOURCE,ABI_TEST_PATH))
    compilers=[c for c in (shutil.which('g++'),shutil.which('clang++')) if c]
    if not compilers:
        raise RuntimeError('NO_CXX_COMPILER_FOR_NATIVE_ABI_PREFLIGHT')
    lines=[]
    for compiler in compilers:
        label=Path(compiler).name
        executable=run/('hle-abi-'+label)
        compile_cmd=[compiler,'-std=c++20','-O2','-mavx2',
                     '-I'+str(WORK/'src'),str(test),'-o',str(executable)]
        compile=subprocess.run(compile_cmd,stdout=subprocess.PIPE,
                               stderr=subprocess.STDOUT,timeout=180)
        lines.append('COMPILER='+compiler+' EXIT='+str(compile.returncode))
        lines.append(compile.stdout.decode(errors='replace')[-10000:])
        if compile.returncode!=0:
            (info/'native-abi-preflight.log').write_text('\n'.join(lines))
            raise RuntimeError('GENERIC_HLE_ABI_PRETEST_COMPILATION_FAILED='+label)
        execution=subprocess.run([str(executable)],stdout=subprocess.PIPE,
                                 stderr=subprocess.STDOUT,timeout=25)
        output=execution.stdout.decode(errors='replace')
        lines.append('RUN='+label+' EXIT='+str(execution.returncode)+' '+output)
        if execution.returncode!=0 or 'NATIVE_HLE_STACK_ALIGNMENT_TEST=PASS' not in output:
            (info/'native-abi-preflight.log').write_text('\n'.join(lines))
            raise RuntimeError('GENERIC_HLE_ABI_PRETEST_FAILED='+label)
        print('NATIVE_HLE_ABI_PREFLIGHT_'+label.upper().replace('+','P')+'=PASS',flush=True)
    (info/'native-abi-preflight.log').write_text('\n'.join(lines))
    print('NATIVE_HLE_ABI_PREFLIGHT=PASS',flush=True)


def build_native_abi(r,revision,tree,run,info):
    print('NATIVE_ABI_SOURCE='+revision,flush=True)
    r.stage('configure_emulator_owned_native_guest_hle_stack_alignment')
    r.tee(['cmake','--fresh','-S',WORK,'-B',BUILD,'-G','Ninja',
           '-DCMAKE_BUILD_TYPE=Release','-DENABLE_TESTS=OFF',
           '-DCMAKE_INTERPROCEDURAL_OPTIMIZATION_RELEASE=ON'],run/'configure.log')
    if not (BUILD/'build.ninja').is_file():
        raise RuntimeError('GENERIC_ABI_BUILD_SYSTEM_MISSING')
    ninja=(BUILD/'build.ninja').read_text(errors='replace')
    for forbidden in ('shadps4_cpu_id_bundle','src/core/cpu_id_translation/launcher.cpp',
                      'shadps4_cpu_id_runtime','ENABLE_CPU_ID_TRANSLATION'):
        if forbidden in ninja:
            raise RuntimeError('REJECTED_CPU_TRANSLATOR_BUILD_TARGET='+forbidden)
    r.stage('compile_emulator_with_native_hle_abi_repair')
    r.tee(['nice','-n','10','cmake','--build',BUILD,
           '--target','shadps4','--parallel','2'],run/'full-build.log')
    binary=r.exe(BUILD)
    if binary is None:
        raise RuntimeError('NATIVE_ABI_EXECUTABLE_NOT_FOUND')
    digest=sha256(binary)
    if digest==NATIVE_SHA:raise RuntimeError('NEW_ABI_BINARY_IDENTICAL_TO_BROKEN_NATIVE')
    candidate=ROOT/'GhostOfTsushima/candidates'/(revision+'-'+digest)/'shadps4'
    candidate.parent.mkdir(parents=True,exist_ok=True)
    shutil.copy2(binary,candidate)
    candidate.chmod(candidate.stat().st_mode|0o111)
    if sha256(candidate)!=digest:
        raise RuntimeError('NATIVE_ABI_CANDIDATE_COPY_CHANGED')
    if (candidate.parent/'cpu-id-runtime').exists():
        raise RuntimeError('DYNAMORIO_RUNTIME_IN_GENERIC_ABI_CANDIDATE')
    if shutil.which('strings'):
        p=subprocess.run(['strings','-a',str(candidate)],
             capture_output=True,timeout=50)
        for forbidden in (b'StartCpuIdTranslation',b'libdynamorio.so',
                          b'ShadCpuIdTranslationActive',b'cpu-id-runtime'):
            if forbidden in p.stdout:
                raise RuntimeError('EXTERNAL_TRANSLATOR_SYMBOL_IN_ABI_BINARY='+str(forbidden))
    r.assert_source_unchanged()
    report={
        'status':'NATIVE_ABI_GENERIC_BUILD_PASS','revision':revision,'tree':tree,
        'source_parent':NATIVE_REV,'candidate':str(candidate),
        'candidate_sha256':digest,'upstream_main':'396c2a09a38d22eaa02aab32c1967263dceab95f',
        'pr_5492_head':'9f55474ad45778f8ac08e923574f214d492997f2',
        'native_guest_cpu':True,'dynamorio':False,'es_de_modified':False,
        'generic_hle_alignment':True,'guest_specific_shader_hacks':False,
        'source_untouched':True,'complete_rebuild_successful':True,
        'gameplay_verified':False,
    }
    (info/'build-result.json').write_text(json.dumps(report,indent=2)+'\n')
    print('NATIVE_ABI_BUILD=PASS',flush=True)
    print('NATIVE_ABI_CANDIDATE='+str(candidate),flush=True)
    print('NATIVE_ABI_SHA256='+digest,flush=True)
    return candidate,report

def pack(run):
    # Collect logs at both ends; shader/runtime logs may be very large.
    for name in ('configure.log','full-build.log','game-console.log'):
        path=run/name
        if not path.is_file():continue
        with path.open('rb') as f:
            (run/(name+'.head')).write_bytes(f.read(1024*1024))
            if path.stat().st_size>8*1024*1024:f.seek(-8*1024*1024,2)
            else:f.seek(0)
            (run/(name+'.tail')).write_bytes(f.read(8*1024*1024))
    out=Path(str(run)+'.tar.gz')
    with tarfile.open(out,'w:gz') as tar:
        for item in run.rglob('*'):
            if (item.is_file() and not item.is_symlink() and
                item.stat().st_size<15*1024*1024 and
                item.name not in ('configure.log','full-build.log','game-console.log')):
                tar.add(item,arcname=str(item.relative_to(run)))
    return out

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--seconds',type=int,default=420)
    parser.add_argument('--build-only',action='store_true')
    args=parser.parse_args()
    if not 60<=args.seconds<=1200:parser.error('seconds must be 60..1200')
    (ROOT/'logs').mkdir(parents=True,exist_ok=True)
    run=Path(tempfile.mkdtemp(prefix='got-native-hle-abi-20261024-'+
                              time.strftime('%Y%m%d-%H%M%S')+'-',dir=ROOT/'logs'))
    info=run/'info'
    info.mkdir()
    result=2
    try:
        native,r,prior=ensure_inputs()
        fetch_abi_fix(r)
        with (ROOT/'GhostOfTsushima/.build.lock').open('a') as lock:
            try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
            except BlockingIOError:raise RuntimeError('ANOTHER_SHADPS4_BUILD_OWNS_LOCK')
            revision,tree=compose_abi_candidate(r,info,run)
            r.stage('restore_disposable_checkout_preserving_original_source')
            checkout_verified_candidate(r,revision)
            r.stage('native_guest_to_host_alignment_regression')
            preflight_guest_hle_abi(r,run,info)
            candidate,report=build_native_abi(r,revision,tree,run,info)
        if args.build_only:
            print('GAMEPLAY_TEST=NOT_RUN',flush=True);result=0
        else:
            print('GAME=GHOST_OF_TSUSHIMA; NO_DYNAMORIO; NO_ESDE_CHANGES',flush=True)
            print('Press controller X (keyboard N) at menu to test world graphics.',flush=True)
            game_rc=native.run_game(candidate,info,run,args.seconds)
            result=0 if game_rc==0 else 2
    except BaseException as error:
        traceback.print_exc()
        (info/'failure.txt').write_text(traceback.format_exc())
        print('NATIVE_ABI_BUILD_ERROR='+str(error),flush=True)
    finally:
        print('UPLOAD_THIS='+str(pack(run)),flush=True)
    return result

if __name__=='__main__':
    raise SystemExit(main())
