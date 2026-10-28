#!/usr/bin/env python3
"""Resume the observed signed-image atomic correction after the Sirit API compile fix.
Only two comparisons change relative to the failed candidate. The existing offline
shader validation, native-build, game-capture and rollback gates remain in place.
The host fixture now includes the full real Sirit/SPIR-V headers; its transport is
still a recording collaborator, not GPU execution.
"""
from __future__ import annotations
import ast
import hashlib
from pathlib import Path
import subprocess
import sys
import types

PREVIOUS_REF = 'dff995e87b3b580f6f8be6e0124c7f713c7fc80f'
PREVIOUS_PATH = 'scripts/shadps4_signed_image_atomics_20261028.py'
PREVIOUS_SHA = '8da3a0fc6729f361a4a1b14d806debb4d90d63efb219f7fbdfabe66f17b0f1e6'
PACKAGE = 'eb7e2d52959bb25c78fd0dc98c5e3b38c658b261'
PATCH_BLOB = 'ef0273bc4fe6f6c03d982009af88eb79657bf5b3'
FIXTURE_BLOB = 'c79cb41892313bb8e9aabf9a8660fd07a4550a92'
SIRIT_HEADER_BLOB = '0df7d4cc0069092b4201fa5b695005e2c5f2733e'
OLD_COMPARISON = 'texture.data_types->Get(1) == ctx.S32[1]'
FIXED_COMPARISON = 'texture.data_types->Get(1).value == ctx.S32[1].value'


def previous_bytes():
    scripts = Path.home() / 'projects/shadps4/scripts'
    for p in sorted(scripts.glob('gow-signed-atomics-*.py')):
        if p.is_file() and not p.is_symlink() and p.stat().st_size < 200000:
            raw = p.read_bytes()
            if hashlib.sha256(raw).hexdigest() == PREVIOUS_SHA:
                return raw
    raw = subprocess.run([
        'curl', '-4fsSL', '--connect-timeout', '15', '--max-time', '60',
        f'https://raw.githubusercontent.com/Chreece/shadPS4/{PREVIOUS_REF}/{PREVIOUS_PATH}'
    ], capture_output=True, check=True, timeout=65).stdout
    if hashlib.sha256(raw).hexdigest() != PREVIOUS_SHA:
        raise RuntimeError('PREVIOUS_RUNNER_HASH_MISMATCH')
    return raw


def load_previous(raw):
    if hashlib.sha256(raw).hexdigest() != PREVIOUS_SHA:
        raise RuntimeError('PREVIOUS_RUNNER_BYTES_CHANGED')
    ast.parse(raw)
    p = types.ModuleType('signed_atomic_original_runner')
    p.__file__ = str(Path(__file__).resolve())
    exec(compile(raw, p.__file__, 'exec'), p.__dict__)
    p.PACKAGE = PACKAGE
    p.BLOBS = dict(p.BLOBS)
    p.BLOBS[p.PATCH_PATH] = PATCH_BLOB
    p.BLOBS[p.TEST_PATH] = FIXTURE_BLOB
    old_fixture = p.make_fixture

    def strict_fixture(template, code):
        # Adapt only the operation recorder's class name. Sirit::Id stays real,
        # with no invented operator== or implicit integer conversion.
        return old_fixture(template, code).replace('Sirit::Module', 'Recording::Module')
    p.make_fixture = strict_fixture
    return p


def attach_header_gates(m):
    original_cmd = m.cmd
    original_cpu_tests = m.cpu_tests
    sirit = m.SRC / 'externals/sirit'
    full_header = sirit / 'include/sirit/sirit.h'
    spirv_headers = sirit / 'externals/SPIRV-Headers/include'
    include_flags = ['-DSIGNED_ATOMIC_FULL_SIRIT', '-I', str(sirit / 'include'),
                     '-I', str(spirv_headers)]

    def checked_cmd(args, log=None, timeout=120, env=None):
        args = list(args)
        if args and str(args[0]) == 'g++' and any(
            Path(str(a)).name in ('baseline-atomic.cpp', 'corrected-atomic.cpp') for a in args
        ):
            args[1:1] = include_flags
        return original_cmd(args, log=log, timeout=timeout, env=env)
    m.cmd = checked_cmd

    def cpu_tests(old_code, template, work, evidence):
        m.require(full_header.is_file() and not full_header.is_symlink() and
                  m.blob(full_header.read_bytes()) == SIRIT_HEADER_BLOB,
                  'PINNED_REAL_SIRIT_HEADER_MISMATCH')
        m.require((spirv_headers / 'spirv/unified1/spirv.hpp11').is_file(),
                  'REAL_SPIRV_HEADERS_MISSING')
        code = (m.SRC / 'src/shader_recompiler/backend/spirv/emit_spirv_atomic.cpp').read_text()
        m.require(code.count(FIXED_COMPARISON) == 2 and OLD_COMPARISON not in code,
                  'SIRIT_VALUE_COMPARISONS_NOT_APPLIED')
        bad = evidence / 'rejected-sirit-id-comparison.cpp'
        bad.write_text('#include <sirit/sirit.h>\n'
                       'bool rejected_comparison(Sirit::Id left, Sirit::Id right) {\n'
                       '    return left == right;\n}\n')
        result = subprocess.run(
            ['g++', '-std=c++23', '-Wall', '-Wextra', '-Werror', '-pedantic',
             *include_flags, '-fsyntax-only', str(bad)],
            stdin=subprocess.DEVNULL, capture_output=True, timeout=60, check=False)
        log = result.stdout + result.stderr
        (evidence / 'expected-old-api-compile-failure.txt').write_bytes(log)
        m.require(result.returncode != 0 and
                  (b'operator==' in log or b'invalid operands' in log),
                  'SIRIT_API_NEGATIVE_REGRESSION_DID_NOT_MATCH')
        original_cpu_tests(old_code, template, work, evidence)
        print('SIRIT_REAL_HEADER_GATE=PASS opaque_Id; rejected_old_comparison; '
              'native_full_build_pending', flush=True)
    m.cpu_tests = cpu_tests
    return m


if __name__ == '__main__':
    p = load_previous(previous_bytes())
    if len(sys.argv) == 4 and sys.argv[1] == '--private-dumps':
        p.capture().enable_dumps(Path(sys.argv[2]), Path(sys.argv[3]))
    else:
        raw = p.dependency('gow-auto-write-*.py', p.PARENT_REF, p.PARENT_PATH, p.PARENT_SHA)
        worker = attach_header_gates(p.load_worker(raw))
        raise SystemExit(worker.main())
