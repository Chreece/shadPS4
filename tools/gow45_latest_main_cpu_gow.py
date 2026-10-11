#!/usr/bin/env python3
"""GoW Stage45: one persistent build from fresh upstream main + pinned CPU + all Oct 11 GoW.

Inputs are the locally preserved Stage44 sources (24 historical GoW stages plus
mask and PageManager recovery) and the pinned CPU commit 3965a497. Git merges
new upstream main using actual shared ancestry, not file replacement. Any
nontrivial merge conflict fails with an evidence archive; no unverified conflict
resolution is performed. The original GoW, Ghost, ES-DE and SSH are untouched.
"""
from __future__ import annotations
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tarfile
import time
import traceback

HOME = Path.home()
ROOT = HOME / 'projects/shadps4/GodOfWarRagnarok'
ADV = ROOT / 'gow-stage25-restored-20261011-7bpufvsl/stage25-cpu-gpu-oct11-recovered'
ADV_SOURCE = ADV / 'source'
ADV_BINARY = ADV / 'build/shadps4'
ORIGINAL_SOURCE = ROOT / 'source'
ORIGINAL_BINARY = ROOT / 'build/shadps4'
LOGS = HOME / 'projects/shadps4/logs'
CPU_SHA = '3965a497532edb7b18027692ab04eb97fa4c145b'
ORIG_BINARY_SHA = '8de2106a3e0d410904d9c8d6a2903ba6f1f65986af3c848b0bf274a4f4b45c8e'
ADV_BINARY_SHA = 'e27e542076000c0d92ef04e38169a9c1d065a88b1d42e237c5832d37018fec10'
UPSTREAM_URL = 'https://github.com/shadps4-emu/shadPS4.git'
CPU_URL = 'https://github.com/Chreece/shadPS4.git'
UPSTREAM_LAST_VERIFIED = '995e37e4e77ea9f3ec69367f9ec00867201ca191'
ALLOWED_GOW = {
    'src/core/libraries/kernel/process.cpp',
    'src/core/libraries/videoout/driver.cpp',
    'src/shader_recompiler/backend/spirv/emit_spirv.cpp',
    'src/shader_recompiler/backend/spirv/emit_spirv_context_get_set.cpp',
    'src/shader_recompiler/frontend/translate/data_share.cpp',
    'src/shader_recompiler/ir/passes/flatten_extended_userdata_pass.cpp',
    'src/shader_recompiler/ir/passes/resource_patching_pass.cpp',
    'src/shader_recompiler/ir/passes/shader_info_collection_pass.cpp',
    'src/video_core/renderer_vulkan/vk_pipeline_cache.cpp',
    'src/video_core/renderer_vulkan/vk_presenter.cpp',
    'src/video_core/renderer_vulkan/vk_rasterizer.cpp',
}
REQUIRED_GOW_MARKERS = {
    'GOW_9A_CHAIN_GPU_COMPLETE', 'GOW_0C_CHAIN_GPU_COMPLETE',
    'GOW_5367_GPU_AB_COMPLETE', 'GOW_INDIRECT_GPU_CAPTURE',
    'GOW43_5367_MASK', 'GOW_SRT_TOPO_PLAN', 'GOW_FRAME_SOURCE_DIR',
    'SHADPS4_GOW_UPSTREAM_CHAIN_ONCE', 'SHADPS4_GOW_5367_ONESHOT_GPU_AB',
    'SHADPS4_GOW_COMPUTE_CANARIES',
}
STAMP = dt.datetime.now().strftime('%Y%m%d-%H%M%S')
OUT = LOGS / f'gow45-main-cpu-gow-{STAMP}'
NEW = ROOT / 'builds' / f'gow45-main-cpu-gow-{STAMP}'
SOURCE = NEW / 'source'
BUILD = NEW / 'build'
STATE = {'stage': 45, 'purpose': 'latest upstream main + pinned CPU + complete Oct11 GoW Stage44',
         'upstream_last_verified': UPSTREAM_LAST_VERIFIED, 'cpu_base_sha': CPU_SHA,
         'advanced_binary_sha': ADV_BINARY_SHA, 'original_binary_sha': ORIG_BINARY_SHA,
         'source': str(SOURCE), 'build': str(BUILD), 'phase': 'INITIAL',
         'launches_game': False, 'modifies_existing_emulators': False,
         'gpu_fix_set': 'Oct11 Stage25 stages 0..23 + Stage43 5367 mask + Stage44 page map',
         'network_mode': 'IPv4 only via VPN; git HTTPS using HTTP/1.1',
         'upstream_merge_conflicts_auto_resolved': False}


def say(message):
    print('GOW45 ' + str(message), flush=True)


def sha(path):
    h = hashlib.sha256()
    with open(path,'rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def git(*argv):
    return ['git','-C',str(SOURCE),*map(str,argv)]


def run(argv, *, file=None, timeout=None, check=True, cwd=None, retries=0):
    argv = list(map(str,argv))
    env = os.environ.copy()
    env['GIT_TERMINAL_PROMPT'] = '0'
    env['GIT_HTTP_LOW_SPEED_LIMIT'] = '128'
    env['GIT_HTTP_LOW_SPEED_TIME'] = '120'
    # The user's VPN has IPv4 only. Git's libcurl uses IPv4 when no usable
    # IPv6 route exists; HTTPS 1.1 avoids some VPN HTTP/2 intermediaries.
    if argv and argv[0] == 'git':
        argv = ['git','-c','http.version=HTTP/1.1',*argv[1:]]
    if not file:
        last = None
        for attempt in range(retries + 1):
            p = subprocess.run(argv,cwd=cwd,capture_output=True,text=True,
                               errors='replace',env=env,timeout=timeout)
            last = p
            if p.returncode == 0:
                return p.stdout
            if attempt < retries:
                say(f'NETWORK_RETRY={attempt+1} command={argv[0]}')
                time.sleep(min(3 + attempt*4, 11))
        if check:
            raise RuntimeError(f'command exit={last.returncode}: {argv[:9]}\n'
                               f'stderr={last.stderr[-4000:]}\nstdout={last.stdout[-1400:]}')
        return last.stdout
    file = Path(file)
    file.parent.mkdir(parents=True,exist_ok=True)
    last_code = None
    for attempt in range(retries + 1):
        with file.open('a',encoding='utf-8',errors='replace') as log:
            log.write('\n--- COMMAND: ' + ' '.join(argv[:15]) + ' ---\n')
            log.flush()
            proc = subprocess.Popen(argv,cwd=cwd,stdout=subprocess.PIPE,
                                    stderr=subprocess.STDOUT,stdin=subprocess.DEVNULL,
                                    text=True,errors='replace',bufsize=1,env=env)
            line_count = 0
            for line in proc.stdout:
                log.write(line)
                line_count += 1
                if ('FAILED:' in line or 'CMake Error' in line or
                        re.search(r'(^|\s)(fatal:|error:)',line,re.I)):
                    say(line.rstrip()[:260])
                elif re.match(r'^\[\d+/\d+\]',line) and line_count % 125 == 0:
                    say(line.rstrip()[:220])
                if line_count % 150 == 0:
                    log.flush()
            last_code = proc.wait(timeout=timeout)
            log.write('EXIT_CODE='+str(last_code)+'\n')
        if last_code == 0:
            return 0
        if attempt < retries:
            say(f'NETWORK_RETRY={attempt+1}; see {file}')
            time.sleep(min(3 + attempt*4, 11))
    if check:
        lines = file.read_text(errors='replace').splitlines()
        raise RuntimeError(f'command failed exit={last_code}: {argv[:10]}\n'
                           f'LOG={file}\nLAST_LINES:\n'+'\n'.join(lines[-45:]))
    return last_code


def mark(phase):
    STATE['phase'] = phase
    say('PHASE='+phase)
    OUT.mkdir(parents=True,exist_ok=True)
    (OUT/'manifest.json').write_text(json.dumps(STATE,indent=2,ensure_ascii=False)+'\n')


def check_preconditions():
    for path in (ROOT,ADV,ADV_SOURCE,ADV_BINARY,ORIGINAL_SOURCE,ORIGINAL_BINARY):
        if path.is_symlink() or not path.exists():
            raise RuntimeError('Expected non-symlink prerequisite missing: '+str(path))
    if not (ADV_SOURCE/'.git').exists():
        raise RuntimeError('Missing preserved Stage44 source Git metadata')
    if sha(ADV_BINARY) != ADV_BINARY_SHA or sha(ORIGINAL_BINARY) != ORIG_BINARY_SHA:
        raise RuntimeError('Stage44 or original executable changed; refusing to overwrite')
    if run(['git','-C',str(ADV_SOURCE),'rev-parse','HEAD']).strip() != CPU_SHA:
        raise RuntimeError('Advanced source not based on pinned CPU commit')
    if run(['git','-C',str(ORIGINAL_SOURCE),'rev-parse','HEAD']).strip() != CPU_SHA:
        raise RuntimeError('Original CPU source commit differs from pinned baseline')
    current = set(run(['git','-C',str(ADV_SOURCE),'diff','--name-only']).splitlines())
    if current != ALLOWED_GOW:
        raise RuntimeError('Advanced tracked changes differ from expected 11 GoW files: '
                           +str(sorted(current.symmetric_difference(ALLOWED_GOW))))
    text = '\n'.join((ADV_SOURCE/p).read_text(errors='replace') for p in sorted(ALLOWED_GOW))
    for x in REQUIRED_GOW_MARKERS:
        if x not in text:
            raise RuntimeError('Stage44 source lacks proven GPU marker '+x)
    if 'FindILsb32' not in (ADV_SOURCE/'src/shader_recompiler/ir/passes/flatten_extended_userdata_pass.cpp').read_text():
        raise RuntimeError('Historical SRT fix missing')
    if 'force_align_arg_pointer' not in (ADV_SOURCE/'src/core/libraries/kernel/process.cpp').read_text():
        raise RuntimeError('Historical ABI fix missing')
    if 'page_manager.MapMemory(addr, size);' not in (ADV_SOURCE/'src/video_core/renderer_vulkan/vk_rasterizer.cpp').read_text():
        raise RuntimeError('Stage44 PageManager boundary absent')
    if shutil.disk_usage(ROOT).free < 18 * 1024**3:
        raise RuntimeError('Less than 18 GiB free; no new build started')
    for tool in ('git','cmake','ninja','cp','tar','sha256sum'):
        if not shutil.which(tool):
            raise RuntimeError('Missing build prerequisite: '+tool)
    STATE['historical_gow_files']=sorted(current)
    STATE['source_original_checksum_pre']=sha(ORIGINAL_BINARY)
    STATE['stage44_source_hashes_pre']={p:sha(ADV_SOURCE/p) for p in sorted(current)}


def get_function(text,name):
    match=re.search(r'(?m)^void Rasterizer::'+re.escape(name)+r'\([^\n]*\)\s*\{',text)
    if not match:
        raise RuntimeError('Renderer function not uniquely located: '+name)
    if len(re.findall(r'(?m)^void Rasterizer::'+re.escape(name)+r'\(',text))!=1:
        raise RuntimeError('Renderer function duplicated: '+name)
    depth=1; pos=match.end()
    while pos < len(text) and depth:
        if text[pos]=='{': depth+=1
        elif text[pos]=='}': depth-=1
        pos+=1
    if depth!=0:
        raise RuntimeError('Unclosed renderer function '+name)
    return text[match.start():pos],match.start(),pos


def reconcile_renderer(upstream):
    path=SOURCE/'src/video_core/renderer_vulkan/vk_rasterizer.cpp'
    source=path.read_text()
    new=source
    repairs=[]
    fn,_,_=get_function(new,'MapMemory')
    if 'page_manager.MapMemory(addr, size);' not in fn:
        if not fn.startswith('void Rasterizer::MapMemory(VAddr addr, u64 size) {'):
            raise RuntimeError('MapMemory upstream structure changed; no unsafe rewrite')
        new=new.replace('void Rasterizer::MapMemory(VAddr addr, u64 size) {',
                        'void Rasterizer::MapMemory(VAddr addr, u64 size) {\n'
                        '    page_manager.MapMemory(addr, size);',1)
        repairs.append('Restore PageManager::MapMemory call')
    if 'void Rasterizer::ProtectMemory(' not in new:
        if 'void ProtectMemory(' not in (SOURCE/'src/video_core/renderer_vulkan/vk_rasterizer.h').read_text():
            raise RuntimeError('CPU ProtectMemory declaration missing; cannot bridge')
        marker='void Rasterizer::UnmapMemory(VAddr addr, u64 size) {'
        if new.count(marker)!=1 or 'void Rasterizer::RegisterMemory(' not in new:
            raise RuntimeError('Missing Rasterizer memory-map insertion anchor')
        addition=('void Rasterizer::ProtectMemory(VAddr addr, u64 size, Core::MemoryPermission perms) {\n'
                  '    page_manager.ProtectMemory(addr, size, perms);\n}\n\n')
        new=new.replace(marker,addition+marker,1)
        repairs.append('Restore CPU/GPU ProtectMemory entrypoint')
    # Historical GPU instrumentation does not alter BindIndexBuffer's semantics.
    # Reuse this ONE validated latest-main function verbatim if older GoW
    # recovery removed the upstream maintenance6/null-index workaround.
    upstream_raster=run(git('show',upstream+':src/video_core/renderer_vulkan/vk_rasterizer.cpp'))
    current_fn,lo,hi=get_function(new,'BindIndexBuffer')
    upstream_fn,_,_=get_function(upstream_raster,'BindIndexBuffer')
    if 'GetNullIndexBuffer()' in upstream_fn and 'GetNullIndexBuffer()' not in current_fn:
        if any(word in current_fn for word in ('GOW_', 'SHADPS4_GOW_')):
            raise RuntimeError('GoW has instrumented BindIndexBuffer; refusing replacement')
        new=new[:lo]+upstream_fn+new[hi:]
        repairs.append('Restore latest-main null index buffer in BindIndexBuffer')
    # The latest upstream DrawIndirect initializer includes is_indirect. Keep
    # that important semantics even when GoW's older rasterizer is replayed.
    upstream_has_indirect='.is_indirect = true' in upstream_raster
    if upstream_has_indirect and '.is_indirect = true' not in new:
        tag='        .instance_sgpr_offset = instance_sgpr_offset,\n'
        old='const DrawIndirectParams params = {'
        if new.count(old)!=1:
            raise RuntimeError('DrawIndirectParams initializer ambiguous')
        start=new.index(old);end=new.index('};',start)
        part=new[start:end]
        if part.count(tag)!=1:
            raise RuntimeError('GoW DrawIndirectParams layout differs from current main')
        part=part.replace(tag,tag+'        .is_indirect = true,\n',1)
        new=new[:start]+part+new[end:]
        repairs.append('Restore latest-main DrawIndirect is_indirect flag')
    if new != source:
        path.write_text(new)
    STATE['renderer_contract_repairs']=repairs
    (OUT/'renderer-repairs.txt').write_text('\n'.join(repairs or ['No additional repair needed'])+'\n')


def preserve_semantics(upstream):
    marker_source='\n'.join((SOURCE/p).read_text(errors='replace') for p in sorted(ALLOWED_GOW))
    marker_orig='\n'.join((ADV_SOURCE/p).read_text(errors='replace') for p in sorted(ALLOWED_GOW))
    for marker in REQUIRED_GOW_MARKERS:
        if marker not in marker_source:
            raise RuntimeError('Merge lost GoW milestone '+marker)
    names=set(re.findall(r'\b(?:SHADPS4_)?GOW_[A-Z0-9_]{4,}\b',marker_orig))
    dropped=sorted(name for name in names if name not in marker_source)
    if dropped:
        raise RuntimeError('Merge lost historical GoW instrumentation: '+repr(dropped[:30]))
    src=SOURCE
    checks={
       'upstream_main_in_history': False,
       'cpu_commit_in_history': False,
       'cpu_translation': (src/'src/core/cpu_id_translation/launcher.cpp').is_file() and 'ENABLE_CPU_ID_TRANSLATION' in (src/'CMakeLists.txt').read_text(),
       'cpu_affinity': (src/'src/core/cpu_affinity.cpp').is_file() and (src/'src/core/cpu_id.cpp').is_file(),
       'cpu_protection': 'guest_perms' in (src/'src/video_core/page_manager.cpp').read_text(),
       'cpu_abi': 'force_align_arg_pointer' in (src/'src/core/libraries/kernel/process.cpp').read_text(),
       'gow_srt': 'FindILsb32' in (src/'src/shader_recompiler/ir/passes/flatten_extended_userdata_pass.cpp').read_text(),
       'main_fp64': 'case IR::Opcode::FPAbs64:' in (src/'src/shader_recompiler/ir/passes/shader_info_collection_pass.cpp').read_text(),
       'map_contract': 'page_manager.MapMemory(addr, size);' in (src/'src/video_core/renderer_vulkan/vk_rasterizer.cpp').read_text(),
       'protect_contract': 'void Rasterizer::ProtectMemory(' in (src/'src/video_core/renderer_vulkan/vk_rasterizer.cpp').read_text(),
       'main_null_index': 'GetNullIndexBuffer()' in (src/'src/video_core/renderer_vulkan/vk_rasterizer.cpp').read_text(),
       'main_indirect_flag': '.is_indirect = true' in (src/'src/video_core/renderer_vulkan/vk_rasterizer.cpp').read_text(),
    }
    # The new upstream ResourcePatchBuffer includes dw2_shift metadata. It
    # is important that replaying the older GoW code doesn't silently drop it.
    upstream_resource=run(git('show',upstream+':src/shader_recompiler/ir/passes/resource_patching_pass.cpp'))
    if 'post_op_dw2_shift' in upstream_resource:
        checks['main_dw2_shift']='post_op_dw2_shift' in (src/'src/shader_recompiler/ir/passes/resource_patching_pass.cpp').read_text()
    # git merge-base --is-ancestor returns empty stdout for both true/false:
    for label,commit in [('upstream_main_in_history',upstream),('cpu_commit_in_history',CPU_SHA)]:
        p=subprocess.run(git('merge-base','--is-ancestor',commit,'HEAD'),capture_output=True)
        checks[label]=p.returncode==0
    STATE['preservation_checks']=checks
    STATE['gow_marker_count_preserved']=len(names)
    (OUT/'preservation-checks.json').write_text(json.dumps(checks,indent=2)+'\n')
    if not all(checks.values()):
        raise RuntimeError('Source merge failed preservation checks: '+repr([x for x,y in checks.items() if not y]))


def copy_cache():
    old=ADV/'build'
    for name in ('_deps','cpu-id-runtime-source'):
        source=old/name;target=BUILD/name
        if not source.is_dir() or target.exists():
            continue
        say('REUSE_LOCAL_BUILD_DEPENDENCY_CACHE='+name)
        run(['cp','-a','--reflink=auto',str(source),str(target)],file=OUT/'cache-copy.log')
    STATE['cache_copy_done']=True


def configure_and_build():
    BUILD.mkdir(parents=True,exist_ok=True)
    copy_cache()
    cc=shutil.which('ccache')
    flags=['-DCMAKE_BUILD_TYPE=Release','-DENABLE_CPU_ID_TRANSLATION=ON',
           '-DENABLE_TESTS=OFF','-DENABLE_UPDATER=OFF']
    if cc:
        flags.extend(['-DCMAKE_C_COMPILER_LAUNCHER='+cc,'-DCMAKE_CXX_COMPILER_LAUNCHER='+cc])
    say('CMAKE_CONFIGURE_START=YES')
    run(['cmake','-S',str(SOURCE),'-B',str(BUILD),'-G','Ninja',*flags],file=OUT/'configure.log')
    say('CMAKE_BUILD_START=YES (ONE BUILD, NO GAME LAUNCH)')
    run(['cmake','--build',str(BUILD),'--target','shadps4','--parallel','4'],file=OUT/'build.log')
    exe=BUILD/'shadps4'
    runtime=BUILD/'cpu-id-runtime'
    required=('bin64/drrun','libshadps4_cpu_id.so','lib64/release/libdynamorio.so')
    if not exe.is_file() or not os.access(exe,os.X_OK):
        raise RuntimeError('Emulator binary not produced')
    missing=[part for part in required if not (runtime/part).is_file()]
    if missing:
        raise RuntimeError('Built CPU identity runtime incomplete: '+repr(missing))
    STATE['new_binary_sha256']=sha(exe)
    STATE['cpu_id_runtime_bundled']=True
    STATE['build_pass']=True
    create_launcher()


def create_launcher():
    for mode in ('guarded','normal'):
        script=NEW/('play-'+mode+'.sh')
        lines=['#!/usr/bin/env bash','set -Eeuo pipefail',
               'ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"',
               'BIN="$ROOT/build/shadps4"',
               'test -x "$BIN"',
               'export DISPLAY="${DISPLAY:-:0}"']
        lines += ['for k in $(env | sed -n "s/^\\(SHADPS4_GOW_[A-Za-z0-9_]*\\)=.*/\\1/p"); do unset "$k"; done']
        if mode=='guarded':
            # This matches the recorded October 11 safe chain, not playable
            # normal GPU execution. Do not silently activate full compute.
            lines += ['export SHADPS4_ENABLE_IPC=false',
                      'export SHADPS4_GOW_SUPPRESS_GPU_COMPUTE=1',
                      'export SHADPS4_GOW_SRT_AUTO_ROOTS=2',
                      'export SHADPS4_GOW_COMPUTE_CANARIES=1',
                      'export SHADPS4_GOW_UPSTREAM_CHAIN_ONCE=1',
                      'export SHADPS4_GOW_5367_ONESHOT_GPU_AB=1',
                      'export SHADPS4_GOW_INDIRECT_GPU_ARGS=1',
                      'export SHADPS4_GOW_DIAGNOSTIC_GDS_NONEXECUTING=1']
        else:
            lines += ['# Normal gameplay attempt: disables historical GoW diagnostic',
                      '# compute suppression. It can trigger a GPU device loss.']
        lines += ['cd -- "$ROOT"',
                  '"$BIN" --cpu-id-mode auto --game CUSA34384 --fullscreen true "$@"']
        script.write_text('\n'.join(lines)+'\n')
        script.chmod(0o750)
    STATE['launch_guarded']=str(NEW/'play-guarded.sh')
    STATE['launch_normal']=str(NEW/'play-normal.sh')


def main():
    successful=False
    try:
        mark('PREFLIGHT')
        check_preconditions()
        mark('PRESERVE_SOURCE')
        if NEW.exists():
            raise RuntimeError('This version already exists; refusing overwrite: '+str(NEW))
        SOURCE.parent.mkdir(parents=True,exist_ok=False)
        say('COPYING_ADVANCED_STAGE44_SOURCE=YES')
        run(['cp','-a','--reflink=auto',str(ADV_SOURCE),str(SOURCE)],file=OUT/'copy.log')
        if run(git('rev-parse','HEAD')).strip()!=CPU_SHA:
            raise RuntimeError('Copied advanced Git source is not the CPU baseline')
        patch=run(git('diff','--binary','--',*sorted(ALLOWED_GOW)))
        (OUT/'stage44-proven-gow-source.patch').write_text(patch)
        STATE['stage44_patch_sha256']=sha(OUT/'stage44-proven-gow-source.patch')
        if len(patch)<200000:
            raise RuntimeError('Full Stage44 source diff unexpectedly too small')
        # Local commits occur ONLY in the new copy, never in user repositories.
        run(git('config','user.name','GoW Local Build'))
        run(git('config','user.email','gow-local@invalid.example'))
        run(git('switch','-c','gow45-latest-main-cpu-gow'))
        run(git('add','--',*sorted(ALLOWED_GOW)))
        run(git('commit','-m','local(gow): preserve Oct11 Stage25 24 stages and Stage43/44 fixes'))
        gow_commit=run(git('rev-parse','HEAD')).strip()
        STATE['gow_source_commit']=gow_commit
        mark('FETCH_LATEST_UPSTREAM')
        # New workspace starts from the exact CPU baseline, not a naked main
        # checkout. Deepen both branches until the known common ancestor is
        # present so Git can do a real three-way merge.
        remote=run(git('remote','get-url','origin'),check=False).strip()
        if remote != CPU_URL:
            if remote:
                run(git('remote','set-url','origin',CPU_URL))
            else:
                run(git('remote','add','origin',CPU_URL))
        existing_upstream=run(git('remote','get-url','upstream'),check=False).strip()
        if existing_upstream:
            if existing_upstream != UPSTREAM_URL:
                run(git('remote','set-url','upstream',UPSTREAM_URL))
        else:
            run(git('remote','add','upstream',UPSTREAM_URL))
        is_shallow = run(git('rev-parse','--is-shallow-repository')).strip() == 'true'
        cpu_fetch = git('fetch','--no-tags',
                        *(['--deepen=35'] if is_shallow else []),
                        'origin','refs/heads/draft/automatic-cpu-id-fallback-20261009')
        run(cpu_fetch,file=OUT/'cpu-fetch.log',retries=2)
        run(git('fetch','--no-tags','--depth=80','upstream','refs/heads/main'),
            file=OUT/'upstream-fetch.log',retries=2)
        main_sha=run(git('rev-parse','FETCH_HEAD')).strip()
        if not re.fullmatch(r'[0-9a-f]{40}',main_sha):
            raise RuntimeError('Failed to resolve upstream main commit')
        STATE['latest_main_sha']=main_sha
        say('LATEST_UPSTREAM_MAIN='+main_sha)
        ancestor=run(git('merge-base',CPU_SHA,main_sha),check=False).strip()
        if not ancestor:
            say('DEEPEN_BOTH_HISTORIES=YES')
            run(git('fetch','--no-tags',
                    *(['--deepen=120'] if run(git('rev-parse','--is-shallow-repository')).strip() == 'true' else []),
                    'origin','refs/heads/draft/automatic-cpu-id-fallback-20261009'),
                file=OUT/'cpu-fetch.log',retries=2)
            run(git('fetch','--no-tags','--deepen=120','upstream','refs/heads/main'),
                file=OUT/'upstream-fetch.log',retries=2)
            ancestor=run(git('merge-base',CPU_SHA,main_sha),check=False).strip()
        if not ancestor:
            raise RuntimeError('Cannot find historical CPU/main merge base; no cherry-picks attempted')
        STATE['common_ancestor_sha']=ancestor
        mark('REAL_THREE_WAY_MERGE')
        code=run(git('merge','--no-ff','--no-edit',main_sha),file=OUT/'merge.log',check=False)
        if code:
            conflicted=run(git('diff','--name-only','--diff-filter=U'),check=False).splitlines()
            STATE['merge_conflicts']=conflicted
            (OUT/'unmerged-files.txt').write_text('\n'.join(conflicted)+'\n')
            # Keep the working copy and conflict files for targeted resolution.
            raise RuntimeError('CPU+GoW / latest-main merge conflicts; no source overwritten: '
                               +repr(conflicted))
        mark('RECONCILE_CPU_GPU_RENDERER_CONTRACTS')
        reconcile_renderer(main_sha)
        preserve_semantics(main_sha)
        run(git('add','--','src/video_core/renderer_vulkan/vk_rasterizer.cpp'))
        if run(git('diff','--cached','--name-only')).strip():
            run(git('commit','-m','local(gow): reconcile latest-main renderer with CPU memory mapping'))
        STATE['merged_head_sha']=run(git('rev-parse','HEAD')).strip()
        # Any dependency fetch or update applies exclusively to the copied repo.
        mark('SUBMODULES')
        run(git('submodule','sync','--recursive'),file=OUT/'submodule.log')
        # Only update when necessary; most Oct11 upstream revisions share the
        # same modules as our successful Stage44 build.
        substatus=run(git('submodule','status','--recursive'),check=False)
        (OUT/'submodules-before.txt').write_text(substatus)
        if any(row.startswith(('-','+','U')) for row in substatus.splitlines()):
            run(git('submodule','update','--init','--recursive','--jobs','2'),
                file=OUT/'submodule.log',retries=2)
        mark('BUILD')
        configure_and_build()
        if sha(ADV_BINARY)!=ADV_BINARY_SHA or sha(ORIGINAL_BINARY)!=ORIG_BINARY_SHA:
            raise RuntimeError('One of the original executables changed unexpectedly')
        STATE['original_build_preserved']=True
        STATE['advanced_stage44_preserved']=True
        STATE['source_old_cpu_preserved']=(run(['git','-C',str(ORIGINAL_SOURCE),'rev-parse','HEAD']).strip()==CPU_SHA)
        successful=True
        STATE['result']='COMBINED_BUILD_READY'
        say('RESULT=COMBINED_BUILD_READY')
        say('UPSTREAM_MAIN='+main_sha)
        say('CPU_COMMIT='+CPU_SHA)
        say('GOW_HISTORY_COMMIT='+gow_commit)
        say('BUILD='+str(BUILD/'shadps4'))
        say('GUARDED_LAUNCH='+STATE['launch_guarded'])
        say('NORMAL_LAUNCH_NOT_RUN='+STATE['launch_normal'])
        return 0
    except BaseException as error:
        STATE['result']='BUILD_NOT_COMPLETED'
        STATE['error']=str(error)
        say('ERROR='+str(error))
        OUT.mkdir(parents=True,exist_ok=True)
        (OUT/'traceback.txt').write_text(traceback.format_exc())
        return 1
    finally:
        STATE['phase']='READY' if successful else STATE.get('phase')
        for label,path in [('original_binary_after',ORIGINAL_BINARY),
                           ('stage44_binary_after',ADV_BINARY)]:
            if path.is_file():
                STATE[label]=sha(path)
        OUT.mkdir(parents=True,exist_ok=True)
        (OUT/'manifest.json').write_text(json.dumps(STATE,indent=2,ensure_ascii=False)+'\n')
        archive=Path(str(OUT)+'.tar.gz')
        try:
            with tarfile.open(archive,'w:gz') as t:
                for p in OUT.rglob('*'):
                    if p.is_file() and p.stat().st_size<25_000_000:
                        t.add(p,arcname=str(p.relative_to(LOGS)))
            say('REPORT='+str(archive))
        except Exception as exc:
            say('REPORT_PACKAGING_ERROR='+repr(exc))

if __name__=='__main__':
    sys.exit(main())