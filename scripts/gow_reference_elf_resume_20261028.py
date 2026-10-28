#!/usr/bin/env python3
"""Repair reference discovery; never substitute a different emulator executable.

Only real ELF program candidates consume hashing limits. Directory exclusions
are case-insensitive and renamed backups are considered. Search is read-only,
restricted to loose files under the project; archives and private data are not
unpacked. The hash-pinned prior runner retains all launch/locking/diary checks.
"""
from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path
import stat
import struct
import subprocess
import time
import types

RESUME_SHA = 'e9b17cab906873c7ea4e173ac04cfa70717837268e5de71d384f03173e6fa651'
RESUME_URL = ('https://raw.githubusercontent.com/Chreece/shadPS4/'
              '7a829931bf14560bb472b36a2b16c2d032c46864/scripts/gow_reference_resume_20261028.py')
REFERENCE_SHA = '23203faed6bcfbc17e3129c489e16face8fa64266a94275e730e559ca518cb8e'
SKIP_DIRS = frozenset(name.casefold() for name in (
    '.git', 'externals', 'node_modules', '.cache', '__pycache__', '_deps',
    'user', 'games', 'roms', 'savedata', 'saves', 'screenshots', 'shader',
    'shaders', 'logs', 'CMakeFiles', 'evidence', 'verification'))
MAX_DIRECTORIES = 100000
MAX_FILES = 500000
MAX_PROGRAMS = 4096
MAX_HASH_BYTES = 64 * 1024**3
MAX_SECONDS = 180


def identity(s):
    return (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns)


def directory_rank(name):
    lower = name.casefold()
    if any(word in lower for word in ('backup', 'old-binar', 'preserv', 'baseline', 'snapshot')):
        return (0, lower)
    order = {'godofwarragnarok': 1, 'upstream': 2, 'esde': 3, 'ghostoftsushima': 4, 'build': 5, 'candidate': 5}
    return (order.get(lower, 10), lower)


def elf_program(stream, size):
    """Recognize little-endian x86-64 program headers, not just an ELF magic.

    ET_DYN includes libraries. A nonzero entry point and an executable PT_LOAD
    separate program candidates here. An interpreter is not required, so static
    executables and static PIE remain eligible. No executable is ever run here.
    """
    header = stream.read(64)
    if len(header) != 64 or header[:7] != b'\x7fELF\x02\x01\x01':
        return 'NOT_X86_64_ELF_PROGRAM'
    kind, machine, version = struct.unpack_from('<HHI', header, 16)
    entry, phoff = struct.unpack_from('<QQ', header, 24)
    ehsize, phsize, phnum = struct.unpack_from('<HHH', header, 52)
    if machine != 62 or version != 1 or kind not in (2, 3) or entry == 0:
        return 'NOT_X86_64_ELF_PROGRAM'
    if ehsize != 64 or phsize != 56 or not 1 <= phnum <= 4096 or phoff < 64 or phoff + phsize * phnum > size:
        return 'INVALID_PROGRAM_HEADERS'
    stream.seek(phoff)
    table = stream.read(phsize * phnum)
    if len(table) != phsize * phnum:
        return 'INVALID_PROGRAM_HEADERS'
    for i in range(phnum):
        ptype, flags, offset, _va, _pa, filesz, memsz, _align = struct.unpack_from('<IIQQQQQQ', table, i * phsize)
        if ptype == 1 and flags & 1 and filesz and filesz <= memsz and offset <= size and filesz <= size - offset:
            return None
    return 'NO_EXECUTABLE_LOAD_SEGMENT'


def locate_reference(root, original, report_path, expected=REFERENCE_SHA,
                     max_seconds=MAX_SECONDS, max_hash_bytes=MAX_HASH_BYTES):
    root, report_path = Path(root).resolve(strict=True), Path(report_path)
    began = last_progress = time.monotonic()
    try:
        s = Path(original).lstat()
        original_state = {'status': 'symlink' if stat.S_ISLNK(s.st_mode) else 'present', 'bytes': s.st_size}
    except FileNotFoundError:
        original_state = {'status': 'missing'}
    except OSError as ex:
        original_state = {'status': 'unreadable', 'error': str(ex)[:300]}
    report = {'schema_version': 2, 'expected_sha256': expected, 'root': str(root),
              'original_path': str(original), 'original_path_state': original_state,
              'status': 'SEARCHING', 'read_only': True, 'entries': [], 'hash_bytes': 0,
              'directories': 0, 'files_examined': 0, 'program_candidates': 0,
              'skipped_counts': {}, 'errors': [], 'limits_hit': [], 'selected_path': None,
              'scope_exclusions': sorted(SKIP_DIRS),
              'scope': 'Loose regular files only; no symlink traversal or archive extraction; no filename requirement',
              'limits': {'directories': MAX_DIRECTORIES, 'files': MAX_FILES,
                         'programs': MAX_PROGRAMS, 'hash_bytes': max_hash_bytes, 'seconds': max_seconds}}
    seen = set()

    def save():
        report['elapsed_seconds'] = round(time.monotonic() - began, 3)
        report_path.write_text(json.dumps(report, indent=2) + '\n')

    def skip(reason):
        counts = report['skipped_counts']
        counts[reason] = counts.get(reason, 0) + 1

    def error(ex):
        skip('read_errors')
        if len(report['errors']) < 32:
            report['errors'].append(str(ex)[:400])

    def progress(force=False):
        nonlocal last_progress
        now = time.monotonic()
        if force or now - last_progress >= 10:
            save()
            print('REFERENCE_ELF_SEARCH programs=' + str(report['program_candidates']) +
                  ' files=' + str(report['files_examined']) +
                  ' hashed_gib=' + str(round(report['hash_bytes'] / 1024**3, 2)), flush=True)
            last_progress = now

    class LimitReached(Exception):
        pass

    def stop(reason):
        report['limits_hit'].append(reason)
        raise LimitReached(reason)

    def deadline():
        if time.monotonic() - began >= max_seconds:
            stop('time_limit')

    found = None
    try:
        for directory, dirs, names, dirfd in os.fwalk(root, topdown=True, follow_symlinks=False, onerror=error):
            deadline()
            report['directories'] += 1
            if report['directories'] > MAX_DIRECTORIES:
                stop('directory_limit')
            kept = []
            for name in dirs:
                lower = name.casefold()
                if lower in SKIP_DIRS or lower.startswith(('playtest-', 'evidence-')) or lower.endswith('-evidence'):
                    skip('excluded_directories')
                    continue
                try:
                    if stat.S_ISLNK(os.stat(name, dir_fd=dirfd, follow_symlinks=False).st_mode):
                        skip('directory_symlinks')
                        continue
                except OSError as ex:
                    error(ex)
                    continue
                kept.append(name)
            dirs[:] = sorted(kept, key=directory_rank)
            for name in sorted(names):
                deadline()
                report['files_examined'] += 1
                if report['files_examined'] > MAX_FILES:
                    stop('file_inventory_limit')
                path = Path(directory) / name
                try:
                    observed = os.stat(name, dir_fd=dirfd, follow_symlinks=False)
                    if not stat.S_ISREG(observed.st_mode):
                        skip('symlink_or_nonregular_file')
                        continue
                    if observed.st_size < 64:
                        skip('too_small_for_elf_header')
                        continue
                    if identity(observed) in seen:
                        skip('duplicate_inode')
                        continue
                    flags = os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW | os.O_NONBLOCK
                    fd = os.open(name, flags, dir_fd=dirfd)
                    with os.fdopen(fd, 'rb') as stream:
                        before = os.fstat(stream.fileno())
                        if identity(before) != identity(observed) or not stat.S_ISREG(before.st_mode):
                            error('FILE_CHANGED_BEFORE_READ=' + str(path))
                            continue
                        seen.add(identity(before))
                        reason = elf_program(stream, before.st_size)
                        if reason:
                            skip(reason)
                            continue
                        if report['program_candidates'] >= MAX_PROGRAMS:
                            stop('program_candidate_limit')
                        if before.st_size > max_hash_bytes - report['hash_bytes']:
                            stop('hash_byte_limit')
                        report['program_candidates'] += 1
                        row = {'path': str(path), 'resolved_path': str(path), 'bytes': before.st_size,
                               'identity_before': identity(before), 'status': 'HASHING'}
                        report['entries'].append(row)
                        stream.seek(0)
                        digest = hashlib.sha256()
                        while True:
                            deadline()
                            block = stream.read(min(4 * 1024**2, max_hash_bytes - report['hash_bytes'] + 1))
                            if not block:
                                break
                            report['hash_bytes'] += len(block)
                            if report['hash_bytes'] > max_hash_bytes:
                                stop('hash_byte_limit')
                            digest.update(block)
                        after = os.fstat(stream.fileno())
                        named = os.stat(name, dir_fd=dirfd, follow_symlinks=False)
                        if identity(before) != identity(after) or identity(after) != identity(named):
                            row['status'] = 'CHANGED_DURING_HASH_REFUSED'
                            error('FILE_CHANGED_DURING_HASH=' + str(path))
                            continue
                        row['sha256'] = digest.hexdigest()
                        row['status'] = 'DIFFERENT_BINARY'
                        if row['sha256'] == expected:
                            # Do not chmod a backup or substitute another executable.
                            if not os.access(name, os.X_OK, dir_fd=dirfd, follow_symlinks=False):
                                row['status'] = 'EXACT_BYTES_NOT_EXECUTABLE'
                            else:
                                row['status'] = 'EXACT_MATCH'
                                report['selected_path'] = str(path)
                                found = path
                                break
                        progress()
                except OSError as ex:
                    error(str(path) + ': ' + str(ex))
            progress()
            if found:
                break
    except LimitReached:
        pass
    if found:
        report['status'] = 'EXACT_REFERENCE_LOCATED'
    elif report['limits_hit'] or report['errors']:
        report['status'] = 'SEARCH_INCOMPLETE_NO_VERIFIED_MATCH'
    elif any(e.get('status') == 'EXACT_BYTES_NOT_EXECUTABLE' for e in report['entries']):
        report['status'] = 'EXACT_BYTES_FOUND_NOT_RUNNABLE_NO_MUTATION'
    else:
        report['status'] = 'NO_MATCH_IN_SCANNED_LOOSE_FILE_SCOPE'
    progress(force=True)
    print('REFERENCE_RESOLUTION=' + report['status'], flush=True)
    if found:
        print('EXACT_REFERENCE_LOCATED=' + str(found), flush=True)
        print('EXACT_REFERENCE_SHA256=' + expected, flush=True)
    else:
        print('GAME_NOT_LAUNCHED=YES; NO_FALLBACK_EXECUTABLE', flush=True)
    return found, report


def load_previous():
    scripts = Path.home() / 'projects/shadps4/scripts'
    raw = None
    for path in sorted(scripts.glob('gow-ref-resume-*.py')):
        if path.is_file() and not path.is_symlink() and path.stat().st_size < 100000:
            data = path.read_bytes()
            if hashlib.sha256(data).hexdigest() == RESUME_SHA:
                raw = data
                break
    if raw is None:
        raw = subprocess.run(['curl', '-4fsSL', '--connect-timeout', '15', '--max-time', '60', RESUME_URL],
                             stdin=subprocess.DEVNULL, capture_output=True, check=True, timeout=65).stdout
    if hashlib.sha256(raw).hexdigest() != RESUME_SHA:
        raise RuntimeError('PREVIOUS_RESOLVER_HASH_MISMATCH')
    module = types.ModuleType('verified_reference_resolver')
    module.__file__ = str(Path(__file__).resolve())
    exec(compile(raw, module.__file__, 'exec'), module.__dict__)
    module.locate_reference = locate_reference
    return module


if __name__ == '__main__':
    previous = load_previous()
    raise SystemExit(previous.install_resolution(previous.load_parent()).main())
