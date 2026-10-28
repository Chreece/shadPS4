#!/usr/bin/env python3
"""Restore automatic dynamic ReadConst routing lost from the current stack.
The same routing idea was native-tested on October 26 without gameplay recovery.
This retains today's metadata/coherence/PM4 fixes and adds fail-closed capability
checks and compiler-cache invalidation. CPU fixtures do not validate GPU residency.
"""
from __future__ import annotations
import ast
import hashlib
from pathlib import Path
import subprocess
import types

PARENT_SHA = '4b77d51b315d917fc07aa75711a4e1477a26c6e74fc0df1417750027a1b226e6'
PARENT_URL = ('https://raw.githubusercontent.com/Chreece/shadPS4/'
              'f3444f3d68b0f6e127e24a226ff90f7db5d1b80f/scripts/shadps4_auto_cpu_write_20261028.py')
PACKAGE = 'e1fd09438b4cbe5a067f4ff70813800214afd920'
PATCH_PATH = 'scripts/patchsets/automatic-readconst-restoration-20261028.patch'
TEST_PATH = 'tools/development/readconst_route_fixture.cpp.in'
PATCH_BLOB = '123effbc6af3e5144157844f0e638b6a7e426866'
TEST_BLOB = '8321323f9feab4f0588a7826ec3097b55e1a6205'
LAST = '5afdbc64261cef5c50eca2abd29127d5ce6d8bb7'
LAST_ELF = 'fc678780c1b891b3c57757ce2afe834a12f003755d224bba20804ff79d458b16'
OLD_UPSTREAM = 'ca36c7e31e9edb958db8d2b94a0721e58eca9daf'
UPSTREAM = '0cd6e4f2cb6617af12eb8267cca848cc41ad20d7'
COLLECTION = 'src/shader_recompiler/ir/passes/shader_info_collection_pass.cpp'
CONTEXT = 'src/shader_recompiler/backend/spirv/spirv_emit_context.cpp'
EMITTER = 'src/shader_recompiler/backend/spirv/emit_spirv_context_get_set.cpp'
SERIAL = 'src/video_core/renderer_vulkan/vk_pipeline_serialization.cpp'
BOUNDS = 'src/video_core/buffer_cache/bda_address_space.h'
RUNTIME_BLOB = '31a46cf27530c68a0afa2395fa3315025d2c0871'


def parent_bytes():
    scripts = Path.home() / 'projects/shadps4/scripts'
    for p in sorted(scripts.glob('gow-auto-write-*.py')):
        if p.is_file() and not p.is_symlink() and p.stat().st_size < 100000:
            raw = p.read_bytes()
            if hashlib.sha256(raw).hexdigest() == PARENT_SHA:
                return raw
    raw = subprocess.run(['curl', '-4fsSL', '--connect-timeout', '15', '--max-time', '60',
                          PARENT_URL], capture_output=True, check=True, timeout=65).stdout
    if hashlib.sha256(raw).hexdigest() != PARENT_SHA:
        raise RuntimeError('PARENT_HASH_MISMATCH')
    return raw


def function(text, name):
    if text.count(name) != 1:
        raise RuntimeError('SOURCE_FUNCTION_ANCHOR_CHANGED=' + name)
    start = text.index(name)
    pos = text.index('{', start) + 1
    depth = 1
    while depth and pos < len(text):
        depth += (text[pos] == '{') - (text[pos] == '}')
        pos += 1
    if depth:
        raise RuntimeError('FUNCTION_BRACES_UNBALANCED=' + name)
    return text[start:pos]


def fixture(collection, emitter, template):
    start = collection.index('    case IR::Opcode::ReadConst:')
    end = collection.index('    case IR::Opcode::PackUfloat10_11_11:', start)
    edits = {
        '/* EXACT_VISITOR_CASE */': collection[start:end],
        '/* EXACT_COLLECT */': function(collection, 'void CollectShaderInfoPass('),
        '/* EXACT_EMIT */': function(emitter, 'Id EmitReadConst('),
    }
    for marker, value in edits.items():
        if template.count(marker) != 1:
            raise RuntimeError('FIXTURE_MARKER_CHANGED')
        template = template.replace(marker, value)
    return template


def load_worker(raw):
    if hashlib.sha256(raw).hexdigest() != PARENT_SHA:
        raise RuntimeError('PARENT_BYTES_CHANGED')
    text = raw.decode()
    objects = ', '.join(repr('CMakeFiles/shadps4.dir/' + p + '.o') for p in
                        (COLLECTION, CONTEXT, EMITTER, SERIAL))
    changes = {
        'gow-auto-cpu-write-': 'gow-readconst-restore-',
        'gow-auto-coherence-game-': 'gow-readconst-restore-game-',
        'AUTOMATIC_GPU_CPU_WRITE_COHERENCE': 'RESTORED_AUTOMATIC_DYNAMIC_READCONST',
        'auto-cpu-write-coherence.lock': 'automatic-readconst-restoration.lock',
        'Mandatory GPU-dirty buffer preservation on CPU write; read/backend/image options unchanged':
            'Restore prior generic GPU dynamic constant loads, bounded address resolver and atomic faults; no compatibility flags',
        'automatic-cpu-write.patch': 'automatic-readconst-restoration.patch',
        'baseline-memory-tracker.h': 'baseline-shader-info.cpp',
        'metadata_candidate': 'depth_extent_candidate',
        'NATIVE_BUFFER_CACHE_OBJECT': 'NATIVE_READCONST_COMPILER_OBJECTS',
        "'CMakeFiles/shadps4.dir/src/video_core/buffer_cache/buffer_cache.cpp.o'": objects,
        'memory: state-driven preservation of GPU-owned bytes before CPU writes':
            'shader compiler: restore automatic dynamic ReadConst and reject userdata-zero substitution',
        'coherence-events.txt': 'readconst-route-events.txt',
        "text = console.read_text(errors='replace')":
            "text = console.read_text(errors='replace')\n            status['automatic_readconst_log_entries'] = text.count('AutoReadConst:')",
        "if 'CpuWriteCoherence:' in line or 'Device lost during submit' in line or 'GPU_QUEUE_FAILURE' in line":
            "if 'AutoReadConst:' in line or 'non-GPU cached memory' in line or 'DEVICE_FAULT_' in line or 'Device lost during submit' in line or 'GPU_QUEUE_FAILURE' in line",
        'REVIEW_VISIBLE_GAMEPLAY_AND_CPU_WRITE_COHERENCE_EVIDENCE_NO_PROMOTION':
            'REVIEW_AUTOMATIC_DYNAMIC_LOADS_RESIDENCY_AND_VISIBLE_GAMEPLAY_NO_PROMOTION',
    }
    for before, after in changes.items():
        if before not in text:
            raise RuntimeError('PARENT_ANCHOR_MISSING=' + before[:70])
        text = text.replace(before, after)
    ast.parse(text)
    m = types.ModuleType('readconst_restore_worker')
    m.__file__ = str(Path(__file__).resolve())
    exec(compile(text, m.__file__, 'exec'), m.__dict__)
    m.LAST, m.LAST_ELF = LAST, LAST_ELF
    m.LAST_BIN = m.ROOT / 'GodOfWarRagnarok/tests/gow-depth-extent-20261028T164730Z-3450091/candidate/shadps4'
    m.UPSTREAM_OLD, m.UPSTREAM = OLD_UPSTREAM, UPSTREAM
    m.UPSTREAM_PATHS = {'src/video_core/amdgpu/regs.h', 'src/video_core/renderer_vulkan/vk_rasterizer.cpp'}
    m.PACKAGE, m.PATCH_PATH, m.TEST_PATH = PACKAGE, PATCH_PATH, TEST_PATH
    m.PACKAGE_BLOBS = {PATCH_PATH: PATCH_BLOB, TEST_PATH: TEST_BLOB}
    m.M, m.B, m.H = COLLECTION, SERIAL, BOUNDS
    m.PREIMAGES = {
        COLLECTION: '89b736e1810ba0aa90b248bf517c2c3df1b5fcc3',
        CONTEXT: '6ec69c348a3118165fc38adb6167a84793188859',
        EMITTER: '9dff2ea2ce0347ace0a132ea75619d219bfa7fdc',
    }
    m.CHANGED = {COLLECTION, CONTEXT, EMITTER, SERIAL, BOUNDS}

    def cpu_tests(old_collection, template, work, evidence):
        old_emitter = m.git_bytes('show', m.LAST + ':' + EMITTER).decode()
        old_serial = m.git_bytes('show', m.LAST + ':' + SERIAL).decode()
        new_serial = (m.SRC / SERIAL).read_text()
        m.require('ShaderBinaryVersion = 16u;' in old_serial and
                  'ShaderMetaVersion = 9u;' in old_serial and
                  'ShaderBinaryVersion = 17u;' in new_serial and
                  'ShaderMetaVersion = 10u;' in new_serial, 'CACHE_VERSION_GUARD_FAILED')
        for name, collection, emitter, args in [
            ('baseline', old_collection, old_emitter, ['--baseline']),
            ('corrected', (m.SRC / COLLECTION).read_text(), (m.SRC / EMITTER).read_text(), []),
        ]:
            test = evidence / (name + '-readconst-route.cpp')
            test.write_text(fixture(collection, emitter, template))
            exe = work / (name + '-readconst-route-test')
            m.cmd(['g++', '-std=c++23', '-O2', '-DNDEBUG', '-Wall', '-Wextra', '-Werror',
                   '-pedantic', '-I', m.SRC / 'src', test, '-o', exe],
                  evidence / (name + '-readconst-build.log'), timeout=180)
            output = m.cmd([exe, *args], evidence / (name + '-readconst-result.txt'), timeout=30)
            expected = b'BASELINE_READCONST_WORD_ZERO=REPRODUCED' if args else b'AUTOMATIC_READCONST_ROUTING=PASS'
            m.require(expected in output, 'READCONST_ROUTE_TEST_FAILED=' + name)
        print('EXACT_COLLECTOR_AND_EMITTER_ROUTE=PASS FULL_GPU_VALIDATION_PENDING', flush=True)
    m.cpu_tests = cpu_tests

    def derive_runtime(target, pin, binary_sha):
        previous = m.LOGS / 'gow-depth-extent-20261028T164730Z-3450091-evidence/native-game-runner.sh'
        m.require(previous.is_file() and m.blob(previous.read_bytes()) == RUNTIME_BLOB,
                  'PREVIOUS_RUNTIME_CHANGED')
        script = previous.read_text()
        for before, after, count in [
            ('gow-depth-extent-game-', 'gow-readconst-restore-game-', 2),
            (LAST, pin, 2), (LAST_ELF, binary_sha, 2), (OLD_UPSTREAM, m.UPSTREAM, 2),
            ('DEPTH_EXTENT_COMPONENTWISE_GOW', 'RESTORED_AUTOMATIC_DYNAMIC_READCONST_GOW', 1),
        ]:
            m.require(script.count(before) == count, 'RUNTIME_ANCHOR_CHANGED=' + before[:60])
            script = script.replace(before, after)
        m.require('--seconds 118' in script and 'shutil.copy2(old,dest)' in script and
                  'GOW_COHERENCE_PARENT_LOCKS' in script, 'RUNTIME_GUARDS_MISSING')
        target.write_text(script)
        m.cmd(['bash', '-n', target])
    m.derive_runtime = derive_runtime
    return m


if __name__ == '__main__':
    raise SystemExit(load_worker(parent_bytes()).main())
