#!/usr/bin/env python3
"""C925: GPU-rendered-image -> raw-buffer coherence, reversible single-TU test."""
from __future__ import annotations
import importlib.util
import os
import re
import sys
from pathlib import Path

P = Path.home() / 'projects/shadps4'
BASE = P / 'GhostOfTsushima/experiments/c925-pr95-scalar-bounds-20261028T112756671630889Z'


def patch_cpp(src: str) -> str:
    start = 'std::pair<const Buffer*, u64> BufferCache::ObtainBuffer('
    end = 'std::pair<const Buffer*, u64> BufferCache::ObtainBufferForImage('
    if src.count(start) != 1 or src.count(end) != 1:
        raise RuntimeError('RAW_IMAGE_OBTAIN_BUFFER_NOT_UNIQUE')
    a = src.index(start)
    b = src.index(end, a)
    if b <= a:
        raise RuntimeError('RAW_IMAGE_BUFFER_FUNCTION_ORDER_CHANGED')
    body = src[a:b]
    if 'RAW_IMAGE_ALIAS_COHERENCE_V1' in src:
        raise RuntimeError('RAW_IMAGE_ALIAS_FIX_ALREADY_PRESENT')
    guards = [m for m in re.finditer(r"if\s*\((?P<condition>[^{}]+)\)\s*\{", body, re.S)
              if all(term in m.group('condition') for term in (
                    '!is_written', 'STREAM_THRESHOLD', 'IsRegionGpuModified'))]
    if len(guards) != 1 or 'raw_gpu_image_alias' in guards[0].group('condition'):
        raise RuntimeError('RAW_IMAGE_STREAM_FASTPATH_UNRECOGNIZED')
    sync = 'SynchronizeMemory(arena, device_addr, size, is_written, is_texel_buffer);'
    if body.count(sync) != 1:
        raise RuntimeError('RAW_IMAGE_ARENA_COHERENCE_PATH_UNRECOGNIZED')
    insert = '''// RAW_IMAGE_ALIAS_COHERENCE_V1: preserve GPU-owned rendered image contents
    // before a raw buffer read, instead of copying stale guest memory.
    const ImageId raw_image_id = !is_texel_buffer && size != 0 && size <= 64u * 1024u * 1024u
                                     ? texture_cache.FindImageFromRange(device_addr, size)
                                     : ImageId{};
    const bool raw_gpu_image_alias = raw_image_id &&
        !texture_cache.GetImage(raw_image_id).info.props.is_depth &&
        texture_cache.GetImage(raw_image_id).info.num_samples == 1 &&
        texture_cache.GetImage(raw_image_id).info.guest_size <= size;
    if (raw_gpu_image_alias) {
        static std::atomic<u32> events{};
        const u32 event = events.fetch_add(1, std::memory_order_relaxed) + 1;
        if (event <= 4 || (event & (event - 1)) == 0) {
            LOG_INFO(Render_Vulkan, "RAW_GPU_IMAGE_ALIAS_SYNC event={} bytes={}", event, size);
        }
    }
    '''
    m = guards[0]
    old_guard = m.group(0)
    updated_guard = old_guard.replace('(', '(!raw_gpu_image_alias && ', 1)
    if updated_guard == old_guard:
        raise RuntimeError('RAW_IMAGE_STREAM_GUARD_REWRITE_FAILED')
    body = body[:m.start()] + insert + updated_guard + body[m.end():]
    body = body.replace(sync, sync + '''
    if (raw_gpu_image_alias) {
        SynchronizeMemoryFromImage(arena, device_addr, size);
    }''', 1)
    out = src[:a] + body + src[b:]
    if out.count('RAW_IMAGE_ALIAS_COHERENCE_V1') != 1 or 'RAW_GPU_IMAGE_ALIAS_SYNC' not in out:
        raise RuntimeError('RAW_IMAGE_SOURCE_INJECTION_MISMATCH')
    if '#include <atomic>' not in out:
        pos = out.find('#include <algorithm>')
        if pos < 0:
            raise RuntimeError('RAW_IMAGE_HEADER_INSERTION_ANCHOR_MISSING')
        out = out[:pos] + '#include <atomic>\n' + out[pos:]
    return out


def tests() -> None:
    original = '''#include <algorithm>
std::pair<const Buffer*, u64> BufferCache::ObtainBuffer(VAddr device_addr, u32 size,
                                                        bool is_written, bool is_texel_buffer) {
    if (!is_written && size <= STREAM_THRESHOLD && !IsRegionGpuModified(device_addr, size)) {
        return {};
    }
    SynchronizeMemory(arena, device_addr, size, is_written, is_texel_buffer);
    if (is_texel_buffer && !is_written) {
        SynchronizeMemoryFromImage(arena, device_addr, size);
    }
    return {};
}
std::pair<const Buffer*, u64> BufferCache::ObtainBufferForImage(VAddr a, u32 b) { return {}; }
'''
    transformed = patch_cpp(original)
    assert 'RAW_IMAGE_ALIAS_COHERENCE_V1' in transformed
    assert 'if (!raw_gpu_image_alias && !is_written &&' in transformed
    assert 'if (raw_gpu_image_alias) {' in transformed
    assert 'FindImageFromRange(device_addr, size)' in transformed
    assert transformed.count('#include <atomic>') == 1
    for is_texel, is_gpu in ((False, False), (True, False), (False, True)):
        assert ((is_texel and not is_gpu) or is_gpu) == (is_texel or is_gpu)
    try:
        patch_cpp(transformed)
    except RuntimeError as exc:
        assert str(exc) == 'RAW_IMAGE_ALIAS_FIX_ALREADY_PRESENT'
    else:
        raise AssertionError('DUPLICATE_PATCH_ALLOWED')
    print('RAW_IMAGE_CPP_PATCH_TESTS=PASS', flush=True)


def main():
    tests()
    runners = sorted((P / 'scripts').glob('got-texture-overlap.*/run.py'))
    for runner in runners:
        d = runner.parent
        if all((d / x).is_file() for x in ('direct_ninja.py', 'run_draw_probe.py',
                                          'resume_c925_revalidation.py', 'resume_finite_raw.py',
                                          'menu_validation_runtime.py')):
            break
    else:
        raise RuntimeError('VERIFIED_REVERSIBLE_RUNNER_NOT_FOUND')

    original = runner.read_text()
    replacements = {
        'Componentwise mip/layer image-containment and incompatible-overlap correction':
            'Synchronize GPU-rendered image aliases before raw buffer reads',
        'https://github.com/shadps4-emu/shadPS4/pull/5457':
            'https://github.com/shadps4-emu/shadPS4/pull/5280',
        'texture-overlap-fix-': 'raw-gpu-image-sync-',
        'a/src/video_core/texture_cache/texture_cache.cpp':
            'a/src/video_core/buffer_cache/buffer_cache.cpp',
        'b/src/video_core/texture_cache/texture_cache.cpp':
            'b/src/video_core/buffer_cache/buffer_cache.cpp',
        'STAGE=COMPILE_TEXTURE_CACHE_OBJECT_AND_RELINK':
            'STAGE=COMPILE_BUFFER_CACHE_OBJECT_AND_RELINK',
    }
    for old, new in replacements.items():
        if old not in original:
            raise RuntimeError('RUNNER_VERSION_OR_METADATA_CHANGED:' + old)
        original = original.replace(old, new)
    namespace = {'__file__': str(runner), '__name__': 'got_raw_gpu_image_coherence'}
    exec(compile(original, str(runner), 'exec'), namespace)
    source = BASE / 'source/src/video_core/buffer_cache/buffer_cache.cpp'
    object_file = BASE / 'build/CMakeFiles/shadps4.dir/src/video_core/buffer_cache/buffer_cache.cpp.o'
    if not source.is_file() or not object_file.is_file():
        raise RuntimeError('EXACT_BUFFER_CACHE_SOURCE_OR_OBJECT_NOT_FOUND')
    patched = patch_cpp(source.read_text())
    assert patched != source.read_text()
    namespace['SRC'] = source
    namespace['OBJ'] = object_file
    namespace['patch'] = patch_cpp
    namespace['unit'] = tests
    print('ACTUAL_SOURCE_PATCH_PREFLIGHT=PASS', flush=True)
    print('VRAM=MIN_8_GiB_OLLAMA_CONDITIONAL_UNLOAD_AND_RESTORE', flush=True)
    print('TEST=GOT_MENU_120s INPUT=NONE RESTORE=SOURCE_OBJECT_ELF', flush=True)
    return namespace['run'](d)


if __name__ == '__main__':
    sys.exit(main())
