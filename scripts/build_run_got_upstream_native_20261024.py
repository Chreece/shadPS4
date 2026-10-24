#!/usr/bin/env python3
"""Rebuild verified generic GoT candidate using emulator-owned native CPU handling.

Reuses the already-composed, fully compiled generic shader source and Ninja
cache. Makes only audited, generic, upstream-compatible removals of the
third-party DynamoRIO launcher/client and its CMake integration.
The original GhostOfTsushima/source checkout and previous executable are
never modified. Never promotes to ES-DE until tested.
"""
from __future__ import annotations
import argparse
import fcntl
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import sys
import tarfile
import tempfile
import time
import traceback
import urllib.request

ROOT=Path.home()/'projects/shadps4'
SRC=ROOT/'GhostOfTsushima/source'
WORK=ROOT/'GhostOfTsushima/fresh-source/ca69b6509427-3967624'
BUILD=ROOT/'GhostOfTsushima/fresh-builds/ca69b6509427-3967624'
ORIGINAL_REV='79dc2126ac8ab88eacaa0f36a0e0ae71471efc55'
ORIGINAL_TREE='6882f67fd734cba9c3d084c6fbadaf3b7f7859d2'
ORIGINAL_SHA='5efed1a93796331556016548da2710f94f2efe00d8cb9ecbb1e4aef4f1d9fcc6'
ORIGINAL_CANDIDATE=ROOT/'GhostOfTsushima/candidates'/(ORIGINAL_REV+'-'+ORIGINAL_SHA)/'shadps4'
ORIGINAL_REPORT=ROOT/'logs/got-generic-srt-resume-20261024-124735-cwbxipfj/info/build-result.json'
COMPOSER=ROOT/'scripts/build_run_generic_large_indexed_resources_20261023.py'
COMPOSER_BLOB='4d4db01296af760928806720b57ffcf17a510537'
POLICY_REV='c106b69baacc53cfb4452bb4fd0e201495759ac1'
GAME=Path('/mnt/roms-all/ps4-games/CUSA11456/eboot.bin')
SESSION=ROOT/'test-profiles/got-generic-world-20261024'
SOURCE_REMOTE='https://github.com/Chreece/shadPS4.git'

def sha256(p):
    if not p.is_file() or p.is_symlink():
        raise RuntimeError('REGULAR_FILE_REQUIRED='+str(p))
    with p.open('rb') as stream:
        return hashlib.file_digest(stream,'sha256').hexdigest()

def git_blob(path):
    b=path.read_bytes()
    return hashlib.sha1(b'blob '+str(len(b)).encode()+b'\0'+b).hexdigest()

def run_command(args,**kw):
    return subprocess.run([str(a) for a in args],check=True,**kw)

def import_composer():
    if not COMPOSER.is_file() or git_blob(COMPOSER)!=COMPOSER_BLOB:
        raise RuntimeError('NATIVE_COMPOSER_HASH_MISMATCH')
    spec=importlib.util.spec_from_file_location('_native_candidate_composer',COMPOSER)
    if not spec or not spec.loader:
        raise RuntimeError('NATIVE_COMPOSER_MODULE_UNAVAILABLE')
    m=importlib.util.module_from_spec(spec)
    sys.modules[spec.name]=m
    spec.loader.exec_module(m)
    if m.NATIVE_CPU_POLICY_SOURCE!=POLICY_REV:
        raise RuntimeError('COMPOSER_NATIVE_POLICY_PIN_CHANGED')
    return m

def verify_old_build(r):
    data=json.loads(ORIGINAL_REPORT.read_text())
    checks={
        'status':'GENERIC_LARGE_INDEXED_RESOURCE_BUILD_PASS',
        'revision':ORIGINAL_REV,
        'tree':ORIGINAL_TREE,
        'candidate_sha256':ORIGINAL_SHA,
        'source_untouched':True,
        'full_build':'PASS',
        'upstream_main':'396c2a09a38d22eaa02aab32c1967263dceab95f',
        'pr_5492_head':'9f55474ad45778f8ac08e923574f214d492997f2',
        'game_specific_options':False,
    }
    for key,wanted in checks.items():
        if data.get(key)!=wanted:
            raise RuntimeError('VERIFIED_OLD_BUILD_REPORT_DRIFT='+key)
    if Path(data['candidate'])!=ORIGINAL_CANDIDATE or sha256(ORIGINAL_CANDIDATE)!=ORIGINAL_SHA:
        raise RuntimeError('VERIFIED_OLD_CANDIDATE_CHANGED')
    if not r.exists(ORIGINAL_REV+'^{commit}'):
        raise RuntimeError('VERIFIED_OLD_SOURCE_COMMIT_NOT_PRESENT')
    if r.git('rev-parse',ORIGINAL_REV+'^{tree}').decode().strip()!=ORIGINAL_TREE:
        raise RuntimeError('VERIFIED_OLD_SOURCE_TREE_MISMATCH')
    return data

def ensure_policy_commit(r):
    if r.exists(POLICY_REV+'^{commit}'):
        return
    r.stage('fetch_verified_native_policy_commit')
    p=r.run(['git','-C',SRC,'fetch','--no-tags','--recurse-submodules=no',
             SOURCE_REMOTE,POLICY_REV],check=False,timeout=600)
    if p.returncode or not r.exists(POLICY_REV+'^{commit}'):
        raise RuntimeError('PINNED_NATIVE_POLICY_FETCH_FAILED='+
                           (p.stderr or b'').decode(errors='replace')[-1800:])

def compose_native(r,run,info):
    idx=run/'native.index'
    e=r.env({'GIT_INDEX_FILE':str(idx)})
    r.git('read-tree',ORIGINAL_REV,e=e)
    modified=r.apply_upstream_native_cpu_policy(idx)
    if len(modified)<6:
        raise RuntimeError('INCOMPLETE_EXTERNAL_CPU_TRANSLATOR_REMOVAL')
    tree=r.git('write-tree',e=e).decode().strip()
    if tree==ORIGINAL_TREE:
        raise RuntimeError('NATIVE_CPU_SOURCE_UNCHANGED')
    rev=r.commit_tree(tree,ORIGINAL_REV,
       b'Use upstream-compatible native CPU execution; remove DynamoRIO entirely\n')
    files=r.git('diff','--name-only',ORIGINAL_REV,rev).decode().splitlines()
    if sorted(files)!=sorted(modified):
        raise RuntimeError('UNREVIEWED_NATIVE_SOURCE_DIFF='+
                           str(sorted(set(files).symmetric_difference(modified))))
    if any(p.startswith(('src/video_core/','src/shader_recompiler/')) for p in files):
        raise RuntimeError('NATIVE_CPU_CHANGE_TOUCHED_RENDERER_OR_SHADERS')
    if r.git('rev-parse',rev+'^').decode().strip()!=ORIGINAL_REV:
        raise RuntimeError('NATIVE_BUILD_LOST_GENERIC_FIX_PARENT')
    if r.git('rev-parse',rev+'^{tree}').decode().strip()!=tree:
        raise RuntimeError('NATIVE_BUILD_TREE_CHANGED')
    (info/'removed-external-translation-files.json').write_text(
        json.dumps({'removed_or_rewritten':files,'native_source_parent':ORIGINAL_REV,
                    'native_revision':rev,'source_tree':tree,
                    'cpu_execution':'emulator-owned-native',
                    'dynamorio':'rejected','upstream_lineage_preserved':True},
                   indent=2)+'\n')
    (info/'native-vs-previous.patch').write_bytes(
        r.git('diff','--binary',ORIGINAL_REV,rev))
    return rev,tree

def prepare_disposable_checkout(r,rev):
    if not WORK.exists():
        ref='refs/shadps4-composer/native-original-'+ORIGINAL_REV[:12]
        r.git('update-ref',ref,ORIGINAL_REV)
        try:
            r.add_fresh_source(WORK,ORIGINAL_REV,ref)
            r.submodules_at(WORK)
        finally:
            r.git('update-ref','-d',ref,check=False)
    if WORK.is_symlink() or not (WORK/'.git').exists():
        raise RuntimeError('NATIVE_DISPOSABLE_CHECKOUT_UNSAFE')
    old_head=run_command(['git','-C',WORK,'rev-parse','HEAD'],
                         stdout=subprocess.PIPE).stdout.decode().strip()
    if old_head not in (ORIGINAL_REV,rev):
        raise RuntimeError('NATIVE_DISPOSABLE_CHECKOUT_UNEXPECTED_HEAD='+old_head)
    tracked=run_command(['git','-C',WORK,'diff','--name-only','HEAD','--'],
                        stdout=subprocess.PIPE).stdout.decode().splitlines()
    staged=run_command(['git','-C',WORK,'diff','--cached','--name-only'],
                       stdout=subprocess.PIPE).stdout.decode().splitlines()
    if tracked or staged:
        raise RuntimeError('NATIVE_DISPOSABLE_SOURCE_HAS_LOCAL_EDITS='+str(tracked+staged))
    tmpref='refs/shadps4-composer/upstream-native-'+rev[:12]
    r.git('update-ref',tmpref,rev)
    try:
        p=r.run(['git','-C',WORK,'fetch','--no-tags','--recurse-submodules=no',
                 str(SRC),tmpref+':refs/remotes/composer/upstream-native'],
                check=False,timeout=300)
        if p.returncode:
            raise RuntimeError('NATIVE_RECONSTRUCTED_SOURCE_FETCH_FAILED='+
                               (p.stderr or b'').decode(errors='replace')[-1500:])
        run_command(['git','-C',WORK,'checkout','--detach',rev],
                    stdout=subprocess.PIPE,stderr=subprocess.PIPE)
    finally:
        r.git('update-ref','-d',tmpref,check=False)
    if (WORK/'cmake/CpuIdTranslation.cmake').exists():
        raise RuntimeError('TRANSLATION_CMAKE_FILE_STILL_IN_CHECKOUT')
    if (WORK/'src/core/cpu_id_translation').exists():
        raise RuntimeError('DYNAMORIO_SOURCE_DIRECTORY_STILL_IN_CHECKOUT')
    r.submodules_at(WORK)

def verify_native_build_files():
    ninja=BUILD/'build.ninja'
    if not ninja.is_file():
        raise RuntimeError('NATIVE_BUILD_NINJA_MISSING')
    body=ninja.read_text(errors='replace')
    for unwanted in ('ENABLE_CPU_ID_TRANSLATION','shadps4_cpu_id_bundle',
                     'shadps4_cpu_id_runtime','core/cpu_id_translation/launcher.cpp'):
        if unwanted in body:
            raise RuntimeError('DYNAMORIO_TARGET_REMAINED='+unwanted)

def prepare_display():
    env=os.environ.copy()
    env.pop('SHADPS4_CPU_ID_MODE',None)
    env.pop('SHADPS4_CPU_ID_RESTART',None)
    if not env.get('DISPLAY') and not env.get('WAYLAND_DISPLAY'):
        try:
            x=run_command(['systemctl','--user','show-environment'],
                          stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,
                          timeout=10).stdout.decode(errors='replace')
            for line in x.splitlines():
                if '=' in line:
                    key,value=line.split('=',1)
                    if key in ('DISPLAY','WAYLAND_DISPLAY','XAUTHORITY',
                               'DBUS_SESSION_BUS_ADDRESS','XDG_RUNTIME_DIR'):
                        env.setdefault(key,value)
        except (OSError,subprocess.CalledProcessError,subprocess.TimeoutExpired):
            pass
    return env

def free_vram():
    for root in Path('/sys/class/drm').glob('card*/device/mem_info_vram_total'):
        try:return max(0,int(root.read_text())-
                       int(root.with_name('mem_info_vram_used').read_text()))
        except (OSError,ValueError):pass
    return None

def ollama(path,payload=None):
    req=urllib.request.Request('http://127.0.0.1:7869'+path,
        data=json.dumps(payload).encode() if payload is not None else None,
        headers={'Content-Type':'application/json'})
    with urllib.request.urlopen(req,timeout=12) as f:
        return json.loads(f.read())

def vram_prepare():
    free=free_vram()
    print('FREE_VRAM_GIB='+('UNAVAILABLE' if free is None else f'{free/2**30:.2f}'),
          flush=True)
    unloaded=[]
    if free is None or free>=8*2**30:return unloaded
    try:
        for model in ollama('/api/ps').get('models',[]):
            name=model.get('name') or model.get('model')
            if name:
                ollama('/api/generate',{'model':name,'keep_alive':0})
                unloaded.append(name)
    except Exception as e:print('OLLAMA_UNLOAD_WARNING='+str(e),flush=True)
    return unloaded

def vram_restore(unloaded):
    for name in unloaded:
        try:ollama('/api/generate',{'model':name,'prompt':'','keep_alive':'20m'})
        except Exception as e:print('OLLAMA_RELOAD_WARNING='+str(e),flush=True)

def run_game(candidate,info,run,seconds):
    if not GAME.is_file():
        raise RuntimeError('GOT_GAME_EXECUTABLE_MISSING='+str(GAME))
    if not (SESSION/'user/config.json').is_file():
        raise RuntimeError('ISOLATED_GOT_TEST_PROFILE_MISSING='+str(SESSION))
    if candidate.parent.joinpath('cpu-id-runtime').exists():
        raise RuntimeError('REJECTED_CPU_RUNTIME_INSTALLED_WITH_CANDIDATE')
    print('GAME_BINARY='+str(candidate),flush=True)
    print('CPU_MODE=EMULATOR_OWNED_NATIVE_NO_DYNAMORIO',flush=True)
    print('ES_DE_DEFAULT=UNCHANGED',flush=True)
    print('At GoT menu: press controller X (keyboard N) to enter gameplay.',flush=True)
    env=prepare_display()
    unloaded=vram_prepare()
    command=[str(candidate),'--game',str(GAME),
             '--fullscreen','true','--show-fps']
    log=run/'game-console.log'
    proc=None
    elapsed=0
    snapshots=[]
    try:
        with log.open('wb') as f:
            proc=subprocess.Popen(command,cwd=SESSION,env=env,
                                  stdout=f,stderr=subprocess.STDOUT,
                                  start_new_session=True)
            started=time.monotonic();next_print=25;next_capture=90
            while proc.poll() is None:
                elapsed=int(time.monotonic()-started)
                if elapsed>=seconds:
                    print('RUNTIME_TIME_LIMIT=REACHED',flush=True)
                    break
                if elapsed>=next_print:
                    print('GAME_RUNNING_SECONDS='+str(elapsed),flush=True)
                    next_print+=30
                if elapsed>=next_capture and len(snapshots)<4:
                    shot=run/('frame-'+str(len(snapshots)+1)+'.png')
                    for method in (
                        ['scrot','-o',str(shot)],
                        ['import','-window','root',str(shot)],
                    ):
                        if not shutil.which(method[0]):continue
                        try:
                            z=subprocess.run(method,env=env,
                              stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,
                              timeout=8)
                            if z.returncode==0 and shot.is_file():break
                        except (OSError,subprocess.TimeoutExpired):pass
                    if shot.is_file():snapshots.append(str(shot))
                    next_capture+=100
                time.sleep(2)
    except KeyboardInterrupt:
        print('GAME_STOP_REQUESTED=YES',flush=True)
    finally:
        if proc is not None and proc.poll() is None:
            for sig,wait in ((signal.SIGINT,4),(signal.SIGTERM,5),(signal.SIGKILL,3)):
                try:
                    os.killpg(proc.pid,sig);proc.wait(timeout=wait);break
                except (ProcessLookupError,subprocess.TimeoutExpired):continue
        vram_restore(unloaded)
    exit_code=None if proc is None else proc.returncode
    (info/'gameplay-status.json').write_text(
        json.dumps({'exit_code':exit_code,'elapsed_seconds':elapsed,
        'full_gameplay_graphics_verified':False,'screenshots':len(snapshots),
        'automatic_input':False,'dynamorio':False,'es_de_modified':False},
        indent=2)+'\n')
    print('GAME_EXIT_CODE='+str(exit_code),flush=True)
    return exit_code

def package_log(run):
    for name in ('configure.log','full-build.log','game-console.log'):
        file=run/name
        if file.is_file():
            with file.open('rb') as f:
                (run/(name+'.head')).write_bytes(f.read(1024*1024))
                if file.stat().st_size>8*1024*1024:f.seek(-8*1024*1024,2)
                else:f.seek(0)
                (run/(name+'.tail')).write_bytes(f.read(8*1024*1024))
    out=Path(str(run)+'.tar.gz')
    with tarfile.open(out,'w:gz') as t:
        for f in run.rglob('*'):
            if f.is_file() and f.stat().st_size<12*1024*1024 and f.name not in (
                    'full-build.log','configure.log','game-console.log'):
                t.add(f,arcname=str(f.relative_to(run)))
    return out

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--seconds',type=int,default=420)
    ap.add_argument('--build-only',action='store_true')
    args=ap.parse_args()
    if not 60<=args.seconds<=1200:ap.error('seconds out of 60..1200')
    (ROOT/'logs').mkdir(parents=True,exist_ok=True)
    run=Path(tempfile.mkdtemp(prefix='got-upstream-native-'+
          time.strftime('%Y%m%d-%H%M%S')+'-',dir=ROOT/'logs'))
    info=run/'info';info.mkdir()
    result=2
    try:
        r=import_composer()
        r.BASE=r.git('rev-parse','HEAD').decode().strip()
        r.BASE_STATUS=r.source_status()
        r.validate_source_status(r.BASE_STATUS)
        verify_old_build(r)
        ensure_policy_commit(r)
        with (ROOT/'GhostOfTsushima/.build.lock').open('a') as lock:
            try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
            except BlockingIOError:
                raise RuntimeError('ANOTHER_BUILD_OWNS_LOCK')
            r.stage('create_upstream_native_source_without_external_cpu_translation')
            rev,tree=compose_native(r,run,info)
            print('NATIVE_REVISION='+rev,flush=True)
            r.stage('restore_verified_disposable_source_and_ninja_cache')
            prepare_disposable_checkout(r,rev)
            BUILD.mkdir(parents=True,exist_ok=True)
            r.stage('configure_upstream_native_without_dynamorio')
            r.tee(['cmake','--fresh','-S',WORK,'-B',BUILD,'-G','Ninja',
                   '-DCMAKE_BUILD_TYPE=Release','-DENABLE_TESTS=OFF',
                   '-DCMAKE_INTERPROCEDURAL_OPTIMIZATION_RELEASE=ON'],
                  run/'configure.log')
            verify_native_build_files()
            r.stage('compile_generic_emulator_without_dynamorio')
            r.tee(['nice','-n','10','cmake','--build',BUILD,
                   '--target','shadps4','--parallel','2'],run/'full-build.log')
            r.stage('validate_upstream_native_binary')
            exe=r.exe(BUILD)
            if not exe:raise RuntimeError('NATIVE_BINARY_NOT_FOUND')
            binary_sha=sha256(exe)
            if binary_sha==ORIGINAL_SHA:
                raise RuntimeError('BINARY_IDENTICAL_TO_OLD_TRANSLATED_BUILD')
            candidate=ROOT/'GhostOfTsushima/candidates'/(rev+'-'+binary_sha)/'shadps4'
            candidate.parent.mkdir(parents=True,exist_ok=True)
            shutil.copy2(exe,candidate)
            candidate.chmod(candidate.stat().st_mode|0o111)
            if sha256(candidate)!=binary_sha:
                raise RuntimeError('NATIVE_COPY_HASH_FAILED')
            if (candidate.parent/'cpu-id-runtime').exists():
                raise RuntimeError('DYNAMORIO_RUNTIME_IN_NATIVE_CANDIDATE')
            if shutil.which('strings'):
                p=subprocess.run(['strings','-a',str(candidate)],
                    stdout=subprocess.PIPE,stderr=subprocess.PIPE)
                found=[tok for tok in (
                    b'StartCpuIdTranslation',b'ShadCpuIdTranslationActive',
                    b'cpu-id-runtime',b'libdynamorio.so',
                    b'CPU identity translation active',
                ) if tok in p.stdout]
                if found:raise RuntimeError('EXTERNAL_TRANSLATION_STILL_IN_BINARY='+str(found))
            r.assert_source_unchanged()
            (info/'build-result.json').write_text(json.dumps({
                'status':'UPSTREAM_NATIVE_GENERIC_BUILD_PASS',
                'candidate':str(candidate),'candidate_sha256':binary_sha,
                'revision':rev,'tree':tree,'parent':ORIGINAL_REV,
                'generic_fixes_preserved':True,
                'upstream':'396c2a09a38d22eaa02aab32c1967263dceab95f',
                'pr_5492':'9f55474ad45778f8ac08e923574f214d492997f2',
                'dynamorio_in_source':False,'dynamorio_in_build':False,
                'dynamorio_installed':False,'dynamorio_required':False,
                'native_guest_cpu_handling':True,
                'es_de_modified':False},indent=2)+'\n')
        print('NATIVE_BUILD=PASS',flush=True)
        print('NATIVE_CANDIDATE='+str(candidate),flush=True)
        print('NATIVE_CANDIDATE_SHA256='+binary_sha,flush=True)
        if args.build_only:
            print('GAMEPLAY_TEST=NOT_RUN',flush=True);result=0
        else:
            r.stage('test_got_with_native_cpu_only')
            game_rc=run_game(candidate,info,run,args.seconds)
            result=0 if game_rc==0 else 2
    except BaseException as error:
        traceback.print_exc()
        (info/'failure.txt').write_text(traceback.format_exc())
        print('UPSTREAM_NATIVE_BUILD_ERROR='+str(error),flush=True)
        result=2
    finally:
        print('UPLOAD_THIS='+str(package_log(run)),flush=True)
    return result

if __name__=='__main__':
    raise SystemExit(main())
