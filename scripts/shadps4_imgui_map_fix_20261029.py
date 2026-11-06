#!/usr/bin/env python3
"""Correct observed ImGui mapped-range validation, then resume core/sync checking.
One source file; no shader change, game workaround, validation suppression or rebase.
Uses checksum-pinned prior protection/observer helpers; restores PR94 after the trial.
"""
from __future__ import annotations
import ast
import hashlib
import importlib.util
from pathlib import Path
import re
import shlex
import subprocess
import types

PARENT_SHA = '4b77d51b315d917fc07aa75711a4e1477a26c6e74fc0df1417750027a1b226e6'
PARENT_REF = 'f3444f3d68b0f6e127e24a226ff90f7db5d1b80f'
PARENT_PATH = 'scripts/shadps4_auto_cpu_write_20261028.py'
HELPER_SHA = 'fce6ea36962179421c267fb988e18f1ce3f3176f7a60c3d6c337bba5f7f01ad5'
HELPER_REF = 'f7aad54f791b513b0023a49cd520077493a0f8b1'
HELPER_PATH = 'scripts/gow_runtime_validation_20261028.py'
PACKAGE = '0c814c38f5b69638e6d64a11ff305d6ab759d921'
PATCH_PATH = 'scripts/patchsets/imgui-mapped-upload-20261029.patch'
TEST_PATH = 'tools/development/imgui_mapped_upload_fixture.cpp.in'
PATCH_BLOB = 'ccdfdf78a0eda1a5de7bdf2cebb7486776a89da3'
TEST_BLOB = '3d80b813549f64972279ae8eda391a6801c6a0a6'
CODE = 'src/imgui/renderer/imgui_impl_vulkan.cpp'
PREIMAGE = 'd8a8fc2914ff6a054a3e007ca94a640cfc7162ca'
SOURCE = '2d76a32c69bd9a7bc322c10c6f4821fb60e7f2a4'
ELF = '58a5dcbcdce10e63c34e2e8db502fbad33269c44e466eebd58a0b50bd5ecf852'
RUNTIME_SHA = 'e2a76e02441421e780a1dd63812b18a72c944c6a0edd35f1f2738a33a02302ac'


def pinned_helper(pattern, ref, path, digest):
    scripts = Path.home() / 'projects/shadps4/scripts'
    scripts.mkdir(parents=True, exist_ok=True)
    for f in sorted(scripts.glob(pattern)):
        if f.is_file() and not f.is_symlink() and f.stat().st_size < 100000:
            raw = f.read_bytes()
            if hashlib.sha256(raw).hexdigest() == digest:
                return f, raw
    url = f'https://raw.githubusercontent.com/Chreece/shadPS4/{ref}/{path}'
    raw = subprocess.run(['curl', '-4fsSL', '--retry', '2', '--connect-timeout', '15',
                          '--max-time', '90', url], capture_output=True, check=True, timeout=280).stdout
    if hashlib.sha256(raw).hexdigest() != digest:
        raise RuntimeError('HELPER_CHECKSUM_MISMATCH=' + path)
    ast.parse(raw)
    target = scripts / ('pinned-' + digest + '.py')
    if target.exists() or target.is_symlink():
        if target.is_symlink() or not target.is_file() or target.read_bytes() != raw:
            raise RuntimeError('PINNED_HELPER_PATH_CHANGED')
    else:
        with target.open('xb') as f:
            f.write(raw)
        target.chmod(0o700)
    return target, raw


def fixture(source, template):
    handles = {'TEXTURE':'info.upload_buffer_memory','VERTEX':'frb.vertex.buffer_memory',
               'INDEX':'frb.index.buffer_memory','ATLAS':'staging.memory'}
    for label, handle in handles.items():
        maps = re.findall(r'v\.device\.mapMemory\(\s*'+re.escape(handle)+r'\s*,\s*0\s*,\s*(?:[A-Za-z_][A-Za-z_0-9]*)(?:\s*,\s*vk::MemoryMapFlags\{\})?\s*\)', source)
        flush = re.findall(r'\.memory\s*=\s*'+re.escape(handle)+r'\s*,\s*\.size\s*=\s*([A-Za-z_][A-Za-z_0-9]*)\s*,',source)
        if len(maps)!=1 or len(flush)!=1:
            raise RuntimeError('UPLOAD_STATEMENT_COUNT_CHANGED='+label)
        for marker,statement in [(f'/* MAP_{label} */','(void)'+maps[0]+';'),
                                 (f'/* FLUSH_{label} */',f'device.flush({{.memory={handle}, .size={flush[0]}}});')]:
            if template.count(marker)!=1:
                raise RuntimeError('FIXTURE_MARKER_CHANGED='+marker)
            template=template.replace(marker,statement)
    return template


def load_worker(raw, validation):
    if hashlib.sha256(raw).hexdigest() != PARENT_SHA:
        raise RuntimeError('PARENT_BYTES_CHANGED')
    text = raw.decode()
    changes = {
        'gow-auto-cpu-write-':'gow-imgui-map-fix-',
        'gow-auto-coherence-game-':'gow-vulkan-validation-game-',
        'AUTOMATIC_GPU_CPU_WRITE_COHERENCE':'IMGUI_DEDICATED_UPLOAD_MAPPING',
        'auto-cpu-write-coherence.lock':'imgui-dedicated-upload-mapping.lock',
        'Mandatory GPU-dirty buffer preservation on CPU write; read/backend/image options unchanged':
            'Full dedicated ImGui mappings with matching flush ranges; core/sync validation retained',
        'automatic-cpu-write.patch':'imgui-mapped-upload.patch',
        'baseline-memory-tracker.h':'baseline-imgui-impl-vulkan.cpp',
        'metadata_candidate':'signed_atomic_candidate',
        'NATIVE_BUFFER_CACHE_OBJECT':'NATIVE_IMGUI_BUILD',
        "cmd(['nice', '-n', '10', 'ninja', '-C', BUILD, '-j', '2',\n             'CMakeFiles/shadps4.dir/src/video_core/buffer_cache/buffer_cache.cpp.o'],":
            "cmd(['nice', '-n', '10', 'cmake', '--build', BUILD, '--target', 'shadps4', '--parallel', '2'],",
        "require(not (SRC / H).exists(), 'NEW_HEADER_COLLISION')":
            "require((SRC / M).is_file(), 'IMGUI_SOURCE_MISSING')",
        'memory: state-driven preservation of GPU-owned bytes before CPU writes':
            'imgui: map dedicated upload memory through allocation end before flush',
        'coherence-events.txt':'vulkan-validation-messages.txt',
        "if 'CpuWriteCoherence:' in line or 'Device lost during submit' in line or 'GPU_QUEUE_FAILURE' in line":
            "if 'VUID-' in line or 'SYNC-HAZARD-' in line or 'Device lost during submit' in line",
        "status['game_rc'] = game_rc":
            "status['game_rc'] = game_rc\n        status.update(validation_results)",
        "status['stage'] = 'BUILD_PASS_RUNTIME_CAPTURED' if game_rc == 0 else 'BUILD_PASS_RUNTIME_FAILED'":
            "status['stage'] = 'VALIDATION_ERROR_CAPTURED' if validation_results.get('first_validation_error') else ('BUILD_PASS_RUNTIME_CAPTURED' if game_rc == 0 else 'BUILD_PASS_RUNTIME_FAILED')",
        'REVIEW_VISIBLE_GAMEPLAY_AND_CPU_WRITE_COHERENCE_EVIDENCE_NO_PROMOTION':
            'REVIEW_OBSERVED_MAP_VUID_AND_NEXT_RUNTIME_VALIDATION; NO_GAMEPLAY_CLAIM',
        'rc = 0 if game_rc == 0 else 1':
            "rc = 0 if game_rc == 0 or validation_results.get('first_validation_error') else 1",
    }
    for before, after in changes.items():
        if text.count(before) != 1 and before not in ('gow-auto-cpu-write-', 'metadata_candidate'):
            # Global tags and protected-file labels intentionally occur more than once.
            if before not in text:
                raise RuntimeError('PARENT_ANCHOR_MISSING='+before[:80])
        if before not in text:
            raise RuntimeError('PARENT_ANCHOR_MISSING='+before[:80])
        text=text.replace(before,after)
    ast.parse(text)
    m=types.ModuleType('imgui_map_worker')
    m.__file__=str(Path(__file__).resolve())
    exec(compile(text,m.__file__,'exec'),m.__dict__)
    m.LAST,m.LAST_ELF=SOURCE,ELF
    m.LAST_BIN=m.ROOT/'GodOfWarRagnarok/tests/gow-signed-atomics-20261028T195338Z-1066378/candidate/shadps4'
    m.UPSTREAM_OLD=m.UPSTREAM='0cd6e4f2cb6617af12eb8267cca848cc41ad20d7'
    m.UPSTREAM_PATHS=set()
    m.PACKAGE,m.PATCH_PATH,m.TEST_PATH=PACKAGE,PATCH_PATH,TEST_PATH
    m.PACKAGE_BLOBS={PATCH_PATH:PATCH_BLOB,TEST_PATH:TEST_BLOB}
    m.M=m.B=CODE
    m.PREIMAGES={CODE:PREIMAGE}
    m.CHANGED={CODE}
    m.LOCKS=(*m.LOCKS,'vulkan-runtime-validation.lock')
    m.TEMPLATE=m.LOGS/'gow-vulkan-validation-20261029T044838Z-2344468-evidence/native-game-runner.sh'
    m.TEMPLATE_BLOB='a72d8b10332b45ab9ad4793d9f7bc7b7dd056830'
    results={}
    m.validation_results=results
    session={}

    def cpu_tests(old,template,work,evidence):
        for name,source,args in [('baseline',old,['--baseline']),('corrected',(m.SRC/CODE).read_text(),[])]:
            cpp=evidence/(name+'-imgui-map.cpp')
            cpp.write_text(fixture(source,template))
            exe=work/(name+'-imgui-map-test')
            m.cmd(['g++','-std=c++23','-O2','-Wall','-Wextra','-Werror','-pedantic',cpp,'-o',exe],
                  evidence/(name+'-map-build.log'),timeout=180)
            output=m.cmd([exe,*args],evidence/(name+'-map-result.txt'),timeout=30)
            expected=b'BASELINE_IMGUI_01389=REPRODUCED' if args else b'IMGUI_FULL_ALLOCATION_MAPPING=PASS'
            m.require(expected in output,'MAPPED_UPLOAD_REGRESSION_FAILED')
        env,layer=validation.prepare_layer(work,evidence)
        session['env']=env
        results['validation_layer']=layer
        print('MAPPED_RANGE_CPU_REGRESSION=PASS VALIDATION_LAYER_LOAD=PASS',flush=True)
    m.cpu_tests=cpu_tests

    def derive_runtime(target,pin,binary_sha):
        original=m.TEMPLATE.read_bytes()
        m.require(hashlib.sha256(original).hexdigest()==RUNTIME_SHA,'VALIDATION_RUNTIME_CHANGED')
        script=original.decode()
        pattern=r'^CANDIDATE=.+$'
        m.require(len(re.findall(pattern,script,re.M))==1,'CANDIDATE_ANCHOR_CHANGED')
        script=re.sub(pattern,'CANDIDATE='+shlex.quote(str(m.TREE)),script,flags=re.M)
        for before,after,count in [(SOURCE,pin,2),(ELF,binary_sha,2),
            ('VULKAN_CORE_SYNC_VALIDATION_GOW','IMGUI_MAPPED_UPLOAD_VALIDATION_GOW',1),
            ('"recorded_at":"2026-10-28"','"recorded_at":"2026-10-29"',1)]:
            m.require(script.count(before)==count,'RUNTIME_SUBSTITUTION_CHANGED='+before[:50])
            script=script.replace(before,after)
        pattern=r'^python3 -B [^\n]+ --private-config "\$WORK/user/config.json" "\$EVIDENCE/diagnostic-config-change.json"$'
        m.require(len(re.findall(pattern,script,re.M))==1,'CONFIG_HOOK_CHANGED')
        replacement='python3 -B '+shlex.quote(str(Path(validation.__file__).resolve()))+' --private-config "$WORK/user/config.json" "$EVIDENCE/diagnostic-config-change.json"'
        script=re.sub(pattern,replacement,script,flags=re.M)
        m.require('--seconds 118' in script and 'shutil.copy2(old,dest)' in script and
                  'GOW_COHERENCE_PARENT_LOCKS' in script,'RUNTIME_GUARDS_MISSING')
        target.write_text(script);m.cmd(['bash','-n',target])
    m.derive_runtime=derive_runtime

    def run_game(runner,tag,game_log):
        evidence=runner.parent
        env=session['env'].copy()
        env.update(GOW_COHERENCE_PARENT_LOCKS='YES',
            VK_KHRONOS_VALIDATION_DEBUG_ACTION='VK_DBG_LAYER_ACTION_LOG_MSG',
            VK_KHRONOS_VALIDATION_LOG_FILENAME=str(evidence/'validation-layer.log'),
            VK_KHRONOS_VALIDATION_ENABLE_MESSAGE_LIMIT='true',
            VK_KHRONOS_VALIDATION_DUPLICATE_MESSAGE_LIMIT='1')
        work=m.ROOT/'GodOfWarRagnarok/tests'/('playtest-'+tag)
        rc,first=validation.monitored_run(runner,tag,work,game_log,env,m.BIN,evidence)
        results['first_validation_error']=first
        results['intentional_validation_stop']=first is not None
        text=''
        for path in (evidence/'validation-layer.log',work/'evidence/game/full-console.log'):
            if path.is_file() and path.stat().st_size<64*1024**2:
                text+='\n'+path.read_text(errors='replace')
        results['validation_ids']=sorted(set(re.findall(r'(?:VUID-[A-Za-z0-9_-]+|SYNC-HAZARD-[A-Z_-]+)',text)))
        results['observed_mapping_vuid_reported']='VUID-VkMappedMemoryRange-size-01389' in text
        results['device_loss_reported']='Device lost during submit' in text
        results['runtime_validation_enabled']=all(s in text for s in
            ('Enabled instance layers: VK_LAYER_KHRONOS_validation','Vulkan vkValidationCore: true','Vulkan vkValidationSync: true'))
        return rc
    m.run_game=run_game
    return m


def main():
    _,raw=pinned_helper('gow-auto-write-*.py',PARENT_REF,PARENT_PATH,PARENT_SHA)
    path,data=pinned_helper('gow-vk-validation-*.py',HELPER_REF,HELPER_PATH,HELPER_SHA)
    spec=importlib.util.spec_from_file_location('pinned_vulkan_validation',path)
    if spec is None or spec.loader is None:
        raise RuntimeError('HELPER_IMPORT_FAILED')
    helper=importlib.util.module_from_spec(spec);spec.loader.exec_module(helper)
    return load_worker(raw,helper).main()


if __name__=='__main__':
    raise SystemExit(main())
