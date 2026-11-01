#!/usr/bin/env python3
"""Execute the production sparse allocator methods with recorded host operations.

The real Vulkan header mode also checks API compatibility. No GPU is created;
alias ordering and host-driver behavior still need native GPU validation.
"""
import os
from pathlib import Path
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]


def function(text, signature):
    assert text.count(signature) == 1, signature
    start = text.index(signature)
    end = text.index('{', start) + 1
    depth = 1
    while depth:
        depth += (text[end] == '{') - (text[end] == '}')
        end += 1
    return text[start:end]


def main():
    source = (ROOT / 'src/video_core/buffer_cache/buffer_cache.cpp').read_text()
    header = (ROOT / 'src/video_core/buffer_cache/buffer_cache.h').read_text()
    fixture = (ROOT / 'tools/development/sparse_arena_fixture.cpp.in').read_text()
    methods = '\n\n'.join(function(source, signature) for signature in (
        'void BufferCache::SynchronizeDmaBuffers()',
        'void BufferCache::BindBackingToArena(',
        'void BufferCache::SynchronizeSparseAliases()',
        'const Buffer* BufferCache::GetArena(',
        'void BufferCache::EnsureResident(',
    ))
    fixture = fixture.replace('/* EXACT_BACKING */',
                              function(header, '    struct Backing : public Interval') + ';')
    fixture = fixture.replace('/* EXACT_METHODS */', methods)
    assert '/* EXACT_' not in fixture
    with tempfile.TemporaryDirectory(prefix='sparse-production-') as tmp:
        path = Path(tmp)
        (path / 'test.cpp').write_text(fixture)
        for mode, extra in [('recorder', []), ('vulkan-headers', ['-DREAL_VULKAN_TYPES'])]:
            subprocess.run([os.environ.get('CXX', 'g++'), '-std=c++23', '-O2',
                            '-UNDEBUG', '-Wall', '-Wextra', '-Werror', *extra,
                            '-I', str(ROOT / 'src'),
                            '-I', str(ROOT / 'externals/vulkan-headers/include'),
                            str(path / 'test.cpp'), '-o', str(path / mode)], check=True)
            subprocess.run([str(path / mode)], check=True)
            print('SPARSE_PRODUCTION_MODE=' + mode, flush=True)


if __name__ == '__main__':
    main()
