#!/usr/bin/env python3
"""Respect the observed VkBuffer limit without truncating guest memory.
Retain signed atomics and ImGui upload mapping; continue core/sync validation.
CPU fixtures exercise real arena/binding methods with recorded Vulkan calls,
not a real sparse-memory GPU. Full native compilation is required before launch.
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
PACKAGE = 'a7b1396fc7d0ba2c32af9dd67d67f8ff3e5b8947'
PATCH_PATH = 'scripts/patchsets/sparse-buffer-host-limit-20261029.patch'
TEST_PATH = 'tools/development/sparse_arena_fixture.cpp.in'
PATCH_BLOB = '078127d4c82dd44631f4437267656ad504fc2cc6'
TEST_BLOB = 'b069b44591ca557c20c0728e8fddcdb404f538e9'
CODE = 'src/video_core/buffer_cache/buffer_cache.cpp'
HEADER = 'src/video_core/buffer_cache/buffer_cache.h'
WINDOW = 'src/video_core/buffer_cache/sparse_arena_window.h'
PREIMAGES = {
    CODE: 'e4e337b3610a4f5ee4f7f8e9420f4c447c5ed39c',
    HEADER: 'c6cde61727b73262366b985c3f3e273c5d16646a',
    'src/video_core/buffer_cache/buffer.cpp': 'f601c277a1038d71c194ba691754309dd14a2977',
    'src/video_core/renderer_vulkan/vk_instance.cpp': 'f0dca48617e7f93ea082159601b8c0efa0295faa',
    'src/video_core/renderer_vulkan/vk_instance.h': 'd53ee2676464b5e5968d4a94e4c73dc7da5e4683',
}
SOURCE = '9892eb99ac79efa5be58e0a6ef97c1478ebd1fb1'
ELF = '1b603c47f6d0e8ea67ce5e611ff3b7b320dd5ba20e97d828100112ab2aab55a9'
RUNTIME_SHA = 'cc3f113df7689f0d21332f824868fcb3077b7873ba4ee64e7210c97f4fa33206'


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


def function(text, signature):
    if text.count(signature) != 1:
        raise RuntimeError('SOURCE_METHOD_CHANGED=' + signature)
    start = text.index(signature)
    pos = text.index('{', start) + 1
    depth = 1
    while depth and pos < len(text):
        depth += (text[pos] == '{') - (text[pos] == '}')
        pos += 1
    if depth:
        raise RuntimeError('SOURCE_BRACES_CHANGED=' + signature)
    return text[start:pos]


def fixture(original, corrected, header, template, baseline):
    backing = function(header, '    struct Backing : public Interval') + ';'
    methods = []
    for signature in (
        'void BufferCache::SynchronizeDmaBuffers()',
        'void BufferCache::BindBackingToArena(',
        'void BufferCache::SynchronizeSparseAliases()',
        'const Buffer* BufferCache::GetArena(',
        'void BufferCache::EnsureResident(',
    ):
        source = original if baseline and signature.startswith('const Buffer*') else corrected
        methods.append(function(source, signature))
    for marker, value in [('/* EXACT_BACKING */', backing),
                          ('/* EXACT_METHODS */', '\n\n'.join(methods))]:
        if template.count(marker) != 1:
            raise RuntimeError('FIXTURE_MARKER_CHANGED=' + marker)
        template = template.replace(marker, value)
    return template


def load_worker(raw, validation):
    if hashlib.sha256(raw).hexdigest() != PARENT_SHA:
        raise RuntimeError('PARENT_BYTES_CHANGED')
    text = raw.decode()
    changes = {
        'gow-auto-cpu-write-':'gow-sparse-buffer-limit-',
        'gow-auto-coherence-game-':'gow-vulkan-validation-game-',
        'AUTOMATIC_GPU_CPU_WRITE_COHERENCE':'SPARSE_BUFFER_HOST_LIMIT',
        'auto-cpu-write-coherence.lock':'sparse-buffer-host-limit.lock',
        'Mandatory GPU-dirty buffer preservation on CPU write; read/backend/image options unchanged':
            'Device-limited sparse views, shared backing and stable BDA ownership; validation retained',
        'automatic-cpu-write.patch':'sparse-buffer-host-limit.patch',
        'baseline-memory-tracker.h':'baseline-buffer-cache.cpp',
        'metadata_candidate':'imgui_mapping_candidate',
        'NATIVE_BUFFER_CACHE_OBJECT':'NATIVE_SPARSE_BUFFER_BUILD',
        "cmd(['nice', '-n', '10', 'ninja', '-C', BUILD, '-j', '2',\n             'CMakeFiles/shadps4.dir/src/video_core/buffer_cache/buffer_cache.cpp.o'],":
            "cmd(['nice', '-n', '10', 'cmake', '--build', BUILD, '--target', 'shadps4', '--parallel', '2'],",
        'memory: state-driven preservation of GPU-owned bytes before CPU writes':
            'buffer cache: honor host VkBuffer limits while preserving overlapping guest backing',
        'coherence-events.txt':'vulkan-validation-messages.txt',
        "if 'CpuWriteCoherence:' in line or 'Device lost during submit' in line or 'GPU_QUEUE_FAILURE' in line":
            "if 'SparseArena' in line or 'VUID-' in line or 'SYNC-HAZARD-' in line or 'Device lost during submit' in line",
        "status['game_rc'] = game_rc":
            "status['game_rc'] = game_rc\n        status.update(validation_results)",
        "status['stage'] = 'BUILD_PASS_RUNTIME_CAPTURED' if game_rc == 0 else 'BUILD_PASS_RUNTIME_FAILED'":
            "status['stage'] = 'VALIDATION_ERROR_CAPTURED' if validation_results.get('first_validation_error') else ('BUILD_PASS_RUNTIME_CAPTURED' if game_rc == 0 else 'BUILD_PASS_RUNTIME_FAILED')",
        'REVIEW_VISIBLE_GAMEPLAY_AND_CPU_WRITE_COHERENCE_EVIDENCE_NO_PROMOTION':
            'REVIEW_SPARSE_BUFFER_06409_AND_NEXT_VALIDATION; NO_GAMEPLAY_CLAIM',
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
    m=types.ModuleType('sparse_buffer_worker')
    m.__file__=str(Path(__file__).resolve())
    exec(compile(text,m.__file__,'exec'),m.__dict__)
    m.LAST,m.LAST_ELF=SOURCE,ELF
    m.LAST_BIN=m.ROOT/'GodOfWarRagnarok/tests/gow-imgui-map-fix-20261029T055754Z-2499958/candidate/shadps4'
    m.UPSTREAM_OLD=m.UPSTREAM='0cd6e4f2cb6617af12eb8267cca848cc41ad20d7'
    m.UPSTREAM_PATHS=set()
    m.PACKAGE,m.PATCH_PATH,m.TEST_PATH=PACKAGE,PATCH_PATH,TEST_PATH
    m.PACKAGE_BLOBS={PATCH_PATH:PATCH_BLOB,TEST_PATH:TEST_BLOB}
    m.M=m.B=CODE
    m.H=WINDOW
    m.PREIMAGES=PREIMAGES
    m.CHANGED=set(PREIMAGES)|{WINDOW}
    m.LOCKS=(*m.LOCKS,'vulkan-runtime-validation.lock')
    m.TEMPLATE=m.LOGS/'gow-imgui-map-fix-20261029T055754Z-2499958-evidence/native-game-runner.sh'
    m.TEMPLATE_BLOB='51db49d1feea4c414a4799b43b319be8b1c94502'
    results={}
    m.validation_results=results
    session={}

    def cpu_tests(old, template, work, evidence):
        headers = m.SRC/'externals/vulkan-headers/include'
        m.require((headers/'vulkan/vulkan.hpp').is_file(), 'REAL_VULKAN_HEADERS_MISSING')
        corrected = (m.SRC/CODE).read_text()
        header = (m.SRC/HEADER).read_text()
        for name, baseline, args in [('baseline', True, ['--baseline']),
                                      ('corrected', False, [])]:
            cpp = evidence/(name+'-sparse-arena.cpp')
            cpp.write_text(fixture(old, corrected, header, template, baseline))
            exe = work/(name+'-sparse-arena-test')
            m.cmd(['g++', '-std=c++23', '-O2', '-Wall', '-Wextra', '-Werror', '-pedantic',
                   '-DREAL_VULKAN_TYPES', '-I', headers, '-I', m.SRC/'src', cpp, '-o', exe],
                  evidence/(name+'-sparse-build.log'), timeout=180)
            output = m.cmd([exe, *args], evidence/(name+'-sparse-result.txt'), timeout=30)
            expected = b'BASELINE_SPARSE_06409=REPRODUCED' if baseline else b'SPARSE_HOST_LIMIT_AND_ALIAS_BACKING=PASS'
            m.require(expected in output, 'SPARSE_ARENA_REGRESSION_FAILED')
        env,layer=validation.prepare_layer(work,evidence)
        session['env']=env
        results['validation_layer']=layer
        print('SPARSE_ARENA_REAL_HEADER_CPU_TESTS=PASS VALIDATION_LAYER_LOAD=PASS',flush=True)
    m.cpu_tests=cpu_tests

    def derive_runtime(target,pin,binary_sha):
        original=m.TEMPLATE.read_bytes()
        m.require(hashlib.sha256(original).hexdigest()==RUNTIME_SHA,'VALIDATION_RUNTIME_CHANGED')
        script=original.decode()
        pattern=r'^CANDIDATE=.+$'
        m.require(len(re.findall(pattern,script,re.M))==1,'CANDIDATE_ANCHOR_CHANGED')
        script=re.sub(pattern,'CANDIDATE='+shlex.quote(str(m.TREE)),script,flags=re.M)
        for before,after,count in [(SOURCE,pin,2),(ELF,binary_sha,2),
            ('IMGUI_MAPPED_UPLOAD_VALIDATION_GOW','SPARSE_BUFFER_LIMIT_VALIDATION_GOW',1)]:
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
        results['observed_buffer_limit_vuid_reported']='VUID-VkBufferCreateInfo-size-06409' in text
        results['previous_mapping_vuid_reported']='VUID-VkMappedMemoryRange-size-01389' in text
        results['sparse_arena_window_log_count']=text.count('SparseArenaWindow:')
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
