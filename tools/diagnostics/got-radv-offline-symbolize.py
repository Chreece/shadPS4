#!/usr/bin/env python3
"""Resolve the GoT dual-capture host fault without starting any GPU process.

Only reads the recorded RADV library. Reuses local symbols or downloads the
exact GNU build-ID's debuginfo into the GoT project. Never installs packages,
changes the driver, invokes sudo, or starts shadPS4/GDB/UMR. Report excludes the
large debug file and all user/game data. This diagnoses, not fixes, the crash.
"""
from __future__ import annotations

import datetime as dt
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import struct
import subprocess
import sys
import tarfile
import tempfile

BUILD_ID = '8a5c889901316f0caf5e5d8d10be9351dac2eb9c'
DRIVER_SHA = '7ad998b763b899649facdb4e53ac37d666dff372e84184accc0d19508e8c55e1'
DRIVER = Path('/usr/lib/x86_64-linux-gnu/libvulkan_radeon.so')
# File offsets reconstructed from the captured process mappings. Convert through
# ELF PT_LOAD headers before addr2line: file offsets need not equal ELF VMAs.
FILE_OFFSETS = (0x9CA84, 0x12A244, 0x1337A7, 0x13529B, 0x136E54, 0x137AE4)
FAULT_BYTES = bytes.fromhex('80482404')  # orb $0x4, 0x24(%rax)
MAX_DEBUG_BYTES = 256 * 1024 * 1024


def digest(path: Path) -> str:
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def run(args: list[str], timeout: int = 45) -> str:
    result = subprocess.run(args, stdin=subprocess.DEVNULL, capture_output=True,
                            text=True, errors='replace', timeout=timeout,
                            env=dict(os.environ, LC_ALL='C', DEBUGINFOD_URLS=''))
    if result.returncode:
        raise RuntimeError(f'{Path(args[0]).name} exit {result.returncode}: '
                           + result.stderr[-1500:])
    return result.stdout


def build_id(path: Path) -> str:
    output = run(['readelf', '-n', str(path)])
    values = set(re.findall(r'Build ID:\s*([0-9a-fA-F]+)', output))
    if len(values) != 1:
        raise RuntimeError('No unique GNU build ID: ' + str(path))
    return next(iter(values)).lower()


def file_offset_to_vma(path: Path, offset: int) -> int:
    with path.open('rb') as stream:
        data = stream.read(64)
        if len(data) != 64 or data[:6] != b'\x7fELF\x02\x01':
            raise RuntimeError('Expected an ELF64 little-endian library')
        header = struct.unpack('<16sHHIQQQIHHHHHH', data)
        if header[2] != 62:
            raise RuntimeError('Expected an x86-64 ELF library')
        phoff, entry_size, count = header[5], header[9], header[10]
        if entry_size < 56 or not 0 < count <= 256:
            raise RuntimeError('Unexpected ELF program-header layout')
        found = []
        for index in range(count):
            stream.seek(phoff + index * entry_size)
            raw = stream.read(56)
            if len(raw) != 56:
                raise RuntimeError('Truncated ELF program header')
            kind, flags, file_start, vaddr, _, file_size, _, _ = struct.unpack('<IIQQQQQQ', raw)
            if kind == 1 and flags & 1 and file_start <= offset < file_start + file_size:
                found.append(vaddr + offset - file_start)
        if len(found) != 1:
            raise RuntimeError(f'Offset {offset:#x} is not in one executable PT_LOAD segment')
        return found[0]


def verify_driver(path: Path) -> None:
    if not path.is_file():
        raise RuntimeError('Recorded RADV library is missing; no symbols requested')
    if digest(path) != DRIVER_SHA or build_id(path) != BUILD_ID:
        raise RuntimeError('RADV library differs from the captured build; refusing mismatched symbols')
    with path.open('rb') as stream:
        stream.seek(FILE_OFFSETS[0])
        if stream.read(len(FAULT_BYTES)) != FAULT_BYTES:
            raise RuntimeError('Recorded fault-instruction bytes do not match this library')


def parse_locations(output: str) -> list[dict]:
    entries, current = [], None
    for line in output.splitlines():
        if re.fullmatch(r'0x[0-9a-fA-F]+', line.strip()):
            current = {'address': line.strip(), 'detail': []}
            entries.append(current)
        elif current is not None:
            current['detail'].append(line)
    for item in entries:
        detail = item['detail']
        item['function_resolved'] = bool(detail and detail[0].strip() not in ('', '??'))
        item['source_line_resolved'] = any(re.search(r':(?:[1-9][0-9]*)(?:\s|$)', s)
                                             and not s.startswith('??') for s in detail[1::2])
    return entries


def acquire_symbols(home: Path, cache: Path, out: Path, state: dict) -> Path:
    cached = cache / (BUILD_ID + '.debug')
    candidates = [cached,
        Path('/usr/lib/debug/.build-id') / BUILD_ID[:2] / (BUILD_ID[2:] + '.debug'),
        home / '.cache/debuginfod_client' / BUILD_ID / 'debuginfo']
    custom = os.environ.get('DEBUGINFOD_CACHE_PATH')
    if custom:
        candidates.append(Path(custom) / BUILD_ID / 'debuginfo')
    elif os.environ.get('XDG_CACHE_HOME'):
        candidates.append(Path(os.environ['XDG_CACHE_HOME']) / 'debuginfod_client' / BUILD_ID / 'debuginfo')
    for candidate in candidates:
        if not candidate.exists() and not candidate.is_symlink():
            continue
        if candidate.is_symlink() or not candidate.is_file():
            if candidate == cached:
                raise RuntimeError('Unsafe pre-existing project symbol cache')
            continue
        try:
            matching = candidate.stat().st_size <= MAX_DEBUG_BYTES and build_id(candidate) == BUILD_ID
        except (RuntimeError, subprocess.TimeoutExpired):
            matching = False
        if matching:
            state['symbol_source'] = 'existing build-ID-matched file'
            return candidate
        if candidate == cached:
            raise RuntimeError('Project symbol cache has the wrong build ID; preserved, not overwritten')
    if not shutil.which('curl'):
        raise RuntimeError('curl missing and matching local debug symbols were not found')
    if shutil.disk_usage(cache).free < MAX_DEBUG_BYTES + 64 * 1024 * 1024:
        raise RuntimeError('Less than 320 MiB free; no debug-symbol download attempted')
    url = 'https://debuginfod.debian.net/buildid/' + BUILD_ID + '/debuginfo'
    state['symbol_url'] = url
    fd, name = tempfile.mkstemp(prefix='.got-symbol-', suffix='.partial', dir=cache)
    os.close(fd)
    part = Path(name)
    try:
        command = ['curl', '-4', '--fail', '--location', '--silent', '--show-error',
                   '--proto', '=https', '--proto-redir', '=https',
                   '--connect-timeout', '10', '--max-time', '180',
                   '--max-filesize', str(MAX_DEBUG_BYTES), '--output', str(part), url]
        print('SYMBOL_DOWNLOAD=DEBIAN_BUILD_ID; MAX_WAIT=180s; NO_INSTALL', flush=True)
        with (out / 'symbol-download.log').open('wb') as log:
            result = subprocess.run(command, stdin=subprocess.DEVNULL, stdout=log,
                                    stderr=subprocess.STDOUT, timeout=190)
        if result.returncode:
            raise RuntimeError(f'Debug-symbol download failed (curl {result.returncode}); '
                               'see symbol-download.log. No game was launched.')
        if not part.is_file() or part.stat().st_size > MAX_DEBUG_BYTES or build_id(part) != BUILD_ID:
            raise RuntimeError('Downloaded file is not the captured RADV build; not using it')
        os.replace(part, cached)
        state['symbol_source'] = 'Debian debuginfod, verified GNU build ID'
        return cached
    finally:
        # Remove only this invocation's own partial download, not prior caches.
        part.unlink(missing_ok=True)


def main() -> int:
    os.umask(0o077)
    home = Path.home()
    project = home / 'projects/shadps4/GhostOfTsushima'
    logroot = home / 'projects/shadps4/logs'
    if os.geteuid() == 0:
        print('STOP: run as chreece, without sudo.', file=sys.stderr)
        return 2
    if not project.is_dir() or project.is_symlink() or not logroot.is_dir() or logroot.is_symlink():
        print('STOP: existing GoT project/log directories are missing or symlinked.', file=sys.stderr)
        return 2
    out = Path(tempfile.mkdtemp(prefix='got-radv-symbols-' +
                              dt.datetime.now().strftime('%Y%m%d-%H%M%S') + '-', dir=logroot))
    state = {'result': 'STARTED', 'game_launched': False, 'gpu_access': False,
             'packages_installed': False, 'driver_modified': False,
             'build_id': BUILD_ID, 'driver_sha256': DRIVER_SHA,
             'started': dt.datetime.now().astimezone().isoformat()}
    lock = None
    rc = 0
    verified = False
    try:
        for tool in ('readelf', 'addr2line', 'objdump'):
            if not shutil.which(tool):
                raise RuntimeError('Missing ' + tool + '; no packages will be installed')
        verify_driver(DRIVER)
        verified = True
        print('RADV_BINARY_AND_FAULT_BYTES=MATCH_CAPTURE', flush=True)
        cache = project / 'tools/radv-symbols'
        if not cache.resolve().is_relative_to(project.resolve()):
            raise RuntimeError('Symbol-cache path escapes the GoT project')
        cache.mkdir(parents=True, exist_ok=True)
        if cache.is_symlink():
            raise RuntimeError('Refusing a symlinked symbol-cache directory')
        lock_path = cache / '.symbolize.lock'
        if lock_path.is_symlink():
            raise RuntimeError('Refusing a symlinked lock')
        lock = lock_path.open('a')
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        addresses = [file_offset_to_vma(DRIVER, offset) for offset in FILE_OFFSETS]
        state['frames'] = [{'captured_frame': i + 5, 'file_offset': hex(offset),
                            'elf_address': hex(address)}
                           for i, (offset, address) in enumerate(zip(FILE_OFFSETS, addresses))]
        (out / 'driver-elf.txt').write_text(run(['readelf', '-W', '-h', '-l', '-n', str(DRIVER)]))
        (out / 'fault-disassembly.txt').write_text(run(['objdump', '-d',
            '--start-address=' + hex(addresses[0]),
            '--stop-address=' + hex(addresses[0] + 128), str(DRIVER)]))
        if shutil.which('dpkg-query'):
            try:
                (out / 'driver-package.txt').write_text(run(['dpkg-query', '-W',
                    '-f=${Package}=${Version}\n', 'mesa-vulkan-drivers']))
            except RuntimeError as exc:
                state['package_warning'] = str(exc)
        symbols = acquire_symbols(home, cache, out, state)
        state['debug_file'] = str(symbols)
        state['debug_sha256'] = digest(symbols)
        state['debug_build_id'] = build_id(symbols)
        # Original return PCs and call-site PCs are both retained. Only non-fault
        # frames subtract one byte for source line resolution at the call site.
        exact = run(['addr2line', '-a', '-f', '-C', '-i', '-e', str(symbols)] +
                    [hex(address) for address in addresses], timeout=90)
        callsites = run(['addr2line', '-a', '-f', '-C', '-i', '-e', str(symbols)] +
                        [hex(address if i == 0 else address - 1)
                         for i, address in enumerate(addresses)], timeout=90)
        (out / 'resolved-exact-pcs.txt').write_text(exact)
        (out / 'resolved-call-sites.txt').write_text(callsites)
        parsed = parse_locations(exact)
        state['symbolization'] = parsed
        if len(parsed) != len(addresses) or not parsed[0]['function_resolved']:
            raise RuntimeError('Matching symbols did not resolve the faulting function; see report')
        state['result'] = 'FAULT_FUNCTION_RESOLVED_NO_GAME_RUN'
        print('\n=== Native fault and RADV callers ===\n' + callsites, flush=True)
    except Exception as exc:
        state.update(result='SYMBOLIZATION_STOPPED', error=str(exc))
        rc = 1
    finally:
        if verified:
            try:
                state['driver_unchanged'] = digest(DRIVER) == DRIVER_SHA
                if not state['driver_unchanged']:
                    state['result'] = 'DRIVER_CHANGED_DURING_READONLY_ANALYSIS'
                    rc = 1
            except OSError as exc:
                state['driver_after_error'] = str(exc)
                rc = 1
        if lock is not None:
            lock.close()
        state['finished'] = dt.datetime.now().astimezone().isoformat()
        (out / 'result.json').write_text(json.dumps(state, indent=2) + '\n')
        archive = out.with_name(out.name + '.tar.gz')
        with tarfile.open(archive, 'w:gz') as tar:
            for p in sorted(out.iterdir()):
                if p.is_file() and not p.is_symlink() and p.stat().st_size < 4 * 1024 * 1024:
                    tar.add(p, arcname=p.name, recursive=False)
        print('RESULT=' + state['result'], flush=True)
        if state.get('error'):
            print('DETAIL=' + state['error'], flush=True)
        print('GAME_LAUNCHED=NO; GPU_ACCESS=NO; REBUILD=NO', flush=True)
        print('UPLOAD=' + str(archive), flush=True)
    return rc


if __name__ == '__main__':
    sys.exit(main())
