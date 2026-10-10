#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
# SPDX-License-Identifier: GPL-2.0-or-later
"""Build and run the isolated scalar review; Python standard library only."""
import argparse
import ctypes
import ctypes.util
import gzip
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--repo', type=Path)
    parser.add_argument('--references', type=Path)
    parser.add_argument('--no-fetch', action='store_true')
    args = parser.parse_args()
    sources = Path(__file__).resolve().parent
    repo = (args.repo or sources.parents[2]).resolve()
    references = (args.references or repo / 'hardware/ps4_reference').resolve()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    compiler = shutil.which('g++')
    if not compiler:
        raise SystemExit('g++ is required; nothing was installed or changed.')
    if platform.system() != 'Linux':
        raise SystemExit('This diagnostic harness requires Linux.')
    cpuinfo = Path('/proc/cpuinfo').read_text()
    flags = next((l.split(':', 1)[1].split() for l in cpuinfo.splitlines() if l.startswith('flags')), [])
    if not {'avx2', 'sse4_1', 'lahf_lm'} <= set(flags):
        raise SystemExit('The baseline comparison requires AVX2, SSE4.1 and LAHF/SAHF.')
    metadata = {
        'platform': platform.platform(),
        'cpu': next((l.split(':', 1)[1].strip() for l in cpuinfo.splitlines() if l.startswith('model name')), ''),
        'compiler': subprocess.check_output([compiler, '--version'], text=True).splitlines()[0],
        'affinity_before': sorted(os.sched_getaffinity(0)),
        'rounds': 21, 'iterations_per_round': 100000,
        'modes': {'0': 'native scalar / baseline packed', '1': 'native scalar / corrected packed',
                  '2': 'original scalar', '3': 'integer loads with PUSHFQ/POPFQ',
                  '4': 'final scalar optimization'},
    }
    def run(label, command, cwd=output):
        print(label, flush=True)
        with (output / (label + '.log')).open('w') as log:
            result = subprocess.run(command, cwd=cwd, stdout=log, stderr=subprocess.STDOUT)
        if result.returncode:
            raise RuntimeError(f'{label} failed ({result.returncode}); see {label}.log')
    if not args.no_fetch:
        run('submodules', ['git', 'submodule', 'update', '--init', '--depth', '1', 'externals/xbyak', 'externals/zydis'], repo)
        run('zycore', ['git', 'submodule', 'update', '--init', '--depth', '1'], repo / 'externals/zydis')
    library = ctypes.util.find_library('zstd')
    if not library:
        raise SystemExit('libzstd is required; nothing was installed.')
    zstd = ctypes.CDLL(library)
    zstd.ZSTD_decompress.argtypes = [ctypes.c_void_p, ctypes.c_size_t, ctypes.c_void_p, ctypes.c_size_t]
    zstd.ZSTD_decompress.restype = ctypes.c_size_t
    for name in ('rcp', 'rsqrt'):
        compressed = (repo / f'src/resources/amd_{name}_index_table.bin.zstd').read_bytes()
        data = ctypes.create_string_buffer((1 << 21) + 3)
        size = zstd.ZSTD_decompress(data, 1 << 21, compressed, len(compressed))
        if size != 1 << 21:
            raise RuntimeError('Invalid reciprocal table size')
        (output / (name + '.bin')).write_bytes(data.raw)
    shutil.copyfile(sources / 'oracle.tsv', output / 'oracle.tsv')
    includes = [repo / 'externals/xbyak', repo / 'externals/zydis/include', repo / 'externals/zydis/dependencies/zycore/include']
    for name in ('probe-optimized', 'state_probe'):
        command = [compiler, '-std=c++20', '-O2']
        for include in includes:
            command += ['-I', str(include)]
        run('build-' + name, command + [str(sources / (name + '.cpp')), '-o', str(output / name)])
    run('hardware-cases', [str(output / 'probe-optimized'), str(output / 'candidate.txt'), '4'])
    def read_rows(path):
        text = gzip.decompress(path.read_bytes()).decode() if path.suffix == '.gz' else path.read_text()
        rows = [dict(v.split('=', 1) for v in line.split()[1:]) for line in text.splitlines() if line.startswith('RAW ')]
        if len(rows) != 121856:
            raise RuntimeError(f'Incomplete capture: {path.name}')
        return rows
    actual = read_rows(output / 'candidate.txt')
    comparison = {}
    for console in ('ps4', 'ps4_pro'):
        expected = read_rows(references / f'{console}-reciprocal.txt.gz')
        counts = {'rows': len(actual), 'result_mismatches': 0, 'state_mismatches': 0}
        for left, right in zip(expected, actual):
            if any(left[key] != right[key] for key in ('op', 'in', 'mxcsr_in')):
                raise RuntimeError('Capture input/order mismatch')
            counts['result_mismatches'] += left['out'] != right['out']
            counts['state_mismatches'] += left['mxcsr_out'] != right['mxcsr_out'] or right['errors'] != '0'
        comparison[console] = counts
    (output / 'hardware-comparison.json').write_text(json.dumps(comparison, indent=2) + '\n')
    if any(r['result_mismatches'] or r['state_mismatches'] for r in comparison.values()):
        raise RuntimeError('Console comparison failed')
    run('state-validation', [str(output / 'state_probe')])
    cpu = min(os.sched_getaffinity(0))
    os.sched_setaffinity(0, {cpu})
    metadata['benchmark_cpu'] = cpu
    (output / 'environment.json').write_text(json.dumps(metadata, indent=2) + '\n')
    run('benchmark-hot', [str(output / 'probe-optimized'), 'bench'])
    run('benchmark-varied', [str(output / 'probe-optimized'), 'bench-varied'])
    print('SCALAR_REVIEW=PASS', flush=True)
    print(json.dumps(comparison, indent=2), flush=True)


if __name__ == '__main__':
    main()
