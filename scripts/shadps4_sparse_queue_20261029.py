#!/usr/bin/env python3
"""Use a sparse-capable Vulkan queue for the observed binding operation.
Retain buffer-size, signed-atomic and ImGui fixes; no rebase or shader changes.
The regression extracts real queue selection/submission code into CPU recorders;
actual Vulkan-header and full native compilation precede one validation run.
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
PACKAGE = '81f7b3ca9e2034bb3d8cd1c854d61af80f905b7a'
PATCH_PATH = 'scripts/patchsets/sparse-queue-capability-20261029.patch'
TEST_PATH = 'tools/development/sparse_queue_fixture.cpp.in'
PATCH_BLOB = 'e734d944109136a2e7445c38529bfd2ef8599795'
TEST_BLOB = 'd2eb4962664fd2baaf302fc1d38faad0d25dd7a5'
CODE = 'src/video_core/renderer_vulkan/vk_instance.cpp'
HEADER = 'src/video_core/renderer_vulkan/vk_instance.h'
BUFFER = 'src/video_core/buffer_cache/buffer_cache.cpp'
PREIMAGES = {'src/video_core/renderer_vulkan/vk_instance.cpp': '003b949f717865cd036e073fce3d5db7be9def29', 'src/video_core/renderer_vulkan/vk_instance.h': 'b811ab04b214780f6a9f1a9a58b89a457ff3235d', 'src/video_core/buffer_cache/buffer_cache.cpp': '83b94f1a0f946bcaee8e7c3b853a352826966f6c'}
SOURCE = '8a3b22e7f3f4f447447bf6bbe49a5fb0af6390c2'
ELF = '560115fbf053d4f79196f5ce1e29856f8b1eee4d48d41056acdb736d54facce8'
RUNTIME_SHA = '329ae86068e3333a7fc68533ee2f2f37b87f6f30304f69f54c177d158c810ac2'


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


def fixture(instance_code, buffer_code, template, baseline):
    start = instance_code.index('    const auto family_properties =')
    end = instance_code.index('    const auto vk11_features', start)
    helper = '' if baseline else function(instance_code, 'static std::optional<u32> FindSparseQueueFamily(')
    capture = ('requests.assign(1,queue_info);' if baseline else
               'requests.assign(queue_infos.begin(),queue_infos.begin()+queue_info_count);')
    edits = {
        '/* EXACT_SELECT_HELPER */': helper,
        '/* EXACT_SELECTION_BLOCK */': instance_code[start:end],
        '/* CAPTURE_REQUESTS */': capture,
        '/* EXACT_SUBMIT */': function(buffer_code, 'void BufferCache::SubmitPendingArenaBinds('),
    }
    for marker,value in edits.items():
        if template.count(marker) != 1:
            raise RuntimeError('FIXTURE_MARKER_CHANGED='+marker)
        template = template.replace(marker,value)
    if baseline:
        # The old source has no sparse-family helper. Stop the baseline fixture
        # after its explicit reproduction; never transplant the new helper into it.
        cut = ' Check(sample.requests.size()==2'
        if template.count(cut) != 1:
            raise RuntimeError('BASELINE_FIXTURE_END_CHANGED')
        template = template[:template.index(cut)] + '\n return 3;\n}\n'
    return template


def load_worker(raw, validation):
    if hashlib.sha256(raw).hexdigest() != PARENT_SHA:
        raise RuntimeError('PARENT_BYTES_CHANGED')
    text = raw.decode()
    changes = {
        'gow-auto-cpu-write-':'gow-sparse-queue-',
        'gow-auto-coherence-game-':'gow-vulkan-validation-game-',
        'AUTOMATIC_GPU_CPU_WRITE_COHERENCE':'SPARSE_QUEUE_CAPABILITY',
        'auto-cpu-write-coherence.lock':'sparse-queue-capability.lock',
        'Mandatory GPU-dirty buffer preservation on CPU write; read/backend/image options unchanged':
            'Sparse-capable queue selection and ordered bind signals; retained guest memory and shader corrections',
        'automatic-cpu-write.patch':'sparse-queue-capability.patch',
        'baseline-memory-tracker.h':'baseline-vk-instance.cpp',
        'metadata_candidate':'sparse_buffer_candidate',
        'NATIVE_BUFFER_CACHE_OBJECT':'NATIVE_SPARSE_QUEUE_BUILD',
        "cmd(['nice', '-n', '10', 'ninja', '-C', BUILD, '-j', '2',\n             'CMakeFiles/shadps4.dir/src/video_core/buffer_cache/buffer_cache.cpp.o'],":
            "cmd(['nice', '-n', '10', 'cmake', '--build', BUILD, '--target', 'shadps4', '--parallel', '2'],",
        'memory: state-driven preservation of GPU-owned bytes before CPU writes':
            'vulkan: use a supported sparse queue and synchronize binding signals',
        'coherence-events.txt':'vulkan-validation-messages.txt',
        "require(not (SRC / H).exists(), 'NEW_HEADER_COLLISION')":
            "require((SRC / M).is_file(), 'INSTANCE_SOURCE_MISSING')",
        "if 'CpuWriteCoherence:' in line or 'Device lost during submit' in line or 'GPU_QUEUE_FAILURE' in line":
            "if 'SparseQueue' in line or 'SparseArena' in line or 'VUID-' in line or 'SYNC-HAZARD-' in line or 'Device lost during submit' in line",
        "status['game_rc'] = game_rc":
            "status['game_rc'] = game_rc\n        status.update(validation_results)",
        "status['stage'] = 'BUILD_PASS_RUNTIME_CAPTURED' if game_rc == 0 else 'BUILD_PASS_RUNTIME_FAILED'":
            "status['stage'] = 'VALIDATION_ERROR_CAPTURED' if validation_results.get('first_validation_error') else ('BUILD_PASS_RUNTIME_CAPTURED' if game_rc == 0 else 'BUILD_PASS_RUNTIME_FAILED')",
        'REVIEW_VISIBLE_GAMEPLAY_AND_CPU_WRITE_COHERENCE_EVIDENCE_NO_PROMOTION':
            'REVIEW_SPARSE_QUEUE_CAPABILITY_AND_NEXT_VALIDATION; NO_GAMEPLAY_CLAIM',
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
    m=types.ModuleType('sparse_queue_worker')
    m.__file__=str(Path(__file__).resolve())
    exec(compile(text,m.__file__,'exec'),m.__dict__)
    m.LAST,m.LAST_ELF=SOURCE,ELF
    m.LAST_BIN=m.ROOT/'GodOfWarRagnarok/tests/gow-sparse-buffer-limit-20261029T064420Z-2604924/candidate/shadps4'
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
    m.TEMPLATE=m.LOGS/'gow-sparse-buffer-limit-20261029T064420Z-2604924-evidence/native-game-runner.sh'
    m.TEMPLATE_BLOB='0a97a17d52649fc2a1af390807ae54899e7bf5df'
    results={}
    m.validation_results=results
    session={}

    def cpu_tests(old, template, work, evidence):
        headers = m.SRC/'externals/vulkan-headers/include'
        m.require((headers/'vulkan/vulkan.hpp').is_file(), 'REAL_VULKAN_HEADERS_MISSING')
        corrected = (m.SRC/CODE).read_text()
        old_buffer = m.git_bytes('show',m.LAST+':'+BUFFER).decode()
        corrected_buffer = (m.SRC/BUFFER).read_text()
        scheduler = (m.SRC/'src/video_core/renderer_vulkan/vk_scheduler.cpp').read_text()
        submit = function(scheduler,'void Scheduler::SubmitExecution(')
        m.require('std::scoped_lock lk{submit_mutex};' in submit and
                  submit.index('std::scoped_lock lk{submit_mutex};') < submit.index('on_submit(info);'),
                  'SCHEDULER_HOST_QUEUE_LOCK_GUARD_CHANGED')
        for name, baseline, args in [('baseline', True, ['--baseline']),
                                      ('corrected', False, [])]:
            cpp = evidence/(name+'-sparse-queue.cpp')
            cpp.write_text(fixture(old if baseline else corrected,
                                   old_buffer if baseline else corrected_buffer,template,baseline))
            exe = work/(name+'-sparse-queue-test')
            m.cmd(['g++', '-std=c++23', '-O2', '-Wall', '-Wextra', '-Werror', '-pedantic',
                   '-DREAL_VULKAN_TYPES', '-I', headers, cpp, '-o', exe],
                  evidence/(name+'-queue-build.log'), timeout=180)
            output = m.cmd([exe, *args], evidence/(name+'-sparse-queue-result.txt'), timeout=30)
            expected = b'BASELINE_SPARSE_QUEUE_TYPE=REPRODUCED' if baseline else b'SPARSE_QUEUE_CAPABILITY=PASS'
            m.require(expected in output, 'SPARSE_QUEUE_REGRESSION_FAILED')
        env,layer=validation.prepare_layer(work,evidence)
        session['env']=env
        results['validation_layer']=layer
        print('SPARSE_QUEUE_REAL_HEADER_CPU_TESTS=PASS VALIDATION_LAYER_LOAD=PASS',flush=True)
    m.cpu_tests=cpu_tests

    def derive_runtime(target,pin,binary_sha):
        original=m.TEMPLATE.read_bytes()
        m.require(hashlib.sha256(original).hexdigest()==RUNTIME_SHA,'VALIDATION_RUNTIME_CHANGED')
        script=original.decode()
        pattern=r'^CANDIDATE=.+$'
        m.require(len(re.findall(pattern,script,re.M))==1,'CANDIDATE_ANCHOR_CHANGED')
        script=re.sub(pattern,'CANDIDATE='+shlex.quote(str(m.TREE)),script,flags=re.M)
        for before,after,count in [(SOURCE,pin,2),(ELF,binary_sha,2),
            ('SPARSE_BUFFER_LIMIT_VALIDATION_GOW','SPARSE_QUEUE_CAPABILITY_VALIDATION_GOW',1)]:
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
