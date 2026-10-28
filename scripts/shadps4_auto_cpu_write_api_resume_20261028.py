#!/usr/bin/env python3
"""Resume the CPU-write coherence experiment after the PageManager API build fix.

Retain the immutable original worker's protections and rollback. Only change the
patch pin and add a real-header/production-call compiler gate before native build.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
import types

BASE_COMMIT = 'f3444f3d68b0f6e127e24a226ff90f7db5d1b80f'
BASE_PATH = 'scripts/shadps4_auto_cpu_write_20261028.py'
BASE_SHA256 = '4b77d51b315d917fc07aa75711a4e1477a26c6e74fc0df1417750027a1b226e6'
PACKAGE = '51251675f7b515219c37834cb47160c1ae662933'
PATCH_BLOB = '9f36ee0dbd68e616588e502b7e88445db8b6d3eb'


def parent_bytes() -> bytes:
    scripts = Path.home() / 'projects/shadps4/scripts'
    # Reuse only an exact original runner, not a filename or modification time.
    for path in sorted(scripts.glob('gow-auto-write-*.py')):
        try:
            if path.is_symlink() or not path.is_file() or path.stat().st_size > 262144:
                continue
            raw = path.read_bytes()
            if hashlib.sha256(raw).hexdigest() == BASE_SHA256:
                return raw
        except OSError:
            continue
    url = f'https://raw.githubusercontent.com/Chreece/shadPS4/{BASE_COMMIT}/{BASE_PATH}'
    result = subprocess.run(
        ['curl', '-4fsSL', '--retry', '2', '--connect-timeout', '15', '--max-time', '40', url],
        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        timeout=150, check=False,
    )
    if result.returncode != 0:
        raise RuntimeError('ORIGINAL_RUNNER_FETCH_FAILED: ' + result.stderr.decode(errors='replace')[-1000:])
    if hashlib.sha256(result.stdout).hexdigest() != BASE_SHA256:
        raise RuntimeError('ORIGINAL_RUNNER_HASH_MISMATCH')
    return result.stdout


def page_api_gate(base, work: Path, evidence: Path) -> None:
    source = (base.SRC / base.B).read_text()
    matches = re.findall(
        r'Coherence::RequiredCpuWriteSpan\(\s*device_addr, size, arena->cpu_addr, '
        r'arena->size_bytes,\s*PageManager::GetPageSize\(\)\)', source,
    )
    base.require(len(matches) == 1 and 'PageManager::PM_PAGE_SIZE' not in source,
                 'PUBLIC_PAGE_SIZE_CALL_NOT_EXACT')
    cpp = '''#include <cstdio>
#include "video_core/page_manager.h"
#include "video_core/buffer_cache/cpu_write_coherence.h"
using namespace VideoCore;
struct Arena { VAddr cpu_addr; u64 size_bytes; };
constexpr auto CallSite(VAddr device_addr, u64 size, const Arena* arena) {
    return ''' + matches[0] + ''';
}
constexpr u64 P = PageManager::GetPageSize();
static_assert(P == BYTES_PER_PAGE);
constexpr Arena a{0, 4*P};
static_assert(CallSite(131,1,&a)->address == 0);
static_assert(CallSite(131,1,&a)->size == P);
static_assert(CallSite(P-1,2,&a)->size == 2*P);
static_assert(!CallSite(4*P,1,&a));
int main() {
    std::puts("REAL_PAGE_MANAGER_API=PASS production_call_exact=true real_header=true");
}
'''
    src = evidence / 'real-page-manager-api.cpp'
    src.write_text(cpp)
    exe = work / 'real-page-manager-api-test'
    base.cmd(['g++', '-std=c++23', '-O2', '-DNDEBUG', '-Wall', '-Wextra', '-Werror',
              '-pedantic', '-I', base.SRC / 'src', src, '-o', exe],
             evidence / 'real-page-manager-api-build.log')
    output = base.cmd([exe], evidence / 'real-page-manager-api-result.txt', timeout=30)
    base.require(b'REAL_PAGE_MANAGER_API=PASS' in output, 'PAGE_MANAGER_API_REGRESSION_FAILED')
    paths = ('src/video_core/page_manager.h', 'src/common/alignment.h', 'src/common/types.h',
             'src/video_core/buffer_cache/region_definitions.h', base.H)
    metadata = {
        'real_header_git_blobs': {p: base.blob((base.SRC / p).read_bytes()) for p in paths},
        'production_call': matches[0],
        'patch_package': PACKAGE,
        'scope': 'Real PageManager header and exact production helper call; not a full Vulkan build',
    }
    (evidence / 'real-page-manager-api-proof.json').write_text(json.dumps(metadata, indent=2) + '\n')
    print('REAL_PAGE_MANAGER_API=PASS', flush=True)


def load_parent():
    raw = parent_bytes()
    base = types.ModuleType('_verified_auto_cpu_write_worker')
    # The parent's detached worker must execute this wrapper, keeping the new pin
    # and API gate on both launches. Its existing source/binary rollback is intact.
    base.__file__ = str(Path(__file__).resolve())
    sys.modules[base.__name__] = base
    exec(compile(raw, 'verified:' + BASE_PATH, 'exec'), base.__dict__)
    base.PACKAGE = PACKAGE
    base.PACKAGE_BLOBS[base.PATCH_PATH] = PATCH_BLOB
    old_tests = base.cpu_tests

    def cpu_tests(old_header, template, work, evidence):
        page_api_gate(base, work, evidence)
        old_tests(old_header, template, work, evidence)

    base.cpu_tests = cpu_tests
    return base


if __name__ == '__main__':
    try:
        raise SystemExit(load_parent().main())
    except Exception as error:
        print('COHERENCE_API_RESUME_FAILED=' + str(error), file=sys.stderr)
        raise SystemExit(1)
