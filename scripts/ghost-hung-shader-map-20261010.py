#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Map the exact RADV hung VS/FS SPIR-V back to shadPS4 guest shader hashes.

Search existing private shader dumps first. If unavailable, build one NEW
private candidate from the established 7-file Ghost tracer, plus an isolated,
environment-gated SPIR-V file writer in vk_pipeline_cache.cpp. Original source
is restored byte-for-byte even after a build error. No production binaries,
game saves, ES-DE, GPU driver, SSH or GPU settings are modified.
"""
from __future__ import annotations

import fcntl
import hashlib
import importlib.util
import json
import os
import re
import shutil
import signal
import stat
import subprocess
import sys
import tarfile
import time
import traceback
from datetime import datetime
from pathlib import Path

HOME=Path.home()
BASE_NAME='ghost-isolated-compute6-irq-20261009.py'
BASE_REV='6a4429decac1b4b38a980ffdcf052a17ed0a7bcd'
BASE_BLOB='be02bb94febddb232a7adc952195f6210845b66b'
TARGETS={
    'vertex':'8fd49a29fa206a07080011b4b06eb925ac4d55c3',
    'pixel':'01ec71b8643d2461b57999293ed7c329fc6a1177'
}
GHOST=HOME/'Applications/shadps4-ghost'
BASE_EXE=GHOST/'shadps4'
OTHER_EXE=HOME/'Applications/shadps4/shadps4'
PRIVATE_EXE=GHOST/'hung-shader-map-candidate'/'shadps4'
SOURCE=HOME/'.cache/shadps4-ghost-fullstack-20261008-131621/source'
BUILD=HOME/'.cache/shadps4-ghost-isolated/build'
PIPELINE_SOURCE=SOURCE/'src/video_core/renderer_vulkan/vk_pipeline_cache.cpp'
STAMP=datetime.now().strftime('%Y%m%d-%H%M%S')
WORK=HOME/'.cache'/('ghost-hung-shader-map-'+STAMP)
OUT=HOME/('ghost-hung-shader-map-'+STAMP+'.tar.gz')
ENV_NAME='GHOST_SPV_MAP_DIR'
LOG_MARKER='GHOST_SPV_GUEST_MAP'
STATUS='not_started'
ERROR=None
RESTORED=None
EVENTS=[]
GAME_RESULT='not_started'
ORIGINAL_CHECKSUMS={}

PATCH_TARGET='''void PipelineCache::DumpShader(std::span<const u32> code, u64 hash, Shader::HwStage stage,
                               size_t perm_idx, std::string_view ext) {
    if (!EmulatorSettings.IsDumpShaders()) {
'''
PATCH_REPLACE='''void PipelineCache::DumpShader(std::span<const u32> code, u64 hash, Shader::HwStage stage,
                               size_t perm_idx, std::string_view ext) {
    // Ghost-only SPIR-V identity probe. Does not change generated code or shaders.
    if (ext == "spv") {
        if (const char* target_dir = std::getenv("GHOST_SPV_MAP_DIR")) {
            const auto dir = std::filesystem::path{target_dir};
            std::filesystem::create_directories(dir);
            const auto filename = GetShaderName(stage, hash, perm_idx) + ".spv";
            const auto destination = dir / filename;
            std::ofstream stream{destination, std::ios::binary | std::ios::trunc};
            if (stream) {
                stream.write(reinterpret_cast<const char*>(code.data()),
                             static_cast<std::streamsize>(code.size_bytes()));
                stream.close();
                LOG_INFO(Render_Vulkan, "GHOST_SPV_GUEST_MAP name={} words={} path={}",
                         filename, code.size(), destination.string());
            }
        }
    }
    if (!EmulatorSettings.IsDumpShaders()) {
'''

def say(line):
    row='['+datetime.now().isoformat(timespec='seconds')+'] '+str(line)
    print(row,flush=True)
    EVENTS.append(row)


def digest(path):
    if not path.is_file() or path.is_symlink():return None
    h=hashlib.sha256()
    with path.open('rb') as f:
        for part in iter(lambda:f.read(1<<20),b''):h.update(part)
    return h.hexdigest()


def git_blob(path):
    return subprocess.check_output(['git','hash-object',str(path)],text=True,timeout=15).strip()


def patch_pipeline(data):
    original=data.decode('utf-8')
    if original.count(PATCH_TARGET)!=1 or LOG_MARKER in original:
        raise ValueError('Unexpected Shader DumpShader function, or duplicate instrumentation')
    if original.count('#include <ranges>')!=1:
        raise ValueError('Pinned shader file includes changed')
    output=original.replace('#include <ranges>','#include <cstdlib>\n#include <fstream>\n#include <ranges>',1)
    output=output.replace(PATCH_TARGET,PATCH_REPLACE,1)
    if output.count(LOG_MARKER)!=1 or output.count('stream.write(')!=1:
        raise ValueError('Ghost source patch did not preserve exact structure')
    if output.count('if (!EmulatorSettings.IsDumpShaders())')!=1:
        raise ValueError('Original shader dump contract unexpectedly changed')
    return output.encode('utf-8')


def load_runner(path):
    url='https://raw.githubusercontent.com/Chreece/shadPS4/'+BASE_REV+'/scripts/'+BASE_NAME
    subprocess.run(['curl','-fsSL','--retry','3','--connect-timeout','15',
                    '--max-time','45',url,'-o',str(path)],check=True,timeout=65)
    if git_blob(path)!=BASE_BLOB:raise RuntimeError('Existing Ghost runner blob mismatch')
    spec=importlib.util.spec_from_file_location('ghost_verified_shader_map_base',str(path))
    module=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.selftest()
    return module


def filename_to_stage(path):
    # Existing shadPS4 emitted names contain an original guest shader hash.
    name=path.stem
    m=re.search(r'(?i)(?:^|_)(?:0x)?([0-9a-f]{8,16})(?:_|$)',name)
    return m.group(1) if m else None


def find_matches(folders, limit=20000):
    found={}
    nfiles=0
    for folder in folders:
        if not folder.is_dir() or folder.is_symlink():continue
        for p in folder.rglob('*.spv'):
            if nfiles>=limit:return found,nfiles
            if not p.is_file() or p.is_symlink() or p.stat().st_size>5*1024*1024:
                continue
            nfiles+=1
            with p.open('rb') as fd:
                sha=hashlib.file_digest(fd,'sha1').hexdigest()
            for stage,target in TARGETS.items():
                if sha==target:
                    found[stage]={'sha1':sha,'path':str(p),
                                  'guest_shader_hash':filename_to_stage(p),
                                  'filename':p.name,'bytes':p.stat().st_size}
        if len(found)==len(TARGETS):break
    return found,nfiles


def prepare_source(runner):
    global RESTORED
    runner.check_clean_source()
    if not runner.BUILD.is_dir() or not (runner.BUILD/'CMakeCache.txt').is_file():
        raise RuntimeError('Original private Ghost CMake cache missing')
    txt=(runner.BUILD/'CMakeCache.txt').read_text(errors='replace')
    m=re.search(r'(?m)^CMAKE_HOME_DIRECTORY:INTERNAL=(.+)$',txt)
    if not m or Path(m.group(1)).resolve()!=runner.SOURCE.resolve():
        raise RuntimeError('Isolated build directory uses wrong source checkout')
    if any(p.is_symlink() for p in (runner.SOURCE,runner.BUILD,GHOST,PRIVATE_EXE)):
        raise RuntimeError('Private source/build/binary path must not be a symlink')

    paths=(runner.FILE,runner.VIDEO_FILE,runner.SRT_FILE,runner.PM4_HEADER,
           runner.GNM_FILE,runner.EQUEUE_FILE,runner.LIVERPOOL_FILE,PIPELINE_SOURCE)
    originals={}
    modes={}
    for p in paths:
        if not p.is_file() or p.is_symlink():
            raise RuntimeError('Invalid original source: '+str(p))
        original=p.read_bytes()
        relative=p.relative_to(runner.SOURCE)
        head_blob=subprocess.check_output(
            ['git','-C',str(runner.SOURCE),'rev-parse','HEAD:'+str(relative)],
            text=True,timeout=15).strip()
        if git_blob(p)!=head_blob:
            raise RuntimeError('Tracked private file modified: '+str(relative))
        originals[p]=original
        modes[p]=stat.S_IMODE(p.stat().st_mode)
        backup=WORK/'original'/relative
        backup.parent.mkdir(parents=True,exist_ok=True)
        backup.write_bytes(original)
        if backup.read_bytes()!=original:raise RuntimeError('Source backup verification failed')

    helpers=[runner.download_patcher(*args) for args in runner.PATCHERS]
    trace=runner.download_existing_trace()
    patched_runtime=helpers[2].update(helpers[1].modify(helpers[0].validate(originals[runner.FILE])))
    patched_c6=helpers[4].transform({
       helpers[4].GNM:originals[runner.GNM_FILE],
       helpers[4].EQUEUE:originals[runner.EQUEUE_FILE],
       helpers[4].LIVERPOOL:originals[runner.LIVERPOOL_FILE]
    })
    staged={
       runner.FILE:patched_runtime,
       runner.VIDEO_FILE:trace.patch_videoout(originals[runner.VIDEO_FILE].decode()).encode(),
       runner.SRT_FILE:trace.patch_flatten(originals[runner.SRT_FILE].decode()).encode(),
       runner.PM4_HEADER:helpers[3].transform(originals[runner.PM4_HEADER]),
       runner.GNM_FILE:patched_c6[helpers[4].GNM],
       runner.EQUEUE_FILE:patched_c6[helpers[4].EQUEUE],
       runner.LIVERPOOL_FILE:patched_c6[helpers[4].LIVERPOOL],
       PIPELINE_SOURCE:patch_pipeline(originals[PIPELINE_SOURCE])
    }
    if any(staged[p]==originals[p] for p in paths):
        raise RuntimeError('One of the expected 8 source patches produced no change')
    RESTORED=False
    say('SOURCE_STAGING_READY=8_PRIVATE_FILES')
    try:
        for p in paths:
            runner.write_atomic(p,staged[p],modes[p])
            if p.read_bytes()!=staged[p]:raise RuntimeError('Source staging readback mismatch')
        say('TEMPORARILY_PATCHED_FILES=8')
        runner.command(['cmake','--build',str(runner.BUILD),'--target','shadps4',
                        '--parallel',str(min(4,max(1,os.cpu_count() or 2)))],
                       WORK/'build.log')
        matches=[p for p in runner.BUILD.rglob('shadps4') if p.is_file() and os.access(p,os.X_OK)]
        if len(matches)!=1:raise RuntimeError('Expected exactly one private Ghost build output')
        compiled=matches[0]
        if not runner.binary_markers_present(compiled,[LOG_MARKER.encode()]):
            raise RuntimeError('Compiled candidate is missing GHOST_SPV_GUEST_MAP')
        PRIVATE_EXE.parent.mkdir(parents=True,exist_ok=True)
        staging=PRIVATE_EXE.with_name('.shadps4-guest-shader-map-staged-'+STAMP)
        shutil.copy2(compiled,staging)
        staging.chmod(0o755)
        if digest(staging)!=digest(compiled):
            raise RuntimeError('Candidate binary staged checksum mismatch')
        if PRIVATE_EXE.is_file():
            backup=PRIVATE_EXE.with_name('shadps4.before-'+STAMP)
            shutil.copy2(PRIVATE_EXE,backup)
            if digest(backup)!=digest(PRIVATE_EXE):
                raise RuntimeError('Previous private candidate backup checksum mismatch')
        os.replace(staging,PRIVATE_EXE)
        if digest(PRIVATE_EXE)!=digest(compiled):
            raise RuntimeError('Private shader mapping binary checksum mismatch')
        say('PRIVATE_SHADER_MAPPING_BINARY_READY='+str(PRIVATE_EXE))
    finally:
        problems=[]
        for p in paths:
            value=p.read_bytes() if p.is_file() and not p.is_symlink() else None
            if value==staged[p]:
                runner.write_atomic(p,originals[p],modes[p])
            elif value!=originals[p]:
                problems.append('source_modified_during_build:'+str(p))
            if p.read_bytes()!=originals[p]:
                problems.append('restoration_not_verified:'+str(p))
        RESTORED=not problems
        say('ALL_8_PRIVATE_SOURCE_FILES_RESTORED='+str(RESTORED))
        if problems:raise RuntimeError('Ghost source restoration failure: '+str(problems))


def stop_owned(runner,proc):
    if proc.poll() is not None:return 'already_exited'
    if not runner.owned(proc.pid) or os.getsid(proc.pid)!=proc.pid:
        return 'refused_unverified_process_identity'
    for sig,seconds in ((signal.SIGTERM,3),(signal.SIGKILL,4)):
        try:os.killpg(proc.pid,sig)
        except ProcessLookupError:return 'already_exited'
        try:
            proc.wait(timeout=seconds)
            return 'exited_after_'+sig.name
        except subprocess.TimeoutExpired:
            pass
    return 'SIGKILL_SENT_BUT_UNREAPED'


def play_and_map(runner):
    global GAME_RESULT
    runner.BIN=PRIVATE_EXE
    active=runner.find_emulators()
    if active:
        GAME_RESULT='other_emulator_active'
        say('REFUSED_TO_INTERRUPT_EXISTING_EMULATOR='+json.dumps(active))
        return {},0,'not_started'
    dump_dir=WORK/'spv'
    dump_dir.mkdir(parents=True,exist_ok=True)
    env=runner.env_for_x11()
    for name in ('RADV_DEBUG','GHOST_CPU_RIP_LOG','SHADPS4_CPU_ID_MODE'):
        env.pop(name,None)
    env['SHADPS4_GRAPHICS_DIAGNOSTICS']='1'
    env['SHADPS4_STARTUP_DIAGNOSTICS']='1'
    env[ENV_NAME]=str(dump_dir)
    log=WORK/'ghost-shader-map-runtime.log'
    found={}
    count=0
    cleanup='not_started'
    start=time.monotonic()
    with log.open('w') as sink:
        proc=subprocess.Popen([str(PRIVATE_EXE),'--game','CUSA11456','--fullscreen','true'],
                              cwd=str(GHOST),env=env,stdin=subprocess.DEVNULL,
                              stdout=sink,stderr=subprocess.STDOUT,start_new_session=True)
        GAME_RESULT='running'
        say('PRIVATE_SHADER_ID_TEST_STARTED_PID='+str(proc.pid))
        try:
            for iteration in range(150):  # maximum 150 seconds
                if proc.poll() is not None:
                    GAME_RESULT='exited'
                    break
                if not runner.owned(proc.pid):
                    if iteration<3:
                        time.sleep(1)
                        continue
                    GAME_RESULT='process_identity_changed'
                    break
                found,count=find_matches([dump_dir],limit=20000)
                if len(found)==len(TARGETS):
                    GAME_RESULT='both_hung_pipeline_shaders_identified'
                    say('EXACT_HUNG_PIPELINE_SHADERS_MAPPED='+json.dumps(found))
                    break
                if iteration%4==0:
                    text=log.read_text(errors='replace')
                    frames=runner.guest_trace_records(text)
                    if frames:
                        say('MAP_PROGRESS='+json.dumps({'flips':frames[-1]['flips'],
                            'shaders':count,'matches':list(found)}))
                    if 'radv: GPU hang detected' in text or 'ErrorDeviceLost' in text:
                        GAME_RESULT='gpu_hang_before_full_mapping'
                        break
                time.sleep(1)
            else:
                GAME_RESULT='time_limit'
        finally:
            found,count=find_matches([dump_dir],limit=20000)
            (WORK/'shader-map-result.json').write_text(json.dumps({
                'status':GAME_RESULT,'target_SHA1':TARGETS,
                'matched':found,'scanned_new_shaders':count,
                'runtime_seconds':round(time.monotonic()-start,2)},indent=2)+'\n')
            cleanup=stop_owned(runner,proc)
            say('OWNED_PRIVATE_CANDIDATE_CLEANUP='+cleanup)
    for stage,entry in found.items():
        p=Path(entry['path'])
        if p.is_file() and p.parent==dump_dir and digest(p):
            out=WORK/'matched_spv'
            out.mkdir(exist_ok=True)
            shutil.copy2(p,out/p.name)
    return found,count,cleanup


def selftest():
    example='''#include <ranges>
void PipelineCache::DumpShader(std::span<const u32> code, u64 hash, Shader::HwStage stage,
                               size_t perm_idx, std::string_view ext) {
    if (!EmulatorSettings.IsDumpShaders()) {
        return;
    }
}'''
    out=patch_pipeline(example.encode()).decode()
    assert out.count(LOG_MARKER)==1 and 'code.size_bytes()' in out
    assert out.count('if (!EmulatorSettings.IsDumpShaders())')==1
    assert out.count('std::getenv("GHOST_SPV_MAP_DIR")')==1
    assert out.count('#include <fstream>')==1
    for bad in (out.encode(),example.replace('if (!EmulatorSettings.IsDumpShaders())','if (false)').encode()):
        try:patch_pipeline(bad)
        except ValueError:pass
        else:raise AssertionError('Malformed/double-applied shader instrumentation accepted')
    from tempfile import TemporaryDirectory
    with TemporaryDirectory() as dir:
        base=Path(dir)
        a=base/'fs_0x1234abcd_0.spv'
        a.write_bytes(b'spvtinyfixture')
        earlier=dict(TARGETS)
        try:
            TARGETS.clear()
            TARGETS['pixel']=hashlib.sha1(a.read_bytes()).hexdigest()
            found,count=find_matches([base])
            assert count==1 and found['pixel']['guest_shader_hash']=='1234abcd'
            assert found['pixel']['filename']==a.name
        finally:
            TARGETS.clear();TARGETS.update(earlier)
    assert len(BASE_BLOB)==len(BASE_REV)==40 and len(TARGETS)==2
    assert BASE_EXE!=OTHER_EXE!=PRIVATE_EXE
    print('HUNG_SHADER_MAP_SELFTEST_PASS=exact_C++_anchor,source_guards,SPV_SHA1_match,guest_ID_parser,isolation')


def main():
    global STATUS,ERROR,GAME_RESULT
    if sys.argv[1:]==['--self-test']:
        selftest()
        return 0
    if sys.argv[1:] not in ([],):
        raise SystemExit('Usage: ghost-hung-shader-map-20261010.py [--self-test]')
    selftest()
    WORK.mkdir(parents=True,exist_ok=False)
    originals={'baseline':digest(BASE_EXE),'other':digest(OTHER_EXE)}
    runner=None
    scan_existing=0
    found={}
    cleanup='not_started'
    try:
        private_paths=[GHOST/'user'/'shaders'/'dumps', GHOST/'user'/'shader'/'dumps',
                       GHOST/'user'/'shader_dumps',
                       HOME/'.local/share/shadps4/shaders/dumps']
        found,scan_existing=find_matches(private_paths,limit=20000)
        if len(found)==len(TARGETS):
            STATUS='matched_preexisting_shader_dumps_without_launch'
            GAME_RESULT='not_started'
            say('BOTH_MATCHED_EXISTING_SHADERS='+json.dumps(found))
        else:
            if PRIVATE_EXE.is_symlink() or SOURCE.is_symlink():
                raise RuntimeError('Unsafe private executable/source symlink')
            active_before=[]
            for proc in Path('/proc').iterdir():
                if not proc.name.isdigit():continue
                try:
                    exe=os.readlink(proc/'exe').removesuffix(' (deleted)')
                    if Path(exe).name.lower()=='shadps4':active_before.append(exe)
                except (OSError,PermissionError):pass
            if active_before:
                STATUS='refused_running_emulator'
                say('EXISTING_EMULATOR_NO_KILL='+json.dumps(active_before))
            else:
                runner_file=WORK/BASE_NAME
                runner=load_runner(runner_file)
                runner.BIN=PRIVATE_EXE
                runner.WORK=WORK
                runner.OUT=OUT
                STATUS='building_isolated_shader_mapper'
                with (GHOST/'.mip-candidate-build.lock').open('a+') as lock:
                    fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
                    if runner.find_emulators():
                        raise RuntimeError('Another emulator launched during build preflight')
                    prepare_source(runner)
                    STATUS='private_candidate_ready'
                    found,scan_new,cleanup=play_and_map(runner)
                    STATUS=GAME_RESULT
        (WORK/'shader-map-result.json').write_text(json.dumps({
            'status':STATUS,'game_result':GAME_RESULT,'found':found,
            'scanned_existing_shader_files':scan_existing,
            'targets':TARGETS,'source_restored':RESTORED},indent=2)+'\n')
    except BaseException:
        ERROR=traceback.format_exc()
        STATUS='failed'
        say('FAILURE='+ERROR.splitlines()[-1])
    finally:
        (WORK/'status.json').write_text(json.dumps({
            'status':STATUS,'error':ERROR,'source_restored':RESTORED,
            'private_candidate':str(PRIVATE_EXE),'cleanup':cleanup,
            'baseline_before':originals['baseline'],'baseline_after':digest(BASE_EXE),
            'other_before':originals['other'],'other_after':digest(OTHER_EXE)},indent=2)+'\n')
        (WORK/'events.txt').write_text('\n'.join(EVENTS)+'\n')
        if (WORK/'build.log').is_file():
            log=(WORK/'build.log')
            with log.open('rb') as f:
                f.seek(0,os.SEEK_END)
                f.seek(max(0,f.tell()-160000))
                (WORK/'build-log-tail.txt').write_bytes(f.read())
        with tarfile.open(OUT,'w:gz') as archive:
            for child in sorted(WORK.iterdir()):
                if child.name in ('original','spv',BASE_NAME,'ghost-instrument-trace.py',
                                  *[p[0] for p in (runner.PATCHERS if runner else [])]):
                    continue
                archive.add(child,arcname=child.name)
        say('ARCHIVE_READY='+str(OUT))
        say('UPLOAD_THIS_FILE='+str(OUT))
        say('SSH_SESSION=REMAINS_OPEN')
    return 0 if ERROR is None and (len(found)==len(TARGETS) or STATUS=='refused_running_emulator') else 1


if __name__=='__main__':
    raise SystemExit(main())
