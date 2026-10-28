#!/usr/bin/env python3
"""Publish real staged GPU downloads to the host memory domain.
Retain the previous CPU-write and metadata fixes; one guarded native trial.
The CPU fixtures model collaborators and optionally use real Vulkan headers.
"""
from __future__ import annotations
import ast
import hashlib
from pathlib import Path
import re
import subprocess
import sys
import types

PARENT_REF = 'f3444f3d68b0f6e127e24a226ff90f7db5d1b80f'
PARENT_PATH = 'scripts/shadps4_auto_cpu_write_20261028.py'
PARENT_SHA = '4b77d51b315d917fc07aa75711a4e1477a26c6e74fc0df1417750027a1b226e6'
PACKAGE = '34805aaf18c3b6ebdf2b4ef56627b97617fabee4'
PATCH_PATH = 'scripts/patchsets/gpu-host-readback-publication-20261028.patch'
TEST_PATH = 'tools/development/host_readback_fixture.cpp.in'
PATCH_BLOB = '65f4e375c451f7a903a1c6af008b28e928f1ca26'
TEST_BLOB = 'bdab02cdcf9be9079605c568cc3d348e50103f64'


def parent_bytes():
    scripts = Path.home() / 'projects/shadps4/scripts'
    for path in sorted(scripts.glob('gow-auto-write-*.py')):
        if path.is_file() and not path.is_symlink() and path.stat().st_size < 100000:
            data = path.read_bytes()
            if hashlib.sha256(data).hexdigest() == PARENT_SHA:
                return data
    url = f'https://raw.githubusercontent.com/Chreece/shadPS4/{PARENT_REF}/{PARENT_PATH}'
    data = subprocess.run(['curl', '-4fsSL', '--connect-timeout', '15', '--max-time', '60', url],
                          capture_output=True, check=True, timeout=65).stdout
    if hashlib.sha256(data).hexdigest() != PARENT_SHA:
        raise RuntimeError('IMMUTABLE_PARENT_RUNNER_MISMATCH')
    return data


def extract(text, start, end):
    if text.count(start) != 1:
        raise RuntimeError('EXACT_METHOD_ANCHOR_MISMATCH=' + start)
    a = text.index(start)
    b = text.index(end, a)
    return text[a:b]


def fixture(template, code):
    helper = ''
    if 'static void PublishReadbackForHost(' in code:
        helper = extract(code, 'static void PublishReadbackForHost(', '\nstatic constexpr')
    replacements = {
        '/* EXACT_HELPER */': helper,
        '/* EXACT_DOWNLOAD */': extract(code, 'void BufferCache::DownloadMemory(', '\nstd::pair<const Buffer*'),
        '/* EXACT_EDGE */': extract(code, 'void BufferCache::ReadEdgeImagePages(', '\nvoid BufferCache::SynchronizeDmaBuffers('),
    }
    for marker, content in replacements.items():
        if template.count(marker) != 1:
            raise RuntimeError('FIXTURE_MARKER_MISMATCH=' + marker)
        template = template.replace(marker, content)
    return template


def load_worker(raw):
    if hashlib.sha256(raw).hexdigest() != PARENT_SHA:
        raise RuntimeError('PARENT_BYTES_CHANGED')
    text = raw.decode()
    changes = {
        'gow-auto-cpu-write-': 'gow-host-readback-',
        'gow-auto-coherence-game-': 'gow-host-readback-game-',
        'AUTOMATIC_GPU_CPU_WRITE_COHERENCE': 'GPU_TRANSFER_HOST_READ_PUBLICATION',
        'auto-cpu-write-coherence.lock': 'gpu-host-readback-publication.lock',
        'Mandatory GPU-dirty buffer preservation on CPU write; read/backend/image options unchanged':
            'Exact staged-buffer COPY/TRANSFER_WRITE to HOST/HOST_READ dependencies; retained write coherence, no compatibility flag changes',
        'automatic-cpu-write.patch': 'gpu-host-readback-publication.patch',
        'baseline-memory-tracker.h': 'baseline-buffer-cache.cpp',
        'metadata_candidate': 'write_coherence_candidate',
        'CPU_WRITE_COHERENCE': 'HOST_READBACK_PUBLICATION',
        "require(not (SRC / H).exists(), 'NEW_HEADER_COLLISION')":
            "require((SRC / B).is_file(), 'BUFFER_CACHE_SOURCE_MISSING')",
    }
    for before, after in changes.items():
        if before not in text:
            raise RuntimeError('PARENT_ANCHOR_MISSING=' + before)
        text = text.replace(before, after)
    ast.parse(text)
    m = types.ModuleType('host_readback_worker')
    m.__file__ = str(Path(__file__).resolve())
    exec(compile(text, m.__file__, 'exec'), m.__dict__)
    m.LAST = '41bc8f70c1ad77ee2eaa91be3a118d18207b8268'
    m.LAST_ELF = 'b6f55aa3bdf09e0247b58edcc2a3a38c58cc453f9a4898e2d17b265d7ff29e70'
    m.LAST_BIN = m.ROOT / 'GodOfWarRagnarok/tests/gow-auto-cpu-write-20261028T145657Z-2448746/candidate/shadps4'
    m.UPSTREAM_OLD = 'caaa5419726c912013ffd5154dd4a9bac461cd94'
    m.UPSTREAM = 'ce74386283f559f2279a7c31416bcdc9359be0b3'
    m.PACKAGE = PACKAGE
    m.PATCH_PATH, m.TEST_PATH = PATCH_PATH, TEST_PATH
    m.PACKAGE_BLOBS = {PATCH_PATH: PATCH_BLOB, TEST_PATH: TEST_BLOB}
    m.M = m.B
    m.PREIMAGES = {m.B: '25be1771510021091c6116eacd6dd026455b09a3'}
    m.CHANGED = {m.B}

    def cpu_tests(old_code, template, work, evidence):
        headers = m.SRC / 'externals/vulkan-headers/include'
        m.require((headers / 'vulkan/vulkan.hpp').is_file(), 'REAL_VULKAN_HEADERS_MISSING')
        corrected = (m.SRC / m.B).read_text()
        for name, code, args in [('baseline', old_code, ['--baseline']), ('corrected', corrected, [])]:
            test = evidence / (name + '-host-readback.cpp')
            test.write_text(fixture(template, code))
            exe = work / (name + '-host-readback-test')
            m.cmd(['g++', '-std=c++23', '-O2', '-DNDEBUG', '-Wall', '-Wextra', '-Werror',
                   '-pedantic', '-DREADBACK_REAL_VULKAN', '-I', headers, test, '-o', exe],
                  evidence / (name + '-host-readback-build.log'), timeout=180)
            output = m.cmd([exe, *args], evidence / (name + '-host-readback-result.txt'), timeout=30).decode()
            expected = 'BASELINE_MISSING_HOST_DEPENDENCY=REPRODUCED' if args else 'HOST_READBACK_PUBLICATION=PASS'
            m.require(expected in output and 'REAL_VULKAN_HEADERS_CPU_ONLY' in output,
                      'HOST_READBACK_FIXTURE_FAILED=' + name)
        print('EXACT_DOWNLOAD_METHODS_REAL_VULKAN_HEADERS=PASS GPU_TEST_NOT_YET_RUN', flush=True)
    m.cpu_tests = cpu_tests
    return m


if __name__ == '__main__':
    raise SystemExit(load_worker(parent_bytes()).main())
