#!/usr/bin/env python3
"""Correct 3D-image creation flags for independently supported 2D descriptors.
Retain the tested sparse queues and all earlier fixes; no rebase or shader change.
Creation compatibility must not require sampling when storage views are supported.
CPU policy tests use actual source expressions; native Vulkan validation is separate.
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
PACKAGE = 'd53b05a5775ce888a6a3312f6b89b2889fb4c739'
PATCH_PATH = 'scripts/patchsets/image-2d3d-creation-flags-20261029.patch'
TEST_PATH = 'tools/development/image_2d3d_flags_fixture.cpp.in'
PATCH_BLOB = '07fc45f3753c8e829ffd31cae93cbbafb1f5082c'
TEST_BLOB = '6bf98866095fec054a11658321450faba2502190'
CODE = 'src/video_core/texture_cache/image.cpp'
HEADER = 'src/video_core/renderer_vulkan/vk_instance.h'
BUFFER = CODE
PREIMAGES = {CODE: '32642ebcc552220e1cbf901d8775dfbb8d7cc91f', HEADER: '85d29384bde8aff2cdc0964364555b112f836009'}
SOURCE = '219908ba91a8fce8889fa28b6eafbad8bcee8e00'
ELF = '84a1b2b4e0d6d30595cf7063307d6c2cd29dcd9a66d95099e7f2d2eb52761f31'
RUNTIME_SHA = '2eb031216c6398c38f91f0640e595bb4f0fed60764a0539e6b06ea9f4d68fbc6'


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


def fixture(header, image, template):
    methods = function(header, 'bool Is2dViewOf3dSupported(')
    if 'bool CanCreate2dViewOf3dImage(' in header:
        methods += '\n' + function(header, 'bool CanCreate2dViewOf3dImage(')
    start = image.index('    vk::ImageCreateFlags flags{')
    end = image.index('    usage_flags = ImageUsageFlags(', start)
    for marker, value in [('/* EXACT_CAPABILITIES */', methods),
                          ('/* EXACT_IMAGE_FLAGS */', image[start:end])]:
        if template.count(marker) != 1:
            raise RuntimeError('FIXTURE_MARKER_CHANGED=' + marker)
        template = template.replace(marker, value)
    return template


def load_worker(raw, validation):
    if hashlib.sha256(raw).hexdigest() != PARENT_SHA:
        raise RuntimeError('PARENT_BYTES_CHANGED')
    text = raw.decode()
    changes = {
        'gow-auto-cpu-write-':'gow-image-view-flags-',
        'gow-auto-coherence-game-':'gow-vulkan-validation-game-',
        'AUTOMATIC_GPU_CPU_WRITE_COHERENCE':'IMAGE_2D3D_CREATION_FLAGS',
        'auto-cpu-write-coherence.lock':'image-2d3d-creation-flags.lock',
        'Mandatory GPU-dirty buffer preservation on CPU write; read/backend/image options unchanged':
            'Independent 2D-of-3D creation capability; existing queue, shader and memory corrections retained',
        'automatic-cpu-write.patch':'image-2d3d-creation-flags.patch',
        'baseline-memory-tracker.h':'baseline-image.cpp',
        'metadata_candidate':'sparse_queue_candidate',
        'NATIVE_BUFFER_CACHE_OBJECT':'NATIVE_IMAGE_VIEW_FLAGS_BUILD',
        "cmd(['nice', '-n', '10', 'ninja', '-C', BUILD, '-j', '2',\n             'CMakeFiles/shadps4.dir/src/video_core/buffer_cache/buffer_cache.cpp.o'],":
            "cmd(['nice', '-n', '10', 'cmake', '--build', BUILD, '--target', 'shadps4', '--parallel', '2'],",
        'memory: state-driven preservation of GPU-owned bytes before CPU writes':
            'image: set creation compatibility for either supported descriptor capability',
        'coherence-events.txt':'vulkan-validation-messages.txt',
        "require(not (SRC / H).exists(), 'NEW_HEADER_COLLISION')":
            "require((SRC / M).is_file(), 'IMAGE_SOURCE_MISSING')",
        "if 'CpuWriteCoherence:' in line or 'Device lost during submit' in line or 'GPU_QUEUE_FAILURE' in line":
            "if '2DViewOf3D:' in line or 'SparseQueue' in line or 'VUID-' in line or 'SYNC-HAZARD-' in line or 'Device lost during submit' in line",
        "status['game_rc'] = game_rc":
            "status['game_rc'] = game_rc\n        status.update(validation_results)",
        "status['stage'] = 'BUILD_PASS_RUNTIME_CAPTURED' if game_rc == 0 else 'BUILD_PASS_RUNTIME_FAILED'":
            "status['stage'] = 'VALIDATION_ERROR_CAPTURED' if validation_results.get('first_validation_error') else ('BUILD_PASS_RUNTIME_CAPTURED' if game_rc == 0 else 'BUILD_PASS_RUNTIME_FAILED')",
        'REVIEW_VISIBLE_GAMEPLAY_AND_CPU_WRITE_COHERENCE_EVIDENCE_NO_PROMOTION':
            'REVIEW_IMAGE_VIEW_CREATION_FLAGS_AND_NEXT_VALIDATION; NO_GAMEPLAY_CLAIM',
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
    m=types.ModuleType('image_view_flags_worker')
    m.__file__=str(Path(__file__).resolve())
    exec(compile(text,m.__file__,'exec'),m.__dict__)
    m.LAST,m.LAST_ELF=SOURCE,ELF
    m.LAST_BIN=m.ROOT/'GodOfWarRagnarok/tests/gow-sparse-queue-20261029T073422Z-2717735/candidate/shadps4'
    m.UPSTREAM_OLD=m.UPSTREAM='0cd6e4f2cb6617af12eb8267cca848cc41ad20d7'
    m.UPSTREAM_PATHS=set()
    m.PACKAGE,m.PATCH_PATH,m.TEST_PATH=PACKAGE,PATCH_PATH,TEST_PATH
    m.PACKAGE_BLOBS={PATCH_PATH:PATCH_BLOB,TEST_PATH:TEST_BLOB}
    m.M=CODE
    m.B=BUFFER
    m.H=HEADER
    m.PREIMAGES=PREIMAGES
    m.CHANGED=set(PREIMAGES)
    m.LOCKS=(*m.LOCKS,'vulkan-runtime-validation.lock')
    m.TEMPLATE=m.LOGS/'gow-sparse-queue-20261029T073422Z-2717735-evidence/native-game-runner.sh'
    m.TEMPLATE_BLOB='7f6b3c78abc46815e1ba480620ac3e3d1846e10a'
    results={}
    m.validation_results=results
    session={}

    def cpu_tests(old, template, work, evidence):
        headers = m.SRC/'externals/vulkan-headers/include'
        m.require((headers/'vulkan/vulkan.hpp').is_file(), 'REAL_VULKAN_HEADERS_MISSING')
        old_header = m.git_bytes('show', m.LAST+':'+HEADER).decode()
        corrected_header = (m.SRC/HEADER).read_text()
        corrected = (m.SRC/CODE).read_text()
        m.require(function(old_header, 'bool Is2dViewOf3dSupported(') ==
                  function(corrected_header, 'bool Is2dViewOf3dSupported('),
                  'FULL_DESCRIPTOR_CAPABILITY_QUERY_MUST_REMAIN_UNCHANGED')
        # The creation fix must not enable an unsupported sampled-view feature.
        instance = (m.SRC/'src/video_core/renderer_vulkan/vk_instance.cpp').read_text()
        m.require('.image2DViewOf3D = image_2d_view_of_3d_features.image2DViewOf3D' in instance and
                  '.sampler2DViewOf3D = image_2d_view_of_3d_features.sampler2DViewOf3D' in instance,
                  'DEVICE_FEATURE_ENABLEMENT_CHANGED')
        for name, h, code, args in [('baseline', old_header, old, ['--baseline']),
                                     ('corrected', corrected_header, corrected, [])]:
            cpp = evidence/(name+'-image-flags.cpp')
            cpp.write_text(fixture(h, code, template))
            exe = work/(name+'-image-flags-test')
            m.cmd(['g++', '-std=c++23', '-O2', '-Wall', '-Wextra', '-Werror', '-pedantic',
                   '-DREAL_VULKAN_TYPES', '-I', headers, cpp, '-o', exe],
                  evidence/(name+'-image-flags-build.log'), timeout=180)
            output = m.cmd([exe, *args], evidence/(name+'-image-flags-result.txt'), timeout=30)
            expected = b'BASELINE_IMAGE_VIEW_07796=REPRODUCED' if args else b'IMAGE_2D3D_CREATION_FLAGS=PASS'
            m.require(expected in output, 'IMAGE_CREATION_FLAG_REGRESSION_FAILED')
        env, layer = validation.prepare_layer(work, evidence)
        session['env'] = env
        results['validation_layer'] = layer
        print('IMAGE_CREATION_FLAGS_REAL_HEADER_CPU_TESTS=PASS VALIDATION_LAYER_LOAD=PASS', flush=True)
    m.cpu_tests=cpu_tests

    def derive_runtime(target,pin,binary_sha):
        original=m.TEMPLATE.read_bytes()
        m.require(hashlib.sha256(original).hexdigest()==RUNTIME_SHA,'VALIDATION_RUNTIME_CHANGED')
        script=original.decode()
        pattern=r'^CANDIDATE=.+$'
        m.require(len(re.findall(pattern,script,re.M))==1,'CANDIDATE_ANCHOR_CHANGED')
        script=re.sub(pattern,'CANDIDATE='+shlex.quote(str(m.TREE)),script,flags=re.M)
        for before,after,count in [(SOURCE,pin,2),(ELF,binary_sha,2),
            ('SPARSE_QUEUE_CAPABILITY_VALIDATION_GOW','IMAGE_2D3D_CREATION_FLAGS_VALIDATION_GOW',1)]:
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
        results['observed_image_creation_vuid_reported'] = 'VUID-VkDescriptorImageInfo-imageView-07796' in text
        results['descriptor_feature_vuids_reported'] = [v for v in results['validation_ids']
            if v in ('VUID-VkDescriptorImageInfo-descriptorType-06713',
                     'VUID-VkDescriptorImageInfo-descriptorType-06714')]
        results['image_view_feature_messages'] = [line for line in text.splitlines() if '2DViewOf3D:' in line]
        results['observed_queue_type_vuid_reported']='VUID-vkQueueBindSparse-queuetype' in text
        results['sparse_queue_selection_messages']=[line for line in text.splitlines() if 'SparseQueueSelection:' in line]
        results['sparse_queue_submit_messages']=[line for line in text.splitlines() if 'SparseQueueSubmit:' in line]
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
