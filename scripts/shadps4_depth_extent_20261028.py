#!/usr/bin/env python3
"""Fix depth-alias allocation extents without a game-specific option.
Retain the PM4/coherence candidate and preserve existing equivalent upstream checks.
Local CPU fixtures exercise the exact extent struct and allocation assignment,
not Vulkan allocation/copy behaviour. One native trial follows successful build.
"""
from __future__ import annotations
import ast
import hashlib
import json
from pathlib import Path
import re
import subprocess
import types

PARENT_SHA = '4b77d51b315d917fc07aa75711a4e1477a26c6e74fc0df1417750027a1b226e6'
PARENT_URL = ('https://raw.githubusercontent.com/Chreece/shadPS4/'
              'f3444f3d68b0f6e127e24a226ff90f7db5d1b80f/scripts/shadps4_auto_cpu_write_20261028.py')
PACKAGE = 'ae7e586213541a32054d3caf78282a5ac924e413'
PATCH_PATH = 'scripts/patchsets/depth-extent-componentwise-20261028.patch'
TEST_PATH = 'tools/development/depth_extent_fixture.cpp.in'
PATCH_BLOB = '49a91d00fca7159f9c03af72fd66cd95b397fc67'
TEST_BLOB = 'a3487643c8f82fa62957d252e32125890a45a118'
LAST = '4078722f97ffb872d8cae4575e7e4a71ecabe170'
LAST_ELF = '438305d20f3838fab7c01a4bc0b4a0da0fcbb289f6aae66831a4ce239e88e834'
OLD_UPSTREAM = '7ea677e56d75bf3d90ace2b0a5144a665d1f38b6'
UPSTREAM = 'ca36c7e31e9edb958db8d2b94a0721e58eca9daf'
CODE = 'src/video_core/texture_cache/texture_cache.cpp'
TYPES = 'src/video_core/texture_cache/types.h'
CODE_BLOB = 'ce9ca78c89d078a0b1b733392abd94d8e8436c62'
TYPES_BLOB = 'ed36a2ff1cb2e59d8bc93e2f2711bc9763ecc9e0'
RUNTIME_BLOB = '5ea8bd6d566a4bfe78869cbb575ca38c90a5539f'


def parent_bytes():
    scripts = Path.home() / 'projects/shadps4/scripts'
    for p in sorted(scripts.glob('gow-auto-write-*.py')):
        if p.is_file() and not p.is_symlink() and p.stat().st_size < 100000:
            data = p.read_bytes()
            if hashlib.sha256(data).hexdigest() == PARENT_SHA:
                return data
    data = subprocess.run(['curl', '-4fsSL', '--connect-timeout', '15', '--max-time', '60',
                           PARENT_URL], capture_output=True, check=True, timeout=65).stdout
    if hashlib.sha256(data).hexdigest() != PARENT_SHA:
        raise RuntimeError('PARENT_HASH_MISMATCH')
    return data


def make_fixture(code, types, template):
    a = types.index('struct SubresourceExtent {')
    extent = types[a:types.index('\nstruct SubresourceRange', a)]
    a = code.index('ImageId TextureCache::ResolveDepthOverlap(')
    section = code[a:code.index('\nstd::tuple<ImageId', a)]
    if section.count('new_info.resources =') != 1:
        raise RuntimeError('ALLOCATION_ASSIGNMENT_ANCHOR_CHANGED')
    a = section.index('new_info.resources =')
    assignment = section[a:section.index(';', a) + 1]
    for marker, value in [('/* EXACT_EXTENT */', extent),
                          ('/* EXACT_ALLOCATION_ASSIGNMENT */', assignment)]:
        if template.count(marker) != 1:
            raise RuntimeError('FIXTURE_MARKER_CHANGED')
        template = template.replace(marker, value)
    return template


def semantic_upstream_tree(m, evidence):
    """Resolve the duplicated upstream change only after proving its entire delta.

    The retained candidate already has all three Contains checks and the same
    member body (constexpr). Preserve those exact files, not an arbitrary merge
    resolution. No other upstream edit is accepted by this proof.
    """
    old_code = m.git_bytes('show', m.UPSTREAM_OLD + ':' + CODE).decode()
    new_code = m.git_bytes('show', m.UPSTREAM + ':' + CODE).decode()
    current = m.git_bytes('show', m.LAST + ':' + CODE).decode()
    replacements = [
        ('bool recreate = cache_image.info.resources < requested_info.resources;',
         'bool recreate = !cache_image.info.resources.Contains(requested_info.resources);'),
        ('image_info.resources > cache_image.info.resources)',
         '!cache_image.info.resources.Contains(image_info.resources))'),
        ('image_resolved.info.resources < info.resources)',
         '!image_resolved.info.resources.Contains(info.resources))'),
    ]
    expected = old_code
    for before, after in replacements:
        m.require(expected.count(before) == 1 and current.count(after) == 1,
                  'UPSTREAM_CONTAINMENT_CALLSITE_CHANGED')
        expected = expected.replace(before, after)
    m.require(expected == new_code, 'UPSTREAM_HAS_OTHER_TEXTURE_EDITS')
    old_types = m.git_bytes('show', m.UPSTREAM_OLD + ':' + TYPES).decode()
    new_types = m.git_bytes('show', m.UPSTREAM + ':' + TYPES).decode()
    own_types = m.git_bytes('show', m.LAST + ':' + TYPES).decode()
    addition = '''

    // True if an image with this extent can serve every subresource of `other`. The defaulted
    // operators compare levels first and layers only on a tie, which cannot answer this.
    bool Contains(const SubresourceExtent& other) const noexcept {
        return levels >= other.levels && layers >= other.layers;
    }'''
    anchor = '    auto operator<=>(const SubresourceExtent&) const = default;'
    m.require(old_types.count(anchor) == 1 and old_types.replace(anchor, anchor + addition) == new_types,
              'UPSTREAM_HAS_OTHER_EXTENT_EDITS')
    pattern = (r'(?:constexpr\s+)?bool Contains\(const SubresourceExtent& other\) const noexcept\s*'
               r'\{\s*return levels >= other\.levels && layers >= other\.layers;\s*\}')
    m.require(len(re.findall(pattern, own_types)) == 1, 'RETAINED_CONTAINMENT_BODY_CHANGED')
    m.require(m.blob(current.encode()) == m.PREIMAGES[CODE] and
              m.blob(own_types.encode()) == m.PREIMAGES[TYPES], 'RETAINED_TEXTURE_PREIMAGE_CHANGED')
    (evidence / 'upstream-equivalence.json').write_text(json.dumps({
        'upstream': m.UPSTREAM, 'previous_upstream': m.UPSTREAM_OLD,
        'status': 'ENTIRE_TWO_FILE_UPSTREAM_DELTA_ALREADY_EQUIVALENT_IN_RETAINED_SOURCE',
        'resolution': 'Preserve retained constexpr Contains and three callsites byte-for-byte',
        'new_correction': 'Replace remaining lexicographic depth-allocation std::max with per-axis maxima',
    }, indent=2) + '\n')
    return m.git('rev-parse', m.LAST + '^{tree}')


def load_worker(raw):
    if hashlib.sha256(raw).hexdigest() != PARENT_SHA:
        raise RuntimeError('PARENT_BYTES_CHANGED')
    text = raw.decode()
    replacements = {
        'gow-auto-cpu-write-': 'gow-depth-extent-',
        'gow-auto-coherence-game-': 'gow-depth-extent-game-',
        'AUTOMATIC_GPU_CPU_WRITE_COHERENCE': 'DEPTH_EXTENT_COMPONENTWISE_ALLOCATION',
        'auto-cpu-write-coherence.lock': 'depth-extent-componentwise.lock',
        'Mandatory GPU-dirty buffer preservation on CPU write; read/backend/image options unchanged':
            'Per-axis mip/layer allocation in depth alias recreation; retained PM4, metadata and coherence fixes',
        'automatic-cpu-write.patch': 'depth-extent-componentwise.patch',
        'baseline-memory-tracker.h': 'baseline-texture-cache.cpp',
        'metadata_candidate': 'pm4_candidate',
        'NATIVE_BUFFER_CACHE_OBJECT': 'NATIVE_TEXTURE_CACHE_OBJECT',
        "'CMakeFiles/shadps4.dir/src/video_core/buffer_cache/buffer_cache.cpp.o'":
            "'CMakeFiles/shadps4.dir/src/video_core/texture_cache/texture_cache.cpp.o'",
        'memory: state-driven preservation of GPU-owned bytes before CPU writes':
            'texture cache: preserve both mip and layer counts when recreating depth aliases',
        'Isolated upstream integration retaining verified metadata source':
            'Integrate equivalent upstream containment checks while preserving retained source',
        "require(not (SRC / H).exists(), 'NEW_HEADER_COLLISION')":
            "require((SRC / M).is_file(), 'TEXTURE_CACHE_MISSING')",
        "merged_tree = git('merge-tree', '--write-tree', LAST, UPSTREAM).splitlines()[0]":
            'merged_tree = verified_upstream_tree(e)',
        "require(set(git('diff', '--name-only', LAST, merged).splitlines()) == UPSTREAM_PATHS,\n                'MERGE_CHANGED_UNRELATED_SOURCE')":
            "require(not git('diff', '--name-only', LAST, merged), 'EQUIVALENCE_MERGE_CHANGED_SOURCE')",
        'coherence-events.txt': 'depth-extent-events.txt',
        "text = console.read_text(errors='replace')":
            "text = console.read_text(errors='replace')\n            status['depth_extent_correction_log_entries'] = text.count('DepthExtentMerge:')",
        "if 'CpuWriteCoherence:' in line or 'Device lost during submit' in line or 'GPU_QUEUE_FAILURE' in line":
            "if 'DepthExtentMerge:' in line or 'DEVICE_FAULT_' in line or 'Device lost during submit' in line or 'GPU_QUEUE_FAILURE' in line",
        'REVIEW_VISIBLE_GAMEPLAY_AND_CPU_WRITE_COHERENCE_EVIDENCE_NO_PROMOTION':
            'REVIEW_DEPTH_EXTENT_EXECUTION_AND_VISIBLE_GAMEPLAY_NO_PROMOTION',
    }
    for before, after in replacements.items():
        if before not in text:
            raise RuntimeError('PARENT_ANCHOR_MISSING=' + before[:80])
        text = text.replace(before, after)
    ast.parse(text)
    m = types.ModuleType('depth_extent_worker')
    m.__file__ = str(Path(__file__).resolve())
    exec(compile(text, m.__file__, 'exec'), m.__dict__)
    m.LAST, m.LAST_ELF = LAST, LAST_ELF
    m.LAST_BIN = m.ROOT / 'GodOfWarRagnarok/tests/gow-upstream-pm4-20261028T162436Z-3216248/candidate/shadps4'
    m.UPSTREAM_OLD, m.UPSTREAM = OLD_UPSTREAM, UPSTREAM
    m.UPSTREAM_PATHS = {CODE, TYPES}
    m.PACKAGE = PACKAGE
    m.PATCH_PATH, m.TEST_PATH = PATCH_PATH, TEST_PATH
    m.PACKAGE_BLOBS = {PATCH_PATH: PATCH_BLOB, TEST_PATH: TEST_BLOB}
    m.M = m.B = CODE
    m.PREIMAGES = {CODE: CODE_BLOB, TYPES: TYPES_BLOB}
    m.CHANGED = {CODE}
    m.verified_upstream_tree = lambda evidence: semantic_upstream_tree(m, evidence)

    def cpu_tests(old_code, template, work, evidence):
        types_source = (m.SRC / TYPES).read_text()
        for name, code, args in [('baseline', old_code, ['--baseline']),
                                  ('corrected', (m.SRC / CODE).read_text(), [])]:
            test = evidence / (name + '-depth-extent.cpp')
            test.write_text(make_fixture(code, types_source, template))
            exe = work / (name + '-depth-extent-test')
            m.cmd(['g++', '-std=c++23', '-O2', '-DNDEBUG', '-Wall', '-Wextra', '-Werror',
                   '-pedantic', test, '-o', exe], evidence / (name + '-depth-extent-build.log'), timeout=180)
            result = m.cmd([exe, *args], evidence / (name + '-depth-extent-result.txt'), timeout=30)
            expected = b'BASELINE_DEPTH_EXTENT_LOSS=REPRODUCED' if args else b'DEPTH_EXTENT_COMPONENTWISE=PASS'
            m.require(expected in result, 'DEPTH_EXTENT_FIXTURE_FAILED=' + name)
        print('EXACT_DEPTH_ALLOCATION_REGRESSION=PASS NO_GPU_TEST_YET', flush=True)
    m.cpu_tests = cpu_tests

    def derive_runtime(target, pin, binary_sha):
        previous = m.LOGS / 'gow-upstream-pm4-20261028T162436Z-3216248-evidence/native-game-runner.sh'
        m.require(previous.is_file() and m.blob(previous.read_bytes()) == RUNTIME_BLOB,
                  'PREVIOUS_TEST_RUNTIME_CHANGED')
        script = previous.read_text()
        for before, after, count in [
            ('gow-upstream-pm4-game-', 'gow-depth-extent-game-', 2),
            (LAST, pin, 2), (LAST_ELF, binary_sha, 2), (OLD_UPSTREAM, m.UPSTREAM, 2),
            ('UPSTREAM_PM4_CHAIN_AND_REUSE_GOW', 'DEPTH_EXTENT_COMPONENTWISE_GOW', 1),
        ]:
            m.require(script.count(before) == count, 'PINNED_RUNTIME_ANCHOR_CHANGED=' + before[:50])
            script = script.replace(before, after)
        m.require('--seconds 118' in script and 'shutil.copy2(old,dest)' in script and
                  'GOW_COHERENCE_PARENT_LOCKS' in script, 'RUNTIME_GUARDS_MISSING')
        target.write_text(script)
        m.cmd(['bash', '-n', target])
    m.derive_runtime = derive_runtime
    return m


if __name__ == '__main__':
    raise SystemExit(load_worker(parent_bytes()).main())
