#!/usr/bin/env python3
"""GoT: test the remaining RADV NUW metadata write, keeping earlier workarounds.

This is a narrowly scoped private-driver candidate, not a verified gameplay fix.
The verified retained library is copied, and only its four-byte flag-setting
instruction at 0x9ca54 is replaced. No shader arithmetic, additional optimizer
call, installed driver, emulator binary, or saved-game data is changed.

The pinned existing runner supplies its process checks, retry recovery, GDB
load verification, native crash evidence, screenshots, and bounded UMR broker.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

RUNNER_SHA = 'a1b08afea8ecb2f30341e82153b139ebc6d4959aa18405bfa8369b9537a8d885'
RETAINED_SHA = '5b69473448ecb10d0fd1d4451cd344b9e3d677a7ab14c47045a8f26c0e4426cf'
DEBUG_SHA = '2adff34225eaaf278be352821daefb0046a5a1b3cd96039ac10462997e1ecb48'
BUILD_ID = '8a5c889901316f0caf5e5d8d10be9351dac2eb9c'
LABEL = 'got-radv-nuw-complete-visual'
OFFSET = 0x9ca54
OLD = bytes.fromhex('80 48 24 04')
NEW = b'\x90' * 4


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def replace_once(text: str, before: str, after: str, label: str) -> str:
    if text.count(before) != 1:
        raise RuntimeError('Pinned helper changed at '+label+'; refusing an ambiguous rewrite')
    return text.replace(before, after, 1)


def original_paths(project: Path) -> tuple[Path, Path]:
    root = project / 'tools/radv-retained-25.0.7'
    return root/'libvulkan_radeon.so', root/'radeon_icd.json'


def candidate_paths(project: Path) -> tuple[Path, Path]:
    root = project / 'tools/radv-nuw-complete-25.0.7'
    return root/'libvulkan_radeon.so', root/'radeon_icd.json'


def patch_bytes(raw: bytes) -> bytes:
    if sha(raw) != RETAINED_SHA:
        raise RuntimeError('Input is not the exact retained v17 driver')
    if raw[OFFSET:OFFSET+len(OLD)] != OLD:
        raise RuntimeError('Remaining NUW instruction differs from the recorded build')
    for off, length in ((0x9ca84, 4), (0x12a72c, 5)):
        if raw[off:off+length] != b'\x90' * length:
            raise RuntimeError('Previously retained workaround is missing at '+hex(off))
    return raw[:OFFSET] + NEW + raw[OFFSET+len(OLD):]


def validate_candidate(raw: bytes) -> str:
    if raw[OFFSET:OFFSET+len(NEW)] != NEW:
        raise RuntimeError('Candidate has no completed NUW workaround')
    reverse = raw[:OFFSET]+OLD+raw[OFFSET+len(NEW):]
    if sha(reverse) != RETAINED_SHA or patch_bytes(reverse) != raw:
        raise RuntimeError('Candidate changed outside the one permitted instruction')
    return sha(raw)


def offline_symbols(project: Path, out: Path) -> dict:
    """Reuse existing cached symbols; no download and no GPU access."""
    debug = project / 'tools/radv-symbols' / (BUILD_ID+'.debug')
    tool = shutil.which('addr2line')
    if not tool or not debug.is_file() or debug.is_symlink():
        return {'status': 'CACHED_SYMBOLS_UNAVAILABLE', 'download_attempted': False}
    if debug.stat().st_size > 256*1024*1024:
        raise RuntimeError('Cached debug-symbol file exceeds the expected size bound')
    with debug.open('rb') as stream:
        actual = hashlib.file_digest(stream, 'sha256').hexdigest()
    if actual != DEBUG_SHA:
        raise RuntimeError('Cached Mesa symbols differ from the already verified report')
    command = [tool, '-a', '-f', '-i', '-e', str(debug),
               '0x9ca54', '0x9ca84', '0x396d25', '0x12a757']
    result = subprocess.run(command, capture_output=True, text=True, timeout=30,
                            stdin=subprocess.DEVNULL, env=dict(os.environ, LC_ALL='C'))
    (out/'offline-symbols.txt').write_text(result.stdout+result.stderr)
    if result.returncode:
        raise RuntimeError('Cached symbol resolution failed before any game launch')
    return {'status': 'EXACT_CACHED_SYMBOLS_RESOLVED', 'debug_sha256': actual,
            'addresses': ['0x9ca54','0x9ca84','0x396d25','0x12a757'],
            'download_attempted': False}


def install_candidate_hooks(ret) -> None:
    def verify(project: Path) -> tuple[Path, Path]:
        lib, icd = candidate_paths(project)
        validate_candidate(ret.read_regular(lib))
        doc = json.loads(ret.read_regular(icd, 32_000))
        if doc.get('ICD', {}).get('library_path') != str(lib):
            raise RuntimeError('Candidate ICD no longer points to the candidate library')
        old_lib, _ = original_paths(project)
        ret.validate_retained(ret.read_regular(old_lib))
        if sha(ret.read_regular(ret.SYSTEM_DRIVER)) != ret.SYSTEM_SHA:
            raise RuntimeError('Installed RADV changed; no candidate run allowed')
        return lib, icd

    def prepare(project: Path) -> dict:
        if os.geteuid() == 0:
            raise RuntimeError('Run as the normal user, not sudo python')
        for directory in (project, project/'tools'):
            if not directory.is_dir() or directory.is_symlink():
                raise RuntimeError('Missing/unsafe project directory: '+str(directory))
        old_lib, old_icd = original_paths(project)
        original = ret.read_regular(old_lib)
        ret.validate_retained(original)
        ret.validate_original(ret.read_regular(ret.SYSTEM_DRIVER))
        icd_before = ret.read_regular(old_icd, 32_000)
        template = json.loads(icd_before)
        if (template.get('ICD', {}).get('library_path') != str(old_lib) or
                not isinstance(template['ICD'].get('api_version'), str)):
            raise RuntimeError('Retained ICD is not the previously verified private setup')
        lib, icd = candidate_paths(project)
        if lib.parent.is_symlink():
            raise RuntimeError('Candidate directory is symlinked')
        if not lib.exists() and shutil.disk_usage(project).free < len(original)+16*1024*1024:
            raise RuntimeError('Insufficient project space for one private driver copy')
        lib.parent.mkdir(mode=0o700, exist_ok=True)
        changed = patch_bytes(original)
        ret.write_or_verify(lib, changed, 0o700)
        candidate_sha = validate_candidate(ret.read_regular(lib))
        template['ICD']['library_path'] = str(lib)
        ret.write_or_verify(icd, (json.dumps(template, indent=2)+'\n').encode(), 0o600)
        symbols = offline_symbols(project, lib.parent)
        verify(project)
        if ret.read_regular(old_icd, 32_000) != icd_before:
            raise RuntimeError('Retained ICD changed during preparation')
        info = {'result': 'PRIVATE_NUW_CANDIDATE_READY_NOT_GAMEPLAY_VERIFIED',
                'candidate_sha256': candidate_sha, 'retained_sha256': RETAINED_SHA,
                'private_library': str(lib), 'icd': str(icd),
                'difference_from_retained': {'offset': hex(OFFSET), 'old': OLD.hex(),
                                            'new': NEW.hex(), 'changed_bytes': 4},
                'arithmetic_instructions_changed': False,
                'additional_optimizer_calls_disabled': False,
                'retained_workarounds': ['v10_at_0x9ca84', 'v14_at_0x12a72c'],
                'system_driver_modified': False, 'retained_driver_modified': False,
                'emulator_rebuilt': False, 'gameplay_fixed': False,
                'hypothesis': 'unchecked ALU metadata cast can corrupt a constant parent pointer',
                'captured_pointer_xor': '0x400000000', 'offline_symbols': symbols}
        (lib.parent/'preparation.json').write_text(json.dumps(info, indent=2)+'\n')
        return info

    old_extend = ret.extend_gdb
    def extend(dual, project: Path) -> None:
        old_extend(dual, project)
        original = dual.gdb_source
        def gdb_source(session: Path) -> str:
            lib, _ = verify(project)
            candidate_sha = validate_candidate(ret.read_regular(lib))
            text = original(session)
            text = replace_once(text, 'RETAINED_DRIVER_SHA = '+repr(RETAINED_SHA),
                                'RETAINED_DRIVER_SHA = '+repr(candidate_sha), 'GDB hash')
            text = replace_once(text,
                'for off, length in ((0x9ca84, 4), (0x12a72c, 5)):',
                'for off, length in ((0x9ca54, 4), (0x9ca84, 4), (0x12a72c, 5)):',
                'GDB three-instruction verification')
            text = text.replace('GOT_RETAINED_RADV_LOADED=', 'GOT_RADV_NUW_COMPLETE_LOADED=')
            compile(text, '<NUW-candidate-GDB-script>', 'exec')
            return text
        dual.gdb_source = gdb_source
    ret.paths, ret.prepare_driver, ret.verify_files = candidate_paths, prepare, verify
    ret.extend_gdb = extend

    old_load = ret.load_stack
    def load_stack(project: Path):
        dual, base = old_load(project)
        old_gather = base.gather
        def gather(session: Path, report: Path) -> int:
            result = old_gather(session, report)
            lib, _ = candidate_paths(project)
            info = {'gameplay_verified': False, 'retained_driver_modified': False,
                    'additional_optimizer_calls_disabled': False}
            try:
                verify(project)
                info.update(candidate_sha256=validate_candidate(ret.read_regular(lib)),
                            candidate_unchanged=True, system_unchanged=True)
            except Exception as exc:
                info.update(candidate_unchanged=False, error=str(exc))
            base.store(report/'info/nuw-candidate.json', info)
            p = lib.parent/'offline-symbols.txt'
            if base.regular(p) and p.stat().st_size < 100_000:
                shutil.copyfile(p, report/'logs/nuw-offline-symbols.txt')
            return result
        base.gather = gather
        return dual, base
    ret.load_stack = load_stack


def load_runner():
    script = Path(__file__).resolve()
    path = script.with_name('got-radv-retained-capture.py')
    if not path.is_file() or path.is_symlink() or path.stat().st_size > 100_000:
        raise RuntimeError('Pinned retained helper missing; no run started')
    raw = path.read_bytes()
    if sha(raw) != RUNNER_SHA:
        raise RuntimeError('Retained helper differs from verified f7ef13b revision')
    text = raw.decode('utf-8')
    # In-memory parameterization only; the old helper and recorded launcher stay intact.
    text = replace_once(text, "LABEL = 'got-radv-retained-visual'", 'LABEL = '+repr(LABEL), 'label')
    text = replace_once(text,
        'PRIVATE_RADV=EXACT_RETAINED_V17_HASH; SYSTEM_RADV=UNTOUCHED',
        'PRIVATE_RADV=NUW_CANDIDATE; RETAINED_AND_SYSTEM_RADV=UNTOUCHED', 'setup status')
    text = text.replace('got-radv-retained-portable-ready-', 'got-radv-nuw-complete-portable-ready-')
    # Preserve the permissive unrelated-process handling while splitting actual NULs.
    text = replace_once(text, "argv = raw.split(b'\\\\0')",
                        'argv = raw.split(bytes([0]))', 'proc argv delimiter')
    spec = importlib.util.spec_from_file_location('got_nuw_retained_base', path)
    ret = importlib.util.module_from_spec(spec)
    exec(compile(text, str(path), 'exec'), ret.__dict__)
    ret.__file__ = str(script)
    install_candidate_hooks(ret)
    return ret


def main() -> int:
    return load_runner().main()


if __name__ == '__main__':
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)
    except Exception as exc:
        print('STOP: '+str(exc), file=sys.stderr)
        sys.exit(1)
